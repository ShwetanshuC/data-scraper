from __future__ import annotations

import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from app.utils import _host_of


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
    # Exclude career/join pages that often contain misleading keywords
    if _is_career_or_nonstaff(u):
        return False
    strong = [
        "our-team","team","providers","provider","doctors","physicians","veterinarians","vets","our-doctors","meet-the-team","meet-our-team","our-veterinarians","our-staff","medical-team",
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
                # Skip career/join links
                try:
                    href_l = (el.get_attribute('href') or '').strip().lower()
                except Exception:
                    href_l = ''
                txt_l = (el.text or '').strip().lower()
                if _is_career_or_nonstaff(txt_l) or _is_career_or_nonstaff(href_l):
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
                if (_likely_staff_url(href) and a.is_displayed() and not _is_career_or_nonstaff(href)):
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", a)
                    try:
                        a.click()
                    except Exception:
                        _dispatch_real_click(driver, a)
                    if _wait_for_navigation(driver, start_url, timeout=6.0):
                        return True
            except Exception:
                continue
        # Fallback: use JS to fetch all hrefs and navigate directly to the best staff-like href
        try:
            hrefs = driver.execute_script("return Array.from(document.querySelectorAll('a[href]')).map(a=>a.href);") or []
        except Exception:
            hrefs = []
        best, score = None, 0
        for h in hrefs:
            if _is_career_or_nonstaff(h):
                continue
            sc = 0
            if _likely_staff_url(h): sc += 100
            for k, w in (("/veterinarians", 50),("/our-veterinarians", 60),("/our-doctors", 45),("/providers", 40),("/team", 35),("/our-team", 45),("/staff", 30)):
                if k in (h or '').lower(): sc += w
            if sc > score:
                best, score = h, sc
        if best and score >= 100:
            try:
                driver.get(best)
                if _wait_for_navigation(driver, start_url, timeout=8.0):
                    return True
            except Exception:
                pass
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
        if _is_career_or_nonstaff(text) or _is_career_or_nonstaff(href): s -= 200
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
    # Penalize non-staff pages that contain misleading keywords
    if _is_career_or_nonstaff(l):
        score -= 120
    return score


def _navigate_best_staff_link_anywhere(driver: webdriver.Chrome) -> bool:
    start_url = driver.current_url or ""
    cur_host = _host_of(start_url)

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
        if _is_career_or_nonstaff(text) or _is_career_or_nonstaff(href): s -= 200
        # prefer same-host links slightly
        try:
            if cur_host and _host_of(href).endswith(cur_host):
                s += 10
        except Exception:
            pass
        s -= min(len(href), 200) // 50
        return s

    # Pass 1: DOM anchors (up to 2 small retries to allow menus to render)
    best, best_score = None, 0
    for _ in range(3):
        try:
            anchors = driver.find_elements(By.XPATH, "//a[@href]")
        except Exception:
            anchors = []
        for a in anchors:
            sc = _score_any(a)
            if sc > best_score:
                best, best_score = a, sc
        if best_score >= 90:
            break
        time.sleep(0.3)

    if best and best_score >= 90:
        href = best.get_attribute('href') or ''
        if href:
            try:
                driver.get(href)
                if _wait_for_navigation(driver, start_url, timeout=8.0):
                    return True
            except Exception:
                pass

    # Pass 2: JS-queried hrefs (absolute URLs), in case anchors are hidden/not attached
    try:
        hrefs = driver.execute_script("return Array.from(document.querySelectorAll('a[href]')).map(a=>a.href);") or []
    except Exception:
        hrefs = []

    def _score_href_only(href: str) -> int:
        if not href or href.startswith('#') or href.lower().startswith('javascript:'):
            return -1
        s = 0
        if _likely_staff_url(href): s += 100
        # Prefer slugs that are especially relevant
        for k, w in (("/veterinarians", 50),("/our-veterinarians", 60),("/our-doctors", 45),("/providers", 40),("/team", 35),("/our-team", 45),("/staff", 30)):
            if k in href.lower():
                s += w
        if _is_career_or_nonstaff(href): s -= 220
        try:
            if cur_host and _host_of(href).endswith(cur_host):
                s += 10
        except Exception:
            pass
        s -= min(len(href), 200) // 50
        return s

    best_href, best_href_score = None, 0
    for h in hrefs:
        sc = _score_href_only(h)
        if sc > best_href_score:
            best_href, best_href_score = h, sc
    if best_href and best_href_score >= 100:
        try:
            driver.get(best_href)
            if _wait_for_navigation(driver, start_url, timeout=8.0):
                return True
        except Exception:
            pass
    return False


def find_best_staff_href(driver: webdriver.Chrome) -> str | None:
    """Return the single best staff-like absolute href found anywhere on the page.

    Uses similar scoring as navigation helpers; excludes career/join/apply links.
    """
    try:
        cur_host = _host_of(driver.current_url or "")
    except Exception:
        cur_host = ""
    try:
        hrefs = driver.execute_script("return Array.from(document.querySelectorAll('a[href]')).map(a=>a.href);") or []
    except Exception:
        hrefs = []

    def _score_href_only(href: str) -> int:
        if not href or href.startswith('#') or href.lower().startswith('javascript:'):
            return -1
        s = 0
        hl = (href or '').lower()
        if _likely_staff_url(hl): s += 100
        for k, w in (("/veterinarians", 60),("/our-veterinarians", 70),("/our-doctors", 55),("/providers", 45),("/team", 40),("/our-team", 50),("/staff", 35)):
            if k in hl:
                s += w
        if _is_career_or_nonstaff(hl): s -= 220
        try:
            if cur_host and _host_of(href).endswith(cur_host):
                s += 10
        except Exception:
            pass
        s -= min(len(href), 200) // 50
        return s

    best, best_score = None, 0
    for h in hrefs:
        sc = _score_href_only(h)
        if sc > best_score:
            best, best_score = h, sc
    return best if best_score >= 100 else None


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


def page_looks_like_staff_listing(driver: webdriver.Chrome) -> bool:
    """Heuristically detect if the current page already shows a staff/providers section.

    This catches homepages that list the team inline (no navigation needed).
    The heuristic looks for:
      - Headings containing team/doctor/provider keywords
      - Body text containing multiple person/role tokens (e.g., Dr., DVM, Veterinarian)
      - Multiple images/alts with doctor/vet tokens
      - Containers with id/class including team/provider/doctor/staff having meaningful text
    """
    try:
        from selenium.webdriver.common.by import By
    except Exception:
        return False

    try:
        # Quick heading keyword match
        heading_keywords = [
            "our team", "team", "providers", "our providers",
            "doctors", "physicians", "veterinarians", "veterinarian",
            "staff", "meet the team", "meet our team", "our veterinarians", "our doctors", "medical team",
        ]
        try:
            headings = driver.find_elements(By.XPATH, "//h1 | //h2 | //h3 | //h4 | //h5 | //h6")
        except Exception:
            headings = []
        for h in headings:
            try:
                if not h.is_displayed():
                    continue
                t = (h.text or "").strip().lower()
                if not t:
                    continue
                # Ignore career-oriented headings like "Join our team" to avoid false positives
                if any(k in t for k in heading_keywords):
                    if any(bad in t for bad in ("join", "career", "hiring", "employment", "job", "opportunit", "apply")):
                        continue
                    return True
            except Exception:
                continue

        # Aggregate body text once for token counting
        try:
            body_el = driver.find_element(By.TAG_NAME, "body")
            body_text = (body_el.text or "").lower()
        except Exception:
            body_text = ""

        # Count person/role tokens across the page
        tokens = [
            "dr.", "dr ", "doctor ", "dvm", "vmd", "md", "dds", "dmd", "bvsc",
            "veterinarian", "veterinary", "physician", "provider",
            "practice manager", "hospital manager", "owner", "co-owner",
        ]
        token_hits = 0
        if body_text:
            for tok in tokens:
                # Count occurrences up to a cap to prevent huge pages over-weighting
                cnt = body_text.count(tok)
                token_hits += min(cnt, 5)

        # Images/alts suggesting doctor/provider cards
        img_hits = 0
        try:
            imgs = driver.find_elements(By.XPATH, "//img[@alt or @title]")
        except Exception:
            imgs = []
        for img in imgs:
            try:
                if not img.is_displayed():
                    continue
                alt = ((img.get_attribute('alt') or '') + ' ' + (img.get_attribute('title') or '')).lower()
                if any(k in alt for k in ["dr", "dvm", "vmd", "doctor", "veterinarian", "provider", "our team", "team"]):
                    img_hits += 1
                    if img_hits >= 3:
                        break
            except Exception:
                continue

        # Containers with suggestive id/class having non-trivial text
        container_hits = 0
        try:
            containers = driver.find_elements(
                By.XPATH,
                "//*[contains(translate(@id,'TEAMPROVIDERDOCTORSTAFF','teamproviderdoctorstaff'),'team') or "
                "contains(translate(@id,'TEAMPROVIDERDOCTORSTAFF','teamproviderdoctorstaff'),'provider') or "
                "contains(translate(@id,'TEAMPROVIDERDOCTORSTAFF','teamproviderdoctorstaff'),'doctor') or "
                "contains(translate(@id,'TEAMPROVIDERDOCTORSTAFF','teamproviderdoctorstaff'),'staff') or "
                "contains(translate(@class,'TEAMPROVIDERDOCTORSTAFF','teamproviderdoctorstaff'),'team') or "
                "contains(translate(@class,'TEAMPROVIDERDOCTORSTAFF','teamproviderdoctorstaff'),'provider') or "
                "contains(translate(@class,'TEAMPROVIDERDOCTORSTAFF','teamproviderdoctorstaff'),'doctor') or "
                "contains(translate(@class,'TEAMPROVIDERDOCTORSTAFF','teamproviderdoctorstaff'),'staff')]"
            )
        except Exception:
            containers = []
        for c in containers[:20]:
            try:
                if not c.is_displayed():
                    continue
                t = (c.text or "").strip()
                # Skip containers that are obviously career-oriented
                tl = t.lower()
                if _is_career_or_nonstaff(tl):
                    continue
                if len(t) >= 40:  # likely a real block with content
                    container_hits += 1
                    if container_hits >= 2:
                        break
            except Exception:
                continue

        # Simple score aggregation
        score = 0
        if token_hits >= 3:
            score += 2
        if img_hits >= 2:
            score += 2
        if container_hits >= 1:
            score += 2
        # Extra boost if very keyword-y body text
        if body_text and any(k in body_text for k in ["meet the team", "our team", "our providers", "our doctors"]):
            score += 2

        # Penalize career/join related content on the page to reduce false positives
        neg_hits = 0
        for tok in ("career", "careers", "employment", "job", "jobs", "hiring", "apply", "application", "opportunit", "join our team", "join-our-team", "work with us", "work-with-us"):
            if body_text and tok in body_text:
                neg_hits += 1
        if neg_hits >= 2:
            score -= 4

        return score >= 3
    except Exception:
        return False


def _is_career_or_nonstaff(s: str) -> bool:
    """Return True if the string clearly refers to careers/join/apply type pages."""
    if not s:
        return False
    t = s.strip().lower()
    if not t:
        return False
    bad = (
        "career", "careers", "employment", "job", "jobs", "hiring", "apply", "application", "opportunit",
        "join our team", "join-our-team", "work with us", "work-with-us", "volunteer", "internship", "residency", "fellowship",
    )
    return any(b in t for b in bad)
