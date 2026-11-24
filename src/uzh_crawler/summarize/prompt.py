from __future__ import annotations

LANG_NAMES = {"fr": "français", "de": "allemand", "en": "anglais"}
SUMMARY_HEADINGS = {"fr": "Résumé", "de": "Zusammenfassung", "en": "Summary"}
_INPUT_LIMIT = 80_000


def system_prompt(lang: str) -> str:
    language = LANG_NAMES[lang]
    return f"""Tu résumes un document du web de l'Université de Zurich déjà extrait en Markdown.
Tu t'appuies uniquement sur ce Markdown.

Réponds par un objet JSON à deux champs.
- title : le titre du document, court, sans mise en forme.
- resume_markdown : le résumé en {language}, fidèle au texte, concis, structuré avec des titres de niveau ## et des listes.

Le résumé sert ensuite à classer le document sans relire le texte. Il nomme donc, quand le texte les indique :
- l'unité de l'UZH qui publie le document (faculté, institut, département, service) ;
- le public auquel il s'adresse ;
- sa nature (page d'accueil, règlement, formulaire, actualité, événement, offre d'emploi, etc.) ;
- le cycle ou la forme d'études concernés (bachelor, master, doctorat, formation continue, etc.).
Ce que le texte ne dit pas, le résumé ne le dit pas non plus : aucune supposition, aucune déduction à partir de l'URL.
Pas de préambule en dehors du JSON.
"""


def user_prompt(url: str, markdown: str) -> str:
    return f"URL: {url}\n\nMarkdown:\n{truncate_for_llm(markdown)}"


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
