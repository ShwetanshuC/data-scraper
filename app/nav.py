from __future__ import annotations

import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains


def _open_hamburger_if_present(driver: webdriver.Chrome) -> None:
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


def _likely_staff_url(u: str) -> bool:
    u = (u or "").lower()
    if not u:
        return False
    strong = [
        "our-team","team","providers","provider","doctors","physicians","veterinarians","vets","our-doctors","meet-the-team","meet-our-team",
    ]
    if any(k in u for k in strong):
        return True
    if "meet" in u and any(k in u for k in ["team","doctor","provider","staff","physician","veterinarian"]):
        return True
    return False


def _wait_for_navigation(driver: webdriver.Chrome, prev_url: str, timeout: float = 5.0) -> bool:
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
    _open_hamburger_if_present(driver)
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
    for _, fn in strategies:
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


def _click_anchor_by_text(driver: webdriver.Chrome, anchor_text: str) -> bool:
    target = (anchor_text or "").strip().lower()
    if not target:
        return False
    start_url = driver.current_url or ""
    try:
        elements = driver.find_elements(By.XPATH, "//a | //button")
    except Exception:
        elements = []
    def _attempt_click(el) -> bool:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()
            except Exception:
                _dispatch_real_click(driver, el)
            end = time.time() + 6.0
            while time.time() < end:
                try:
                    if driver.current_url and driver.current_url != start_url:
                        return True
                except Exception:
                    pass
                time.sleep(0.2)
        except Exception:
            pass
        return False
    for el in elements:
        try:
            if not el.is_displayed():
                continue
            txt = (el.text or "").strip().lower()
            if txt == target:
                if _attempt_click(el):
                    return True
        except Exception:
            continue
    for el in elements:
        try:
            if not el.is_displayed():
                continue
            txt = (el.text or "").strip().lower()
            if not txt:
                continue
            if target in txt or txt in target:
                if _attempt_click(el):
                    return True
        except Exception:
            continue
    return False


def _expand_dropdowns_and_try(driver: webdriver.Chrome, nav_text: str) -> bool:
    try:
        xpath_toggles = (
            "//a[(contains(@class,'dropdown') or contains(@class,'menu') or contains(@class,'nav')) and (@aria-haspopup or @aria-expanded)]"
            " | //button[(contains(@class,'dropdown') or contains(@class,'menu') or contains(@class,'nav')) and (@aria-haspopup or @aria-expanded)]"
            " | //a[contains(@aria-haspopup,'true') or contains(@aria-expanded,'false')]"
            " | //button[contains(@aria-haspopup,'true') or contains(@aria-expanded,'false')]"
        )
        toggles = driver.find_elements(By.XPATH, xpath_toggles)
    except Exception:
        toggles = []
    for t in toggles:
        try:
            if not t.is_displayed():
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
            clicked = False
            try:
                t.click(); clicked = True
            except Exception:
                try:
                    ActionChains(driver).move_to_element(t).click().perform(); clicked = True
                except Exception:
                    clicked = False
            if not clicked:
                continue
            time.sleep(0.5)
            if navigate_to_suggested_section(driver, nav_text):
                return True
        except Exception:
            continue
    return False


def _expand_specific_dropdown_and_navigate(driver: webdriver.Chrome, parent_text: str, child_text: str) -> bool:
    try:
        xpath_toggles = (
            "//a[(contains(@class,'dropdown') or contains(@class,'menu') or contains(@class,'nav')) and (@aria-haspopup or @aria-expanded)]"
            " | //button[(contains(@class,'dropdown') or contains(@class,'menu') or contains(@class,'nav')) and (@aria-haspopup or @aria-expanded)]"
            " | //a[contains(@aria-haspopup,'true') or contains(@aria-expanded,'false')]"
            " | //button[contains(@aria-haspopup,'true') or contains(@aria-expanded,'false')]"
        )
        toggles = driver.find_elements(By.XPATH, xpath_toggles)
    except Exception:
        toggles = []
    target = (parent_text or "").strip().lower()
    for t in toggles:
        try:
            if not t.is_displayed():
                continue
            visible = (t.text or "").strip().lower() or (t.get_attribute('aria-label') or '').strip().lower()
            if not visible or target not in visible:
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", t)
            try:
                ActionChains(driver).move_to_element(t).perform(); time.sleep(0.5)
            except Exception:
                pass
            if _click_anchor_by_text(driver, child_text):
                return True
            tag = (t.tag_name or "").lower()
            href = t.get_attribute('href') or ''
            if tag != 'a' or not href:
                clicked = False
                try:
                    t.click(); clicked = True
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(t).click().perform(); clicked = True
                    except Exception:
                        clicked = False
                if clicked:
                    time.sleep(0.5)
                    if _click_anchor_by_text(driver, child_text):
                        return True
        except Exception:
            continue
    return False


def _navigate_by_text_via_direct_get(driver: webdriver.Chrome, anchor_text: str) -> bool:
    target = (anchor_text or "").strip()
    if not target:
        return False
    start_url = driver.current_url or ""
    try:
        anchors = driver.find_elements(By.XPATH, "//a[normalize-space()]")
    except Exception:
        anchors = []
    def _score(a) -> int:
        try:
            text = (a.text or "").strip()
            href = (a.get_attribute('href') or '').strip()
        except Exception:
            return -1
        if not href or href.startswith('#') or href.lower().startswith('javascript:'):
            return -1
        s = 0
        if text.lower() == target.lower(): s += 50
        if target.lower() in text.lower() or text.lower() in target.lower(): s += 20
        s += _score_staff_label(text)
        if _likely_staff_url(href): s += 80
        return s
    best, best_score = None, 0
    for a in anchors:
        sc = _score(a)
        if sc > best_score:
            best, best_score = a, sc
    if best and best_score > 0:
        href = best.get_attribute('href') or ''
        if href:
            try:
                driver.get(href)
                if _wait_for_navigation(driver, start_url, timeout=8.0):
                    return True
            except Exception:
                pass
    return False


def _score_staff_label(label: str) -> int:
    l = (label or "").strip().lower()
    if not l:
        return 0
    scores = [
        ("our team", 100),("team", 90),("providers", 90),("meet the team", 95),("meet our team", 95),
        ("doctors", 85),("physicians", 85),("staff", 80),("veterinarians", 80),("provider", 75),("doctor", 75),("meet", 60),
        ("about us", 10),("about", 5),
    ]
    score = 0
    for k, s in scores:
        if k in l:
            score = max(score, s)
    return score


def _navigate_best_staff_link_anywhere(driver: webdriver.Chrome) -> bool:
    start_url = driver.current_url or ""
    try:
        anchors = driver.find_elements(By.XPATH, "//a[@href]")
    except Exception:
        anchors = []
    def _score_any(a) -> int:
        try:
            text = (a.text or "").strip()
            href = (a.get_attribute('href') or '').strip()
        except Exception:
            return -1
        if not href or href.startswith('#') or href.lower().startswith('javascript:'):
            return -1
        s = 0
        s += _score_staff_label(text)
        if _likely_staff_url(href): s += 100
        s -= min(len(href), 200) // 50
        return s
    best, best_score = None, 0
    for a in anchors:
        sc = _score_any(a)
        if sc > best_score:
            best, best_score = a, sc
    if best and best_score >= 90:
        href = best.get_attribute('href') or ''
        if href:
            try:
                driver.get(href)
                if _wait_for_navigation(driver, start_url, timeout=8.0):
                    return True
            except Exception:
                pass
    return False


def _expand_parent_and_click_best_staff_child(driver: webdriver.Chrome, parent_text: str) -> bool:
    """Expand a parent menu by label and click the most staff-like child under it."""
    try:
        toggles = driver.find_elements(By.XPATH, "//a | //button")
    except Exception:
        toggles = []
    target = (parent_text or "").strip().lower()
    for t in toggles:
        try:
            if not t.is_displayed():
                continue
            visible = (t.text or "").strip()
            if not visible or target not in visible.strip().lower():
                continue
            # Ascend to parent LI container
            li = t
            try:
                li = t.find_element(By.XPATH, "ancestor::li[1]")
            except Exception:
                pass
            # Hover to reveal submenu if possible
            try:
                ActionChains(driver).move_to_element(t).perform(); time.sleep(0.5)
            except Exception:
                pass
            # Collect child anchors under LI only
            try:
                children = li.find_elements(By.XPATH, ".//ul//a")
            except Exception:
                children = []
            best = None
            best_score = 0
            for a in children:
                try:
                    if not a.is_displayed():
                        continue
                    txt = (a.text or "").strip()
                    if not txt:
                        continue
                    sc = _score_staff_label(txt)
                    if sc > best_score:
                        best = a; best_score = sc
                except Exception:
                    continue
            if best and best_score >= 60:
                start_url = driver.current_url or ""
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
                try:
                    best.click()
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(best).click().perform()
                    except Exception:
                        _dispatch_real_click(driver, best)
                if _wait_for_navigation(driver, start_url, timeout=6.0):
                    return True
        except Exception:
            continue
    return False
