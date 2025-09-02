# smart_nav.py — GPT-guided navigation with iframe support + no-early-return

import os, re, time, json, base64, tempfile
from typing import Any, Dict, List, Tuple, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from t import find_editor
from chatgpt_response_checker import wait_for_chatgpt_response_via_send_button


def navigate_to_about_or_team(
    driver: webdriver.Chrome,
    chat_handle: str,
    url: str,
    timeout: int = 15,
    max_candidates: int = 60,
    browse_handle: Optional[str] = None,
    settle_ms: int = 1200,          # wait after navigation
    scan_retries: int = 3,          # retry DOM scans
    scan_retry_delay_ms: int = 400, # between retries
) -> Tuple[bool, Optional[str], str]:
    """
    Open `url` in a dedicated browse tab, collect clickable candidates from the main document
    and all visible iframes (1 level deep), send screenshot + candidates to ChatGPT,
    ask for the best link to a Staff/Team/Providers page, parse a JSON answer
    like {"id": "<fX-N>", "label": "Meet the Team", "href_hint": "/team", "reason": "..."}
    where id encodes frame index and node id. Then click the element and return
    (ok, chosen_id, reason). If clicking by id fails (DOM shifted), fall back to
    locating by visible text, href hint, or coordinates.

    chosen_id format:
      - "f0-12"  => main document, auto-id 12
      - "f2-5"   => iframe index 2 (visible-only enumeration), auto-id 5
    """

    # -------- helpers (local) --------

    def _ensure_browse_tab(_drv: webdriver.Chrome) -> str:
        for h in _drv.window_handles:
            _drv.switch_to.window(h)
            url_ = (_drv.current_url or "").lower()
            title_ = (_drv.title or "").lower()
            if "chatgpt" in title_ or "chatgpt.com" in url_ or "openai.com" in url_:
                continue
            return h
        _drv.execute_script("window.open('about:blank', '_blank');")
        time.sleep(0.5)
        return _drv.window_handles[-1]

    def _fullpage_screenshot_to_png(_drv: webdriver.Chrome, out_path: str) -> bool:
        try:
            metrics = _drv.execute_cdp_cmd("Page.getLayoutMetrics", {})
            content = metrics["contentSize"]
            width, height = int(content["width"]), int(content["height"])
            _drv.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
                "mobile": False, "width": width, "height": height,
                "deviceScaleFactor": 1, "screenWidth": width, "screenHeight": height
            })
            res = _drv.execute_cdp_cmd("Page.captureScreenshot", {
                "format": "png", "fromSurface": True, "captureBeyondViewport": True
            })
            if not res.get("data"): return False
            with open(out_path, "wb") as f:
                f.write(base64.b64decode(res["data"]))
            return True
        finally:
            try: _drv.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
            except Exception: pass

    def _js_collect_clickables(_drv: webdriver.Chrome, budget: int) -> List[Dict[str, Any]]:
        js = r"""
        (function(maxN){
          function isVisible(el){
            if(!el) return false;
            const cs = getComputedStyle(el);
            if (cs.display==='none' || cs.visibility==='hidden' || cs.opacity==='0') return false;
            const r = el.getBoundingClientRect();
            return r.width > 1 && r.height > 1;
          }
          const nodes = Array.from(document.querySelectorAll(
            'a, button, [role="button"], [role="link"], nav a, header a, li a, [onclick], [tabindex]'
          ));
          let id = 0, out = [];
          for (const el of nodes){
            if (!el.dataset) continue;
            if (!el.dataset.autoId) el.dataset.autoId = (++id).toString();
            const r = el.getBoundingClientRect();
            let text = (el.innerText || '').trim().replace(/\s+/g,' ');
            const href = (el.getAttribute('href')||'').trim();
            const role = el.getAttribute('role')||'';
            const tag = el.tagName.toLowerCase();
            const visible = isVisible(el);
            out.push({
              id: parseInt(el.dataset.autoId,10),
              tag, role, text, href, visible,
              rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}
            });
          }
          // de-dupe by text|href
          const seen = new Set(), uniq = [];
          for (const it of out){
            const k = (it.text + '|' + it.href).toLowerCase();
            if (seen.has(k)) continue;
            seen.add(k);
            uniq.push(it);
            if (uniq.length >= maxN) break;
          }
          return uniq;
        })(arguments[0]);
        """
        try:
            return _drv.execute_script(js, int(budget)) or []
        except Exception:
            return []

    def _list_visible_iframes(_drv: webdriver.Chrome) -> List[Any]:
        frames = []
        for f in _drv.find_elements(By.TAG_NAME, "iframe"):
            try:
                # only add visible frames with dimensions
                rect = f.rect
                if rect and rect.get("width", 0) > 2 and rect.get("height", 0) > 2 and f.is_displayed():
                    frames.append(f)
            except Exception:
                continue
        return frames

    def _collect_all_frames(_drv: webdriver.Chrome, budget: int) -> Tuple[List[Dict[str, Any]], List[Any]]:
        """
        Collect candidates from main doc (frame index 0) and each visible iframe (1..N).
        Returns (candidates, frame_list). Candidate ids are strings: "f{idx}-{id}".
        """
        all_items: List[Dict[str, Any]] = []
        frames: List[Any] = []

        # main doc
        _drv.switch_to.default_content()
        main_items = _js_collect_clickables(_drv, budget)
        for it in main_items:
            it["fid"] = f"f0-{it['id']}"
            it["frame_index"] = 0
        all_items.extend(main_items)

        # visible iframes (1 level)
        visible_frames = _list_visible_iframes(_drv)
        frames = [None] + visible_frames  # frames[0] is main doc, frames[i] is ith visible iframe

        for idx, iframe in enumerate(visible_frames, start=1):
            try:
                _drv.switch_to.default_content()
                _drv.switch_to.frame(iframe)
                sub = _js_collect_clickables(_drv, budget)
                for it in sub:
                    it["fid"] = f"f{idx}-{it['id']}"   # encode frame index + local id
                    it["frame_index"] = idx
                all_items.extend(sub)
            except Exception:
                continue
            finally:
                _drv.switch_to.default_content()

        # cap the total (preserve earlier items)
        if len(all_items) > budget:
            all_items = all_items[:budget]
        return all_items, frames

    def _click_by_fid(_drv: webdriver.Chrome, fid: str, frames: List[Any]) -> bool:
        """
        Click by encoded id 'f{frameIndex}-{id}'. Switches into the correct frame first.
        """
        m = re.match(r"^f(\d+)-(\d+)$", fid or "")
        if not m:
            return False
        fidx = int(m.group(1))
        local_id = m.group(2)

        _drv.switch_to.default_content()
        if fidx > 0:
            try:
                frame_el = frames[fidx]
                _drv.switch_to.frame(frame_el)
            except Exception:
                return False
        try:
            el = _drv.execute_script(
                "return document.querySelector('[data-auto-id=\"'+arguments[0]+'\"]')", local_id
            )
            if not el:
                return False
            _drv.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.1)
            _drv.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            return False
        finally:
            _drv.switch_to.default_content()

    def _fallback_choose(items: List[Dict[str, Any]]) -> Optional[str]:
        # Choose best visible, keyword-matching item; return fid (e.g., "f0-12")
        # Strongly prioritize staff/team/providers over generic about/company
        kws = [
            "team","our team","meet the team","staff","providers","doctors","physicians",
            "dentists","leadership","people","about","our story","company","who we are"
        ]
        best, score = None, -1
        for it in items:
            s = 0
            text = (it.get("text") or "").lower()
            href = (it.get("href") or "").lower()
            if it.get("visible"): s += 2
            for kw in kws:
                if kw in text: s += 5
                if kw in href: s += 3
            y = (it.get("rect") or {}).get("y", 9999)
            if y < 180: s += 1  # mild bias to top nav
            if s > score:
                score, best = s, it
        return None if not best else str(best.get("fid"))

    # -------- A) ensure browse tab + open URL --------
    if browse_handle is None:
        browse_handle = _ensure_browse_tab(driver)
    driver.switch_to.window(browse_handle)
    try:
        driver.get(url)
    except Exception:
        pass
    time.sleep(max(0.2, settle_ms / 1000.0))

    # -------- B) scan DOM (with retries + iframes) --------
    items: List[Dict[str, Any]] = []
    frames: List[Any] = []
    for attempt in range(scan_retries):
        items, frames = _collect_all_frames(driver, max_candidates)
        if items:
            break
        time.sleep(max(0.1, scan_retry_delay_ms / 1000.0))

    # Prepare prompt candidates for GPT (always, even if empty)
    prompt_items = []
    for it in items:
        prompt_items.append({
            "id": it["fid"],  # encoded with frame index
            "tag": it.get("tag",""),
            "role": it.get("role",""),
            "text": (it.get("text","")[:160]),
            "href": (it.get("href","")[:160]),
            "visible": bool(it.get("visible", False)),
            "rect": it.get("rect", {}),
        })
    prompt_json = json.dumps(prompt_items, ensure_ascii=False)
    # Map for fallbacks by fid
    fid_to_item = {it["id"]: it for it in prompt_items}

    # -------- C) screenshot browse tab --------
    tmp_png = os.path.join(tempfile.gettempdir(), f"page_{int(time.time()*1000)}.png")
    _fullpage_screenshot_to_png(driver, tmp_png)  # best-effort

    # -------- D) switch to ChatGPT, send prompt regardless --------
    driver.switch_to.window(chat_handle)
    editor = find_editor(driver, timeout=5)
    if not editor:
        return (False, None, "ChatGPT composer not found")

    def _click_send_button(_drv, max_wait_ms=2000):
        sels = [
            "button[aria-label*='Send' i]",
            "button[data-testid='send-button']",
            "button:has(svg[aria-label*='Send' i])",
            "button:has(path[d])",  # loose, fallback
        ]
        end = time.time() + max_wait_ms/1000.0
        while time.time() < end:
            for sel in sels:
                try:
                    for b in _drv.find_elements(By.CSS_SELECTOR, sel):
                        if not b.is_displayed():
                            continue
                        disabled = (b.get_attribute("disabled") is not None) or \
                                   ("true" in (b.get_attribute("aria-disabled") or "").lower())
                        if disabled:
                            continue
                        try:
                            _drv.execute_script("arguments[0].focus();", b)
                            _drv.execute_script("arguments[0].click();", b)
                            return True
                        except Exception:
                            pass
                except Exception:
                    continue
            time.sleep(0.1)
        return False

    def _send_prompt_now(_drv, _editor, _text):
        # Ensure focus on composer
        try:
            _drv.execute_script("arguments[0].scrollIntoView({block:'center'});", _editor)
            _drv.execute_script("arguments[0].focus();", _editor)
        except Exception:
            pass

        # Clear and type
        try:
            _editor.send_keys(Keys.CONTROL, 'a'); _editor.send_keys(Keys.DELETE)
        except Exception:
            pass
        _editor.send_keys(_text)

        # Try clicking Send (preferred)
        if _click_send_button(_drv, max_wait_ms=1200):
            return True

        # Fallbacks if no visible send button yet
        try:
            _editor.send_keys(Keys.ENTER)
            time.sleep(0.2)
        except Exception:
            pass
        if _click_send_button(_drv, max_wait_ms=800):
            return True

        try:
            _editor.send_keys(Keys.CONTROL, Keys.ENTER)  # some layouts use Ctrl+Enter to send
            time.sleep(0.2)
        except Exception:
            pass
        if _click_send_button(_drv, max_wait_ms=800):
            return True

        # Last-ditch: tiny nudge + Enter (ensures composer registers input)
        try:
            _editor.send_keys(" ")
            _editor.send_keys(Keys.ENTER)
        except Exception:
            pass
        return _click_send_button(_drv, max_wait_ms=800)

    # --- Upload screenshot (best-effort) ---
    if os.path.exists(tmp_png):
        try:
            # Try to expose the file input via an attach/upload button first
            attach_btn_selectors = [
                'button[aria-label*="Attach" i]',
                'button[aria-label*="Upload" i]',
                'button[aria-label*="Image" i]',
                '[data-testid*="attach"]',
                'button:has(svg[aria-label*="Upload" i])'
            ]
            clicked_attach = False
            for sel in attach_btn_selectors:
                try:
                    for b in driver.find_elements(By.CSS_SELECTOR, sel):
                        if b.is_displayed():
                            driver.execute_script("arguments[0].click();", b)
                            time.sleep(0.25)
                            clicked_attach = True
                            break
                    if clicked_attach:
                        break
                except Exception:
                    continue

            # Send to any file input that accepts images
            inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
            target = None
            for inp in inputs:
                try:
                    accept = (inp.get_attribute("accept") or "").lower()
                except Exception:
                    accept = ""
                if ("image" in accept) or accept == "":
                    target = inp; break
            if target:
                target.send_keys(tmp_png)
                time.sleep(0.5)
        except Exception:
            pass

    # --- Build instruction and SEND using the robust sender ---
    instruction = (
        "You are assisting a browsing agent.\n"
        "We just opened the site's homepage in a BROWSER TAB. You are given a JSON list of clickable candidates (may be empty) extracted from the DOM, and a screenshot image is attached.\n"
        "Task: Pick the ONE element that leads to the page listing people (Team/Staff/Providers/Doctors/Physicians/Dentists/Our Team/Meet the Team). Prefer direct staff/team pages over generic About/Company pages.\n\n"
        "Return STRICT JSON only (no prose) in a fenced block exactly like:\n"
        "```json\n"
        '{"id": "<fid>", "label": "<visible text>", "href_hint": "</path-or-keyword>", "reason": "<short>"}\n'
        "```\n"
        "Where <fid> is the encoded id (e.g., \"f0-12\" for main doc, \"f2-5\" for the 3rd iframe). If the best choice is not in the list, set id to null and provide a label/href_hint extracted from the screenshot.\n\n"
        "CANDIDATES:\n"
        "```\n"
        f"{prompt_json}\n"
        "```\n"
    )

    _sent_ok = _send_prompt_now(driver, editor, instruction)

    # -------- E) wait for GPT and parse --------
    reply = wait_for_chatgpt_response_via_send_button(
        driver,
        timeout=timeout,
        poll_interval=0.25,
        composer_css='div#prompt-textarea.ProseMirror[contenteditable="true"]',
        nudge_text="xx",
    )

    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        if not text: return None
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.I)
        blob = m.group(1) if m else None
        if not blob:
            m2 = re.search(r"\{[\s\S]*\}", text)
            blob = m2.group(0) if m2 else None
        if not blob: return None
        try:
            return json.loads(blob)
        except Exception:
            try:
                safe = re.sub(r",\s*}", "}", blob)
                safe = re.sub(r",\s*]", "]", safe)
                return json.loads(safe)
            except Exception:
                return None

    data = _extract_json(reply) if reply else None
    chosen_fid: Optional[str] = None
    reason = ""
    label_hint = None
    href_hint = None
    if isinstance(data, dict):
        if data.get("id") is not None:
            chosen_fid = str(data.get("id")).strip()
        label_hint = (data.get("label") or None)
        href_hint = (data.get("href_hint") or None)
        reason = str(data.get("reason", ""))

    # -------- F) click in browse tab --------
    driver.switch_to.window(browse_handle)

    def _try_click_by_text_or_href(_drv: webdriver.Chrome,
                                   label: Optional[str],
                                   href_sub: Optional[str],
                                   frame_index_hint: Optional[int]) -> bool:
        def _click_in_context(find_fn):
            try:
                el = find_fn()
                if not el:
                    return False
                _drv.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.1)
                _drv.execute_script("arguments[0].click();", el)
                return True
            except Exception:
                return False

        def _by_xpath_text(ctx, text):
            # Case-insensitive contains match for visible text
            xp = (
                f"//*[self::a or self::button or @role='button' or @role='link']"
                f"[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),"
                f" '{text.lower()}')]"
            )
            try:
                els = ctx.find_elements(By.XPATH, xp)
                for e in els:
                    if e.is_displayed():
                        return e
            except Exception:
                return None
            return None

        def _by_css_href(ctx, sub):
            try:
                els = ctx.find_elements(By.CSS_SELECTOR, f"a[href*='{sub}'], [onclick*='{sub}']")
                for e in els:
                    if e.is_displayed():
                        return e
            except Exception:
                return None
            return None

        # Try hinted frame first, then all frames
        frame_orders: List[int] = [frame_index_hint] if frame_index_hint is not None else []
        # Append all frame indices including main (0..N)
        frame_orders += [i for i in range(len(frames)) if i not in frame_orders]

        for fidx in frame_orders:
            try:
                _drv.switch_to.default_content()
                if fidx > 0:
                    _drv.switch_to.frame(frames[fidx])
            except Exception:
                continue

            if label:
                if _click_in_context(lambda: _by_xpath_text(_drv, label)):
                    return True
            if href_sub:
                if _click_in_context(lambda: _by_css_href(_drv, href_sub)):
                    return True

        _drv.switch_to.default_content()
        return False

    def _try_click_by_rect(_drv: webdriver.Chrome, rect: Optional[Dict[str, int]], frame_index_hint: Optional[int]) -> bool:
        if not rect:
            return False
        try:
            x = int(rect.get('x', 0) + rect.get('w', 0)//2)
            y = int(rect.get('y', 0) + rect.get('h', 0)//2)
        except Exception:
            return False

        try:
            _drv.switch_to.default_content()
            if frame_index_hint and frame_index_hint > 0:
                _drv.switch_to.frame(frames[frame_index_hint])
            _drv.execute_script("window.scrollTo(0, Math.max(0, arguments[0]-200));", y)
            time.sleep(0.1)
            el = _drv.execute_script("return document.elementFromPoint(arguments[0], arguments[1]);", x, y)
            if not el:
                return False
            _drv.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            return False
        finally:
            try:
                _drv.switch_to.default_content()
            except Exception:
                pass

    # First, try GPT's id directly
    if chosen_fid:
        if _click_by_fid(driver, chosen_fid, frames):
            return (True, chosen_fid, reason or "GPT choice")
        # DOM may have shifted — re-scan once then try again
        items2, frames2 = _collect_all_frames(driver, max_candidates)
        if _click_by_fid(driver, chosen_fid, frames2):
            return (True, chosen_fid, reason or "GPT choice (after rescan)")

        # Use original candidate info for fallback strategies
        chosen_item = fid_to_item.get(chosen_fid)
        if chosen_item:
            if _try_click_by_text_or_href(driver,
                                          label=chosen_item.get("text") or label_hint,
                                          href_sub=(chosen_item.get("href") or href_hint or "").split("/")[-1] or None,
                                          frame_index_hint=int(chosen_fid.split('-')[0][1:])):
                return (True, chosen_fid, reason or "GPT choice (text/href fallback)")
            if _try_click_by_rect(driver, chosen_item.get("rect"), int(chosen_fid.split('-')[0][1:])):
                return (True, chosen_fid, reason or "GPT choice (rect fallback)")

    # If GPT gave no/invalid id but provided hints, try those globally
    if not chosen_fid and (label_hint or href_hint):
        if _try_click_by_text_or_href(driver, label_hint, href_hint, None):
            return (True, None, reason or "GPT hints (no id)")

    # -------- G) fallback heuristic if GPT failed or empty --------
    if items:
        fb = _fallback_choose(items)
        if fb and _click_by_fid(driver, fb, frames):
            return (True, fb, "Fallback heuristic")

    return (False, None, "No suitable element found")
