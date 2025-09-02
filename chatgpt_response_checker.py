"""
chatgpt_response_checker.py
---------------------------
Detects when ChatGPT has finished responding by nudging the message composer so that the Send button reappears only after the model is done. Returns the last assistant reply text.
"""

import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


# CSS selectors for UI elements in ChatGPT web interface
SEND_SEL = [
    '[data-testid*="send-button"]',         # Send button by test id (varies by build/version)
    'button[aria-label*="Send"]'            # Fallback: button with Send aria-label
]
COMPOSER_SEL = [
    'div#prompt-textarea.ProseMirror[contenteditable="true"]',  # Composer in ProseMirror mode (id)
    'div.ProseMirror[contenteditable="true"]',                  # Composer in ProseMirror mode (class)
    'div[role="textbox"][contenteditable="true"]',              # Generic textbox role
    'textarea#prompt-textarea'                                  # Fallback: textarea
]
ASSISTANT_SEL = [
    '[data-message-author-role="assistant"]',                   # Assistant message by role
    '[data-testid="assistant-turn"]',                           # Assistant turn by test id
    '[data-testid="conversation-turn"] article',                # Conversation turn as article
    'main [data-role="assistant"]',                             # Assistant role in main
]

def _visible(d, sels):
    """
    Returns the first visible element found for any CSS selector in `sels`.
    Used to robustly locate UI elements that may change across ChatGPT builds.
    """
    for css in sels:
        try:
            for el in d.find_elements(By.CSS_SELECTOR, css):
                if el.is_displayed():
                    return el  # Return the first visible element found
        except Exception:
            pass  # Ignore errors for missing selectors
    return None  # No visible element matched

def _composer(d, prefer=None):
    """
    Returns the message composer element, optionally preferring a specific CSS selector.
    Falls back to searching known composer selectors and then a generic contenteditable.
    """
    if prefer:
        try:
            el = d.find_element(By.CSS_SELECTOR, prefer)
            if el.is_displayed():
                return el  # Use preferred selector if visible
        except Exception:
            pass
    el = _visible(d, COMPOSER_SEL)
    if el:
        return el  # Found composer from known selectors
    try:
        # Fallback: find a <p> with data-placeholder, then its closest contenteditable ancestor
        return d.execute_script(
            "const p=document.querySelector('p[data-placeholder]');"
            "return p?p.closest('[contenteditable=\"true\"]'):null;"
        )
    except Exception:
        return None  # Could not find composer

def _last_assistant_text(d):
    """
    Returns the text of the last assistant (ChatGPT) message.
    Tries several selectors for robustness, with a JS fallback.
    """
    for css in ASSISTANT_SEL:
        try:
            els = d.find_elements(By.CSS_SELECTOR, css)
            if els:
                # Return the text of the last matching element (most recent assistant message)
                return (els[-1].text or "").strip()
        except Exception:
            pass
    try:
        # Fallback: get the last child of the conversation/messages container
        t = d.execute_script(
            """const c=document.querySelector('[data-testid="conversation-turns"]')
            ||document.querySelector('[data-testid="chat-messages"]')||document.querySelector('main');
            return c&&c.lastElementChild?c.lastElementChild.innerText:'';"""
        )
        return (t or "").strip()
    except Exception:
        return ""

def wait_for_chatgpt_response_via_send_button(
    driver, timeout=5.0, poll_interval=0.25, status_callback=None,
    composer_css=None, nudge_text="xx"
):
    """
    Waits for ChatGPT to finish responding by "nudging" the composer so the Send button
    reappears only after the model is done. Returns the last assistant message text, or
    None on timeout.

    Args:
        driver: Selenium WebDriver instance, already on ChatGPT page.
        timeout: Max seconds to wait for response to finish.
        poll_interval: How often (sec) to poll for Send button.
        status_callback: Optional callback("response_ready") when ready.
        composer_css: Explicit CSS selector for composer, or None.
        nudge_text: Dummy text to type in composer to trigger Send button.
    Returns:
        The last assistant message text, or None if timeout.
    """
    end = time.time() + float(timeout)  # Calculate when to stop waiting
    typed = False  # Whether we've already typed the nudge text
    comp = None    # Reference to the composer element
    while time.time() < end:
        if comp is None:
            # Find the composer element if not already found
            comp = _composer(driver, composer_css)
        if comp:
            try:
                # Focus the composer to ensure key events work
                driver.execute_script("arguments[0].focus();", comp)
                if not typed:
                    # Type the nudge text (does not send, just triggers Send button)
                    comp.send_keys(nudge_text)
                    typed = True
            except Exception:
                pass  # Ignore errors (element might be temporarily detached)
        # Check if the Send button is now visible (means model is done)
        btn = _visible(driver, SEND_SEL)
        if btn:
            if typed and comp:
                try:
                    # Delete the nudge text from the composer (clean up)
                    for _ in range(len(nudge_text)):
                        comp.send_keys(Keys.BACK_SPACE)
                except Exception:
                    pass
            if status_callback:
                status_callback("response_ready")  # Notify callback if provided
            # Return the most recent assistant message text
            return _last_assistant_text(driver)
        # Not ready yet; wait and poll again
        time.sleep(float(poll_interval))
    # Timeout: clean up nudge text if we typed it
    if typed and comp:
        try:
            for _ in range(len(nudge_text)):
                comp.send_keys(Keys.BACK_SPACE)
        except Exception:
            pass
    return None  # Timed out waiting for response