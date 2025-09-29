from __future__ import annotations

import time
import pyperclip
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains


def find_grok_handle(driver: webdriver.Chrome) -> str | None:
    """Return handle for an existing Grok tab (by URL host), if any."""
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
            url = (driver.current_url or "").lower()
            if "grok.com" in url or "x.ai" in url or "xai.com" in url:
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


COMPOSER_SELECTORS = [
    "textarea[aria-label*='Ask Grok' i]",
    "textarea[data-testid='prompt-textarea']",
    "div[contenteditable='true'][data-testid='prompt-textarea']",
    "div[contenteditable='true'][role='textbox']",
    "textarea",
    "div[contenteditable='true']",
]


def _find_composer(driver: webdriver.Chrome, timeout: float = 8.0):
    end = time.time() + timeout
    while time.time() < end:
        for css in COMPOSER_SELECTORS:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, css)
                for el in els:
                    if el.is_displayed():
                        return el
            except Exception:
                pass
        time.sleep(0.15)
    return None


def _click_send(driver: webdriver.Chrome) -> bool:
    candidates = [
        "button[data-testid='send-button']",
        "button[type='submit']",
        "button[aria-label*='send' i]",
        "//button[@type='submit' and (contains(@aria-label,'Send') or contains(., 'Send'))]",
        "//button[@aria-label='Send message']",
    ]
    # CSS first
    try:
        els = driver.find_elements(By.CSS_SELECTOR, candidates[0])
        for b in els:
            if b.is_displayed() and b.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                b.click(); return True
    except Exception:
        pass
    try:
        els = driver.find_elements(By.CSS_SELECTOR, candidates[1])
        for b in els:
            if b.is_displayed() and b.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                b.click(); return True
    except Exception:
        pass
    try:
        els = driver.find_elements(By.CSS_SELECTOR, candidates[2])
        for b in els:
            if b.is_displayed() and b.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                b.click(); return True
    except Exception:
        pass
    # XPath fallbacks
    for xp in candidates[3:]:
        try:
            for b in driver.find_elements(By.XPATH, xp):
                if b.is_displayed() and b.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    b.click(); return True
        except Exception:
            continue
    return False


def open_fresh_grok_chat(driver: webdriver.Chrome, grok_handle: str, model_url: str = "https://grok.com/") -> None:
    """Open Grok and ensure a fresh composer is ready."""
    driver.switch_to.window(grok_handle)
    try:
        # If not on Grok, navigate
        if "grok.com" not in (driver.current_url or "").lower():
            driver.get(model_url)
    except Exception:
        pass
    # Wait for composer
    end = time.time() + 10.0
    while time.time() < end:
        ed = _find_composer(driver, timeout=0.5)
        if ed:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].focus();", ed)
            except Exception:
                pass
            try:
                ed.send_keys(Keys.CONTROL, 'a'); ed.send_keys(Keys.DELETE)
            except Exception:
                pass
            break
        time.sleep(0.2)


def _last_assistant_text_generic(driver: webdriver.Chrome) -> str:
    # Try common containers for chat messages
    candidates = [
        "[data-message-author-role='assistant']",
        "[data-role='assistant']",
        "[data-testid*='assistant']",
        "main [data-testid] article",
        "main article",
        "[role='log'] *",
    ]
    for css in candidates:
        try:
            nodes = driver.find_elements(By.CSS_SELECTOR, css)
            if nodes:
                t = (nodes[-1].text or "").strip()
                if t:
                    return t
        except Exception:
            pass
    try:
        t = driver.execute_script(
            """
            const c = document.querySelector('main') || document.body;
            if (!c) return '';
            const last = c.lastElementChild || c;
            return last && last.innerText ? last.innerText.trim() : '';
            """
        )
        return (t or "").strip()
    except Exception:
        return ""


def wait_for_grok_response(driver: webdriver.Chrome, timeout: float = 1200.0, poll_interval: float = 0.5) -> str | None:
    """Wait until Grok finishes responding by monitoring last assistant text stabilization."""
    end = time.time() + float(timeout)
    last_text = _last_assistant_text_generic(driver)
    last_change = time.time()
    observed_any_change = False
    stable_required = 3.0  # seconds of stability to consider complete
    while time.time() < end:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        current = _last_assistant_text_generic(driver)
        if current != last_text:
            last_text = current
            last_change = time.time()
            observed_any_change = True
        else:
            if observed_any_change and (time.time() - last_change) >= stable_required:
                return last_text or None
        time.sleep(poll_interval)
    return last_text if (last_text and observed_any_change) else None


def ask_grok_and_get_reply(driver: webdriver.Chrome, grok_handle: str, prompt: str, response_timeout: float = 1200.0) -> str:
    """Paste prompt into Grok composer, send, and wait for reply text."""
    driver.switch_to.window(grok_handle)
    editor = _find_composer(driver, timeout=10)
    if not editor:
        return ""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
        driver.execute_script("arguments[0].focus();", editor)
    except Exception:
        pass
    try:
        editor.send_keys(Keys.CONTROL, 'a'); editor.send_keys(Keys.DELETE)
    except Exception:
        pass
    pyperclip.copy(prompt)
    pasted = False
    try:
        editor.send_keys(Keys.CONTROL, 'v'); pasted = True
    except Exception:
        try:
            ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform(); pasted = True
        except Exception:
            try:
                ActionChains(driver).key_down(Keys.COMMAND).send_keys('v').key_up(Keys.COMMAND).perform(); pasted = True
            except Exception:
                try:
                    editor.send_keys(prompt); pasted = True
                except Exception:
                    pasted = False
    time.sleep(0.15)
    # Ensure most of the prompt is present; if not, inject via JS and dispatch input event
    def _read_editor_value() -> str:
        try:
            tag = (editor.tag_name or "").lower()
        except Exception:
            tag = ""
        if tag == "textarea":
            try:
                v = editor.get_attribute("value") or ""
                return v.strip()
            except Exception:
                pass
        try:
            return (editor.text or "").strip()
        except Exception:
            return ""

    current = _read_editor_value()
    threshold = max(10, int(len(prompt) * 0.6))
    if len(current) < threshold:
        try:
            driver.execute_script(
                """
                (function(el, txt){
                  try{
                    el.focus();
                    const isTextarea = el.tagName && el.tagName.toLowerCase() === 'textarea';
                    if (isTextarea) {
                      el.value = txt;
                    } else {
                      el.textContent = txt;
                    }
                    const evt = new InputEvent('input', {bubbles: true, cancelable: true});
                    el.dispatchEvent(evt);
                  }catch(e){}
                })(arguments[0], arguments[1]);
                """,
                editor, prompt,
            )
            time.sleep(0.05)
            # Re-read after JS injection and consider it a successful paste if threshold met
            current = _read_editor_value()
            if len(current) >= threshold:
                pasted = True
        except Exception:
            pass
    # If neither paste nor JS injection managed to populate sufficiently, abort
    if not pasted and len(current) < threshold:
        return ""
    # Try multiple send strategies like ChatGPT flow
    try:
        editor.send_keys(Keys.ENTER); time.sleep(0.15)
    except Exception:
        pass
    try:
        ActionChains(driver).key_down(Keys.COMMAND).send_keys(Keys.ENTER).key_up(Keys.COMMAND).perform(); time.sleep(0.15)
    except Exception:
        try:
            ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.ENTER).key_up(Keys.CONTROL).perform(); time.sleep(0.15)
        except Exception:
            pass
    if not _click_send(driver):
        # Nudge
        try:
            editor.send_keys(Keys.SPACE); editor.send_keys(Keys.BACK_SPACE); editor.send_keys(Keys.ENTER)
        except Exception:
            pass
    reply = wait_for_grok_response(driver, timeout=response_timeout, poll_interval=0.5)
    return reply or ""


