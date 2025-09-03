from __future__ import annotations

import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains
import pyperclip


def find_sheets_handle(driver: webdriver.Chrome) -> str | None:
    """Return the window handle for a Google Sheets tab, if any.
    Matches by URL host only and restores focus afterward.
    """
    try:
        orig = driver.current_window_handle
    except Exception:
        orig = None
    found = None
    for h in list(driver.window_handles):
        try:
            driver.switch_to.window(h)
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            try:
                url = (driver.current_url or "").lower()
            except Exception:
                url = ""
            if ("docs.google.com" in url and "/spreadsheets/" in url):
                found = h
                break
        except Exception:
            continue
    if orig and orig in driver.window_handles:
        try:
            driver.switch_to.window(orig)
        except Exception:
            pass
    return found


def ensure_sheets_tab(driver: webdriver.Chrome, sheet_url: str) -> str:
    h = find_sheets_handle(driver)
    if h:
        return h
    driver.execute_script(f"window.open('{sheet_url}', '_blank');")
    time.sleep(0.8)
    return driver.window_handles[-1]


def enter_sheets_iframe_if_needed(driver: webdriver.Chrome, timeout: float = 10.0) -> None:
    """Switch into the Google Sheets grid iframe if present."""
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


def goto_cell(driver: webdriver.Chrome, cell_ref: str) -> None:
    """Jump to a cell via the Name box; robust against flaky clicks."""
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
            const set = (node,val)=>{
              try{ node.value = val; node.dispatchEvent(new InputEvent('input',{bubbles:true})); }catch(e){}
            };
            set(el, v);
            """,
            el, cell_ref,
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
            time.sleep(0.05)
    js_set_and_submit(name_box, cell_ref)
    time.sleep(0.05)
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)


def _copy_active_cell_text(driver: webdriver.Chrome) -> str:
    ActionChains(driver).key_down(Keys.CONTROL).send_keys('c').key_up(Keys.CONTROL).perform()
    time.sleep(0.04)
    return (pyperclip.paste() or "").strip()


def read_cell(driver: webdriver.Chrome, cell_ref: str) -> str:
    goto_cell(driver, cell_ref)
    return _copy_active_cell_text(driver)


def get_col_values(driver: webdriver.Chrome, col_letter: str) -> list[str]:
    enter_sheets_iframe_if_needed(driver, timeout=10)
    goto_cell(driver, f"{col_letter}1")
    ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.SPACE).key_up(Keys.CONTROL).perform()
    time.sleep(0.05)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys('c').key_up(Keys.CONTROL).perform()
    time.sleep(0.08)
    raw = pyperclip.paste() or ""
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


def find_next_empty_row(driver: webdriver.Chrome) -> int:
    col_a = get_col_values(driver, "A")
    return (len(col_a) + 1) if col_a else 2


def write_headers_once_simple(driver: webdriver.Chrome) -> None:
    try:
        if read_cell(driver, "A1"):
            return
    except Exception:
        pass
    goto_cell(driver, "A1")
    active = driver.switch_to.active_element
    headers = ["Website", "Clinic Phone Number", "Owner First Name", "Owner Last Name", "Number of Doctors"]
    for i, h in enumerate(headers):
        active.send_keys(h)
        if i < len(headers) - 1:
            active.send_keys(Keys.TAB)
    active.send_keys(Keys.ENTER)
    time.sleep(0.03)


def paste_row_into_row(driver: webdriver.Chrome, row: int, values: list[str]) -> None:
    cols = ["A", "B", "C", "D", "E"]
    vals = (values[:5] + [""] * 5)[:5]
    for col, val in zip(cols, vals):
        goto_cell(driver, f"{col}{row}")
        active = driver.switch_to.active_element
        try:
            active.send_keys(Keys.CONTROL, 'a'); active.send_keys(Keys.DELETE)
        except Exception:
            pass
        if val is None or str(val) == "":
            time.sleep(0.01)
            continue
        pyperclip.copy(str(val))
        pasted = False
        try:
            ActionChains(driver).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform(); pasted = True
        except Exception:
            try:
                ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform(); pasted = True
            except Exception:
                try:
                    active.send_keys(str(val)); pasted = True
                except Exception:
                    pasted = False
        time.sleep(0.02)


def paste_row_at_next_empty(driver: webdriver.Chrome, values: list[str]) -> int:
    row = find_next_empty_row(driver)
    paste_row_into_row(driver, row, values)
    return row

