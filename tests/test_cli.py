from pathlib import Path

import pytest

from uzh_crawler.cli import main

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "taxonomy.json"


def test_seed_outside_uzh_is_a_config_error(tmp_path: Path):
    code = main(
        [
            "crawl-only",
            "--seed",
            "https://example.com/",
            "--out",
            str(tmp_path),
            "--taxonomy",
            str(TAXONOMY),
        ]
    )
    assert code == 1


def test_invalid_limits_and_missing_inputs_exit_1(tmp_path: Path):
    code = main(
        [
            "crawl-only",
            "--seed",
            "https://www.uzh.ch/de.html",
            "--out",
            str(tmp_path),
            "--taxonomy",
            str(TAXONOMY),
            "--max-pages",
            "0",
        ]
    )
    assert code == 1
    missing = main(
        ["validate", "--out", str(tmp_path), "--taxonomy", str(tmp_path / "missing.json")]
    )
    assert missing == 1
    assert (
        main(["digest-only", "--out", str(tmp_path), "--taxonomy", str(TAXONOMY)]) == 1
    )


def test_same_host_and_allow_subdomains_are_exclusive(tmp_path: Path):
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "crawl-only",
                "--seed",
                "https://www.uzh.ch/",
                "--out",
                str(tmp_path),
                "--taxonomy",
                str(TAXONOMY),
                "--same-host",
                "--allow-subdomains",
            ]
        )
    assert caught.value.code == 1
