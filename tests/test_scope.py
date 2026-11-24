from uzh_crawler.crawl.sitemap import parse_robots_sitemaps, parse_sitemap
from uzh_crawler.scope import (
    frontier_dedupe_key,
    prefix_matches,
    url_excluded,
    url_matches_prefixes,
)


def test_prefix_matches_the_section_and_not_its_sibling():
    assert prefix_matches("/de/studies", "/de/studies")
    assert prefix_matches("/de/studies/bachelor.html", "/de/studies")
    assert prefix_matches("/de/studies.html", "/de/studies.html")
    assert not prefix_matches("/de/studies.html", "/de/studies")
    assert not prefix_matches("/de/studies-extra", "/de/studies")


def test_prefix_confinement_blocks_dot_segments_and_encoded_slashes():
    prefixes = {"www.oec.uzh.ch": ("/de/studies",)}
    assert url_matches_prefixes("https://oec.uzh.ch/de/studies/a.html", prefixes)
    assert not url_matches_prefixes("https://www.oec.uzh.ch/de/studies/../faculty.html", prefixes)
    assert not url_matches_prefixes("https://www.oec.uzh.ch/de/ueber-uns%2F..%2Fstudies/a", prefixes)
    assert not url_matches_prefixes(
        "https://www.oec.uzh.ch/de/faculty?next=/de/studies/a",
        prefixes,
    )


def test_exclude_and_frontier_identity():
    assert url_excluded("https://www.oec.uzh.ch/de/Intranet/board.html", ("/intranet",))
    assert not url_excluded("https://www.oec.uzh.ch/de/studies.html", ("/intranet",))
    assert frontier_dedupe_key("https://www.OEC.uzh.ch/de/studies/?utm_source=x") == (
        "https://www.oec.uzh.ch/de/studies"
    )
    assert frontier_dedupe_key("https://www.oec.uzh.ch/de/studies/page/2") == (
        "https://www.oec.uzh.ch/de/studies"
    )


def test_sitemap_text_xml_and_robots():
    pages, nested = parse_sitemap(
        b"https://www.oec.uzh.ch/de/studies.html\n# comment\n",
        content_type="text/plain",
    )
    assert pages == ["https://www.oec.uzh.ch/de/studies.html"]
    assert nested == []
    xml = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.oec.uzh.ch/de/studies/a.html</loc></url>
    </urlset>"""
    pages, nested = parse_sitemap(xml, content_type="application/xml")
    assert pages == ["https://www.oec.uzh.ch/de/studies/a.html"]
    index = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://www.oec.uzh.ch/sitemap-de.xml</loc></sitemap>
    </sitemapindex>"""
    pages, nested = parse_sitemap(index, content_type="application/xml")
    assert pages == []
    assert nested == ["https://www.oec.uzh.ch/sitemap-de.xml"]
    assert parse_robots_sitemaps("User-agent: *\nSitemap: https://www.oec.uzh.ch/sitemap.txt\n") == [
        "https://www.oec.uzh.ch/sitemap.txt"
    ]
