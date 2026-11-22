from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

_SLASHES = re.compile(r"/{2,}")

# Campaign and analytics parameters. They do not identify a UZH document.
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "igshid",
        "mkt_tok",
        "vero_id",
        "yclid",
        "wickedid",
    }
)

# Percent is safe so an already-encoded octet is left as-is.
_PATH_SAFE = "/%:@-._~!$&'()*+,;="


def normalize_url(url: str) -> str:
    """Canonical http(s) URL: lower host, no fragment, no tracking query."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"unsupported URL scheme: {url}")
    if parts.username or parts.password:
        raise ValueError(f"URL userinfo is not allowed: {url}")
    host = parts.hostname
    if not host:
        raise ValueError(f"URL has no host: {url}")
    try:
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError(f"URL host is not valid: {url}") from exc
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"URL port is not valid: {url}") from exc
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = _SLASHES.sub("/", parts.path or "/")
    path = quote(path, safe=_PATH_SAFE)
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query_pairs.sort()
    query = "&".join(_encode_query_pair(key, value) for key, value in query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def _encode_query_pair(key: str, value: str) -> str:
    return f"{quote(key, safe='')}={quote(value, safe='')}"


def try_normalize(url: str) -> str | None:
    try:
        return normalize_url(url)
    except ValueError:
        return None


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""


def is_uzh_host(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    return lowered == "uzh.ch" or lowered.endswith(".uzh.ch")


def url_in_scope(url: str, seed_url: str, allow_subdomains: bool) -> bool:
    """Same host as the seed, or any uzh.ch host when subdomains are allowed.

    A host outside uzh.ch is always out of scope, including with --allow-subdomains.
    """
    host = host_of(normalize_url(url))
    seed_host = host_of(normalize_url(seed_url))
    if not is_uzh_host(host):
        return False
    if allow_subdomains:
        return True
    return host == seed_host
