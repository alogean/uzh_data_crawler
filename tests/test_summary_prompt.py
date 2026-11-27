import asyncio
import json

import httpx
import pytest

from uzh_crawler.llm.ollama import ModelOutputError, OllamaChat
from uzh_crawler.summarize.prompt import clean_for_llm, user_prompt
from uzh_crawler.summarize.summarizer import OllamaSummarizer, parse_answer


def test_cleaning_keeps_the_words_and_drops_the_noise():
    markdown = (
        "![Logo](/a.jpg)\n[Master in Politik](/de/master.html)\n"
        "| Modul | | |\n| --- | --- | --- |\n"
        "Mehr Informationen zum Studiengang\nMehr Informationen zum Studiengang\n**ECTS**\n"
    )
    text = clean_for_llm(markdown)
    assert "Master in Politik" in text and "Modul" in text and "ECTS" in text
    assert "/a.jpg" not in text and "master.html" not in text and "---" not in text
    assert text.count("Mehr Informationen zum Studiengang") == 1


def test_long_document_is_cut_and_the_language_is_asked_after_it():
    prompt = user_prompt("https://www.uzh.ch/x", "Studium. " * 20_000, "fr")
    assert len(prompt) < 33_000
    assert prompt.endswith("en français.")


def test_answer_gives_title_and_summary():
    summary = parse_answer("```markdown\n# Studium am IPZ\n\n## Public\n- étudiants\n```")
    assert summary.title == "Studium am IPZ"
    assert summary.resume_markdown == "## Public\n- étudiants"
    with pytest.raises(ModelOutputError):
        parse_answer("# Un titre seul")


@pytest.mark.parametrize("done_reason", ["stop", "length"])
def test_summary_call_is_free_text_and_refuses_a_cut_answer(done_reason):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200, json={"message": {"content": "# Titre\n\nRésumé."}, "done_reason": done_reason}
        )

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            chat = OllamaChat(client, ollama_url="http://ollama", temperature=0.1, num_ctx=1000)
            return await OllamaSummarizer(chat, model="m").summarize(url="u", markdown="texte", lang="fr")

    if done_reason == "length":
        with pytest.raises(ModelOutputError):
            asyncio.run(call())
    else:
        assert asyncio.run(call()).title == "Titre"
    assert "format" not in sent[0]
    assert sent[0]["options"]["repeat_penalty"] == 1.0
