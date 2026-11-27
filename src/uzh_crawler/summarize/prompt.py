from __future__ import annotations

import re

LANG_NAMES = {"fr": "français", "de": "allemand", "en": "anglais"}
SUMMARY_HEADINGS = {"fr": "Résumé", "de": "Zusammenfassung", "en": "Summary"}
# Reading a document costs the model as much time as writing its summary, so
# only this much of it is sent (docs/summarize-performance.md).
_INPUT_LIMIT = 32_000

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TABLE_RULE = re.compile(r"^[|: \t-]*-{3}[|: \t-]*$\n?", re.MULTILINE)
_EMPTY_CELLS = re.compile(r"(\|[ \t]*){2,}")  # never across lines: a row stays a row
_BARE_PIPE = re.compile(r"^\|[ \t]*$\n?", re.MULTILINE)
_SPACES = re.compile(r"[ \t]{2,}")
_BLANK_LINES = re.compile(r"\n{3,}")


def system_prompt(lang: str) -> str:
    language = LANG_NAMES[lang]
    return f"""Tu résumes un document du web de l'Université de Zurich déjà extrait en Markdown.
Tu t'appuies uniquement sur ce Markdown.

Réponds directement en Markdown, sans préambule. La première ligne est le titre du document, court, précédé de « # ». Vient ensuite le résumé en {language}, fidèle au texte, concis, structuré avec des titres de niveau ## et des listes.

Le résumé sert ensuite à classer le document sans relire le texte. Il nomme donc, quand le texte les indique :
- l'unité de l'UZH qui publie le document (faculté, institut, département, service) ;
- le public auquel il s'adresse ;
- sa nature (page d'accueil, règlement, formulaire, actualité, événement, offre d'emploi, etc.) ;
- le cycle ou la forme d'études concernés (bachelor, master, doctorat, formation continue, etc.).
Ce que le texte ne dit pas, le résumé ne le dit pas non plus : aucune supposition, aucune déduction à partir de l'URL.

Le résumé compte 250 mots au plus.
Il est rédigé entièrement en {language}, même quand le document est en allemand ou en anglais.
"""


def user_prompt(url: str, markdown: str, lang: str) -> str:
    text = truncate_for_llm(clean_for_llm(markdown))
    # The reminder comes after the document: put only before it, a long German
    # document made the model answer in German.
    return (
        f"URL: {url}\n\nMarkdown:\n{text}"
        f"\n\n---\nFin du document. Rédige maintenant le titre et le résumé en {LANG_NAMES[lang]}."
    )


def clean_for_llm(markdown: str) -> str:
    """Drop what carries no text for a summary: images, link targets, table rules, repeats."""
    text = _IMAGE.sub("", markdown)
    text = _LINK.sub(r"\1", text)
    text = _TABLE_RULE.sub("", text)
    text = _EMPTY_CELLS.sub("| ", text)
    text = _BARE_PIPE.sub("", text)
    text = _SPACES.sub(" ", text)
    seen: set[str] = set()
    kept = []
    for line in text.splitlines():
        key = line.strip()
        if len(key) > 25 and key in seen:
            continue
        seen.add(key)
        kept.append(line)
    text = "\n".join(kept).replace("**", "").replace("__", "")
    return _BLANK_LINES.sub("\n\n", text)


def truncate_for_llm(markdown: str, limit: int = _INPUT_LIMIT) -> str:
    """Keep the head, a heading list, and the tail when a document is very long."""
    if len(markdown) <= limit:
        return markdown
    headings = [line for line in markdown.splitlines() if line.startswith("#")][:100]
    marker = "\n\n[...]\n\n"
    if headings:
        toc = "\n".join(headings)
        marker = f"\n\n## Table des matières extraite\n{toc}\n\n[...]\n\n"
    budget = limit - len(marker)
    if budget < 1000:
        return markdown[:limit]
    head_len = int(budget * 0.75)
    tail_len = budget - head_len
    combined = markdown[:head_len] + marker + markdown[-tail_len:]
    return combined[:limit]
