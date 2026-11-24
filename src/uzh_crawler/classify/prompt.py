from __future__ import annotations

from uzh_crawler.taxonomy import REQUIRED_AXES, Taxonomy


def system_prompt(taxonomy: Taxonomy, lang: str) -> str:
    sections = []
    for axis_id in REQUIRED_AXES:
        lines = [f"- {node_id} — {label}" for node_id, label in taxonomy.labels_for(axis_id, lang)]
        sections.append(f"### {axis_id}\n" + "\n".join(lines))
    catalogue = "\n\n".join(sections)
    return f"""Tu classes un document du web de l'Université de Zurich à partir de son résumé.
Tu t'appuies uniquement sur l'URL, le titre et le résumé fournis.

Choisis les tags uniquement parmi les id du schéma JSON. N'invente aucun terme.
- organisation : exactement un id, le nœud le plus spécifique plausible (une faculté plutôt que universitaet quand le résumé l'indique).
- publics : au moins un id.
- types_document : exactement un id, le type principal du document.
- offre_etudes : une liste vide si le document n'est pas clairement lié à un cycle ou à une forme de programme.
- unite_manquante : le nom de l'unité (institut, département, chaire, service) qui publie le document, quand aucun id d'organisation ne la désigne et que tu as dû choisir un nœud plus général. Sinon null.
Pas de préambule en dehors du JSON.

Annexe (id — libellé). Seuls ces id sont autorisés :
{catalogue}
"""


def user_prompt(url: str, title: str, summary: str) -> str:
    return f"URL: {url}\nTitre: {title}\n\nRésumé:\n{summary}"
