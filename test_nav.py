# test_nav.py — runner for smart_nav.navigate_to_about_or_team (separate ChatGPT and Browse tabs)
# Opens the target site in a new tab, captures a Chrome screenshot, sends it +
# DOM candidates to ChatGPT, asks for the Team/Staff/Providers page, and clicks it.
# Usage:
#   python test_nav.py https://example.com --timeout 15 --max-candidates 60

import sys
import argparse
import time
from selenium import webdriver

from t import attach
from smart_nav import navigate_to_about_or_team


def _ensure_chatgpt_tab(driver: webdriver.Chrome) -> str:
    for h in driver.window_handles:
        driver.switch_to.window(h)
        title = (driver.title or "").lower()
        url = (driver.current_url or "").lower()
        if "chatgpt" in title or "chatgpt.com" in url or "openai.com" in url:
            return h
    driver.execute_script("window.open('https://chatgpt.com/', '_blank');")
    time.sleep(1.0)
    return driver.window_handles[-1]


def _ensure_browse_tab(driver: webdriver.Chrome) -> str:
    for h in driver.window_handles:
        driver.switch_to.window(h)
        url = (driver.current_url or "").lower()
        title = (driver.title or "").lower()
        if "chatgpt" in title or "chatgpt.com" in url or "openai.com" in url:
            continue
        return h
    driver.execute_script("window.open('about:blank', '_blank');")
    time.sleep(0.5)
    return driver.window_handles[-1]


def main():
    parser = argparse.ArgumentParser(description="Run GPT-guided navigation to a staff/providers/team page for a URL.")
    parser.add_argument("url", help="Website to open and navigate (e.g., https://example.com)")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--max-candidates", type=int, default=60)
    args = parser.parse_args()

    print("[attach] Connecting to Chrome with remote debugging ...")
    driver = attach()
    time.sleep(0.5)

    chat_handle = _ensure_chatgpt_tab(driver)
    print(f"[chat] Using ChatGPT tab: title='{driver.title}' url='{driver.current_url}'")

    browse_handle = _ensure_browse_tab(driver)
    driver.switch_to.window(browse_handle)
    print(f"[browse] Using Browse tab: title='{driver.title}' url='{driver.current_url}'")

    print(f"[nav] Navigating to: {args.url}")
    ok, chosen_id, reason = navigate_to_about_or_team(
        driver=driver,
        chat_handle=chat_handle,
        url=args.url,
        timeout=args.timeout,
        max_candidates=args.max_candidates,
        browse_handle=browse_handle,
    )

    print("---------- RESULT ----------")
    print(f"ok:         {ok}")
    print(f"chosen_id:  {chosen_id}")
    print(f"reason:     {reason}")
    print("----------------------------")

    time.sleep(1.0)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Usage: python test_nav.py https://example.com --timeout 15")
        sys.exit(1)
    main()
