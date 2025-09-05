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



def find_row_for_site(driver: webdriver.Chrome, col_letter: str, site: str) -> int | None:
    """Return 1-based row index in column `col_letter` whose value matches the site (normalized)."""
    vals = get_col_values(driver, col_letter)
    from app.utils import normalize_site
    target = normalize_site(site)
    row_idx = 0
    for i, v in enumerate(vals, start=1):
        if normalize_site(v) == target:
            row_idx = i
            break
    return row_idx or None


def set_cell_value(driver: webdriver.Chrome, col_letter: str, row: int, value: str) -> None:
    """Set a single cell value using grid paste semantics (never typing in the Name box or Formula bar).

    Flow:
    - Jump selection to the cell via Name box (goto_cell).
    - Ensure nothing editable (Name box / Formula bar) has focus (ESC + blur).
    - If empty value: issue Delete on the selected cell.
    - Else: clipboard copy value, then Cmd/Ctrl+V to paste directly into the grid.
    - Press Enter to commit.
    """
    goto_cell(driver, f"{col_letter}{row}")

    # Exit any input (name box / formula bar) so the grid owns the next keystrokes
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass
    try:
        driver.execute_script("document.activeElement && document.activeElement.blur && document.activeElement.blur();")
    except Exception:
        pass

    # If clearing, just hit Delete on the selected cell and commit
    if value is None or str(value) == "":
        try:
            ActionChains(driver).send_keys(Keys.DELETE).perform()
            ActionChains(driver).send_keys(Keys.ENTER).perform()
        except Exception:
            pass
        time.sleep(0.03)
        return

    # Paste the value directly into the grid
    import pyperclip
    pyperclip.copy(str(value))
    pasted = False
    try:
        ActionChains(driver).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform(); pasted = True
    except Exception:
        try:
            ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform(); pasted = True
        except Exception:
            pasted = False
    if not pasted:
        try:
            # Fallback: type as text and commit
            ActionChains(driver).send_keys(str(value)).perform()
        except Exception:
            pass
    try:
        ActionChains(driver).send_keys(Keys.ENTER).perform()
    except Exception:
        pass
    time.sleep(0.03)


def ensure_column_header(driver: webdriver.Chrome, col_letter: str, header_text: str) -> None:
    """Set header in row 1 for a given column if empty or different."""
    enter_sheets_iframe_if_needed(driver, timeout=5)
    try:
        current = read_cell(driver, f"{col_letter}1")
    except Exception:
        current = ""
    if (current or "").strip() != (header_text or "").strip():
        set_cell_value(driver, col_letter, 1, header_text)


# -------- Sheet tabs (bottom bar) helpers --------

def _find_sheet_tab_elements(driver: webdriver.Chrome):
    """Return a list of candidate tab elements at the bottom bar.

    Uses multiple selectors to be resilient to minor UI changes.
    Caller is responsible for driver context (default content).
    """
    sels = [
        (By.XPATH, "//div[contains(@class,'docs-sheet-tab') and .//div[contains(@class,'docs-sheet-tab-name')]]"),
        (By.XPATH, "//*[contains(@class,'docs-sheet-tab-name')]/ancestor::div[contains(@class,'docs-sheet-tab')]"),
        (By.XPATH, "//*[@role='tab' and (normalize-space(.)!='' or .//*[@class])]")
    ]
    for by, sel in sels:
        try:
            els = driver.find_elements(by, sel)
            if els:
                return els
        except Exception:
            continue
    return []


def list_sheet_tab_names(driver: webdriver.Chrome) -> list[str]:
    """Return visible sheet tab names in order.

    If none are found, returns an empty list, and callers can treat the sheet as single-tab.
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    tabs = _find_sheet_tab_elements(driver)
    names: list[str] = []
    for t in tabs:
        name = ""
        try:
            # Prefer the explicit name container
            name = (t.find_element(By.XPATH, ".//*[contains(@class,'docs-sheet-tab-name')]").text or "").strip()
        except Exception:
            try:
                name = (t.text or "").strip()
            except Exception:
                name = ""
        if name:
            names.append(name)
    # Deduplicate while preserving order
    seen = set()
    ordered = []
    for n in names:
        if n not in seen:
            ordered.append(n); seen.add(n)
    return ordered


def select_sheet_tab_by_name(driver: webdriver.Chrome, name: str) -> bool:
    """Click the sheet tab with the given name. Returns True on success."""
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    tabs = _find_sheet_tab_elements(driver)
    target = (name or "").strip().lower()
    for t in tabs:
        try:
            nm = ""
            try:
                nm = (t.find_element(By.XPATH, ".//*[contains(@class,'docs-sheet-tab-name')]").text or "").strip()
            except Exception:
                nm = (t.text or "").strip()
            if (nm or "").strip().lower() != target:
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'nearest'});", t)
            try:
                t.click(); time.sleep(0.2)
            except Exception:
                try:
                    ActionChains(driver).move_to_element(t).click().perform(); time.sleep(0.2)
                except Exception:
                    pass
            # Enter iframe again so grid ops work
            enter_sheets_iframe_if_needed(driver, timeout=5)
            return True
        except Exception:
            continue
    return False


def select_next_sheet_tab(driver: webdriver.Chrome, current_name: str) -> bool:
    """Activate the tab following the current tab by name. Returns True if switched."""
    names = list_sheet_tab_names(driver)
    if not names:
        return False
    try:
        idx = names.index((current_name or "").strip())
    except ValueError:
        idx = -1
    if idx >= 0 and idx + 1 < len(names):
        return select_sheet_tab_by_name(driver, names[idx + 1])
    return False


# -------- Header detection helpers --------

def _col_index_to_letter(idx1: int) -> str:
    """Convert 1-based column index to Excel-style letters (A, B, ..., Z, AA, AB, ...)."""
    n = idx1
    letters = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(ord('A') + rem))
    return ''.join(reversed(letters))


def get_row_values(driver: webdriver.Chrome, row: int) -> list[str]:
    """Return values of a given row as a list using copy semantics."""
    enter_sheets_iframe_if_needed(driver, timeout=8)
    goto_cell(driver, f"A{row}")
    ActionChains(driver).key_down(Keys.SHIFT).send_keys(Keys.SPACE).key_up(Keys.SHIFT).perform()
    time.sleep(0.06)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys('c').key_up(Keys.CONTROL).perform()
    time.sleep(0.08)
    raw = pyperclip.paste() or ""
    # Row copy usually yields a single line with tab-delimited cells
    first_line = raw.splitlines()[0] if raw else ""
    return [c.strip() for c in first_line.split("\t")]


def detect_header_columns(driver: webdriver.Chrome) -> dict:
    """Detect important column letters by looking at row 1 headers.

    Returns a mapping with possible keys: website, owner_first, owner_last,
    owner_name, doctor_count. Values are column letters like 'A', 'K', etc.
    Missing keys mean header isn't present.
    """
    headers = get_row_values(driver, 1)
    mapping: dict[str, str] = {}
    norm = [h.strip().lower() for h in headers]

    def find_col(pred) -> int | None:
        for i, h in enumerate(norm, start=1):
            try:
                if pred(h):
                    return i
            except Exception:
                continue
        return None

    # Website column
    i = find_col(lambda h: h in ("website", "site", "url") or ("website" in h and len(h) <= 20))
    if i: mapping['website'] = _col_index_to_letter(i)

    # Owner First/Last or Owner Name
    i = find_col(lambda h: ("owner" in h and "first" in h) or h in ("owner first", "first name", "owner fname"))
    if i: mapping['owner_first'] = _col_index_to_letter(i)
    i = find_col(lambda h: ("owner" in h and "last" in h) or h in ("owner last", "last name", "owner lname", "surname"))
    if i: mapping['owner_last'] = _col_index_to_letter(i)
    i = find_col(lambda h: h in ("owner", "owner name", "business owner", "founder") or ("owner" in h and "name" in h))
    if i: mapping['owner_name'] = _col_index_to_letter(i)

    # Doctor count (also accept providers/veterinarians synonyms)
    i = find_col(lambda h: ("doctor" in h and ("number" in h or "count" in h)) or h == "doctors"
                          or ("provider" in h and ("number" in h or "count" in h))
                          or ("veterinarian" in h and ("number" in h or "count" in h)))
    if i: mapping['doctor_count'] = _col_index_to_letter(i)

    return mapping
