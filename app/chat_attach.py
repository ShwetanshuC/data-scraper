from __future__ import annotations

import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from chatgpt_response_checker import wait_for_chatgpt_response_via_send_button
import app.chat as chat
from t import find_editor


def _find_composer_file_input(driver: webdriver.Chrome):
    candidates = [
        "form input[type='file']",
        "input[type='file'][accept*='image']",
        "input[type='file'][multiple]",
        "input[type='file']",
    ]
    for css in candidates:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    if (el.get_attribute("type") or "").lower() == "file":
                        return el
                except Exception:
                    continue
        except Exception:
            continue
    # Try attach/upload buttons to reveal input
    xps = [
        "//button[contains(@aria-label,'Attach') or contains(@aria-label,'Upload') or contains(@aria-label,'Image') or contains(@aria-label,'Photo')]",
        "//button[contains(.,'Attach') or contains(.,'Upload') or contains(.,'Image') or contains(.,'Photo')]",
        "//*[self::button or self::a][contains(@class,'attach') or contains(@class,'upload') or contains(@class,'image') or contains(@class,'photo')]",
        "//label[contains(@for,'file') or contains(.,'Upload')]",
    ]
    for xp in xps:
        try:
            for b in driver.find_elements(By.XPATH, xp):
                if not b.is_displayed():
                    continue
                try:
                    b.click()
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(b).click().perform()
                    except Exception:
                        pass
                time.sleep(0.2)
        except Exception:
            continue
    for css in candidates:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                try:
                    if (el.get_attribute("type") or "").lower() == "file":
                        return el
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _hide_camera_tile_in_composer(driver: webdriver.Chrome) -> None:
    try:
        form = driver.find_element(By.XPATH, "//form[.//textarea or .//div[@contenteditable='true']]")
    except Exception:
        return
    try:
        driver.execute_script(
            """
            (function(form){
              try{
                const STYLE_ID = 'gpt-hide-camera-tile-style';
                let st = form.querySelector('#'+STYLE_ID);
                if(!st){
                  st = document.createElement('style');
                  st.id = STYLE_ID;
                  st.textContent = `
                    [aria-label*="camera" i], [class*="camera" i], button[data-testid*="camera" i], div[class*="capture" i] { display: none !important; }
                  `;
                  form.appendChild(st);
                }
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
    try:
        form = driver.find_element(By.XPATH, "//form[.//textarea or .//div[@contenteditable='true']]")
    except Exception:
        return
    def _thumb_nodes():
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
        seen, uniq = set(), []
        for n in out:
            k = getattr(n, 'id', None) or id(n)
            if k in seen: continue
            seen.add(k)
            if n.is_displayed(): uniq.append(n)
        return uniq
    def _remove_buttons():
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
        removed = _remove_buttons(); time.sleep(0.05)
        nodes = _thumb_nodes()
        if nodes:
            try:
                driver.execute_script("arguments[0].forEach(n=>{try{n.remove()}catch(e){}});", nodes)
                removed = True
            except Exception:
                pass
        try:
            cam = form.find_elements(By.XPATH, ".//*[contains(@aria-label,'camera') or contains(@class,'camera')]")
            if cam:
                driver.execute_script("arguments[0].forEach(n=>{try{n.remove()}catch(e){}});", cam)
                removed = True
        except Exception:
            pass
        if not _thumb_nodes():
            break
        if not removed:
            break


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
    file_input = _find_composer_file_input(driver)
    if not file_input:
        raise RuntimeError("Could not find ChatGPT file input to upload image")
    abs_path = os.path.abspath(image_path)
    file_input.send_keys(abs_path)
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//*[contains(@class,'image') or contains(@class,'preview') or contains(@aria-label,'image preview') or contains(@data-testid,'image')]"
            ))
        )
    except Exception:
        pass


def _wait_send_button_enabled(driver: webdriver.Chrome, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    btn = None
    while time.time() < end:
        try:
            btn = chat._find_send_button(driver)
            if btn and btn.is_displayed():
                aria = (btn.get_attribute('aria-disabled') or '').strip().lower()
                disabled = (aria == 'true') or (not btn.is_enabled())
                if not disabled:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def send_image_and_prompt_get_reply(driver: webdriver.Chrome, chat_handle: str, image_path: str, prompt: str) -> str:
    """Switch to ChatGPT, upload image via file input, paste prompt, send, and return reply text."""
    driver.switch_to.window(chat_handle)
    # Find composer
    editor = chat._find_composer(driver, timeout=8) or find_editor(driver, timeout=8)
    if not editor:
        return ""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
    driver.execute_script("arguments[0].focus();", editor)
    # Clear attachments and upload
    clear_chatgpt_attachments(driver)
    _hide_camera_tile_in_composer(driver)
    upload_image_to_chatgpt(driver, image_path)
    time.sleep(0.25)
    _hide_camera_tile_in_composer(driver)
    # Wait until image finishes processing and the Send button becomes enabled
    _wait_send_button_enabled(driver, timeout=25)
    # Re-find composer
    editor = chat._find_composer(driver, timeout=8) or find_editor(driver, timeout=8)
    if not editor:
        return ""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
    driver.execute_script("arguments[0].focus();", editor)
    # Clear and paste prompt
    try:
        editor.send_keys(Keys.CONTROL, 'a'); editor.send_keys(Keys.DELETE)
    except Exception:
        pass
    import pyperclip
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
    # Give the DOM a moment to apply the paste and format bullets
    time.sleep(0.15)
    # Verify the composer contains most of our prompt; if not, inject via JS as a fallback
    try:
        current = (editor.text or "").strip()
    except Exception:
        current = ""
    if len(current) < max(10, int(len(prompt) * 0.6)):
        try:
            driver.execute_script(
                "arguments[0].focus(); arguments[0].textContent = arguments[1]; arguments[0].dispatchEvent(new InputEvent('input',{bubbles:true}));",
                editor, prompt,
            )
            time.sleep(0.05)
        except Exception:
            pass
    # Send
    chat._send_message(driver, editor)
    if not chat._likely_streaming(driver):
        try:
            btn = chat._find_send_button(driver)
            if btn:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.2)
        except Exception:
            pass
    if not chat._likely_streaming(driver):
        try:
            driver.execute_script("arguments[0].focus();", editor)
            editor.send_keys(Keys.SPACE); editor.send_keys(Keys.BACK_SPACE); editor.send_keys(Keys.ENTER)
        except Exception:
            pass
    reply = wait_for_chatgpt_response_via_send_button(
        driver,
        timeout=12,
        poll_interval=0.25,
        status_callback=None,
        composer_css="textarea[data-testid='prompt-textarea'], div[contenteditable='true'][data-testid='prompt-textarea'], div[contenteditable='true'][role='textbox']",
        nudge_text='.',
    )
    if not reply:
        chat._send_message(driver, editor)
        reply = wait_for_chatgpt_response_via_send_button(
            driver,
            timeout=10,
            poll_interval=0.25,
            status_callback=None,
            composer_css="textarea[data-testid='prompt-textarea'], div[contenteditable='true'][data-testid='prompt-textarea'], div[contenteditable='true'][role='textbox']",
            nudge_text='.',
        )
    # Best-effort cleanup to avoid attachment buildup for the next run
    try:
        clear_chatgpt_attachments(driver)
    except Exception:
        pass
    return reply or ""
