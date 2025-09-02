"""
automation.py  — simplified row appender (macOS-friendly)
=========================================================

Flow:
  1) Write table headers once (A1..E1) with these columns:
       Website | Clinic Phone Number | Owner First Name | Owner Last Name | Number of locations
  2) Scan column Z for entries (websites).
  3) For each NEW entry:
       a) Send the website to ChatGPT
       b) Get the reply (expected as a single comma-separated line:
          Phone, OwnerFirst, OwnerLast, NumLocations)
       c) Parse & paste into the NEXT EMPTY ROW across A..E: [website, phone, first, last, locations]
  4) If no entry, loop back to scanning.

Notes:
  • Uses clipboard paste to avoid ChromeDriver “BMP only” limitations.
  • Robust Name-box navigation with JS fallback (prevents hangs).
  • No row insert shortcuts; we only compute “next empty row” by reading col A.
"""

import time
import re
import pyperclip
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException

# Your existing helpers (from your project)
from t import attach, find_editor
from chatgpt_response_checker import wait_for_chatgpt_response_via_send_button

# ---------- Config ----------
SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1gIJOeJQh4Vu8jhCdVY-ETR01zgQ58buQ2ztmUs7p7u0/edit?gid=0"
)

# ---------- Tab / frame utils ----------

def find_handle(driver: webdriver.Chrome, *, want_chatgpt: bool = False, want_sheets: bool = False):
    """Return the window handle for ChatGPT or Sheets (if found)."""
    for h in driver.window_handles:
        driver.switch_to.window(h)
        url = (driver.current_url or "").lower()
        title = (driver.title or "").lower()
        if want_chatgpt and ("chatgpt" in title or "openai.com" in url or "chatgpt.com" in url):
            return h
        if want_sheets and ("docs.google.com" in url and "/spreadsheets/" in url):
            return h
    return None

def enter_sheets_iframe_if_needed(driver: webdriver.Chrome, timeout: float = 10.0) -> None:
    """If the grid lives in an iframe, switch into it."""
    driver.switch_to.default_content()
    end = time.time() + timeout
    while time.time() < end:
        try:
            driver.find_element(By.CSS_SELECTOR, "input.waffle-name-box")
            return
        except NoSuchElementException:
            pass
        for f in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(f)
                if driver.find_elements(By.CSS_SELECTOR, "input.waffle-name-box"):
                    return
            except Exception:
                continue
        time.sleep(0.1)
    driver.switch_to.default_content()

# ---------- Robust Name-box navigation ----------

def _close_invalid_range_modal_if_present(driver: webdriver.Chrome) -> bool:
    """If the 'Invalid range' dialog is open, click OK and return True."""
    try:
        dlg = driver.find_elements(
            By.XPATH, "//div[@role='dialog' and .//div[contains(., 'Invalid range')]]"
        )
        if not dlg:
            return False
        ok = driver.find_elements(
            By.XPATH, "//div[@role='dialog']//button[.='OK' or .='Ok' or .='ok']"
        )
        if ok:
            ok[0].click()
            time.sleep(0.1)
            return True
    except Exception:
        pass
    return False

def goto_cell(driver: webdriver.Chrome, cell_ref: str) -> None:
    """
    Jump to a cell via the Name box; robust against flaky clicks.
    1) try native click/type
    2) JS focus+value+input fallback
    """
    enter_sheets_iframe_if_needed(driver, timeout=5)

    namebox_selectors = [
        "input.waffle-name-box",
        "input[aria-label='Name box']",
        "input[aria-label*='Name box']",
        "input[aria-label*='Range']",
    ]

    name_box = None
    for css in namebox_selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, css)
            if el.is_displayed():
                name_box = el
                break
        except Exception:
            continue
    if not name_box:
        raise NoSuchElementException("Name box not found (are we on the sheet tab?)")

    def js_set_and_submit(el, value):
        driver.execute_script(
            """
            const el = arguments[0], v = arguments[1];
            el.focus();
            el.value = '';
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.value = v;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            """,
            el, value
        )
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ENTER)

    for _ in range(2):
        try:
            try:
                name_box.click()
            except Exception:
                driver.execute_script("arguments[0].focus(); arguments[0].click && arguments[0].click();", name_box)
            try:
                name_box.clear()
                name_box.send_keys(cell_ref)
                name_box.send_keys(Keys.ENTER)
            except Exception:
                js_set_and_submit(name_box, cell_ref)

            time.sleep(0.05)
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.02)
            return
        except Exception:
            _close_invalid_range_modal_if_present(driver)
            time.sleep(0.05)

    js_set_and_submit(name_box, cell_ref)
    time.sleep(0.05)
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)

# ---------- Sheets helpers (copy, paste, headers, next-empty-row) ----------

def _copy_active_cell_text(driver: webdriver.Chrome) -> str:
    """Copy the active cell to clipboard and return the text."""
    ActionChains(driver).key_down(Keys.CONTROL).send_keys('c').key_up(Keys.CONTROL).perform()
    time.sleep(0.04)
    return (pyperclip.paste() or "").strip()

def read_cell(driver: webdriver.Chrome, cell_ref: str) -> str:
    """Read one cell's text."""
    goto_cell(driver, cell_ref)
    return _copy_active_cell_text(driver)

def get_col_values(driver: webdriver.Chrome, col_letter: str) -> list[str]:
    """Copy an entire column and return non-empty lines in order."""
    enter_sheets_iframe_if_needed(driver, timeout=10)
    goto_cell(driver, f"{col_letter}1")
    ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.SPACE).key_up(Keys.CONTROL).perform()
    time.sleep(0.05)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys('c').key_up(Keys.CONTROL).perform()
    time.sleep(0.08)
    raw = pyperclip.paste() or ""
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]

def find_next_empty_row(driver: webdriver.Chrome) -> int:
    """Next empty row = len(non-empty Col A) + 1 (headers assumed on row 1)."""
    col_a = get_col_values(driver, "A")
    return (len(col_a) + 1) if col_a else 2

def write_headers_once_simple(driver: webdriver.Chrome) -> None:
    """Write A1..E1 only if empty: Website | Clinic Phone Number | Owner First | Owner Last | Number of locations"""
    try:
        if read_cell(driver, "A1"):
            return
    except Exception:
        pass
    goto_cell(driver, "A1")
    active = driver.switch_to.active_element
    headers = ["Website", "Clinic Phone Number", "Owner First Name", "Owner Last Name", "Number of locations"]
    for i, h in enumerate(headers):
        active.send_keys(h)
        if i < len(headers) - 1:
            active.send_keys(Keys.TAB)
    active.send_keys(Keys.ENTER)
    time.sleep(0.03)

def paste_row_at_next_empty(driver: webdriver.Chrome, values: list[str]) -> None:
    """Paste values across A..E at next empty row (clipboard paste for reliability)."""
    row = find_next_empty_row(driver)
    goto_cell(driver, f"A{row}")
    vals = (values[:5] + [""] * 5)[:5]
    for i, val in enumerate(vals):
        active = driver.switch_to.active_element
        active.send_keys(Keys.CONTROL, 'a'); active.send_keys(Keys.DELETE)
        pyperclip.copy(val or "")
        ActionChains(driver).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform()
        time.sleep(0.02)
        if i < 4:
            active.send_keys(Keys.TAB)
    driver.switch_to.active_element.send_keys(Keys.ENTER)
    time.sleep(0.02)

# ---------- ChatGPT side ----------

def _assistant_count_and_last_text(driver: webdriver.Chrome):
    sels = [
        "div[data-message-author-role='assistant']",
        "article[data-message-author-role='assistant']",
    ]
    best_text, count = "", 0
    for sel in sels:
        nodes = driver.find_elements(By.CSS_SELECTOR, sel)
        if nodes:
            t = nodes[-1].text or ""
            if len(t) >= len(best_text):
                best_text = t
            count = max(count, len(nodes))
    return count, best_text

def _likely_streaming(driver: webdriver.Chrome) -> bool:
    try:
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            txt = (btn.text or "").strip().lower()
            aria = (btn.get_attribute("aria-label") or "").strip().lower()
            if "stop" in txt or "stop" in aria:
                return True
    except Exception:
        pass
    return False

def _ensure_prompt_sent(driver: webdriver.Chrome, editor, prompt: str, max_attempts=2) -> bool:
    base_count, base_last = _assistant_count_and_last_text(driver)
    editor.send_keys(Keys.CONTROL, 'a'); editor.send_keys(Keys.DELETE)
    editor.send_keys(prompt)
    editor.send_keys(Keys.ENTER)
    time.sleep(0.12)
    for _ in range(max_attempts):
        if _likely_streaming(driver):
            return True
        new_count, new_last = _assistant_count_and_last_text(driver)
        if new_count > base_count or (new_count == base_count and new_last != base_last):
            return True
        driver.execute_script("arguments[0].focus();", editor)
        editor.send_keys(Keys.ENTER)
        time.sleep(0.12)
    return False

def ask_gpt_and_get_reply(driver: webdriver.Chrome, chat_handle: str, prompt: str) -> str:
    driver.switch_to.window(chat_handle)
    editor = find_editor(driver, timeout=10)
    if not editor:
        return ""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
    driver.execute_script("arguments[0].focus();", editor)

    sent = _ensure_prompt_sent(driver, editor, prompt, max_attempts=2)
    if not sent:
        driver.execute_script("arguments[0].focus();", editor)
        editor.send_keys(" ")
        editor.send_keys(Keys.ENTER)

    reply = wait_for_chatgpt_response_via_send_button(
        driver,
        timeout=10,
        poll_interval=0.2,
        status_callback=None,
        composer_css='div#prompt-textarea.ProseMirror[contenteditable="true"]',
        nudge_text="xx",
    )
    return reply or ""

# ---------- Parsing / filtering ----------

def _strip_fences_and_ws(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    if s.startswith("```"):
        # remove any fenced code ticks
        s = "\n".join(ln for ln in s.splitlines() if not ln.strip().startswith("```")).strip()
    return s

def _clean_piece(p: str) -> str:
    """Trim, drop surrounding quotes/backticks."""
    if p is None:
        return ""
    x = p.strip().strip("`").strip().strip('"').strip("'")
    return x

def parse_comma_reply(reply: str) -> tuple[str, str, str, str]:
    """
    Expect: 'Phone, OwnerFirst, OwnerLast, NumLocations'
    Returns a 4-tuple (phone, first, last, locations), padding with "" if missing.
    """
    s = _strip_fences_and_ws(reply).replace("\n", " ").replace("  ", " ")
    parts = [ _clean_piece(x) for x in s.split(",") ]
    parts = [p for p in parts if p != ""]  # drop empty between commas
    while len(parts) < 4:
        parts.append("")
    phone, first, last, locs = parts[:4]

    # Optional light normalization
    # phone: keep common characters
    phone = re.sub(r"[^0-9xX()+\-.\s]", "", phone).strip()
    # locations: keep leading integer if any
    m = re.search(r"\d+", locs)
    if m:
        locs = m.group(0)
    return phone, first, last, locs

def filter_reply(text: str) -> tuple[str, str, str, str]:
    """High-level filter: convert raw assistant text into the 4 fields we store."""
    return parse_comma_reply(text)

# ---------- Main loop ----------

def monitor_loop(sheet_url: str = SHEET_URL) -> None:
    driver = attach()
    time.sleep(0.5)

    # Ensure tabs
    sheet_handle = find_handle(driver, want_sheets=True)
    if not sheet_handle:
        driver.execute_script(f"window.open('{sheet_url}', '_blank');")
        time.sleep(0.8)
        sheet_handle = driver.window_handles[-1]
    chat_handle = find_handle(driver, want_chatgpt=True)
    if not chat_handle:
        driver.execute_script("window.open('https://chatgpt.com/', '_blank');")
        time.sleep(0.8)
        chat_handle = driver.window_handles[-1]

    # Step 1: headers
    driver.switch_to.window(sheet_handle)
    write_headers_once_simple(driver)

    processed: set[str] = set()

    while True:
        # Step 2: scan column Z
        driver.switch_to.window(sheet_handle)
        try:
            z_vals = get_col_values(driver, "Z")
        except Exception as e:
            print(f"[scan] failed: {e}")
            time.sleep(0.6)
            continue

        new_sites = [v for v in z_vals if v and v not in processed]
        if not new_sites:
            time.sleep(0.6)  # Step 4: no entries → loop
            continue

        for site in new_sites:
            processed.add(site)

            # Step 3a: send to ChatGPT
            reply = ask_gpt_and_get_reply(driver, chat_handle, site)
            if not reply:
                print(f"[gpt] no reply for: {site}")
                continue

            # Step 3b: filter reply → (phone, first, last, locations)
            phone, first, last, locs = filter_reply(reply)

            # Step 3c: paste into next empty row (A..E)
            try:
                driver.switch_to.window(sheet_handle)
                paste_row_at_next_empty(
                    driver,
                    [site, phone, first, last, locs]
                )
                print(f"[sheet] wrote row for {site}")
            except Exception as e:
                print(f"[sheet] write failed for {site}: {e}")

        time.sleep(0.4)

if __name__ == "__main__":
    monitor_loop()