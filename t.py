# t.py — attach to existing Chrome (9222), find ProseMirror editor ≤5s, send, return reply text

import json, time, urllib.request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# Address of the Chrome instance with remote debugging enabled.
DEBUG_ADDR = "127.0.0.1:9222"

# JavaScript snippet to locate the ProseMirror editor element.
# It tries to find a div with id 'prompt-textarea' and class 'ProseMirror' that is contenteditable,
# or alternatively finds a paragraph with a data-placeholder attribute and then gets its closest contenteditable ancestor.
# Returns the element if visible (offsetParent !== null), else null.
PM_JS = """return(()=>{const p=document.querySelector('div#prompt-textarea.ProseMirror[contenteditable="true"]')
|| (document.querySelector('p[data-placeholder]')?.closest('[contenteditable="true"]'));
if(p&&p.offsetParent!==null)return p;return null;})();"""

def attach():
    """
    Attach to an existing Chrome instance running with remote debugging enabled at DEBUG_ADDR.
    
    Steps:
    - Verify that the Chrome DevTools protocol endpoint is reachable by fetching the version info.
    - Configure Selenium Chrome WebDriver to connect to this existing Chrome instance via the debugger address.
    
    Returns:
        A Selenium WebDriver instance attached to the existing Chrome.
    """
    # Confirm the remote debugging endpoint is responsive (fast check).
    urllib.request.urlopen(f"http://{DEBUG_ADDR}/json/version", timeout=1).read()
    # Set Chrome options to connect to the debugger address.
    o=Options()
    o.add_experimental_option("debuggerAddress", DEBUG_ADDR)
    # Return a WebDriver connected to the running Chrome instance.
    return webdriver.Chrome(options=o)

def goto_chatgpt_tab(d):
    """
    Switch to an existing browser tab that has ChatGPT loaded, or open ChatGPT if none found.
    
    Args:
        d: Selenium WebDriver instance.
        
    Behavior:
    - Iterate over all open window handles.
    - Switch to each window and check if its URL contains 'chatgpt.com' or 'chat.openai.com'.
    - If found, stay on that tab.
    - Otherwise, open a new tab with 'https://chatgpt.com/'.
    
    This ensures we are interacting with a ChatGPT page.
    """
    for h in d.window_handles:
        d.switch_to.window(h)
        # Check if current tab is a ChatGPT page by URL substring.
        if "chatgpt.com" in (d.current_url or "") or "chat.openai.com" in (d.current_url or ""):
            return
    # If no ChatGPT tab found, open a new one.
    d.get("https://chatgpt.com/")

def find_editor(d, timeout=5):
    """
    Attempt to locate the ProseMirror editor element on the current page or within iframes.
    
    Args:
        d: Selenium WebDriver instance.
        timeout: Maximum seconds to keep trying to find the editor.
        
    Returns:
        The editor WebElement if found and visible, else None.
    
    Approach:
    - Use the PM_JS script to find the editor element in the main document.
    - If not found, iterate over up to 6 iframes and try to find the editor inside each iframe.
    - Return the first visible editor found.
    - Retry until timeout expires, checking every 0.1 seconds.
    
    This function is critical to reliably detect the input area where prompts can be sent.
    """
    end=time.time()+timeout
    while time.time()<end:
        try:
            # Try finding editor in main document.
            el=d.execute_script(PM_JS)
            if el: return el
        except Exception:
            # Ignore exceptions such as stale elements or cross-origin iframe issues.
            pass
        # If not found, scan a few iframes (up to 6) for the editor.
        for fr in d.find_elements(By.TAG_NAME,"iframe")[:6]:
            try:
                d.switch_to.frame(fr)
                el=d.execute_script(PM_JS)
                if el: return el   # Found editor inside iframe, stay in this frame context.
            except Exception:
                # Ignore exceptions such as cross-origin or inaccessible frames.
                pass
            finally:
                # Return to the main document context to continue searching.
                try:
                    d.switch_to.default_content()
                except Exception:
                    pass
        # Short delay before retrying to avoid tight loop.
        time.sleep(0.1)
    # Editor not found within timeout.
    return None

def main():
    """
    Main workflow to:
    - Attach to existing Chrome with debugging enabled.
    - Navigate to or switch to a ChatGPT tab.
    - Locate the ProseMirror editor input area.
    - Send a prompt ("Say hello!") to the editor and submit it.
    - Wait for ChatGPT's reply using an external helper function.
    - Print the reply or indicate timeout.
    
    This orchestrates the entire process of sending a message to ChatGPT and retrieving the response.
    """
    # Attach to the running Chrome instance.
    d=attach()
    # Ensure we are on a ChatGPT tab.
    goto_chatgpt_tab(d)
    # Find the input editor where we can type the prompt.
    editor=find_editor(d,5)
    if not editor:
        print("No editor found")
        return
    # Scroll the editor into view to ensure it can receive input.
    d.execute_script("arguments[0].scrollIntoView({block:'center'});", editor)
    # Focus the editor so that send_keys works correctly.
    d.execute_script("arguments[0].focus();", editor)
    # Send the prompt text followed by ENTER to submit.
    editor.send_keys("Say hello!", Keys.ENTER)
    # Import helper to wait for ChatGPT's response after sending input.
    from chatgpt_response_checker import wait_for_chatgpt_response_via_send_button
    reply = wait_for_chatgpt_response_via_send_button(
        d, timeout=5, poll_interval=0.2,
        composer_css='div#prompt-textarea.ProseMirror[contenteditable="true"]'
    )
    # Print the received reply or indicate if timed out.
    print("Reply:", reply if reply is not None else "(timeout)")

if __name__=="__main__":
    main()