"""Section scope: path prefixes, exclusions, and frontier identity.

The same rules as the data-catalog crawl. A configured prefix matches the
path exactly or when the next character is ``/``. ``www`` and the bare host
are one site. Query strings and pagination do not create a second page.
"""

from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from uzh_crawler.urls import host_of, is_uzh_host, normalize_url

_PAGINATION_PATH_RE = re.compile(r"/(?:page|seite|pg)/\d+/?$", re.IGNORECASE)


def bare_host(host: str) -> str:
    lowered = host.lower().rstrip(".")
    if lowered.startswith("www."):
        return lowered[4:]
    return lowered


def same_site(left: str, right: str) -> bool:
    return bare_host(left) == bare_host(right)


def host_variants(host: str) -> set[str]:
    bare = bare_host(host)
    if not bare:
        return set()
    return {bare, f"www.{bare}"}


def prefixes_for_host(
    host: str,
    prefixes_by_host: dict[str, tuple[str, ...]],
) -> tuple[str, ...] | None:
    """Prefixes for this site, or None when the host is not confined."""
    wanted = bare_host(host)
    for configured, prefixes in prefixes_by_host.items():
        if bare_host(configured) == wanted:
            return prefixes
    return None


def frontier_dedupe_key(url: str) -> str:
    """Identity used to decide that a URL is already in the frontier.

    The stored page keeps the normalized URL. This key only collapses the
    variants that would otherwise be fetched twice: a trailing slash, the
    query string, and a trailing pagination segment.
    """
    parts = urlsplit(normalize_url(url))
    path = _PAGINATION_PATH_RE.sub("", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def url_excluded(url: str, tokens: tuple[str, ...]) -> bool:
    if not tokens:
        return False
    path = urlsplit(url).path.casefold()
    whole = url.casefold()
    for token in tokens:
        value = token.strip().casefold()
        if not value:
            continue
        if value.startswith(("http://", "https://")):
            value = urlsplit(value).path.casefold()
        if value and (value in path or value in whole):
            return True
    return False


def confinement_path(url: str) -> str | None:
    """Path used for prefix checks.

    ``None`` means the path is refused: an encoded slash would make the
    string we match differ from the path the origin serves. Dot segments
    are resolved so ``/de/faculty/../other`` cannot stay inside ``/de/faculty``.
    """
    raw = urlsplit(url).path or "/"
    if "%2f" in raw.casefold() or "%5c" in raw.casefold():
        return None
    decoded = unquote(raw)
    if "/." not in decoded and not decoded.startswith("."):
        return decoded or "/"
    normalized = posixpath.normpath(decoded)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    if decoded.endswith("/") and normalized != "/" and not normalized.endswith("/"):
        normalized += "/"
    return normalized


def prefix_matches(path: str, prefix: str) -> bool:
    root = "/" + prefix.strip("/")
    return path == root or path.startswith(root + "/")


def url_matches_prefixes(url: str, prefixes_by_host: dict[str, tuple[str, ...]]) -> bool:
    """True when the URL is inside its host's prefixes.

    A host with no entry is not confined and stays inside. A refused path
    (encoded slash) is outside.
    """
    if not prefixes_by_host:
        return True
    prefixes = prefixes_for_host(host_of(url), prefixes_by_host)
    if prefixes is None:
        return True
    path = confinement_path(url)
    if path is None:
        return False
    return any(prefix_matches(path, prefix) for prefix in prefixes)


class Scope:
    """Decides whether a URL belongs to the run: host first, then the section."""

    def __init__(
        self,
        *,
        seed_urls: tuple[str, ...],
        allow_subdomains: bool,
        include_prefixes: dict[str, tuple[str, ...]] | None,
        exclude_path_contains: tuple[str, ...],
    ):
        self.seed_hosts = {host_of(seed) for seed in seed_urls}
        self.allow_subdomains = allow_subdomains
        self.prefixes = include_prefixes or {}
        self.excludes = exclude_path_contains

    @classmethod
    def from_config(cls, config) -> Scope:
        return cls(
            seed_urls=config.seed_urls,
            allow_subdomains=config.allow_subdomains,
            include_prefixes=config.include_prefixes,
            exclude_path_contains=config.exclude_path_contains,
        )

    def classify(self, url: str) -> str:
        """``in``, ``foreign`` (other host), or ``skip`` (same site, outside the section)."""
        try:
            normalized = normalize_url(url)
        except ValueError:
            return "skip"
        host = host_of(normalized)
        if not is_uzh_host(host):
            return "foreign"
        if not self.allow_subdomains and not any(same_site(host, seed) for seed in self.seed_hosts):
            return "foreign"
        if url_excluded(normalized, self.excludes):
            return "skip"
        if self.prefixes and not url_matches_prefixes(normalized, self.prefixes):
            return "skip"
        return "in"
