from pathlib import Path

import office_files
import pymupdf
import pytest

from uzh_crawler.scrape.extract import convert, extract_docx, extract_html, extract_pdf, extract_xlsx
from uzh_crawler.scrape.fetch import sniff_kind

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "page.html"


def test_html_keeps_main_text_and_links_but_drops_chrome():
    extracted = extract_html(FIXTURE.read_text(encoding="utf-8"), "https://www.phil.uzh.ch/de/studium.html")
    assert extracted.title == "Studium an der Philosophischen Fakultät"
    assert "bachelor et de master" in extracted.markdown
    assert "Navigation secrète" not in extracted.markdown
    assert "ne-pas-extraire" not in extracted.markdown
    assert "Pied de page" not in extracted.markdown
    assert "Encart à ignorer" not in extracted.markdown
    assert "https://www.phil.uzh.ch/de/home.html" in extracted.links
    assert "https://www.uzh.ch/de.html" in extracted.links
    assert "https://www.phil.uzh.ch/de/studium/bachelor.html" in extracted.links
    assert "https://example.com/hors" in extracted.links
    assert "https://www.phil.uzh.ch/de/doc.pdf?id=7" in extracted.links
    assert all(not link.startswith("mailto:") for link in extracted.links)


def test_pdf_text_is_extracted():
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Reglement de la faculte de lettres")
    data = document.tobytes()
    document.close()
    assert "Reglement" in extract_pdf(data)


def test_word_file_becomes_markdown_with_headings_lists_and_tables():
    data = office_files.docx(
        office_files.paragraph("Antrag auf Anrechnung", style="Titel"),
        office_files.paragraph("Angaben zur Person", style="berschrift2"),
        office_files.paragraph("Bitte füllen Sie   alle Felder aus."),
        office_files.paragraph("Erster Punkt", listed=True),
        office_files.paragraph("Zweiter Punkt", listed=True),
        office_files.table([["Modul", "ECTS"], ["Mikro | Makro", "6"], ["<2>Total über beide Spalten"], ["^", "9"]]),
    )
    assert extract_docx(data) == "\n".join(
        [
            "# Antrag auf Anrechnung",
            "",
            "## Angaben zur Person",
            "",
            "Bitte füllen Sie alle Felder aus.",
            "",
            "- Erster Punkt",
            "- Zweiter Punkt",
            "",
            "| Modul | ECTS |",
            "| --- | --- |",
            "| Mikro \\| Makro | 6 |",
            "| Total über beide Spalten | Total über beide Spalten |",
            "| Total über beide Spalten | 9 |",
        ]
    )


def test_excel_file_becomes_one_table_per_sheet():
    data = office_files.xlsx(
        {
            "Noten": [["Modul", "ECTS", "Note"], ["Statistik", 6, 5.25], [None, None, None], ["Total", 6, None]],
            "Leer": [],
        }
    )
    assert extract_xlsx(data) == "\n".join(
        [
            "## Noten",
            "",
            "| Modul | ECTS | Note |",
            "| --- | --- | --- |",
            "| Statistik | 6 | 5.25 |",
            "| Total | 6 |  |",
        ]
    )


def test_unreadable_office_and_pdf_files_say_why():
    for reader, word in ((extract_docx, "Word"), (extract_xlsx, "Excel"), (extract_pdf, "PDF")):
        with pytest.raises(ValueError, match=word):
            reader(b"PK\x03\x04 not a real file")


def test_convert_names_what_it_cannot_read():
    result = convert("other", b"PK\x03\x04", "application/vnd.ms-powerpoint", "https://www.uzh.ch/a.ppt")
    assert result.markdown == ""
    assert result.error == "no text extractor for application/vnd.ms-powerpoint"


def test_the_media_type_decides_what_is_html():
    office = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    sheet = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    slides = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert sniff_kind("https://www.uzh.ch/a.docx", office, b"PK\x03\x04") == "docx"
    assert sniff_kind("https://www.uzh.ch/a", sheet, b"PK\x03\x04") == "xlsx"
    assert sniff_kind("https://www.uzh.ch/a.pptx", slides, b"PK\x03\x04") == "other"
    # A generic type: the zip signature and the suffix decide.
    assert sniff_kind("https://www.uzh.ch/a.xlsx", "application/octet-stream", b"PK\x03\x04") == "xlsx"
    assert sniff_kind("https://www.uzh.ch/a.docx", None, b"PK\x03\x04") == "docx"
    assert sniff_kind("https://www.uzh.ch/a.zip", None, b"PK\x03\x04") == "other"
    assert sniff_kind("https://www.uzh.ch/a", "text/html; charset=utf-8", b"") == "html"
    assert sniff_kind("https://www.uzh.ch/a", "application/pdf", b"") == "pdf"
    assert sniff_kind("https://www.uzh.ch/a", None, b"%PDF-1.7") == "pdf"
    assert sniff_kind("https://www.uzh.ch/a", "application/octet-stream", b"<!doctype html><html>") == "html"
    assert sniff_kind("https://www.uzh.ch/feed", "application/rss+xml", b"<rss>") == "other"
