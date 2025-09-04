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
    # 1) Prefer the provided fallback handle if it matches the expected host
    if fallback_handle and fallback_handle in driver.window_handles:
        try:
            driver.switch_to.window(fallback_handle)
            cur = (driver.current_url or "").strip()
            host = _host_of(cur).lower()
            if host == expected or (expected and (host.endswith("." + expected) or expected.endswith("." + host))):
                return fallback_handle
        except Exception:
            pass
    # 2) Otherwise, choose the best-matching handle among all windows.
    # Prefer the one with the longest URL (likely a deeper path like /veterinarians/ over homepage).
    best_h = None
    best_score = -1
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            cur = (driver.current_url or "").strip()
            host = _host_of(cur).lower()
            if host == expected or (expected and (host.endswith("." + expected) or expected.endswith("." + host))):
                sc = len(cur)
                if sc > best_score:
                    best_h, best_score = h, sc
        except Exception:
            continue
    if best_h:
        driver.switch_to.window(best_h)
        return best_h
    # 3) Fall back to the provided handle even if host check failed (last resort)
    if fallback_handle and fallback_handle in driver.window_handles:
        try:
            driver.switch_to.window(fallback_handle)
            return fallback_handle
        except Exception:
            pass
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



def normalize_site(u: str) -> str:
    """Normalize a website URL for comparison (scheme+host+path without trailing slash)."""
    try:
        from urllib.parse import urlparse
        p = urlparse((u or '').strip())
        host = (p.hostname or '').lower()
        path = (p.path or '/').rstrip('/') or '/'
        scheme = (p.scheme or 'http').lower()
        return f"{scheme}://{host}{path}"
    except Exception:
        return (u or '').strip().rstrip('/')
