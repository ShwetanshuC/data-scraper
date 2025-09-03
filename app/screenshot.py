from __future__ import annotations

import base64
import os
import tempfile
from io import BytesIO
from selenium import webdriver


def screenshot_to_base64(driver: webdriver.Chrome, *, target_width: int = 900, jpeg_quality: int = 40) -> str:
    try:
        raw_png = driver.get_screenshot_as_png()
        if not raw_png:
            return ""
        try:
            from PIL import Image  # type: ignore
            im = Image.open(BytesIO(raw_png)).convert("RGB")
            w, h = im.size
            if w > target_width:
                h2 = max(1, int(h * (target_width / float(w))))
                im = im.resize((target_width, h2))
            out = BytesIO()
            im.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
            return base64.b64encode(out.getvalue()).decode("utf-8")
        except Exception:
            size = driver.get_window_size()
            old_w, old_h = size.get("width", 1200), size.get("height", 800)
            driver.set_window_size(target_width, int(target_width * 0.62))
            small_png = driver.get_screenshot_as_png()
            try:
                driver.set_window_size(old_w, old_h)
            except Exception:
                pass
            if small_png:
                return base64.b64encode(small_png).decode("utf-8")
            return base64.b64encode(raw_png).decode("utf-8")
    except Exception:
        return ""


def save_temp_jpeg_screenshot(driver: webdriver.Chrome, *, target_width: int = 900, jpeg_quality: int = 40) -> str:
    raw_png = driver.get_screenshot_as_png()
    if not raw_png:
        raise RuntimeError("screenshot failed")
    try:
        from PIL import Image  # type: ignore
        im = Image.open(BytesIO(raw_png)).convert("RGB")
        w, h = im.size
        if w > target_width:
            h2 = max(1, int(h * (target_width / float(w))))
            im = im.resize((target_width, h2))
        fd, tmp_path = tempfile.mkstemp(prefix="gpt_shot_", suffix=".jpg")
        os.close(fd)
        im.save(tmp_path, format="JPEG", quality=jpeg_quality, optimize=True)
        return tmp_path
    except Exception:
        fd, tmp_path = tempfile.mkstemp(prefix="gpt_shot_", suffix=".png")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(raw_png)
        return tmp_path


def _cdp_capture_fullpage_jpeg(driver: webdriver.Chrome, *, target_width: int = 1400, quality: int = 50, max_pixels: int = 40_000_000) -> bytes:
    try:
        driver.execute_cdp_cmd("Page.enable", {})
    except Exception:
        pass
    metrics = driver.execute_cdp_cmd("Page.getLayoutMetrics", {})
    cs = metrics.get("contentSize", {})
    width = float(cs.get("width", 1200.0))
    height = float(cs.get("height", 2000.0))
    if width <= 0 or height <= 0:
        size = driver.get_window_size()
        width = float(size.get("width", 1200))
        height = float(size.get("height", 800))
    scale_w = min(1.0, target_width / max(width, 1.0))
    scale_pix = min(1.0, (max_pixels / max(width * height, 1.0)) ** 0.5)
    scale = max(0.05, min(scale_w, scale_pix))
    height = min(height, 60000.0)
    clip = {"x": 0, "y": 0, "width": width, "height": height, "scale": scale}
    res = driver.execute_cdp_cmd(
        "Page.captureScreenshot",
        {"format": "jpeg", "quality": int(quality), "fromSurface": True, "captureBeyondViewport": True, "clip": clip},
    )
    b64 = res.get("data") or ""
    return base64.b64decode(b64)


def save_temp_fullpage_jpeg_screenshot(driver: webdriver.Chrome, *, target_width: int = 1400, jpeg_quality: int = 50) -> str:
    try:
        jpeg_bytes = _cdp_capture_fullpage_jpeg(driver, target_width=target_width, quality=jpeg_quality)
        if jpeg_bytes:
            fd, tmp_path = tempfile.mkstemp(prefix="gpt_fullpage_", suffix=".jpg")
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(jpeg_bytes)
            return tmp_path
    except Exception:
        pass
    raw_png = driver.get_screenshot_as_png()
    fd, tmp_path = tempfile.mkstemp(prefix="gpt_view_", suffix=".jpg")
    os.close(fd)
    try:
        from PIL import Image  # type: ignore
        im = Image.open(BytesIO(raw_png)).convert("RGB")
        w, h = im.size
        if w > target_width:
            h2 = max(1, int(h * (target_width / float(w))))
            im = im.resize((target_width, h2))
        im.save(tmp_path, format="JPEG", quality=jpeg_quality, optimize=True)
    except Exception:
        with open(tmp_path, "wb") as f:
            f.write(raw_png)
    return tmp_path

