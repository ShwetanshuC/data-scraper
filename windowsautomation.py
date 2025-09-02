from __future__ import annotations

import time
import re
import base64
import pyperclip
import os
import tempfile
from io import BytesIO
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
 # ---- Robust ChatGPT composer finders ----

COMPOSER_SELECTORS = [
    "textarea[data-testid='prompt-textarea']",
    "div[contenteditable='true'][data-testid='prompt-textarea']",
    "div[contenteditable='true'][role='textbox']",
    "div[contenteditable='true']",
]

SEND_BUTTON_SELECTORS = [
    "button[data-testid='send-button']",
    "//button[@type='submit' and (contains(@aria-label,'Send') or contains(., 'Send'))]",
    "//button[@aria-label='Send message']",
    "//button[.//*[name()='svg' and (@aria-label='Send' or contains(@class,'send'))]]",
]

def _find_composer(driver: webdriver.Chrome, timeout: float = 5.0):
    end = time.time() + timeout
    while time.time() < end:
        for css in COMPOSER_SELECTORS:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, css)
                if elems:
                    el = elems[0]
                    if el.is_displayed():
                        return el
            except Exception:
                pass
        # XPaths (if any) can be added, but CSS should suffice.
        time.sleep(0.1)
    return None

def _click_send(driver: webdriver.Chrome) -> bool:
    # Try CSS first (fastest)
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "button[data-testid='send-button']")
        for b in btns:
            if b.is_displayed() and b.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                b.click()
                return True
    except Exception:
        pass
    # Try XPath variants
    for xp in SEND_BUTTON_SELECTORS[1:]:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for b in els:
                if b.is_displayed() and b.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    b.click()
                    return True
        except Exception:
            continue
    return False

# Your existing helpers (from your project)
from t import attach, find_editor
from chatgpt_response_checker import wait_for_chatgpt_response_via_send_button

# ---- ChatGPT tab helpers ----

def open_new_chat(driver: webdriver.Chrome, chat_handle: str, model_url: str = "https://chatgpt.com/?model=gpt-5") -> None:
    """Reset the ChatGPT composer by opening a brand-new chat thread.
    Tries clicking the New chat button; if not found, navigates directly to a fresh chat URL.
    """
    driver.switch_to.window(chat_handle)
    # Try clicking a New chat control if present
    selectors = [
        (By.CSS_SELECTOR, "button[data-testid='new-chat-button']"),
        (By.XPATH, "//button[.//span[normalize-space()='New chat'] or normalize-space()='New chat']"),
        (By.XPATH, "//a[normalize-space()='New chat']"),
    ]
    clicked = False
    for by, sel in selectors:
        try:
            els = driver.find_elements(by, sel)
            for el in els:
                if el.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    try:
                        el.click(); clicked = True
                    except Exception:
                        try:
                            ActionChains(driver).move_to_element(el).click().perform(); clicked = True
                        except Exception:
                            pass
                    if clicked:
                        break
        except Exception:
            continue
        if clicked:
            break
    if not clicked:
        # Hard reset by navigating to a fresh chat URL
        try:
            driver.get(model_url)
        except Exception:
            pass
    # Wait briefly for a composer to appear
    end = time.time() + 8.0
    while time.time() < end:
        ed = _find_composer(driver, timeout=0.5) or find_editor(driver, timeout=0.5)
        if ed:
            break
        time.sleep(0.2)

# ---------- New helpers for image handling ----------

def screenshot_to_base64(driver: webdriver.Chrome, *, target_width: int = 900, jpeg_quality: int = 40) -> str:
    """
    Capture the current tab as an image and return a BASE64 string suitable for
    data-URI embedding. We keep the 'no actual PNG on disk' requirement by
    working entirely in memory.

    Strategy:
      1) Try Pillow to downscale + convert to JPEG (much smaller than PNG).
      2) If Pillow is not available, temporarily shrink window size, take a PNG,
         and return its base64 (still smaller due to smaller viewport).
    """
    try:
        # Prefer full-resolution capture
        raw_png = driver.get_screenshot_as_png()
        if not raw_png:
            return ""
        # Try Pillow for downscale + JPEG recompression
        try:
            from io import BytesIO
            from PIL import Image  # type: ignore
            im = Image.open(BytesIO(raw_png)).convert("RGB")
            w, h = im.size
            if w > target_width:
                h2 = max(1, int(h * (target_width / float(w))))
                im = im.resize((target_width, h2))
            out = BytesIO()
            im.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
            return base64.b64encode(out.getvalue()).decode("utf-8")
        except Exception:
            # Fallback: shrink the window, retake a small PNG
            try:
                # Remember current size
                size = driver.get_window_size()
                old_w, old_h = size.get("width", 1200), size.get("height", 800)
                # Shrink to reduce PNG size
                driver.set_window_size(target_width, int(target_width * 0.62))
                time.sleep(0.2)
                small_png = driver.get_screenshot_as_png()
                # Restore size
                try:
                    driver.set_window_size(old_w, old_h)
                except Exception:
                    pass
                if small_png:
                    return base64.b64encode(small_png).decode("utf-8")
                # If even that failed, return the original (may be large)
                return base64.b64encode(raw_png).decode("utf-8")
            except Exception:
                return base64.b64encode(raw_png).decode("utf-8")
    except Exception:
        return ""

# ---------- Additional helpers for visible links and prompt building ----------

def get_visible_link_texts(driver: webdriver.Chrome, limit: int = 60) -> list[str]:
    """Return up to `limit` distinct, non-empty visible anchor texts from the page."""
    try:
        texts = driver.execute_script(
            r"""
            const anchors = Array.from(document.querySelectorAll('a'));
            const seen = new Set();
            const out = [];
            function visible(el){
              const rect = el.getBoundingClientRect();
              return !!(rect.width && rect.height);
            }
            for (const a of anchors){
              const t = (a.innerText || a.textContent || '').trim();
              if (!t) continue;
              if (!visible(a)) continue;
              if (seen.has(t)) continue;
              seen.add(t);
              out.push(t);
              if (out.length >= 200) break; // cap before we return
            }
            return out;
            """
        ) or []
        # Deduplicate and trim to limit
        uniq = []
        seen = set()
        for t in texts:
            tt = t.strip()
            if tt and tt not in seen:
                seen.add(tt)
                uniq.append(tt)
                if len(uniq) >= limit:
                    break
        return uniq
    except Exception:
        return []

# --- helper: nav_text reasonable match to links ---
def _nav_text_matches_links(nav_text: str, links: list[str]) -> bool:
    """Return True if `nav_text` reasonably matches one of the visible link texts.
    We allow exact and tolerant partial matches.
    """
    if not nav_text:
        return False
    t = nav_text.strip().lower()
    if not t:
        return False
    # Exact match first
    for L in links:
        if t == (L or '').strip().lower():
            return True
    # Tolerant partial match (either direction)
    for L in links:
        ll = (L or '').strip().lower()
        if not ll:
            continue
        if t in ll or ll in t:
            return True
    return False

# --- Host utilities ---
def _host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""

def switch_to_site_tab_by_host(driver: webdriver.Chrome, expected_host: str, fallback_handle: str | None = None) -> str | None:
    """Switch to the tab whose URL hostname matches expected_host (case-insensitive).
    If not found, switch to fallback_handle if provided. Returns the handle or None.
    """
    expected = (expected_host or "").lower()
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            cur = (driver.current_url or "").strip()
            host = _host_of(cur).lower()
            if host == expected or (expected and host.endswith("." + expected)):
                return h
        except Exception:
            continue
    if fallback_handle and fallback_handle in driver.window_handles:
        driver.switch_to.window(fallback_handle)
        return fallback_handle
    return None

def debug_where(driver: webdriver.Chrome, label: str = "") -> None:
    try:
        url = driver.current_url
        title = driver.title
        print(f"[where] {label} url={url} title={title}")
    except Exception:
        pass

# ----- Navigation robustness helpers -----

def _likely_staff_url(u: str) -> bool:
    u = (u or "").lower()
    keywords = ["team", "provider", "providers", "staff", "doctors", "our-team", "meet", "about-us", "about"]
    return any(k in u for k in keywords)


def _open_hamburger_if_present(driver: webdriver.Chrome) -> None:
    """Try to open mobile/desktop navigation menus so links become clickable."""
    candidates = [
        "//button[contains(@aria-label,'menu') or contains(@aria-label,'Menu') or contains(@aria-label,'navigation')]",
        "//button[contains(@class,'hamburger') or contains(@class,'menu') or contains(@class,'nav')]",
        "//*[@role='button' and (contains(@aria-label,'menu') or contains(@class,'menu'))]",
    ]
    for xp in candidates:
        try:
            btns = driver.find_elements(By.XPATH, xp)
            for b in btns[:2]:
                if b.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    try:
                        b.click()
                    except Exception:
                        try:
                            ActionChains(driver).move_to_element(b).click().perform()
                        except Exception:
                            pass
                    time.sleep(0.3)
        except Exception:
            continue


def _dispatch_real_click(driver: webdriver.Chrome, el) -> None:
    """Fire real-ish mouse events for stubborn JS sites."""
    driver.execute_script(
        """
        const el = arguments[0];
        const r = el.getBoundingClientRect();
        const x = r.left + r.width/2, y = r.top + r.height/2;
        ['mouseover','mousedown','mouseup','click'].forEach(t=>{
          const ev = new MouseEvent(t,{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window});
          el.dispatchEvent(ev);
        });
        """,
        el,
    )


def _wait_for_navigation(driver: webdriver.Chrome, prev_url: str, timeout: float = 5.0) -> bool:
    """Return True if URL changes or a likely staff keyword appears in the URL within timeout."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            cur = driver.current_url or ""
            if cur != prev_url:
                return True
            if _likely_staff_url(cur):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def navigate_to_suggested_section(driver: webdriver.Chrome, nav_text: str) -> bool:
    """Click the suggested nav_text using multiple strategies. Returns True on navigation."""
    _open_hamburger_if_present(driver)

    # Collect candidate elements by several strategies
    strategies = [
        ("LINK_TEXT", lambda: driver.find_elements(By.LINK_TEXT, nav_text)),
        ("PARTIAL_LINK_TEXT", lambda: driver.find_elements(By.PARTIAL_LINK_TEXT, nav_text)),
        ("XPATH exact a", lambda: driver.find_elements(By.XPATH, f"//a[normalize-space()='{nav_text}']")),
        ("XPATH contains a", lambda: driver.find_elements(By.XPATH, f"//a[contains(normalize-space(), '{nav_text}')]")),
        ("XPATH role=link", lambda: driver.find_elements(By.XPATH, f"//*[@role='link' and contains(normalize-space(), '{nav_text}')]")),
        ("XPATH button", lambda: driver.find_elements(By.XPATH, f"//button[contains(normalize-space(), '{nav_text}')]")),
        ("XPATH nav a", lambda: driver.find_elements(By.XPATH, f"//nav//a[contains(normalize-space(), '{nav_text}')]")),
    ]

    start_url = driver.current_url or ""
    for name, fn in strategies:
        try:
            elems = fn() or []
        except Exception:
            elems = []
        for el in elems:
            try:
                if not el.is_displayed():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                try:
                    el.click()
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(el).click().perform()
                    except Exception:
                        _dispatch_real_click(driver, el)
                if _wait_for_navigation(driver, start_url, timeout=6.0):
                    return True
            except Exception:
                continue

    # Fallback: heuristically click any visible link with a likely keyword
    try:
        heuristic = driver.find_elements(By.XPATH, "//a[@href]")
        for a in heuristic:
            try:
                href = a.get_attribute('href') or ''
                if _likely_staff_url(href) and a.is_displayed():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", a)
                    try:
                        a.click()
                    except Exception:
                        _dispatch_real_click(driver, a)
                    if _wait_for_navigation(driver, start_url, timeout=6.0):
                        return True
            except Exception:
                continue
    except Exception:
        pass

    return False

def build_staff_nav_prompt(image_b64: str | None, link_texts: list[str], *, max_chars: int = 9000) -> str:
    """
    Prefer an image-based prompt, but if the resulting message would be huge, fall
    back to a compact text-only prompt with the page's visible link texts.
    """
    task = (
        "You are seeing a clinic homepage. Identify the ONE best clickable element (exact visible link text) from the navigation bar "
        "that will lead to a page listing doctors/staff (e.g., 'Our Team', 'Providers', 'Meet the Doctors'). "
        "Reply with ONLY the exact link text, and ensure the text is accurate and visible on the image page."
    )
    if image_b64:
        candidate = f"{task}\n\n![homepage](data:image/jpeg;base64,{image_b64})"
        if len(candidate) <= max_chars:
            return candidate
    # Fallback: compact list of links
    compact = "\n".join(f"- {t}" for t in link_texts[:60]) or "(no visible links detected)"
    return (
        f"{task}\n\nHere are the visible links on the homepage. Choose only one of these EXACT texts:\n{compact}"
    )

def build_count_prompt(image_b64: str | None, *, max_chars: int = 9000) -> str:
    task = (
        "Count how many DOCTORS are listed on this page. Exclude non-physician staff. "
        "Reply with a single integer only."
    )
    if image_b64:
        candidate = f"{task}\n\n![staff](data:image/jpeg;base64,{image_b64})"
        if len(candidate) <= max_chars:
            return candidate
    # Fallback (no image): ask for best guess from structure/text (kept short)
    return (
        f"{task}\n\n(Screenshot omitted for length.) Use headings and visible names to infer the count."
    )

# --- New helper: prompt for phone/owner details as strict CSV ---

def build_details_prompt(image_b64: str | None, *, max_chars: int = 9000) -> str:
    """
    Ask for the Clinic Phone Number and Owner's first/last names, returned as a strict CSV:
    Phone, Owner First Name, Owner Last Name
    If a field is unknown/not visible, return it empty but keep the commas, e.g.: ",John,Smith" or "123-456-7890,,"
    Do NOT include any labels or extra words.
    """
    task = (
        "Extract these fields from this clinic website page ONLY from what is visible in the screenshot:\n"
        "1) Clinic Phone Number\n2) Owner First Name\n3) Owner Last Name\n\n"
        "Return exactly as: Phone, First, Last  (no labels, no extra words). "
        "If you cannot find a field on this page, leave it empty but keep its comma."
    )
    if image_b64:
        candidate = f"{task}\n\n![page](data:image/jpeg;base64,{image_b64})"
        if len(candidate) <= max_chars:
            return candidate
    return task

# --- Helper: build URL-only details prompt ---
def build_details_prompt_from_url(site_url: str) -> str:
    """
    Ask GPT to visit the given website and return the phone and owner names as strict CSV.
    Emphasize accuracy and leaving blanks if uncertain.
    """
    return (
        "ACCURACY IS PARAMOUNT. Visit the clinic's official website below and extract ONLY what is certain:\n"
        f"Website: {site_url}\n\n"
        "Return exactly one line in this format (no labels, no extra words):\n"
        "Phone, First, Last\n"
        "• If a field is unknown or not clearly stated, leave it empty but keep the comma.\n"
        "• Do not guess. Do not add explanations."
    )

def extract_first_integer(text: str) -> str:
    """
    Given a string returned from ChatGPT, attempt to extract the first
    occurrence of an integer.  If no integer is found, return the
    stripped text (useful as a fallback).  This helper is used when
    ChatGPT is asked to count doctors on a page; we expect it to return
    either a plain number or text containing a number.
    """
    if not text:
        return ""
    # Look for digits in the response
    m = re.search(r"\d+", text)
    if m:
        return m.group(0)
    return text.strip()

# ---------- New helpers for image upload and ChatGPT image prompt ----------


def save_temp_jpeg_screenshot(driver: webdriver.Chrome, *, target_width: int = 900, jpeg_quality: int = 40) -> str:
    """Capture current tab, compress to JPEG if possible, write to a temp file, and return its path.
    The file is intended to be ephemeral (we remove it right after uploading to ChatGPT)."""
    raw_png = driver.get_screenshot_as_png()
    if not raw_png:
        raise RuntimeError("screenshot failed")
    try:
        # Prefer JPEG to keep upload small
        from PIL import Image  # type: ignore
        im = Image.open(BytesIO(raw_png)).convert("RGB")
        w, h = im.size
        if w > target_width:
            h2 = max(1, int(h * (target_width / float(w))))
            im = im.resize((target_width, h2))
        fd, tmp_path = tempfile.mkstemp(prefix="gpt_shot_", suffix=".jpg")
        os.close(fd)
        im.save(tmp_path, format="JPEG", quality=jpeg_quality, optimize=True)
        return tmp_path
    except Exception:
        # Fallback: just write the PNG to a temp file (still ephemeral)
        fd, tmp_path = tempfile.mkstemp(prefix="gpt_shot_", suffix=".png")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(raw_png)
        return tmp_path

# --- Full-page capture using Chrome DevTools Protocol (no scrolling) ---


# --- Full-page capture using Chrome DevTools Protocol (safe scaled JPEG, no PIL) ---

def _cdp_capture_fullpage_jpeg(driver: webdriver.Chrome, *, target_width: int = 1400, quality: int = 50, max_pixels: int = 40_000_000) -> bytes:
    """Return a JPEG bytes of the full page using CDP captureScreenshot with scaling.
    We cap total pixels via clip.scale to avoid Pillow DecompressionBomb and huge payloads.
    """
    try:
        driver.execute_cdp_cmd("Page.enable", {})
    except Exception:
        pass
    metrics = driver.execute_cdp_cmd("Page.getLayoutMetrics", {})
    cs = metrics.get("contentSize", {})
    width = float(cs.get("width", 1200.0))
    height = float(cs.get("height", 2000.0))
    if width <= 0 or height <= 0:
        size = driver.get_window_size()
        width = float(size.get("width", 1200))
        height = float(size.get("height", 800))
    # Compute a safe scale: bound by target width AND max total pixels
    scale_w = min(1.0, target_width / max(width, 1.0))
    scale_pix = min(1.0, (max_pixels / max(width * height, 1.0)) ** 0.5)
    scale = max(0.05, min(scale_w, scale_pix))  # keep a sane lower bound
    # Also cap insanely tall pages to avoid driver timeouts
    height = min(height, 60000.0)
    clip = {"x": 0, "y": 0, "width": width, "height": height, "scale": scale}
    res = driver.execute_cdp_cmd(
        "Page.captureScreenshot",
        {"format": "jpeg", "quality": int(quality), "fromSurface": True, "captureBeyondViewport": True, "clip": clip},
    )
    b64 = res.get("data") or ""
    return base64.b64decode(b64)


def save_temp_fullpage_jpeg_screenshot(driver: webdriver.Chrome, *, target_width: int = 1400, jpeg_quality: int = 50) -> str:
    """Capture a single full-page screenshot via CDP as JPEG (scaled safely) and return temp file path.
    No PIL used; avoids DecompressionBomb. Falls back to viewport PNG->JPEG only if necessary.
    """
    try:
        jpeg_bytes = _cdp_capture_fullpage_jpeg(driver, target_width=target_width, quality=jpeg_quality)
        if jpeg_bytes:
            fd, tmp_path = tempfile.mkstemp(prefix="gpt_fullpage_", suffix=".jpg")
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(jpeg_bytes)
            return tmp_path
    except Exception:
        pass
    # Fallback: viewport capture -> minimal JPEG using PIL (much smaller image)
    raw_png = driver.get_screenshot_as_png()
    fd, tmp_path = tempfile.mkstemp(prefix="gpt_view_", suffix=".jpg")
    os.close(fd)
    try:
        from PIL import Image  # type: ignore
        im = Image.open(BytesIO(raw_png)).convert("RGB")
        w, h = im.size
        if w > target_width:
            h2 = max(1, int(h * (target_width / float(w))))
            im = im.resize((target_width, h2))
        im.save(tmp_path, format="JPEG", quality=jpeg_quality, optimize=True)
    except Exception:
        # If PIL fails, just dump the PNG
        with open(tmp_path, "wb") as f:
            f.write(raw_png)
    return tmp_path

def _find_composer_file_input(driver: webdriver.Chrome):
    """Locate ChatGPT's hidden <input type=file> used to attach images."""
    # Try common locations/selectors; DOM can vary over time
    candidates = [
        "form input[type='file']",
        "input[type='file'][accept*='image']",
        "input[type='file'][multiple]",
    ]
    for css in candidates:
        els = driver.find_elements(By.CSS_SELECTOR, css)
        for el in els:
            try:
                if (el.get_attribute("type") or "").lower() == "file":
                    return el
            except Exception:
                pass
    # Try to click an attach button to reveal the input
    for b in driver.find_elements(By.XPATH, "//button[@aria-label='Attach files' or .//*[name()='svg']]"):
        try:
            b.click()
        except Exception:
            pass
        els = driver.find_elements(By.CSS_SELECTOR, "form input[type='file']")
        if els:
            return els[0]
    return None


def _hide_camera_tile_in_composer(driver: webdriver.Chrome) -> None:
    """Hide ChatGPT's built-in gray camera tile inside the active composer form (visual only).
    This tile is not an attachment but shows up as a second tile. We apply a scoped CSS style
    to the current form so only this composer is affected.
    """
    try:
        form = driver.find_element(By.XPATH, "//form[.//textarea or .//div[@contenteditable='true']]")
    except Exception:
        return
    try:
        driver.execute_script(
            """
            (function(form){
              try{
                // Inject a one-off style that hides obvious camera tiles in the composer only
                const STYLE_ID = 'gpt-hide-camera-tile-style';
                let st = form.querySelector('#'+STYLE_ID);
                if(!st){
                  st = document.createElement('style');
                  st.id = STYLE_ID;
                  st.textContent = `
                    /* Hide camera / capture tiles inside this composer form */
                    [aria-label*="camera" i],
                    [class*="camera" i],
                    button[data-testid*="camera" i],
                    div[class*="capture" i] { display: none !important; }
                  `;
                  form.appendChild(st);
                }
                // Also try to hide any obvious camera widgets present right now
                const nodes = form.querySelectorAll('[aria-label*="camera" i], [class*="camera" i], button[data-testid*="camera" i], div[class*="capture" i]');
                nodes.forEach(n=>{ n.style.display = 'none'; });
              }catch(e){}
            })(arguments[0]);
            """,
            form,
        )
    except Exception:
        pass

def clear_chatgpt_attachments(driver: webdriver.Chrome, max_passes: int = 6) -> None:
    """Remove any pre-attached images in the *current* composer to avoid duplicates.
    We scope all queries to the active <form> so we don't touch prior messages.
    """
    try:
        form = driver.find_element(By.XPATH, "//form[.//textarea or .//div[@contenteditable='true']]")
    except Exception:
        return

    def _thumb_nodes():
        # Nodes representing attachment chips/thumbnails inside the composer
        xps = [
            ".//*[contains(@class,'preview') or contains(@class,'thumbnail')]",
            ".//*[@data-testid and (contains(@data-testid,'image') or contains(@data-testid,'attachment'))]",
            ".//figure[contains(@class,'image') or contains(@class,'attachment')]",
            ".//img/ancestor::*[contains(@class,'chip') or contains(@class,'thumb') or contains(@class,'preview')][1]",
        ]
        out = []
        for xp in xps:
            try:
                out.extend(form.find_elements(By.XPATH, xp))
            except Exception:
                pass
        # Dedup while preserving order
        seen = set()
        uniq = []
        for n in out:
            try:
                k = n.id
            except Exception:
                k = id(n)
            if k in seen:
                continue
            seen.add(k)
            if n.is_displayed():
                uniq.append(n)
        return uniq

    def _remove_buttons():
        # Click all removal/close buttons we can find in the composer
        btn_xps = [
            ".//button[@aria-label='Remove' or contains(@aria-label,'Remove')]",
            ".//button[contains(@data-testid,'remove') or contains(@data-testid,'close') or contains(@data-testid,'delete')]",
            ".//button[.='×' or .='x' or .='X']",
        ]
        clicked = False
        for xp in btn_xps:
            try:
                for b in form.find_elements(By.XPATH, xp):
                    if not b.is_displayed():
                        continue
                    try:
                        driver.execute_script("arguments[0].click();", b)
                    except Exception:
                        try:
                            ActionChains(driver).move_to_element(b).click().perform()
                        except Exception:
                            pass
                    time.sleep(0.05)
                    clicked = True
            except Exception:
                pass
        return clicked

    for _ in range(max_passes):
        # 1) Click any explicit remove/close buttons
        removed = _remove_buttons()
        time.sleep(0.05)
        # 2) If thumbnails persist, hard-remove nodes from DOM
        nodes = _thumb_nodes()
        if nodes:
            try:
                driver.execute_script(
                    "arguments[0].forEach(n=>{try{n.remove()}catch(e){}});",
                    nodes,
                )
                removed = True
            except Exception:
                pass
        # 3) As a belt-and-suspenders: remove any "camera tile" widgets inside the form
        try:
            cam = form.find_elements(By.XPATH, ".//*[contains(@aria-label,'camera') or contains(@class,'camera')]")
            if cam:
                driver.execute_script("arguments[0].forEach(n=>{try{n.remove()}catch(e){}});", cam)
                removed = True
        except Exception:
            pass
        # Stop if nothing left
        if not _thumb_nodes():
            break
        if not removed:
            # Avoid infinite loop if DOM resists removal
            break


# Helper for debugging: count visible attachments/thumbnails in the current composer <form>
def _count_attachments_for_debug(driver: webdriver.Chrome) -> int:
    try:
        form = driver.find_element(By.XPATH, "//form[.//textarea or .//div[@contenteditable='true']]")
    except Exception:
        return 0
    try:
        return len(form.find_elements(By.XPATH,
            ".//*[contains(@class,'preview') or contains(@class,'thumbnail') or contains(@data-testid,'image') or contains(@data-testid,'attachment')]"
        ))
    except Exception:
        return 0

def upload_image_to_chatgpt(driver: webdriver.Chrome, image_path: str, timeout: float = 10.0) -> None:
    """Attach the given image file to the ChatGPT composer and wait for the preview to appear."""
    file_input = _find_composer_file_input(driver)
    if not file_input:
        raise RuntimeError("Could not find ChatGPT file input to upload image")
    abs_path = os.path.abspath(image_path)
    file_input.send_keys(abs_path)
    # Wait for any thumbnail/preview to appear in the composer
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//*[contains(@class,'image') or contains(@class,'preview') or contains(@aria-label,'image preview') or contains(@data-testid,'image')]"
            ))
        )
    except Exception:
        # Continue anyway; sometimes the preview element names differ
        pass

# ---------- Reliable sending helpers ----------

def _find_send_button(driver: webdriver.Chrome):
    candidates = [
        "//button[@type='submit' and (contains(@aria-label,'Send') or contains(., 'Send'))]",
        "//button[contains(@data-testid,'send') or contains(@data-testid,'Send')]",
        "//button[@aria-label='Send message']",
        "//button[.//*[name()='svg' and (@aria-label='Send' or contains(@class,'send'))]]",
    ]
    for xp in candidates:
        els = driver.find_elements(By.XPATH, xp)
        if els:
            return els[0]
    return None


def _send_message(driver: webdriver.Chrome, editor) -> None:
    """Try multiple reliable ways to send: Enter, Send button, Cmd/Ctrl+Enter, form submit, and a final nudge."""
    def _enter():
        try:
            driver.execute_script("arguments[0].focus();", editor)
            editor.send_keys(Keys.ENTER)
            time.sleep(0.2)
        except Exception:
            pass

    def _cmd_ctrl_enter():
        try:
            ActionChains(driver).key_down(Keys.COMMAND).send_keys(Keys.ENTER).key_up(Keys.COMMAND).perform()
            time.sleep(0.2)
        except Exception:
            try:
                ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.ENTER).key_up(Keys.CONTROL).perform()
                time.sleep(0.2)
            except Exception:
                pass

    def _click_send_btn():
        _click_send(driver)
        time.sleep(0.2)

    def _form_submit():
        try:
            form = driver.find_element(By.XPATH, "//form[.//textarea or .//div[@contenteditable='true']]")
            driver.execute_script("arguments[0].dispatchEvent(new Event('submit', {bubbles:true,cancelable:true}))", form)
            time.sleep(0.2)
        except Exception:
            pass

    _enter()
    if _likely_streaming(driver): return
    _click_send_btn()
    if _likely_streaming(driver): return
    _cmd_ctrl_enter()
    if _likely_streaming(driver): return
    # Nudge to flip composer from 'attached but idle' to 'ready'
    try:
        editor.send_keys(Keys.SPACE)
        editor.send_keys(Keys.BACK_SPACE)
    except Exception:
        pass
    _click_send_btn()
    if _likely_streaming(driver): return
    _form_submit()
    if _likely_streaming(driver): return
    # Final hail mary
    try:
        editor.send_keys('.')
    except Exception:
        pass
    _enter()

def send_image_and_prompt_get_reply(driver: webdriver.Chrome, chat_handle: str, image_path: str, prompt: str) -> str:
    """
    Switch to ChatGPT, upload image via file input, paste prompt, send, and return reply text.
    Improved: re-find the composer after upload, extra send fallbacks, and debug logging.
    """
    driver.switch_to.window(chat_handle)
    debug_where(driver, label="second-prompt: on-chatgpt-tab")

    # Find a fresh composer BEFORE upload
    editor = _find_composer(driver, timeout=8) or find_editor(driver, timeout=8)
    if not editor:
        return ""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
    driver.execute_script("arguments[0].focus();", editor)

    # Clear any previous attachments and hide the non-attachment camera tile, then upload **once**
    clear_chatgpt_attachments(driver)
    _hide_camera_tile_in_composer(driver)
    upload_image_to_chatgpt(driver, image_path)
    time.sleep(0.25)  # small settle for DOM re-render
    _hide_camera_tile_in_composer(driver)
    # Guard: do not re-attach in this function; one attachment per message by design.

    # IMPORTANT: Re-find the composer AFTER upload (DOM often re-renders)
    editor = _find_composer(driver, timeout=8) or find_editor(driver, timeout=8)
    if not editor:
        return ""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
    driver.execute_script("arguments[0].focus();", editor)

    # Clear and paste prompt
    try:
        editor.send_keys(Keys.CONTROL, 'a'); editor.send_keys(Keys.DELETE)
    except Exception:
        pass
    pyperclip.copy(prompt)
    pasted = False
    try:
        ActionChains(driver).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform(); pasted = True
    except Exception:
        try:
            ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform(); pasted = True
        except Exception:
            try:
                editor.send_keys(prompt); pasted = True
            except Exception:
                pasted = False
    if not pasted:
        return ""

    # Robust send (Enter, Send button, Cmd/Ctrl+Enter, submit, nudge)
    _send_message(driver, editor)

    # Extra fallbacks: explicit JS click on the send button and re-focus send
    if not _likely_streaming(driver):
        try:
            btn = _find_send_button(driver)
            if btn:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.2)
        except Exception:
            pass
    if not _likely_streaming(driver):
        try:
            driver.execute_script("arguments[0].focus();", editor)
            editor.send_keys(Keys.SPACE); editor.send_keys(Keys.BACK_SPACE); editor.send_keys(Keys.ENTER)
        except Exception:
            pass

    reply = wait_for_chatgpt_response_via_send_button(
        driver,
        timeout=30,
        poll_interval=0.25,
        status_callback=None,
        composer_css="textarea[data-testid='prompt-textarea'], div[contenteditable='true'][data-testid='prompt-textarea'], div[contenteditable='true'][role='textbox']",
        nudge_text=".",
    )

    # One retry if nothing came back (sometimes the first send doesn't take with an image)
    if not reply:
        _send_message(driver, editor)
        reply = wait_for_chatgpt_response_via_send_button(
            driver,
            timeout=25,
            poll_interval=0.25,
            status_callback=None,
            composer_css="textarea[data-testid='prompt-textarea'], div[contenteditable='true'][data-testid='prompt-textarea'], div[contenteditable='true'][role='textbox']",
            nudge_text=".",
        )

    return reply or ""

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
    """
    Ensure A1..E1 have the exact headers (overwrite if necessary):
    Website | Clinic Phone Number | Owner First Name | Owner Last Name | Number of Doctors
    """
    goto_cell(driver, "A1")
    active = driver.switch_to.active_element
    headers = ["Website", "Clinic Phone Number", "Owner First Name", "Owner Last Name", "Number of Doctors"]
    for i, h in enumerate(headers):
        try:
            active.send_keys(Keys.CONTROL, 'a'); active.send_keys(Keys.DELETE)
        except Exception:
            pass
        pyperclip.copy(h)
        try:
            ActionChains(driver).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform()
        except Exception:
            ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
        if i < len(headers) - 1:
            active.send_keys(Keys.TAB)
    # Do not send ENTER here; keep focus on headers row.

# --- New helpers: normalize site URL and find row for site ---
def _normalize_site(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    # unify trailing slash and lowercase host portion
    try:
        p = urlparse(u)
        host = (p.hostname or "").lower()
        path = (p.path or "").rstrip("/")
        scheme = (p.scheme or "http").lower()
        norm = f"{scheme}://{host}{path or '/'}"
        return norm
    except Exception:
        return u.rstrip("/")

def find_row_for_site(driver: webdriver.Chrome, site: str) -> int | None:
    """
    Return the 1-based row index where Column A equals `site` (normalized),
    or None if not present. Scans the current non-empty A column quickly.
    """
    target = _normalize_site(site)
    vals = get_col_values(driver, "A")
    for idx, val in enumerate(vals, start=1):
        if _normalize_site(val) == target:
            return idx
    return None

# --- New paste helpers: paste into explicit row, and at next empty row ---
def paste_row_into_row(driver: webdriver.Chrome, row: int, values: list[str]) -> None:
    """
    Paste values across A..E at the specified 1-based `row` by **addressing each cell directly**.
    This avoids any TAB/ENTER navigation that can push the active cell to the wrong row/column.
    """
    cols = ["A", "B", "C", "D", "E"]
    vals = (values[:5] + [""] * 5)[:5]
    for col, val in zip(cols, vals):
        # Jump straight to the intended cell
        goto_cell(driver, f"{col}{row}")
        active = driver.switch_to.active_element
        # Clear and paste value
        try:
            active.send_keys(Keys.CONTROL, 'a'); active.send_keys(Keys.DELETE)
        except Exception:
            pass
        # If value is empty, leave the cell blank and avoid clipboard paste (prevents stray pastes).
        if val is None or str(val) == "":
            time.sleep(0.01)
            continue
        pyperclip.copy(str(val))
        # Try Cmd+V first (mac), then Ctrl+V (win/linux), finally direct send_keys
        pasted = False
        try:
            ActionChains(driver).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform()
            pasted = True
        except Exception:
            try:
                ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                pasted = True
            except Exception:
                try:
                    active.send_keys(str(val))
                    pasted = True
                except Exception:
                    pasted = False
        time.sleep(0.02)

def paste_row_at_next_empty(driver: webdriver.Chrome, values: list[str]) -> int:
    """
    Paste values across A..E at the next empty row and return that row index.
    """
    row = find_next_empty_row(driver)
    paste_row_into_row(driver, row, values)
    return row

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

def ask_gpt_and_get_reply(driver: webdriver.Chrome, chat_handle: str, prompt: str, response_timeout: float = 20) -> str:
    """
    Send a (potentially very large) prompt to ChatGPT by pasting from clipboard so
    data URI screenshots are included without saving a PNG file.
    """
    driver.switch_to.window(chat_handle)
    editor = find_editor(driver, timeout=10)
    if not editor:
        return ""

    # Focus the composer
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
    driver.execute_script("arguments[0].focus();", editor)

    # Clear any existing text
    try:
        editor.send_keys(Keys.CONTROL, 'a'); editor.send_keys(Keys.DELETE)
    except Exception:
        pass

    # Paste the prompt via clipboard (supports very long data URIs)
    pyperclip.copy(prompt)
    pasted = False
    try:
        ActionChains(driver).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform()
        pasted = True
    except Exception:
        try:
            ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
            pasted = True
        except Exception:
            pasted = False

    if not pasted:
        # Fallback: type (slower and may truncate very long prompts)
        try:
            editor.send_keys(prompt)
        except Exception:
            pass

    # Send the message reliably
    try:
        editor.send_keys(Keys.ENTER)
        time.sleep(0.15)
    except Exception:
        pass
    if not _likely_streaming(driver):
        _click_send(driver)
        time.sleep(0.2)
    if not _likely_streaming(driver):
        try:
            driver.execute_script("arguments[0].focus();", editor); editor.send_keys(Keys.ENTER)
        except Exception:
            pass

    reply = wait_for_chatgpt_response_via_send_button(
        driver,
        timeout=response_timeout,
        poll_interval=0.25,
        status_callback=None,
        composer_css='div#prompt-textarea.ProseMirror[contenteditable="true"]',
        nudge_text=".",
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
    Expect: 'Phone, OwnerFirst, OwnerLast, NumLocationsOrDoctors'
    Returns a 4-tuple (phone, first, last, locations/doctors), padding with "" if missing.
    IMPORTANT: Preserve empty fields. Do NOT drop empty tokens or we will shift columns.
    """
    s = _strip_fences_and_ws(reply).replace("\n", " ").replace("  ", " ")
    parts = [ _clean_piece(x) for x in s.split(",") ]
    # Preserve empties to keep correct alignment; pad/truncate to exactly 4
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

# --- New parser: strict 3-field CSV (Phone, First, Last) ---
def parse_three_reply(reply: str) -> tuple[str, str, str]:
    """
    Expect: 'Phone, First, Last' -> returns a 3-tuple, empty strings if missing.
    """
    s = _strip_fences_and_ws(reply).replace("\n", " ").replace("  ", " ")
    parts = [ _clean_piece(x) for x in s.split(",") ]
    while len(parts) < 3:
        parts.append("")
    phone, first, last = parts[:3]
    phone = re.sub(r"[^0-9xX()+\-.\s]", "", phone).strip()
    return phone, first, last

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
    # Track reserved rows for sites
    site_row_map: dict[str, int] = {}

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
            # For each website, open it in a new tab, capture a screenshot, ask ChatGPT
            # how to navigate to the staff/doctor page, click the suggested link,
            # take another screenshot, and ask ChatGPT to count the doctors.
            try:
                # Start a brand-new ChatGPT thread for this website
                open_new_chat(driver, chat_handle)
                # --- Open the website in its own tab ---
                driver.switch_to.window(sheet_handle)
                existing_handles = set(driver.window_handles)
                driver.execute_script("window.open(arguments[0], '_blank');", site)
                time.sleep(0.8)
                new_handles = [h for h in driver.window_handles if h not in existing_handles]
                site_handle = new_handles[-1] if new_handles else driver.window_handles[-1]
                driver.switch_to.window(site_handle)

                # Allow initial load
                time.sleep(2.0)

                # --- Screenshot + ask GPT where to click ---
                tmp_img1 = save_temp_fullpage_jpeg_screenshot(driver, target_width=1400, jpeg_quality=50)
                try:
                    prompt1 = (
                        "You are seeing a clinic homepage. Identify the ONE best clickable element (exact visible link text) from the navigation bar "
        "that will lead to a page listing doctors/staff (e.g., 'Our Team', 'Providers', 'Meet the Doctors'). "
        "Reply with ONLY the exact link text, and ensure the text is accurate and visible on the image page."
                    )
                    nav_reply = send_image_and_prompt_get_reply(driver, chat_handle, tmp_img1, prompt1)
                finally:
                    try:
                        os.remove(tmp_img1)
                    except Exception:
                        pass
                nav_text = (nav_reply or "").strip()
                expected_host = _host_of(site)
                # Ensure we are back on the clinic tab before DOM reads
                switched = switch_to_site_tab_by_host(driver, expected_host, fallback_handle=site_handle)
                if not switched:
                    print(f"[warn] Could not switch to site tab for host={expected_host}; continuing")
                    driver.switch_to.window(sheet_handle)
                    enter_sheets_iframe_if_needed(driver, timeout=5)
                    row = find_row_for_site(driver, site)
                    if row is None:
                        row = paste_row_at_next_empty(driver, [site, "", "", "", ""])
                    site_row_map[_normalize_site(site)] = row
                    continue
                debug_where(driver, label="before-candidate-scan")
                try:
                    driver.execute_script("return document.readyState")
                except Exception:
                    time.sleep(0.3)
                link_candidates = get_visible_link_texts(driver, limit=120)
                if not nav_text or not _nav_text_matches_links(nav_text, link_candidates):
                    print(f"[gpt] Navigation text not usable for {site}: '{nav_text}'. Candidates: {link_candidates[:8]}")
                    driver.switch_to.window(sheet_handle)
                    enter_sheets_iframe_if_needed(driver, timeout=5)
                    row = find_row_for_site(driver, site)
                    if row is None:
                        row = paste_row_at_next_empty(driver, [site, "", "", "", ""])
                    site_row_map[_normalize_site(site)] = row
                    continue
                # --- Click/link navigation with robust strategies ---
                if not navigate_to_suggested_section(driver, nav_text):
                    raise RuntimeError(f"Could not navigate using suggested link: {nav_text}")

                # After navigation, handle tabs and confirm we are on the clinic host
                time.sleep(1.0)
                expected_host = _host_of(site)
                switched = switch_to_site_tab_by_host(driver, expected_host, fallback_handle=site_handle)
                if switched:
                    site_handle = switched
                debug_where(driver, label="after-click")

                # --- Screenshot staff page + ask GPT for CSV (Phone, First, Last, Doctors) ---
                time.sleep(1.0)
                debug_where(driver, label="before-second-screenshot (site)")
                tmp_img2 = save_temp_fullpage_jpeg_screenshot(driver, target_width=1400, jpeg_quality=50)
                try:
                    # Start a new chat for the counting prompt (restore previous behavior)
                    open_new_chat(driver, chat_handle)
                    prompt2 = (
                        "You are seeing the clinic's staff/providers page. Using ONLY what is visible in this screenshot, "
                        "return exactly ONE line in strict CSV format with EXACTLY four fields: \n"
                        "Phone, First, Last, Doctors\n"
                        "Rules:\n"
                        "- Phone: the clinic's phone number if visible on this page; else leave empty.\n"
                        "- First, Last: the OWNER's first and last names. IF the owner name is not visible, use the FIRST doctor's first and last names instead.\n"
                        "- Doctors: the number of DOCTORS listed on this page as an INTEGER (exclude non-physician staff).\n"
                        "Formatting constraints:\n"
                        "- Output must be a single CSV line with exactly 3 commas (4 fields).\n"
                        "- If a field is unknown, leave it empty but keep its comma.\n"
                        "- Do not include labels, extra words, or additional commas.\n"
                        "Examples (illustrative):\n"
                        "555-123-4567,Jane,Doe,3\n"
                        ",John,Smith,2\n"
                    )
                    count_reply = send_image_and_prompt_get_reply(driver, chat_handle, tmp_img2, prompt2)
                finally:
                    try:
                        os.remove(tmp_img2)
                    except Exception:
                        pass
                # Parse combined CSV reply: Phone, First, Last, Doctors
                phone, first, last, doctor_count = parse_comma_reply(count_reply or "")
                # doctor_count already normalized to leading integer by parse_comma_reply
                print(f"[gpt] Parsed CSV for {site}: phone='{phone}', first='{first}', last='{last}', doctors='{doctor_count}'")

                # --- Close only the site tab, never ChatGPT/Sheets ---
                try:
                    if site_handle in driver.window_handles and site_handle not in (sheet_handle, chat_handle):
                        driver.switch_to.window(site_handle)
                        driver.close()
                except Exception:
                    pass
                driver.switch_to.window(sheet_handle)

                # Record the result.  Fill phone and owner fields if we got them.
                enter_sheets_iframe_if_needed(driver, timeout=5)
                key = _normalize_site(site)
                row = site_row_map.get(key) or find_row_for_site(driver, site)
                if row is None:
                    row = find_next_empty_row(driver)
                paste_row_into_row(driver, row, [site, phone, first, last, doctor_count])
                print(f"[sheet] wrote doctor count for {site}")
            except Exception as e:
                print(f"[error] failed for site {site}: {e}")
                # Ensure focus back on Sheets and record a blank result row
                driver.switch_to.window(sheet_handle)
                enter_sheets_iframe_if_needed(driver, timeout=5)
                row = find_row_for_site(driver, site)
                if row is None:
                    row = paste_row_at_next_empty(driver, [site, "", "", "", ""])
                site_row_map[_normalize_site(site)] = row
                continue

        # Small delay before rescanning
        time.sleep(0.4)

if __name__ == "__main__":
    monitor_loop()
