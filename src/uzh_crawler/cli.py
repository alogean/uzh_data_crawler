from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from uzh_crawler.config import DEFAULT_MODEL, SUMMARY_LANGUAGES, ConfigError, RunConfig
from uzh_crawler.pipeline import execute
from uzh_crawler.proposals import (
    PROPOSALS_FILE,
    accept_proposals,
    check_proposals,
    load_proposals,
    unit_hints,
)
from uzh_crawler.scope import bare_host
from uzh_crawler.sections import study_section_scope
from uzh_crawler.taxonomy import TaxonomyError, load_taxonomy
from uzh_crawler.urls import host_of, is_uzh_host, normalize_url
from uzh_crawler.validate import validate_run

CRAWLING = {"crawl", "run"}


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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        if args.command == "validate":
            return _validate(args)
        if args.command == "proposals":
            return _proposals(args)
        config = _config_from_args(args)
        urls = tuple(getattr(args, "urls", None) or ())
        return asyncio.run(execute(config, mode=args.command, urls=urls))
    except (ConfigError, TaxonomyError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _parser() -> Parser:
    parser = Parser(
        prog="uzh_crawler",
        description=(
            "Quatre étapes sur un répertoire de run : crawl (lister les URL), "
            "scrape (télécharger), summarize (résumer), classify (taguer)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)

    crawl = sub.add_parser("crawl", help="Liste toutes les URL d'un domaine ou d'une section")
    _add_target_args(crawl)
    _add_out(crawl)
    _add_scope_args(crawl)
    _add_http_args(crawl, max_pages=500)

    scrape = sub.add_parser("scrape", help="Télécharge les URL et en extrait le texte")
    scrape.add_argument("urls", nargs="*", metavar="URL", help="URL à télécharger (défaut : celles du run)")
    _add_out(scrape)
    _add_http_args(scrape, max_pages=0)
    _add_scrape_args(scrape)

    summarize = sub.add_parser("summarize", help="Résume les documents avec le modèle local")
    summarize.add_argument("urls", nargs="*", metavar="URL", help="URL à résumer (défaut : celles du run)")
    _add_out(summarize)
    _add_llm_args(summarize)

    classify = sub.add_parser("classify", help="Tague les résumés avec la taxonomie")
    classify.add_argument("urls", nargs="*", metavar="URL", help="URL à classer (défaut : celles du run)")
    _add_out(classify)
    _add_taxonomy(classify)
    _add_llm_args(classify)

    run = sub.add_parser("run", help="Enchaîne crawl, scrape, summarize et classify")
    _add_target_args(run)
    _add_out(run)
    _add_taxonomy(run)
    _add_scope_args(run)
    _add_http_args(run, max_pages=500)
    _add_scrape_args(run)
    _add_llm_args(run)

    validate = sub.add_parser("validate", help="Vérifie crawl.json, les résumés et les digests")
    _add_out(validate)
    _add_taxonomy(validate)

    proposals = sub.add_parser("proposals", help="Nœuds candidats pour la taxonomie")
    actions = proposals.add_subparsers(dest="action", required=True, parser_class=Parser)
    hints = actions.add_parser("hints", help="Unités sans nœud relevées par la classification")
    _add_out(hints)
    check = actions.add_parser("check", help="Vérifie le fichier de propositions")
    accept = actions.add_parser("accept", help="Déplace des propositions dans taxonomy.json")
    accept.add_argument("ids", nargs="+", metavar="ID")
    for action in (check, accept):
        _add_taxonomy(action)
        action.add_argument("--proposals", default=PROPOSALS_FILE, help="Fichier de propositions")
        action.add_argument("--verbose", action="store_true")
    return parser


def _add_out(command: argparse.ArgumentParser) -> None:
    command.add_argument("--out", required=True, help="Répertoire du run")
    command.add_argument("--verbose", action="store_true")


def _add_taxonomy(command: argparse.ArgumentParser) -> None:
    command.add_argument("--taxonomy", default="taxonomy.json", help="Fichier taxonomy.json")


def _add_target_args(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "targets",
        nargs="*",
        metavar="DOMAINE",
        help="Domaine ou URL de départ sur uzh.ch, par exemple phil.uzh.ch",
    )
    command.add_argument(
        "--seed",
        action="append",
        help="URL de départ sur uzh.ch. Répéter l'option pour plusieurs pages.",
    )
    command.add_argument(
        "--study-sections",
        action="store_true",
        help=(
            "Ajoute les huit sections études et facultés "
            "(phil, oec, es, econ, df, ipz) et confine chacune à son dossier."
        ),
    )


def _add_scope_args(command: argparse.ArgumentParser) -> None:
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
        help="Rester sur l'hôte de départ (défaut)",
    )
    command.add_argument(
        "--include-prefix",
        nargs=2,
        action="append",
        default=[],
        metavar=("HOST", "PREFIX"),
        help=(
            "Confine un hôte à un préfixe de chemin. "
            "Le préfixe couvre le chemin exact et tout ce qui le suit. "
            "Répéter l'option ; lister l'index (.html) et le dossier à part."
        ),
    )
    command.add_argument(
        "--exclude-path",
        action="append",
        default=[],
        metavar="TOKEN",
        help="Ignore les URL dont le chemin contient ce texte. Répéter l'option.",
    )
    sitemap = command.add_mutually_exclusive_group()
    sitemap.add_argument(
        "--no-sitemap",
        action="store_true",
        help="Ne lit pas les sitemaps ; la liste vient des liens seulement.",
    )
    sitemap.add_argument(
        "--sitemap-only",
        action="store_true",
        help="Liste les URL des sitemaps sans lire aucune page. Le plus rapide.",
    )


def _add_http_args(command: argparse.ArgumentParser, *, max_pages: int) -> None:
    command.add_argument(
        "--max-pages",
        type=int,
        default=max_pages,
        help=f"Nombre maximum de requêtes de pages (0 = sans plafond, défaut {max_pages})",
    )
    command.add_argument(
        "--rate-limit",
        type=float,
        default=3.0,
        help="Requêtes par seconde et par hôte (0 = sans pause)",
    )
    command.add_argument("--crawl-concurrency", type=int, default=20)
    command.add_argument("--ignore-robots", action="store_true")


def _add_scrape_args(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--refresh",
        action="store_true",
        help="Redemande aussi les pages déjà stockées (requête conditionnelle)",
    )
    command.add_argument(
        "--js-fallback",
        action="store_true",
        help="Un passage Playwright si le texte utile fait moins de 200 caractères",
    )


def _add_llm_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--model", default=DEFAULT_MODEL)
    command.add_argument("--ollama-url", default="http://localhost:11434")
    command.add_argument("--llm-concurrency", type=int, default=2)
    command.add_argument("--lang", choices=SUMMARY_LANGUAGES, default="fr")
    command.add_argument("--num-ctx", type=int, default=32768)
    command.add_argument("--temperature", type=float, default=0.1)


def _config_from_args(args: argparse.Namespace) -> RunConfig:
    out = Path(args.out)
    if out.exists() and not out.is_dir():
        raise ConfigError(f"--out is not a directory: {out}")
    taxonomy: Path | None = None
    if hasattr(args, "taxonomy"):
        taxonomy = Path(args.taxonomy)
        if not taxonomy.is_file():
            raise ConfigError(f"taxonomy not found: {taxonomy}")

    max_pages = getattr(args, "max_pages", 0)
    if max_pages == 0:
        max_pages = None
    else:
        _check_minimum("--max-pages", max_pages, 1)
    _check_minimum("--crawl-concurrency", getattr(args, "crawl_concurrency", 1), 1)
    _check_minimum("--llm-concurrency", getattr(args, "llm_concurrency", 1), 1)
    _check_minimum("--num-ctx", getattr(args, "num_ctx", 1), 1)
    _check_minimum("--temperature", getattr(args, "temperature", 0), 0)
    _check_minimum("--rate-limit", getattr(args, "rate_limit", 0), 0)
    max_depth = getattr(args, "max_depth", None)
    if max_depth is not None:
        _check_minimum("--max-depth", max_depth, 0)

    seeds: list[str] = []
    section_prefixes: dict[str, tuple[str, ...]] = {}
    if args.command in CRAWLING:
        if args.study_sections:
            section_seeds, section_prefixes = study_section_scope()
            seeds.extend(section_seeds)
        for raw in [*(args.targets or []), *(args.seed or [])]:
            seed = _seed_url(raw)
            if seed not in seeds:
                seeds.append(seed)
        if not seeds:
            raise ConfigError("a seed URL is required")
    prefixes = _merge_prefixes(
        section_prefixes,
        _include_prefixes(getattr(args, "include_prefix", []) or []),
    )
    excludes = tuple(
        token.strip()
        for token in (getattr(args, "exclude_path", []) or [])
        if token and token.strip()
    )
    model = getattr(args, "model", DEFAULT_MODEL)
    return RunConfig(
        out_dir=out,
        seed_urls=tuple(seeds),
        taxonomy_path=taxonomy,
        summary_model=model,
        classify_model=model,
        ollama_url=getattr(args, "ollama_url", "http://localhost:11434"),
        max_pages=max_pages,
        max_depth=max_depth,
        allow_subdomains=bool(getattr(args, "allow_subdomains", False)),
        crawl_concurrency=getattr(args, "crawl_concurrency", 20),
        llm_concurrency=getattr(args, "llm_concurrency", 2),
        lang=getattr(args, "lang", "fr"),
        rate_limit=getattr(args, "rate_limit", 3.0),
        ignore_robots=bool(getattr(args, "ignore_robots", False)),
        js_fallback=bool(getattr(args, "js_fallback", False)),
        num_ctx=getattr(args, "num_ctx", 32768),
        temperature=getattr(args, "temperature", 0.1),
        include_prefixes=prefixes,
        exclude_path_contains=excludes,
        discover_sitemaps=not getattr(args, "no_sitemap", False),
        sitemap_only=bool(getattr(args, "sitemap_only", False)),
        refresh=bool(getattr(args, "refresh", False)),
    )


def _seed_url(raw: str) -> str:
    """A start URL from ``phil.uzh.ch``, ``phil.uzh.ch/de/studium.html`` or a full URL."""
    text = raw.strip()
    if "://" not in text:
        text = f"https://{text}"
    try:
        seed = normalize_url(text)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if not is_uzh_host(host_of(seed)):
        raise ConfigError(f"seed host is outside uzh.ch: {seed}")
    return seed


def _merge_prefixes(
    base: dict[str, tuple[str, ...]],
    extra: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    merged: dict[str, list[str]] = {host: list(prefixes) for host, prefixes in base.items()}
    for host, prefixes in extra.items():
        current = merged.setdefault(host, [])
        for prefix in prefixes:
            if prefix not in current:
                current.append(prefix)
    return {host: tuple(prefixes) for host, prefixes in merged.items()}


def _include_prefixes(pairs: list[list[str]]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for host_raw, prefix_raw in pairs:
        host = bare_host(host_raw.strip())
        if not host or not is_uzh_host(host):
            raise ConfigError(f"--include-prefix host is outside uzh.ch: {host_raw}")
        prefix = prefix_raw.strip()
        if not prefix.startswith("/") or not prefix.strip("/ \t"):
            raise ConfigError(
                f"--include-prefix {prefix_raw!r} would match every URL on {host}"
            )
        if any(char in prefix for char in "?#\\"):
            raise ConfigError(f"--include-prefix must be a path, not {prefix_raw!r}")
        current = grouped.setdefault(host, [])
        if prefix not in current:
            current.append(prefix)
    return {host: tuple(prefixes) for host, prefixes in grouped.items()}


def _validate(args: argparse.Namespace) -> int:
    errors = validate_run(Path(args.out), load_taxonomy(_taxonomy_path(args)))
    if not errors:
        print("validate: ok")
        return 0
    for error in errors:
        print(error, file=sys.stderr)
    return 2


def _proposals(args: argparse.Namespace) -> int:
    if args.action == "hints":
        print(json.dumps(unit_hints(Path(args.out)), ensure_ascii=False, indent=2))
        return 0
    taxonomy_path = _taxonomy_path(args)
    proposals_path = Path(args.proposals)
    if args.action == "check":
        taxonomy_data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
        errors = check_proposals(taxonomy_data, load_proposals(proposals_path))
        if not errors:
            print("proposals: ok")
            return 0
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    for node_id in accept_proposals(taxonomy_path, proposals_path, args.ids):
        print(f"accepted: {node_id}")
    return 0


def _taxonomy_path(args: argparse.Namespace) -> Path:
    path = Path(args.taxonomy)
    if not path.is_file():
        raise ConfigError(f"taxonomy not found: {path}")
    return path


def _check_minimum(flag: str, value: float, minimum: float) -> None:
    if value < minimum:
        raise ConfigError(f"{flag} must be >= {minimum}")
