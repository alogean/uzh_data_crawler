from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from uzh_crawler.config import SUMMARY_LANGUAGES, ConfigError, RunConfig
from uzh_crawler.pipeline import execute
from uzh_crawler.taxonomy import TaxonomyError, load_taxonomy
from uzh_crawler.urls import host_of, is_uzh_host, normalize_url
from uzh_crawler.validate import validate_run


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    try:
        if args.command == "validate":
            return _validate(args)
        config = _config_from_args(args)
        return asyncio.run(execute(config, mode=args.command))
    except (ConfigError, TaxonomyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _parser() -> Parser:
    parser = Parser(
        prog="uzh_crawler",
        description="Crawl un site UZH, résume chaque page et pose des tags fermés.",
    )
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Crawl puis résumé et tags")
    crawl = sub.add_parser("crawl-only", help="Crawl sans appeler le modèle")
    digest = sub.add_parser("digest-only", help="Résume les pages déjà collectées")
    validate = sub.add_parser("validate", help="Vérifie crawl.json et les digests")

    for command in (run, crawl):
        _add_crawl_args(command)
        _add_llm_args(command)
    _add_output_args(digest)
    _add_llm_args(digest)
    _add_output_args(validate)
    return parser


def _add_output_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--out", required=True, help="Répertoire du run")
    command.add_argument("--taxonomy", default="taxonomy.json", help="Fichier taxonomy.json")
    command.add_argument("--verbose", action="store_true")


def _add_crawl_args(command: argparse.ArgumentParser) -> None:
    _add_output_args(command)
    command.add_argument("--seed", required=True, help="URL de départ sur uzh.ch")
    command.add_argument("--max-pages", type=int, default=500)
    command.add_argument("--max-depth", type=int, default=None)
    scope = command.add_mutually_exclusive_group()
    scope.add_argument(
        "--allow-subdomains",
        action="store_true",
        help="Suivre les autres hôtes *.uzh.ch",
    )
    scope.add_argument(
        "--same-host",
        action="store_true",
        help="Rester sur l'hôte de la seed (défaut)",
    )
    command.add_argument("--ignore-robots", action="store_true")
    command.add_argument(
        "--rate-limit",
        type=float,
        default=3.0,
        help="Requêtes par seconde et par hôte (0 = sans pause)",
    )
    command.add_argument("--crawl-concurrency", type=int, default=20)
    command.add_argument(
        "--js-fallback",
        action="store_true",
        help="Un passage Playwright si le texte utile fait moins de 200 caractères",
    )


def _add_llm_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--model", default="qwen3.5:9b")
    command.add_argument("--ollama-url", default="http://localhost:11434")
    command.add_argument("--llm-concurrency", type=int, default=2)
    command.add_argument("--lang", choices=SUMMARY_LANGUAGES, default="fr")
    command.add_argument("--num-ctx", type=int, default=32768)
    command.add_argument("--temperature", type=float, default=0.1)


def _config_from_args(args: argparse.Namespace) -> RunConfig:
    taxonomy = Path(args.taxonomy)
    if not taxonomy.is_file():
        raise ConfigError(f"taxonomy not found: {taxonomy}")
    out = Path(args.out)
    if out.exists() and not out.is_dir():
        raise ConfigError(f"--out is not a directory: {out}")
    _check_positive("--max-pages", getattr(args, "max_pages", 1), minimum=1)
    _check_positive("--crawl-concurrency", getattr(args, "crawl_concurrency", 1), minimum=1)
    _check_positive("--llm-concurrency", args.llm_concurrency, minimum=1)
    _check_positive("--num-ctx", args.num_ctx, minimum=1)
    if args.temperature < 0:
        raise ConfigError("--temperature must be >= 0")
    if getattr(args, "rate_limit", 0) < 0:
        raise ConfigError("--rate-limit must be >= 0")
    max_depth = getattr(args, "max_depth", None)
    if max_depth is not None and max_depth < 0:
        raise ConfigError("--max-depth must be >= 0")

    seed = ""
    if args.command in {"run", "crawl-only"}:
        try:
            seed = normalize_url(args.seed)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if not is_uzh_host(host_of(seed)):
            raise ConfigError(f"seed host is outside uzh.ch: {seed}")
    return RunConfig(
        seed_url=seed,
        taxonomy_path=taxonomy,
        out_dir=out,
        model=args.model,
        ollama_url=args.ollama_url,
        max_pages=getattr(args, "max_pages", 500),
        max_depth=max_depth,
        allow_subdomains=bool(getattr(args, "allow_subdomains", False)),
        crawl_concurrency=getattr(args, "crawl_concurrency", 20),
        llm_concurrency=args.llm_concurrency,
        lang=args.lang,
        rate_limit=getattr(args, "rate_limit", 3.0),
        ignore_robots=bool(getattr(args, "ignore_robots", False)),
        js_fallback=bool(getattr(args, "js_fallback", False)),
        num_ctx=args.num_ctx,
        temperature=args.temperature,
    )


def _validate(args: argparse.Namespace) -> int:
    taxonomy_path = Path(args.taxonomy)
    if not taxonomy_path.is_file():
        raise ConfigError(f"taxonomy not found: {taxonomy_path}")
    errors = validate_run(Path(args.out), load_taxonomy(taxonomy_path))
    if not errors:
        print("validate: ok")
        return 0
    for error in errors:
        print(error, file=sys.stderr)
    return 2


def _check_positive(flag: str, value: int, *, minimum: int) -> None:
    if value < minimum:
        raise ConfigError(f"{flag} must be >= {minimum}")
