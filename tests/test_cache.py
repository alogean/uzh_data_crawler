from pathlib import Path

from uzh_crawler.store.cache_sqlite import StageCache

SOURCE = "https://www.phil.uzh.ch/"


def test_summary_is_reused_only_for_the_same_markdown_model_language_and_file(tmp_path: Path):
    summary = tmp_path / "summaries" / "abc.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("résumé\n", encoding="utf-8")
    hit = dict(source=SOURCE, content_hash="sha256:abc", model="qwen3.5:9b", lang="fr", run_dir=tmp_path)
    with StageCache(tmp_path / "cache.sqlite") as cache:
        assert cache.summary_reusable(**hit) is False
        cache.put_summary(
            source=SOURCE,
            content_hash="sha256:abc",
            model="qwen3.5:9b",
            lang="fr",
            output_file="summaries/abc.md",
            summary_hash="sha256:s",
        )
        assert cache.summary_reusable(**hit) is True
        assert cache.summary_reusable(**dict(hit, model="other-model")) is False
        assert cache.summary_reusable(**dict(hit, content_hash="sha256:other")) is False
        assert cache.summary_reusable(**dict(hit, lang="de")) is False
        summary.unlink()
        assert cache.summary_reusable(**hit) is False


def test_classification_is_reused_only_for_the_same_summary_model_taxonomy_and_file(tmp_path: Path):
    digest = tmp_path / "digests" / "abc.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("tags\n", encoding="utf-8")
    hit = dict(source=SOURCE, summary_hash="sha256:s", model="qwen3.5:9b", taxonomy_sha256="tax", run_dir=tmp_path)
    with StageCache(tmp_path / "cache.sqlite") as cache:
        assert cache.classification_reusable(**hit) is False
        cache.put_classification(
            source=SOURCE,
            summary_hash="sha256:s",
            model="qwen3.5:9b",
            taxonomy_sha256="tax",
            output_file="digests/abc.md",
        )
        assert cache.classification_reusable(**hit) is True
        assert cache.classification_reusable(**dict(hit, model="other-model")) is False
        assert cache.classification_reusable(**dict(hit, summary_hash="sha256:other")) is False
        assert cache.classification_reusable(**dict(hit, taxonomy_sha256="other-tax")) is False
        digest.unlink()
        assert cache.classification_reusable(**hit) is False
