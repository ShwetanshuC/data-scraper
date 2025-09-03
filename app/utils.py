from __future__ import annotations

import time
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By


def _host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def switch_to_site_tab_by_host(driver: webdriver.Chrome, expected_host: str, fallback_handle: str | None = None) -> str | None:
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


def get_visible_link_texts(driver: webdriver.Chrome, limit: int = 60) -> list[str]:
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
              if (out.length >= 200) break;
            }
            return out;
            """
        ) or []
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


def _nav_text_matches_links(nav_text: str, links: list[str]) -> bool:
    if not nav_text:
        return False
    t = nav_text.strip().lower()
    if not t:
        return False
    for L in links:
        if t == (L or '').strip().lower():
            return True
    for L in links:
        ll = (L or '').strip().lower()
        if not ll:
            continue
        if t in ll or ll in t:
            return True
    return False

