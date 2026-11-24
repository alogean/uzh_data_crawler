from pathlib import Path

import pytest

from uzh_crawler.cli import _config_from_args, _parser, main
from uzh_crawler.sections import STUDY_SECTION_URLS

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "taxonomy.json"


def test_seed_outside_uzh_is_a_config_error(tmp_path: Path):
    code = main(
        [
            "crawl",
            "--seed",
            "https://example.com/",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 1


def test_invalid_limits_and_missing_inputs_exit_1(tmp_path: Path):
    code = main(
        [
            "crawl",
            "--seed",
            "https://www.uzh.ch/de.html",
            "--out",
            str(tmp_path),
            "--max-pages",
            "-1",
        ]
    )
    assert code == 1
    missing = main(
        ["validate", "--out", str(tmp_path), "--taxonomy", str(tmp_path / "missing.json")]
    )
    assert missing == 1
    for command in ("summarize", "scrape"):
        assert main([command, "--out", str(tmp_path)]) == 1
    assert main(["classify", "--out", str(tmp_path), "--taxonomy", str(TAXONOMY)]) == 1


def test_include_prefix_rejects_a_host_or_a_prefix_that_matches_everything(tmp_path: Path):
    outside = main(
        [
            "crawl",
            "--seed",
            "https://www.uzh.ch/",
            "--include-prefix",
            "example.com",
            "/de",
            "--out",
            str(tmp_path),
        ]
    )
    assert outside == 1
    everything = main(
        [
            "crawl",
            "--seed",
            "https://www.uzh.ch/",
            "--include-prefix",
            "www.uzh.ch",
            "/",
            "--out",
            str(tmp_path),
        ]
    )
    assert everything == 1


def test_study_sections_include_the_eight_urls(tmp_path: Path):
    args = _parser().parse_args(
        [
            "crawl",
            "--study-sections",
            "--out",
            str(tmp_path),
        ]
    )
    config = _config_from_args(args)
    assert config.seed_urls == tuple(STUDY_SECTION_URLS)
    phil = config.include_prefixes["phil.uzh.ch"]
    assert "/de/studium.html" in phil
    assert "/de/studium" in phil
    assert "/de/fakultaet.html" in phil
    assert "/de/fakultaet" in phil
    assert "/dam/" in phil
    assert "/en/studies.html" in config.include_prefixes["df.uzh.ch"]
    assert "/de/study.html" in config.include_prefixes["econ.uzh.ch"]
    assert config.discover_sitemaps is True


def test_max_pages_zero_removes_the_ceiling(tmp_path: Path):
    args = _parser().parse_args(
        [
            "crawl",
            "--seed",
            "https://www.uzh.ch/",
            "--out",
            str(tmp_path),
            "--max-pages",
            "0",
        ]
    )
    assert _config_from_args(args).max_pages is None


def test_same_host_and_allow_subdomains_are_exclusive(tmp_path: Path):
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "crawl",
                "--seed",
                "https://www.uzh.ch/",
                "--out",
                str(tmp_path),
                "--same-host",
                "--allow-subdomains",
            ]
        )
    assert caught.value.code == 1


def test_a_bare_domain_is_a_seed_and_verbose_is_read_after_the_subcommand(tmp_path: Path):
    args = _parser().parse_args(["crawl", "phil.uzh.ch", "--out", str(tmp_path), "--verbose", "--sitemap-only"])
    config = _config_from_args(args)
    assert args.verbose is True
    assert config.seed_urls == ("https://phil.uzh.ch/",)
    assert config.sitemap_only is True
    assert config.taxonomy_path is None
    assert main(["crawl", "example.com", "--out", str(tmp_path)]) == 1
