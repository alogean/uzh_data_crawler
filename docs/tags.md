# Tags

Tags are `id` values from [`taxonomy.json`](../taxonomy.json), never free labels. The file has `schema_version` 1, `kind` `taxonomy`, and four axes. Each node has an `id` that is unique in the whole file, a `parent` in the same axis or `null`, and `labels` and a `definition` in German, French and English. The full list of fields and the rules they follow are in [Taxonomy management](taxonomy.md#what-a-node-is).

| Axis | Role | Cardinality | Nodes today |
| --- | --- | --- | --- |
| `organisation` | Who publishes the document | exactly 1 | 30 |
| `publics` | Who it is for | 1 to N | 10 |
| `types_document` | Main genre | exactly 1 | 17 |
| `offre_etudes` | Study level or programme form | 0 to N | 9 |

The axis names and node ids are identifiers and stay as they are, whatever the summary language.

An unknown id, a wrong cardinality or an extra field prevents `tagged`. The page stays `tag_error`. An id repeated in a list is kept once. `validate` runs the same check again on pages already tagged.

A node whose `status` is `deprecated` is retired: it stays in the file but is left out of the list the model sees, and a tag with its id is refused. A page tagged with it before it was retired fails `validate` with the ids that replace it, until `classify` runs again. How nodes are added, changed and retired is in [Taxonomy management](taxonomy.md).

The skeleton is incomplete: many institute or department pages have no node named after the unit. The model then picks a broader node that exists in the axis, for example `fakultaeten`, `departement` or `institut`, and names the unit in `missing_unit`. That is the starting point of a [taxonomy proposal](taxonomy.md#hints-from-a-run).

A taxonomy file that is not valid JSON, lacks one of the four axes, repeats a node id, or has a `parent` outside its axis stops the command with exit code 1.

## Two model calls

A document goes through the local model twice. The first call writes a summary and knows nothing of the taxonomy. The second call reads that summary and chooses the tags.

| | `summarize` | `classify` |
| --- | --- | --- |
| Input | The URL and the Markdown of the document | The URL, the title and the summary |
| Model | `gemma4:e2b`, 4 calls at a time | `qwen3.5:9b`, 2 calls at a time |
| Answer | Free text: `# title`, then the summary | JSON: `organisation`, `publics`, `types_document`, `offre_etudes`, `unite_manquante` |
| Constrained by | The prompt only | A schema where each axis is a closed list of taxonomy ids |
| Redone when | The Markdown, the model or the language changes | The summary, the model or `taxonomy.json` changes |

Because classification sees only the summary, the summary prompt asks for what the tags depend on: the publishing unit, the audience, the kind of document and the study level, whenever the text gives them, and nothing the text does not say. A summary that leaves one of these out weakens the tag on that axis.

Both calls share the same mechanics:

- The call is `POST /api/chat` on Ollama, with `think` false. Before the first document, the tool checks that Ollama answers on `/api/version`; if not, the command stops with exit code 1.
- The options sent are the temperature, `num_ctx`, `num_predict` at 4096 and `repeat_penalty`: 1.0 for a summary, 1.25 for tags.
- `classify` sends in `format` the JSON schema of a Pydantic model, and extra fields are forbidden. `summarize` sends no schema: with one, local models looped or stopped early ([Summarize performance](summarize-performance.md)).
- The call times out after 180 seconds. An empty or unusable answer (off-schema tags; a summary without title and text, or cut at 4,096 tokens) is tried a second time; after that the page becomes `summary_error` or `tag_error` with the error message, cut to 500 characters.
- Up to `--llm-concurrency` calls run in parallel (4 by default for summaries, 2 for tags).

Both system prompts are written in French.

## The summary call

Only Markdown goes to the model, never HTML. It is cleaned first: images, link targets, table rules, empty cells, bold marks and repeated lines are removed. `raw/` keeps the full text. A text still longer than 32,000 characters is cut down to that size: the beginning, a list of up to 100 headings found in the document, then the end.

The summary is asked for in the language of `--lang`, 250 words at most, faithful to the text, and structured with `##` headings and lists. The language is asked for in the system prompt and again after the document. Models often write more than 250 words.

## The classification call

The system prompt ends with the list of allowed ids for each axis, each with its label in the language of `--lang`. The schema repeats those ids as closed enumerations, so the model cannot return another value.

The prompt asks for the most specific `organisation` node the summary supports, exactly one `types_document`, at least one `publics`, and an empty `offre_etudes` when the document is not clearly tied to a study level or programme form.

`unite_manquante` is the one free-text field. The model fills it with the name of the publishing unit (institute, department, chair, service) when no `organisation` id names that unit and it had to choose a broader node; otherwise it is `null`. It is stored as the page's `missing_unit` and is never a tag.

A context of 32,768 tokens fits easily in the unified memory of a 48 GB Mac for a model of about 9 billion parameters.
