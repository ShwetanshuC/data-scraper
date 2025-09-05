"""Main orchestrator for site → staff page → CSV write."""

from __future__ import annotations

import time
import re
import os

from selenium import webdriver


# Your existing helpers (from your project)
from t import attach
from app.chat import open_new_chat, open_fresh_chat
from app.screenshot import save_temp_fullpage_jpeg_screenshot
from app.utils import get_visible_link_texts, _nav_text_matches_links, _host_of, switch_to_site_tab_by_host, debug_where, normalize_site
from app.nav import (
    navigate_to_suggested_section,
    _likely_staff_url,
    _expand_specific_dropdown_and_navigate,
    _expand_parent_and_click_best_staff_child,
    _expand_dropdowns_and_try,
    _navigate_by_text_via_direct_get,
    _navigate_best_staff_link_anywhere,
    page_looks_like_staff_listing,
    find_best_staff_href,
)
from app.chat_attach import send_image_and_prompt_get_reply
from app.sheets import (
    find_sheets_handle,
    ensure_sheets_tab,
    enter_sheets_iframe_if_needed,
    get_col_values,
    find_row_for_site,
    set_cell_value,
    list_sheet_tab_names,
    select_sheet_tab_by_name,
    read_cell,
)
from app.prompts import parse_owner_doctors_reply, build_staff_csv_prompt, build_owner_only_prompt, parse_owner_only_reply
from app.chat import find_chat_handle
from app.config import SHEET_URL, WEBSITE_COL, OWNER_FIRST_COL, OWNER_LAST_COL, DOCTOR_COUNT_COL

# ---------- Orchestrator ----------


def monitor_loop(sheet_url: str = SHEET_URL, progress_cb=None, driver: webdriver.Chrome | None = None, control: dict | None = None) -> None:
    driver = driver or attach()
    time.sleep(0.1)

    def _report(msg: str):
        try:
            if progress_cb:
                progress_cb(msg)
        except Exception:
            pass

    def _wait_ready(drv, timeout: float = 2.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                state = drv.execute_script("return document.readyState || ''") or ''
                if state in ("interactive", "complete"):
                    return True
            except Exception:
                pass
            time.sleep(0.05)
        return False

    def _open_tab_and_switch(drv, url: str, timeout: float = 1.0):
        existing = set(drv.window_handles)
        drv.execute_script("window.open(arguments[0], '_blank');", url)
        end = time.time() + timeout
        new_h = None
        while time.time() < end:
            new = [h for h in drv.window_handles if h not in existing]
            if new:
                new_h = new[-1]
                break
            time.sleep(0.05)
        if not new_h:
            new_h = drv.window_handles[-1]
        drv.switch_to.window(new_h)
        _wait_ready(drv, timeout=1.0)
        return new_h

    def _combine_full_names(first: str, last: str) -> str:
        fs = [x.strip() for x in (first or '').split(';') if x.strip()]
        ls = [x.strip() for x in (last or '').split(';') if x.strip()]
        n = max(len(fs), len(ls))
        pairs = []
        for i in range(n):
            f = fs[i] if i < len(fs) else ''
            l = ls[i] if i < len(ls) else ''
            nm = (f + ' ' + l).strip()
            if nm:
                pairs.append(nm)
        return '; '.join(pairs)

    # Locate or open Sheets and ChatGPT tabs
    def _alive(drv) -> bool:
        try:
            _ = drv.window_handles
            return True
        except Exception:
            return False

    def _ensure_tabs(drv):
        nonlocal sheet_handle, chat_handle
        _report("Opening Google Sheet tab…")
        sh = ensure_sheets_tab(drv, sheet_url)
        drv.switch_to.window(sh)
        _report("Opening ChatGPT tab…")
        ch = find_chat_handle(drv)
        if not ch:
            try:
                existing = set(drv.window_handles)
                drv.execute_script("window.open('about:blank','_blank');")
                time.sleep(0.2)
                new_handles = [h for h in drv.window_handles if h not in existing]
                ch = new_handles[-1] if new_handles else drv.window_handles[-1]
            except Exception:
                ch = drv.window_handles[-1]
        open_new_chat(drv, ch)
        sheet_handle, chat_handle = sh, ch

    sheet_handle = None
    chat_handle = None
    _ensure_tabs(driver)
    _report("Ready. Monitoring sheet for new websites…")

    processed: set[str] = set()

    # Discover sheet tabs (if any) and start from the first one
    current_tab_name = None
    tab_names: list[str] = []
    try:
        try:
            driver.switch_to.window(sheet_handle)
            driver.switch_to.default_content()
        except Exception:
            pass
        tab_names = list_sheet_tab_names(driver) or []
        if tab_names:
            current_tab_name = tab_names[0]
            select_sheet_tab_by_name(driver, current_tab_name)
            _report(f"Found {len(tab_names)} tabs. Starting with: {current_tab_name}")
    except Exception:
        tab_names = []
        current_tab_name = None

    tab_index = 0

    while True:
        # Respect pause/stop and handle cooldown windows
        if control:
            try:
                control.get('reset_batch_if_needed', lambda: None)()
            except Exception:
                pass
            # Cooldown countdown with 1-second ticks
            try:
                _rem = int(control.get('cooldown_remaining', lambda: 0)())
            except Exception:
                _rem = 0
            if _rem and _rem > 0:
                _report(f"Cooling down… {_rem} seconds remaining")
                end_tick = time.time() + 1.0
                while time.time() < end_tick:
                    if control.get('should_stop', lambda: False)():
                        return
                    if control.get('should_pause', lambda: False)():
                        _report("Paused…")
                        while control.get('should_pause', lambda: False)():
                            if control.get('should_stop', lambda: False)():
                                return
                            time.sleep(0.2)
                    time.sleep(0.1)
                continue
            if control.get('should_stop', lambda: False)():
                return
            if control.get('should_pause', lambda: False)():
                _report("Paused…")
                while control.get('should_pause', lambda: False)():
                    if control.get('should_stop', lambda: False)():
                        return
                    time.sleep(0.2)
        # Ensure driver and tabs are alive
        if not _alive(driver):
            try:
                driver = attach()
                time.sleep(0.1)
            except Exception as _e:
                _report(f"Reattach failed: {_e}")
                time.sleep(0.8)
                continue
            sheet_handle = None
            chat_handle = None
            _ensure_tabs(driver)

        if sheet_handle not in driver.window_handles or chat_handle not in driver.window_handles:
            _ensure_tabs(driver)

        # Scan Website column for new entries (skip header and invalid cells)
        driver.switch_to.window(sheet_handle)
        # Detect column letters from headers (fallback to config if not present)
        try:
            from app.sheets import detect_header_columns
            cols_map = detect_header_columns(driver)
            website_col = cols_map.get('website', WEBSITE_COL)
            owner_first_col = cols_map.get('owner_first', OWNER_FIRST_COL)
            owner_last_col = cols_map.get('owner_last', OWNER_LAST_COL)
            owner_name_col = cols_map.get('owner_name')
            doctor_count_col = cols_map.get('doctor_count')  # None if header absent
        except Exception:
            website_col = WEBSITE_COL
            owner_first_col = OWNER_FIRST_COL
            owner_last_col = OWNER_LAST_COL
            owner_name_col = None
            doctor_count_col = None

        try:
            z_vals = get_col_values(driver, website_col)
        except Exception as e:
            print(f"[scan] failed: {e}")
            _report(f"Scan failed: {e}")
            time.sleep(0.6)
            continue
        # Filter out header and non-URLs; de-dup by normalized value
        cleaned = []
        for v in z_vals:
            if not v:
                continue
            t = v.strip()
            if not t or t.lower() == 'website':
                continue
            # Accept http(s) and common bare domains
            if not (t.startswith('http://') or t.startswith('https://')):
                # If it looks like a bare domain, prepend http:// for opening
                if '.' in t and ' ' not in t:
                    t = 'http://' + t
                else:
                    continue
            cleaned.append(t)
        new_sites = [s for s in cleaned if normalize_site(s) not in processed]
        if not new_sites:
            # Fallback: queue sites whose output cells are still empty
            missing_sites: list[str] = []
            try:
                for s in cleaned:
                    try:
                        row = find_row_for_site(driver, website_col, s)
                    except Exception:
                        row = None
                    if not row:
                        continue
                    try:
                        c_first = (read_cell(driver, f"{owner_first_col}{row}") or '').strip() if owner_first_col else ''
                        c_last = (read_cell(driver, f"{owner_last_col}{row}") or '').strip() if owner_last_col else ''
                        c_name = (read_cell(driver, f"{owner_name_col}{row}") or '').strip() if owner_name_col else ''
                        c_docs = (read_cell(driver, f"{doctor_count_col}{row}") or '').strip() if doctor_count_col else ''
                    except Exception:
                        c_first = c_last = c_name = c_docs = ''
                    if doctor_count_col:
                        owner_ok = (c_name or c_first or c_last)
                        docs_ok = bool(c_docs)
                        if not (owner_ok and docs_ok):
                            missing_sites.append(s)
                    else:
                        if not (c_name or c_first or c_last):
                            missing_sites.append(s)
            except Exception:
                missing_sites = []
            if missing_sites:
                new_sites = missing_sites
            else:
                # No work left on this tab. If multi-tab, move to next; else finish.
                if tab_names:
                    tab_index += 1
                    if tab_index >= len(tab_names):
                        _report("All tabs processed. Exiting.")
                        break
                    current_tab_name = tab_names[tab_index]
                    _report(f"Switching to next tab: {current_tab_name}")
                    select_sheet_tab_by_name(driver, current_tab_name)
                    time.sleep(0.6)
                    continue
                else:
                    _report("Sheet processed. Exiting.")
                    break

        for site in new_sites:
            if control and control.get('should_stop', lambda: False)():
                return
            if control and control.get('should_pause', lambda: False)():
                _report("Paused…")
                while control.get('should_pause', lambda: False)():
                    if control.get('should_stop', lambda: False)():
                        return
                    time.sleep(0.2)
            processed.add(normalize_site(site))
            try:
                _report(f"Processing site: {site}")
                # Reset chat thread for this site (force fresh, empty composer)
                open_fresh_chat(driver, chat_handle)

                # Open site in new tab
                driver.switch_to.window(sheet_handle)
                site_handle = _open_tab_and_switch(driver, site, timeout=1.0)

                # Decide site type: clinic-like or generic
                def _is_clinic_like() -> bool:
                    from selenium.webdriver.common.by import By as _By
                    try:
                        body = (driver.find_element(_By.TAG_NAME, 'body').text or '').lower()
                    except Exception:
                        body = ''
                    hints = (
                        'veterinar', 'animal hospital', 'pet hospital', 'clinic', 'hospital',
                        'our doctors', 'doctors', 'physicians', 'providers', 'appointment', 'patients'
                    )
                    return any(k in body for k in hints)

                # Sheet headers take precedence: if a doctor-count column exists, treat as clinic; else use heuristics
                is_clinic = bool(doctor_count_col) or _is_clinic_like()

                # Heuristic-only navigation to destination page (no GPT prompt)
                expected_host = _host_of(site)
                switched = switch_to_site_tab_by_host(driver, expected_host, fallback_handle=site_handle)
                if not switched:
                    print(f"[warn] Could not switch to site tab for host={expected_host}; skipping")
                    driver.switch_to.window(sheet_handle)
                    enter_sheets_iframe_if_needed(driver, timeout=5)
                    row = find_row_for_site(driver, website_col, site)
                    if row is None:
                        print(f"[warn] Website not found in {WEBSITE_COL} for {site}; skipping write")
                    continue

                # Debug: print and store best staff-like href visible anywhere on the current page
                pre_best_href = None
                try:
                    pre_best_href = find_best_staff_href(driver)
                    if pre_best_href:
                        print(f"[nav] best staff href (pre-nav scan): {pre_best_href}")
                    else:
                        print("[nav] no staff href found in pre-nav scan")
                except Exception as _e:
                    print(f"[nav] pre-nav scan error: {_e}")

                success = False
                if is_clinic:
                    # 0) Current page already staff-like?
                    saw_staff_section_here = False
                    try:
                        saw_staff_section_here = page_looks_like_staff_listing(driver)
                    except Exception:
                        saw_staff_section_here = False
                    # 1) Best staff-like link anywhere on the page
                    if not success and _navigate_best_staff_link_anywhere(driver):
                        success = True
                    # 2) Try common labels in nav (exact and dropdown expansion)
                    if not success:
                        guesses = [
                            "Our Team", "Team", "Providers", "Our Providers",
                            "Doctors", "Physicians", "Veterinarians", "Our Veterinarians", "Our Doctors", "Our Staff",
                            "Staff", "Medical Team", "Veterinary Team",
                            "Meet the Team", "Meet Our Team", "Meet Our Veterinarians", "Meet Our Doctors",
                        ]
                        for guess in guesses:
                            if navigate_to_suggested_section(driver, guess):
                                success = True; break
                            if _expand_dropdowns_and_try(driver, guess):
                                success = True; break
                    # 3) Expand likely parent menus and click best child
                    if not success:
                        parent_guesses = ["About", "About Us", "Our Practice", "Our Clinic", "Our Hospital", "Meet", "Who We Are"]
                        for parent in parent_guesses:
                            if _expand_parent_and_click_best_staff_child(driver, parent):
                                success = True; break
                    # 4) Direct href by text for last-resort guesses
                    if not success:
                        for guess in ("Team", "Providers", "Doctors"):
                            if _navigate_by_text_via_direct_get(driver, guess):
                                success = True; break
                    # Final check: allow on-page staff if no link found
                    if not success:
                        best_link = None
                        try:
                            best_link = find_best_staff_href(driver)
                        except Exception:
                            best_link = None
                        if not best_link and saw_staff_section_here:
                            success = True
                else:
                    # Generic company: try About/Team/Leadership/Owner-like pages
                    about_guesses = [
                        "About", "About Us", "Our Story", "Who We Are", "Company",
                        "Team", "Our Team", "Leadership", "Management", "Founder", "Founders", "Owner", "Board"
                    ]
                    for guess in about_guesses:
                        if navigate_to_suggested_section(driver, guess):
                            success = True; break
                        if _expand_dropdowns_and_try(driver, guess):
                            success = True; break
                    if not success:
                        success = True  # use current page
                if not success:
                    raise RuntimeError("Could not navigate to a staff/providers page heuristically")

                # Revalidate destination; if we discovered a better staff page, force navigation to it.
                try:
                    from urllib.parse import urlparse
                    try:
                        cur = driver.current_url or ""
                    except Exception:
                        cur = ""
                    # Prefer the pre-scanned best staff href if it differs from current
                    best = pre_best_href
                    if not best:
                        try:
                            best = find_best_staff_href(driver)
                        except Exception:
                            best = None
                    # Normalize paths for comparison
                    def _norm(u: str) -> str:
                        try:
                            p = urlparse(u)
                            path = (p.path or '/').rstrip('/') or '/'
                            return f"{p.scheme}://{p.netloc}{path}"
                        except Exception:
                            return (u or '').strip().rstrip('/')
                    if best and _norm(best) != _norm(cur):
                        print(f"[nav] forcing navigation to best staff href: {best}")
                        try:
                            driver.get(best)
                            time.sleep(0.6)
                        except Exception:
                            pass
                        # Re-check
                        try:
                            cur2 = driver.current_url or ""
                        except Exception:
                            cur2 = ""
                        if _likely_staff_url(cur2) or page_looks_like_staff_listing(driver):
                            success = True
                except Exception:
                    pass

                # Confirm on clinic host (prefer the deepest path tab)
                switched = switch_to_site_tab_by_host(driver, expected_host, fallback_handle=site_handle)
                if switched:
                    site_handle = switched
                debug_where(driver, label="after-click")

                # Screenshot staff page and ask for CSV
                debug_where(driver, label="before-second-screenshot (site)")
                try:
                    driver.switch_to.window(site_handle)
                except Exception:
                    pass
                tmp_img2 = save_temp_fullpage_jpeg_screenshot(driver, target_width=1400, jpeg_quality=50)
                try:
                    open_new_chat(driver, chat_handle)
                    if is_clinic:
                        combined_reply = send_image_and_prompt_get_reply(driver, chat_handle, tmp_img2, build_staff_csv_prompt())
                    else:
                        combined_reply = send_image_and_prompt_get_reply(driver, chat_handle, tmp_img2, build_owner_only_prompt())
                    # Count this attempt towards the 80/site ChatGPT image limit
                    if control:
                        try:
                            control.get('on_attempt', lambda: None)()
                            # Immediately start cooldown if batch limit reached (based on attempts)
                            if control.get('need_cooldown', lambda: False)():
                                control.get('begin_cooldown', lambda s: None)(30 * 60)
                                _report("Batch limit reached (80). Cooling down for 30 minutes…")
                        except Exception:
                            pass
                finally:
                    try:
                        os.remove(tmp_img2)
                    except Exception:
                        pass
                if is_clinic:
                    first, last, doctor_count = parse_owner_doctors_reply(combined_reply or "")
                    if not re.match(r"^\d+$", (doctor_count or "").strip()) or int((doctor_count or "0").strip() or 0) == 0:
                        print(f"[gpt] Non-numeric or zero doctor count for {site}; writing fallback text.")
                        doctor_count = "no number of doctors listed on website"
                else:
                    first, last = parse_owner_only_reply(combined_reply or "")
                    doctor_count = ""

                # Close site tab
                try:
                    if site_handle in driver.window_handles and site_handle not in (sheet_handle, chat_handle):
                        driver.switch_to.window(site_handle)
                        driver.close()
                except Exception:
                    pass
                driver.switch_to.window(sheet_handle)

                # Write result into existing row columns
                enter_sheets_iframe_if_needed(driver, timeout=5)
                row = find_row_for_site(driver, website_col, site)
                if row is None:
                    print(f"[warn] Website not found in {WEBSITE_COL} for {site}; cannot write row")
                    if control:
                        try:
                            control.get('on_error', lambda: None)()
                        except Exception:
                            pass
                else:
                    full_name = _combine_full_names(first, last)
                    if first or last:
                        if owner_first_col:
                            set_cell_value(driver, owner_first_col, row, first)
                        if owner_last_col:
                            set_cell_value(driver, owner_last_col, row, last)
                        if owner_name_col and not (owner_first_col and owner_last_col):
                            set_cell_value(driver, owner_name_col, row, full_name)
                    if is_clinic and doctor_count_col:
                        set_cell_value(driver, doctor_count_col, row, doctor_count)
                    print(f"[sheet] wrote doctor/owner info for {site}")
                    _report(f"Finished: {site}")
                    if control:
                        try:
                            control.get('on_success', lambda: None)()
                        except Exception:
                            pass

            except Exception as e:
                print(f"[error] failed for site {site}: {e}")
                _report(f"Error for {site}: {e}")
                if control:
                    try:
                        control.get('on_error', lambda: None)()
                    except Exception:
                        pass
                continue
        time.sleep(0.4)


if __name__ == "__main__":
    monitor_loop()
