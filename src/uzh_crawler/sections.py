"""The eight faculty and study sections used as the default crawl."""

from __future__ import annotations

from urllib.parse import urlsplit

from uzh_crawler.scope import bare_host
from uzh_crawler.urls import normalize_url

# Each entry is the section index. The crawl also admits the matching folder
# and /dam/ on that host, the same split the data-catalog uses.
STUDY_SECTION_URLS: tuple[str, ...] = (
    "https://phil.uzh.ch/de/studium.html",
    "https://www.phil.uzh.ch/de/fakultaet.html",
    "https://www.oec.uzh.ch/de/faculty.html",
    "https://www.oec.uzh.ch/de/studies.html",
    "https://www.es.uzh.ch/en/studies.html",
    "https://www.econ.uzh.ch/de/study.html",
    "https://www.df.uzh.ch/en/studies.html",
    "https://www.ipz.uzh.ch/de/studium.html",
)


def section_prefixes(url: str) -> tuple[str, tuple[str, ...]]:
    """Host (without www) and the prefixes that confine that section."""
    normalized = normalize_url(url)
    parts = urlsplit(normalized)
    path = parts.path or "/"
    folder = path[:-5] if path.endswith(".html") else path.rstrip("/")
    prefixes = [path]
    if folder and folder != path:
        prefixes.append(folder)
    prefixes.append("/dam/")
    return bare_host(parts.hostname or ""), tuple(prefixes)


def study_section_scope() -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    """Normalized seeds and the merged per-host prefixes for every section."""
    seeds: list[str] = []
    grouped: dict[str, list[str]] = {}
    for raw in STUDY_SECTION_URLS:
        normalized = normalize_url(raw)
        if normalized not in seeds:
            seeds.append(normalized)
        host, prefixes = section_prefixes(normalized)
        current = grouped.setdefault(host, [])
        for prefix in prefixes:
            if prefix not in current:
                current.append(prefix)
    return tuple(seeds), {host: tuple(prefixes) for host, prefixes in grouped.items()}
