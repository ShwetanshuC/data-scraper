"""Main orchestrator for site → staff page → CSV write."""

from __future__ import annotations

import time
import re
import os

from selenium import webdriver


# Your existing helpers (from your project)
from t import attach
from app.chat import open_new_chat
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
)
from app.prompts import parse_owner_doctors_reply, build_staff_csv_prompt
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
        try:
            z_vals = get_col_values(driver, WEBSITE_COL)
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
            time.sleep(0.6)
            continue

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
                # Reset chat thread for this site
                open_new_chat(driver, chat_handle)

                # Open site in new tab
                driver.switch_to.window(sheet_handle)
                site_handle = _open_tab_and_switch(driver, site, timeout=1.0)

                # Heuristic-only navigation to staff/providers page (no GPT prompt)
                expected_host = _host_of(site)
                switched = switch_to_site_tab_by_host(driver, expected_host, fallback_handle=site_handle)
                if not switched:
                    print(f"[warn] Could not switch to site tab for host={expected_host}; skipping")
                    driver.switch_to.window(sheet_handle)
                    enter_sheets_iframe_if_needed(driver, timeout=5)
                    row = find_row_for_site(driver, WEBSITE_COL, site)
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
                # 0) Detect if the current page already has a staff/providers section,
                # but prefer navigating to a dedicated staff page if one exists.
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
                # Final check: only fallback to on-page staff if we cannot find any staff-like link
                if not success:
                    best_link = None
                    try:
                        best_link = find_best_staff_href(driver)
                    except Exception:
                        best_link = None
                    if not best_link and saw_staff_section_here:
                        success = True
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
                    combined_reply = send_image_and_prompt_get_reply(driver, chat_handle, tmp_img2, build_staff_csv_prompt())
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
                first, last, doctor_count = parse_owner_doctors_reply(combined_reply or "")
                # Fallback: if GPT reported no doctors or a non-numeric value, store a friendly message
                if not re.match(r"^\d+$", (doctor_count or "").strip()) or int((doctor_count or "0").strip() or 0) == 0:
                    print(f"[gpt] Non-numeric or zero doctor count for {site}; writing fallback text.")
                    doctor_count = "no number of doctors listed on website"

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
                row = find_row_for_site(driver, WEBSITE_COL, site)
                if row is None:
                    print(f"[warn] Website not found in {WEBSITE_COL} for {site}; cannot write row")
                    if control:
                        try:
                            control.get('on_error', lambda: None)()
                        except Exception:
                            pass
                else:
                    set_cell_value(driver, OWNER_FIRST_COL, row, first)
                    set_cell_value(driver, OWNER_LAST_COL, row, last)
                    set_cell_value(driver, DOCTOR_COUNT_COL, row, doctor_count)
                    print(f"[sheet] wrote doctor count for {site}")
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
