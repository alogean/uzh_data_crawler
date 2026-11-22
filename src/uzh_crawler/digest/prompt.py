from __future__ import annotations

from uzh_crawler.taxonomy import REQUIRED_AXES, Taxonomy

LANG_NAMES = {"fr": "français", "de": "allemand", "en": "anglais"}
SUMMARY_HEADINGS = {"fr": "Résumé", "de": "Zusammenfassung", "en": "Summary"}
_INPUT_LIMIT = 80_000


def system_prompt(taxonomy: Taxonomy, lang: str) -> str:
    language = LANG_NAMES[lang]
    sections = []
    for axis_id in REQUIRED_AXES:
        lines = [f"- {node_id} — {label}" for node_id, label in taxonomy.labels_for(axis_id, lang)]
        sections.append(f"### {axis_id}\n" + "\n".join(lines))
    catalogue = "\n\n".join(sections)
    return f"""Tu analyses une page du web de l'Université de Zurich déjà extraite en Markdown.
Tu t'appuies uniquement sur ce Markdown.

Choisis les tags uniquement parmi les id du schéma JSON. N'invente aucun terme.
- organisation : exactement un id, le nœud le plus spécifique plausible (une faculté plutôt que universitaet quand la page l'indique).
- publics : au moins un id.
- types_document : exactement un id, le type principal de la page.
- offre_etudes : une liste vide si la page n'est pas clairement liée à un cycle ou à une forme de programme.
Le résumé est en {language}, fidèle au texte, concis, et structuré avec des titres de niveau ## et des listes.
Pas de préambule en dehors du JSON.

Annexe (id — libellé). Seuls ces id sont autorisés :
{catalogue}
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
