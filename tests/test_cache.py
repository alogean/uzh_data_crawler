from pathlib import Path

from uzh_crawler.store.cache_sqlite import DigestCache


def test_cache_reuses_only_the_same_hash_model_taxonomy_and_file(tmp_path: Path):
    digest = tmp_path / "digests" / "abc.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("résumé\n", encoding="utf-8")
    with DigestCache(tmp_path / "cache.sqlite") as cache:
        assert (
            cache.reusable(
                source="https://www.phil.uzh.ch/",
                content_hash="sha256:abc",
                model="qwen3.5:9b",
                taxonomy_sha256="tax",
                run_dir=tmp_path,
            )
            is False
        )
        cache.put(
            source="https://www.phil.uzh.ch/",
            content_hash="sha256:abc",
            etag='"v1"',
            last_modified=None,
            output_file="digests/abc.md",
            model="qwen3.5:9b",
            taxonomy_sha256="tax",
        )
        hit = dict(
            source="https://www.phil.uzh.ch/",
            content_hash="sha256:abc",
            model="qwen3.5:9b",
            taxonomy_sha256="tax",
            run_dir=tmp_path,
        )
        assert cache.reusable(**hit) is True
        assert cache.reusable(**dict(hit, model="other-model")) is False
        assert cache.reusable(**dict(hit, content_hash="sha256:other")) is False
        assert cache.reusable(**dict(hit, taxonomy_sha256="other-tax")) is False
        digest.unlink()
        assert cache.reusable(**hit) is False
