from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def normalize_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return parsed._replace(fragment="").geturl()


def is_public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password or parsed.hostname.lower() == "localhost":
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror:
        return False
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        if not ip.is_global:
            return False
    return True


def domain_key(url: str) -> str:
    hostname = (urlparse(normalize_url(url)).hostname or "").lower()
    return hostname.removeprefix("www.")
