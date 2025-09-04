from __future__ import annotations

import re


def _strip_fences_and_ws(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    if s.startswith("```"):
        s = "\n".join(ln for ln in s.splitlines() if not ln.strip().startswith("```")).strip()
    return s


def _clean_piece(p: str) -> str:
    if p is None:
        return ""
    return p.strip().strip("`").strip().strip('"').strip("'")


def parse_comma_reply(reply: str) -> tuple[str, str, str, str]:
    s = _strip_fences_and_ws(reply).replace("\n", " ").replace("  ", " ")
    parts = [_clean_piece(x) for x in s.split(",")]
    while len(parts) < 4:
        parts.append("")
    phone, first, last, locs = parts[:4]
    phone = re.sub(r"[^0-9xX()+\-.\s]", "", phone).strip()
    m = re.search(r"\d+", locs)
    if m:
        locs = m.group(0)
    return phone, first, last, locs


def parse_three_reply(reply: str) -> tuple[str, str, str]:
    s = _strip_fences_and_ws(reply).replace("\n", " ").replace("  ", " ")
    parts = [_clean_piece(x) for x in s.split(",")]
    while len(parts) < 3:
        parts.append("")
    phone, first, last = parts[:3]
    phone = re.sub(r"[^0-9xX()+\-.\s]", "", phone).strip()
    return phone, first, last


def extract_first_integer(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"\d+", text)
    if m:
        return m.group(0)
    return text.strip()


def build_nav_prompt(link_texts: list[str] | None = None) -> str:
    base = (
        "You are seeing a clinic homepage. Identify the ONE best clickable element from the navigation bar "
        "that will lead to a page listing doctors/staff (e.g., 'Our Team', 'Providers', 'Meet the Doctors'). "
        "If the link is inside a dropdown menu, reply using the format 'Parent > Link' (for example, 'About Us > Our Team'). "
        "Otherwise, reply with just the exact visible link text. Ensure the text is accurate and visible on the image page."
    )
    if link_texts:
        bullets = "\n".join(f"- {t}" for t in link_texts[:120])
        return base + "\n\nHere are the visible links on the page:\n" + bullets
    return base


def build_staff_csv_prompt() -> str:
    return (
        "You are seeing the clinic's staff/providers page. Using ONLY what is visible in this screenshot, "
        "return exactly ONE line in strict CSV format: First, Last, Doctors\n"
        "\n"
        "- First, Last: the clinic OWNER's first and last names if visible; else use the first doctor's name.\n"
        "- Doctors: the NUMBER of DOCTORS listed on this page (exclude non-physician staff). This field must be a numeric count with no words.\n"
        "Return only the CSV line, with no labels or extra words."
    )


def parse_owner_doctors_reply(reply: str) -> tuple[str, str, str]:
    """Parse 'First, Last, Doctors' returning (first,last,doctors)."""
    s2 = _strip_fences_and_ws(reply).replace("\n", " ").replace("  ", " ")
    parts = [_clean_piece(x) for x in s2.split(",")]
    while len(parts) < 3:
        parts.append("")
    first, last, doctors = parts[:3]
    import re as _re
    m = _re.search(r"\d+", doctors or "")
    if m:
        doctors = m.group(0)
    return first, last, doctors