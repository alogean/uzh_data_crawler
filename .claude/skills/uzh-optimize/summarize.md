# Summarize: what was tried

Measures, one per idea, are in `docs/summarize-performance.md` (50 rows). Benchmark: `benchmarks/summarize_speed.py`.

## In the code, and why

- **Free text, not JSON with a schema** (`OllamaChat.text`). With a schema, local models wrote one-line summaries, stopped early or looped to 4,096 tokens.
- **`repeat_penalty` 1.0** for this call. 1.25 was slower and degraded Markdown.
- **`gemma4:e2b`**, 4 calls at a time. Ollama's MLX engine serves 4 at once on 48 GB (`parallel=4` in `~/.ollama/logs/server.log`).
- **Markdown cleaning** before sending, **input cut at 32,000 characters**.
- **Language asked again after the document.** In the system prompt alone it is forgotten on long German documents.

## Rejected: do not retry without a new reason

- Fewer words asked for (150, 100): little time gained, facts lost. `gemma4` overruns the limit anyway.
- Input at 8,000 characters. 16,000 is no faster than 32,000 with `gemma4`.
- `num_ctx` 16,384, temperature 0, sorting documents by size, 8 or more calls at once.
- Models: `qwen3.5` 0.8b, 2b, 4b, 9b-mlx, 4b-mtp; `gemma4` e2b-it-qat, e2b-nvfp4, e4b (better coverage, twice slower); `ministral-3` 3b and 8b (good, 5 to 10 times slower); `granite4.1:3b`; `nemotron-3-nano:4b`.

## Traps

- **Prompt cache.** Ollama does not read again what it just read. Unload the model (`keep_alive: 0`) before each measured run, as the benchmark does. Without this, gains of 2x appear that do not exist.
- **Noise** is about 20% per run on a Mac in use (Spotlight, photo analysis). Repeat before believing less than that. Wrap long series in `caffeinate -i`.
- **The checklist does not see invented names or wrong readings.** Read the summaries, or have a large model judge them blind, before keeping a model change.
- **`classify` shares `OllamaChat.json_object`** with schema and penalty 1.25. It was not measured here.

## How to try an idea without editing the package

Write a throwaway script outside the repository that patches `summarize.prompt` or `OllamaChat`, then calls `uzh_crawler.cli.main`, and give it to the benchmark with `--command`.

## Ideas left

- Keep unit names in their original German (quality, no cost).
- A runtime other than Ollama.
