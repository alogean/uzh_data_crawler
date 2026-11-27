"""The ``taxonomy`` command: every read and write of taxonomy.json goes through it."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

from uzh_crawler.config import SUMMARY_LANGUAGES, ConfigError
from uzh_crawler.gate import changes, checks, export, read, store
from uzh_crawler.gate.hints import unit_hints

ACTIONS = (
    "tree", "show", "find", "check", "propose", "pending", "accept", "reject", "history", "export", "hints",
)


def add_parser(sub, parser_class) -> None:
    gate = sub.add_parser(
        "taxonomy",
        help="Lit, vérifie, fait évoluer et exporte la taxonomie",
        description="Seule porte d'entrée de taxonomy.json : lecture, contrôle, propositions, export.",
    )
    actions = gate.add_subparsers(dest="action", required=True, parser_class=parser_class)

    tree = actions.add_parser("tree", help="Affiche l'arbre de chaque axe")
    tree.add_argument("--axis", help="Un seul axe")
    tree.add_argument("--deprecated", action="store_true", help="Montre aussi les nœuds retirés")

    show = actions.add_parser("show", help="Tout sur un nœud : chemin, libellés, historique")
    show.add_argument("id", metavar="ID")

    find = actions.add_parser("find", help="Cherche un nœud par son id ou ses libellés")
    find.add_argument("words", nargs="+", metavar="MOT")

    actions.add_parser("check", help="Contrôle la qualité de la taxonomie et des propositions")

    propose = actions.add_parser("propose", help="Ajoute une proposition (JSON) après l'avoir contrôlée")
    propose.add_argument("file", nargs="?", default="-", metavar="FICHIER", help="Fichier JSON (défaut : entrée standard)")

    actions.add_parser("pending", help="Liste les propositions en attente")

    accept = actions.add_parser("accept", help="Applique les propositions nommées par une personne")
    accept.add_argument("ids", nargs="+", metavar="ID")

    reject = actions.add_parser("reject", help="Écarte des propositions et garde la raison")
    reject.add_argument("ids", nargs="+", metavar="ID")
    reject.add_argument("--reason", required=True, help="Pourquoi la proposition est écartée")

    history = actions.add_parser("history", help="Historique des changements, d'un nœud ou de tous")
    history.add_argument("id", nargs="?", metavar="ID")

    exporter = actions.add_parser("export", help="Exporte en CSV, JSON, Markdown ou SKOS Turtle")
    exporter.add_argument("--format", required=True, choices=export.FORMATS)
    exporter.add_argument("--output", help="Fichier à écrire (défaut : sortie standard)")

    hints = actions.add_parser("hints", help="Unités sans nœud relevées par la classification d'un run")
    hints.add_argument("--out", required=True, help="Répertoire du run")

    for name, action in actions.choices.items():
        action.add_argument("--taxonomy", default="taxonomy.json", help="Fichier taxonomy.json")
        action.add_argument("--verbose", action="store_true")
        if name in ("tree", "show", "find", "pending", "history"):
            action.add_argument("--json", action="store_true", help="Sortie JSON")
        if name in ("tree", "find", "export"):
            action.add_argument("--lang", choices=SUMMARY_LANGUAGES, default="fr", help="Langue des libellés")


def run(args: argparse.Namespace) -> int:
    path = Path(args.taxonomy)
    today = datetime.date.today().isoformat()
    action = args.action
    if action == "hints":
        data = store.read_taxonomy(path) if path.is_file() else None
        _print_json(unit_hints(Path(args.out), data))
        return 0
    data = store.read_taxonomy(path)
    history = store.read_history(store.history_path(path))
    pending = store.read_proposals(store.proposals_path(path))["proposals"]

    if action == "tree":
        if args.json:
            _print_json(json.loads(export.to_json(data, []))["axes"])
        else:
            print(read.tree(data, axis=args.axis, lang=args.lang, deprecated=args.deprecated))
        return 0
    if action == "show":
        shown = read.show(data, history, pending, args.id)
        _print_json(shown) if args.json else print(_yaml(shown), end="")
        return 0
    if action == "find":
        found = read.find(data, " ".join(args.words), lang=args.lang)
        if args.json:
            _print_json(found)
        elif not found:
            print("no node found", file=sys.stderr)
            return 2
        for item in [] if args.json else found:
            print(f"{item['id']}  {item['axis']}  {item['label']}  [{item['status']}]  ({item['match']})")
        return 0
    if action == "check":
        return _check(data, path, len(pending))
    if action == "propose":
        return _propose(path, args.file, today)
    if action == "pending":
        rows = read.pending_rows(pending)
        if args.json:
            _print_json(rows)
        for row in [] if args.json else rows:
            print(f"{row['id']}  {row['change']}  {row['date']}  {row['reason']}")
        if not rows and not args.json:
            print("no pending proposal")
        return 0
    if action == "accept":
        try:
            old, new, entries = changes.accept(path, args.ids, today)
        except changes.ChangeError as exc:
            _print_lines(exc.messages)
            return 2
        for entry in entries:
            print(f"accepted: {entry['id']} ({entry['change']})")
        print(f"version: {old} -> {new}")
        print("taxonomy.json changed: the next classify tags every page again (summaries are kept).")
        return 0
    if action == "reject":
        for entry in changes.reject(path, args.ids, args.reason, today):
            print(f"rejected: {entry['id']} ({entry['proposal'].get('change', 'add')})")
        return 0
    if action == "history":
        if args.id:
            read.Index(data).node(args.id)
        if args.json:
            _print_json([entry for entry in history if not args.id or entry.get("id") == args.id])
        else:
            print("\n".join(read.history_lines(history, args.id)) or "no change recorded")
        return 0
    if action == "export":
        text = export.export(data, history, args.format, lang=args.lang)
        if args.output:
            store.write_text(Path(args.output), text)
            print(f"exported: {args.output} ({args.format})", file=sys.stderr)
        else:
            sys.stdout.write(text)
        return 0
    raise ConfigError(f"unknown action: {action}")


def _check(data: dict, path: Path, pending_count: int) -> int:
    issues = checks.check_taxonomy(data)
    for issue in issues:
        print(issue, file=sys.stderr)
    outcomes = changes.pending_check(path) if pending_count else []
    for outcome in outcomes:
        _print_lines(outcome.lines())
    errors = len(checks.errors_of(issues)) + sum(len(outcome.errors) for outcome in outcomes)
    warnings = len(issues) - len(checks.errors_of(issues)) + sum(len(o.warnings) for o in outcomes)
    nodes = [node for axis in data.get("axes", []) for node in axis.get("nodes", [])]
    statuses: dict[str, int] = {}
    for node in nodes:
        statuses[node.get("status")] = statuses.get(node.get("status"), 0) + 1
    summary = ", ".join(f"{count} {status}" for status, count in statuses.items())
    line = (
        f"version {data.get('version')}, {len(nodes)} nodes ({summary}), "
        f"{pending_count} pending, {errors} errors, {warnings} warnings"
    )
    if errors:
        print(f"taxonomy: errors — {line}", file=sys.stderr)
        return 2
    print(f"taxonomy: ok — {line}")
    return 0


def _propose(path: Path, source: str, today: str) -> int:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"the proposal is not valid JSON: {exc}") from exc
    items = parsed if isinstance(parsed, list) else [parsed]
    outcomes = changes.propose(path, items, today)
    for outcome in outcomes:
        _print_lines(outcome.lines())
    if any(outcome.errors for outcome in outcomes):
        print("propose: refused, nothing was written", file=sys.stderr)
        return 2
    for outcome in outcomes:
        print(f"proposed: {outcome.id} ({outcome.change})")
    print("Waiting for a person to accept or reject: taxonomy pending")
    return 0


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line, file=sys.stderr)


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _yaml(value: object) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100)
