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
from app.utils import get_visible_link_texts, _nav_text_matches_links, _host_of, switch_to_site_tab_by_host, debug_where
from app.nav import (
    navigate_to_suggested_section,
    _likely_staff_url,
    _expand_specific_dropdown_and_navigate,
    _expand_parent_and_click_best_staff_child,
    _expand_dropdowns_and_try,
    _navigate_by_text_via_direct_get,
    _navigate_best_staff_link_anywhere,
)
from app.chat_attach import send_image_and_prompt_get_reply
from app.sheets import (
    ensure_sheets_tab,
    enter_sheets_iframe_if_needed,
    goto_cell,
    read_cell,
    get_col_values,
    find_next_empty_row,
    write_headers_once_simple,
    paste_row_into_row,
    paste_row_at_next_empty,
)
from app.prompts import parse_comma_reply, parse_three_reply, extract_first_integer, build_nav_prompt, build_staff_csv_prompt
from app.chat import find_chat_handle
from app.config import SHEET_URL

# ---------- Orchestrator ----------


def monitor_loop(sheet_url: str = SHEET_URL) -> None:
    driver = attach()
    time.sleep(0.1)

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

    # Ensure tabs
    sheet_handle = ensure_sheets_tab(driver, sheet_url)
    driver.switch_to.window(sheet_handle)
    write_headers_once_simple(driver)

    chat_handle = find_chat_handle(driver)
    if not chat_handle:
        chat_handle = _open_tab_and_switch(driver, 'https://chatgpt.com/?model=gpt-5', timeout=1.0)
    open_new_chat(driver, chat_handle)

    processed: set[str] = set()
    site_row_map: dict[str, int] = {}

    while True:
        # Scan column Z
        driver.switch_to.window(sheet_handle)
        try:
            z_vals = get_col_values(driver, "Z")
        except Exception as e:
            print(f"[scan] failed: {e}")
            time.sleep(0.6)
            continue
        new_sites = [v for v in z_vals if v and v not in processed]
        if not new_sites:
            time.sleep(0.6)
            continue

        for site in new_sites:
            processed.add(site)
            try:
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
                    row = site_row_map.get(site) or paste_row_at_next_empty(driver, [site, "", "", "", ""])
                    site_row_map[site] = row
                    continue

                success = False
                # 1) Best staff-like link anywhere on the page
                if _navigate_best_staff_link_anywhere(driver):
                    success = True
                # 2) Try common labels in nav (exact and dropdown expansion)
                if not success:
                    guesses = [
                        "Our Team", "Team", "Providers", "Our Providers",
                        "Doctors", "Physicians", "Veterinarians", "Staff",
                        "Meet the Team", "Meet Our Team",
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
                if not success:
                    raise RuntimeError("Could not navigate to a staff/providers page heuristically")

                # Confirm on clinic host (quick)
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
                finally:
                    try:
                        os.remove(tmp_img2)
                    except Exception:
                        pass
                phone, first, last, doctor_count = parse_comma_reply(combined_reply or "")
                if not re.match(r"^\d+$", doctor_count or ""):
                    print(f"[gpt] No numeric doctor count returned for {site}. Skipping write.")
                    driver.switch_to.window(sheet_handle)
                    enter_sheets_iframe_if_needed(driver, timeout=5)
                    row = site_row_map.get(site) or paste_row_at_next_empty(driver, [site, "", "", "", ""])
                    site_row_map[site] = row
                    continue

                # Close site tab
                try:
                    if site_handle in driver.window_handles and site_handle not in (sheet_handle, chat_handle):
                        driver.switch_to.window(site_handle)
                        driver.close()
                except Exception:
                    pass
                driver.switch_to.window(sheet_handle)

                # Write result
                enter_sheets_iframe_if_needed(driver, timeout=5)
                row = site_row_map.get(site)
                if row is None:
                    row = find_next_empty_row(driver)
                paste_row_into_row(driver, row, [site, phone, first, last, doctor_count])
                print(f"[sheet] wrote doctor count for {site}")

            except Exception as e:
                print(f"[error] failed for site {site}: {e}")
                driver.switch_to.window(sheet_handle)
                enter_sheets_iframe_if_needed(driver, timeout=5)
                row = site_row_map.get(site) or paste_row_at_next_empty(driver, [site, "", "", "", ""])
                site_row_map[site] = row
                continue
        time.sleep(0.4)


if __name__ == "__main__":
    monitor_loop()
