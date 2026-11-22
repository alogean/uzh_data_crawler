import pytest

from uzh_crawler.urls import normalize_url, url_in_scope


def test_normalize_drops_fragment_tracking_default_port_and_sorts_query():
    raw = "HTTPS://WWW.Phil.UZH.ch:443/de//studium.html?utm_source=news&b=2&a=1#section"
    assert normalize_url(raw) == "https://www.phil.uzh.ch/de/studium.html?a=1&b=2"


def test_normalize_keeps_path_case_non_default_port_and_real_query():
    assert normalize_url("https://www.phil.uzh.ch:8443/De/A?id=7") == (
        "https://www.phil.uzh.ch:8443/De/A?id=7"
    )


def test_normalize_encodes_spaces_without_double_encoding():
    assert normalize_url("https://www.uzh.ch/a b") == "https://www.uzh.ch/a%20b"
    assert normalize_url("https://www.uzh.ch/a%20b") == "https://www.uzh.ch/a%20b"
    assert normalize_url("https://www.uzh.ch") == "https://www.uzh.ch/"
    assert normalize_url("https://www.uzh.ch./de") == "https://www.uzh.ch/de"


def test_normalize_rejects_non_http_and_userinfo():
    with pytest.raises(ValueError):
        normalize_url("javascript:alert(1)")
    with pytest.raises(ValueError):
        normalize_url("https://user:pass@www.uzh.ch/de")
    with pytest.raises(ValueError):
        normalize_url("mailto:info@uzh.ch")


def test_scope_is_the_seed_host_or_uzh_subdomains_when_asked():
    seed = "https://www.phil.uzh.ch/"
    assert url_in_scope("https://www.phil.uzh.ch/de.html", seed, False)
    assert not url_in_scope("https://www.uzh.ch/de.html", seed, False)
    assert url_in_scope("https://www.uzh.ch/de.html", seed, True)
    assert url_in_scope("https://zi.uzh.ch/", seed, True)
    assert not url_in_scope("https://ethz.ch/studium", seed, True)
    assert not url_in_scope("https://notuzh.ch/", seed, True)
    assert not url_in_scope("https://uzh.ch.evil.com/", seed, True)
    assert url_in_scope("https://uzh.ch/de.html", "https://uzh.ch/", False)
