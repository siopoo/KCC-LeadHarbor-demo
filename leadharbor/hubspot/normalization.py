from __future__ import annotations

import re
from urllib.parse import urlsplit


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
_COMPANY_SUFFIXES = {
    ("llc",), ("l", "l", "c"), ("llp",), ("l", "l", "p"),
    ("lp",), ("l", "p"), ("incorporated",), ("inc",),
    ("corporation",), ("corp",), ("company",), ("co",),
    ("limited",), ("ltd",),
}


def normalize_domain(value: str) -> str:
    """Return a safe registrable-looking host without protocol, www or path."""
    raw = (value or "").strip()
    if not raw or any(character.isspace() for character in raw):
        return ""
    candidate = raw if "://" in raw else f"//{raw}"
    try:
        parsed = urlsplit(candidate, scheme="https")
        host = (parsed.hostname or "").strip(".").casefold()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if not _DOMAIN_RE.fullmatch(host):
        return ""
    return host


def normalize_email(value: str) -> str:
    email = (value or "").strip().casefold()
    return email if _EMAIL_RE.fullmatch(email) else ""


def normalize_phone(value: str) -> str:
    raw = (value or "").strip().casefold()
    if not raw:
        return ""
    extension = ""
    match = re.search(r"(?:ext\.?|extension|x)\s*(\d+)\s*$", raw)
    if match:
        extension = f"x{match.group(1)}"
        raw = raw[:match.start()]
    digits = "".join(character for character in raw if character.isdigit())
    return f"{digits}{extension}" if len(digits) >= 7 else ""


def normalize_company_name(value: str) -> str:
    text = (value or "").casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = text.split()
    changed = True
    while tokens and changed:
        changed = False
        for suffix in sorted(_COMPANY_SUFFIXES, key=len, reverse=True):
            if tuple(tokens[-len(suffix):]) == suffix:
                del tokens[-len(suffix):]
                changed = True
                break
    return " ".join(tokens)
