from __future__ import annotations

import time
import pyperclip
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from t import find_editor
from chatgpt_response_checker import wait_for_chatgpt_response_via_send_button


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
        time.sleep(0.1)
    return None


def _click_send(driver: webdriver.Chrome) -> bool:
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, "button[data-testid='send-button']")
        for b in btns:
            if b.is_displayed() and b.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                b.click()
                return True
    except Exception:
        pass
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


def _send_message(driver: webdriver.Chrome, editor) -> None:
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
    try:
        editor.send_keys(Keys.SPACE)
        editor.send_keys(Keys.BACK_SPACE)
    except Exception:
        pass
    _click_send_btn()
    if _likely_streaming(driver): return
    _form_submit()
    if _likely_streaming(driver): return
    try:
        editor.send_keys('.')
    except Exception:
        pass
    _enter()


def open_new_chat(driver: webdriver.Chrome, chat_handle: str, model_url: str = "https://chatgpt.com/?model=gpt-5") -> None:
    driver.switch_to.window(chat_handle)
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
        try:
            driver.get(model_url)
        except Exception:
            pass
    end = time.time() + 8.0
    while time.time() < end:
        ed = _find_composer(driver, timeout=0.5) or find_editor(driver, timeout=0.5)
        if ed:
            break
        time.sleep(0.2)


def ask_gpt_and_get_reply(driver: webdriver.Chrome, chat_handle: str, prompt: str, response_timeout: float = 20) -> str:
    driver.switch_to.window(chat_handle)
    editor = find_editor(driver, timeout=10)
    if not editor:
        return ""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
    driver.execute_script("arguments[0].focus();", editor)
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
            pasted = False
    if not pasted:
        try:
            editor.send_keys(prompt)
        except Exception:
            pass
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
        nudge_text='.',
    )
    return reply or ""


def find_chat_handle(driver: webdriver.Chrome) -> str | None:
    """Return handle for an existing ChatGPT tab (by URL host), if any."""
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
            if "chatgpt.com" in url or "openai.com" in url:
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
