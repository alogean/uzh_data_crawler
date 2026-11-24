from __future__ import annotations

from dataclasses import dataclass, field

from uzh_crawler.crawl.links import links_of
from uzh_crawler.scrape.fetch import decode_text, media_type
from uzh_crawler.text import normalize_markdown

MIN_USEFUL_CHARS = 200
_DROP_SELECTOR = "script, style, nav, footer, header, aside, noscript, iframe, svg"
_markdown_options = None


@dataclass
class Extracted:
    """What one document gave: its Markdown, and for HTML its title and links."""

    kind: str
    markdown: str = ""
    title: str | None = None
    links: list[str] = field(default_factory=list)
    error: str | None = None


def convert(kind: str, body: bytes, content_type: str | None, base_url: str) -> Extracted:
    """Turn the bytes of one document into Markdown. ``kind`` comes from ``sniff_kind``.

    This is the only work of a scrape that needs the processor, so it is the
    function the scraper hands to its worker processes.
    """
    readers = {"pdf": extract_pdf, "docx": extract_docx, "xlsx": extract_xlsx}
    if kind in readers:
        try:
            return Extracted(kind=kind, markdown=readers[kind](body))
        except ValueError as exc:
            return Extracted(kind="failed", error=str(exc))
    if kind == "text":
        return Extracted(kind="text", markdown=decode_text(body, content_type))
    if kind != "html":
        # The file is kept under files/, but nothing here can read its text.
        return Extracted(kind=kind, error=f"no text extractor for {media_type(content_type) or 'unknown type'}")
    html = extract_html(decode_text(body, content_type), base_url)
    return Extracted(kind="html", markdown=html.markdown, title=html.title, links=html.links)


@dataclass(frozen=True)
class ExtractedHtml:
    title: str | None
    markdown: str
    links: list[str]


def extract_html(html: str, page_url: str) -> ExtractedHtml:
    """Turn a fetched document into Markdown plus every absolute a[href].

    Navigation is kept for discovery and removed from the text sent to the LLM.
    """
    import html_to_markdown
    from selectolax.lexbor import LexborHTMLParser

    global _markdown_options
    if _markdown_options is None:
        _markdown_options = html_to_markdown.ConversionOptions(
            bullets="-", compact_tables=True, extract_metadata=False, hidden_content="all"
        )
    tree = LexborHTMLParser(html)
    title, links = links_of(tree, page_url)
    root = tree.css_first("main") or tree.css_first("article")
    if root is None:
        root = tree.body or tree.root
    if root is None:
        return ExtractedHtml(title=title, markdown="", links=links)
    for node in root.css(_DROP_SELECTOR):
        node.decompose()
    markdown = normalize_markdown(html_to_markdown.convert(root.html or "", _markdown_options).content or "")
    return ExtractedHtml(title=title, markdown=markdown, links=links)


def extract_pdf(data: bytes) -> str:
    """The text of a PDF, page after page, read with pdfium."""
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(data)
    except Exception as exc:
        raise ValueError(f"PDF could not be opened: {exc}") from exc
    parts: list[str] = []
    try:
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            textpage.close()
            page.close()
            if text.strip():
                parts.append(text)
    finally:
        document.close()
    return normalize_markdown("\n\n".join(parts))


def extract_docx(data: bytes) -> str:
    """A Word document as Markdown: headings, paragraphs, lists and tables, in document order.

    A .docx file is a zip archive of XML parts. Only two are read: the body
    (``word/document.xml``) and the style names (``word/styles.xml``).
    """
    import io
    import zipfile

    from lxml import etree

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            body_xml = archive.read(_docx_body_name(archive))
            try:
                styles_xml = archive.read("word/styles.xml")
            except KeyError:
                styles_xml = None
        body = etree.fromstring(body_xml).find(f"{_W}body")
        styles = _docx_style_names(etree.fromstring(styles_xml)) if styles_xml else {}
    except Exception as exc:
        raise ValueError(f"Word file could not be opened: {exc}") from exc
    parts: list[str] = []
    if body is not None:
        _docx_blocks(body, styles, parts)
    return normalize_markdown(_join_blocks(parts))


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W_P, _W_TBL, _W_TR, _W_TC, _W_R, _W_T = (f"{_W}{name}" for name in ("p", "tbl", "tr", "tc", "r", "t"))
_W_VAL = f"{_W}val"
# Elements that only wrap other text: a link, a tracked insertion, a field.
# Content controls (w:sdt) are not followed: they hold the placeholders of form fields.
_W_WRAPPERS = frozenset(f"{_W}{name}" for name in ("hyperlink", "ins", "smartTag", "fldSimple"))
_W_BREAKS = {f"{_W}tab": "\t", f"{_W}br": "\n", f"{_W}cr": "\n"}


def _docx_body_name(archive) -> str:
    """Path of the main part. It is ``word/document.xml`` unless the package says otherwise."""
    names = archive.namelist()
    if "word/document.xml" in names:
        return "word/document.xml"
    from lxml import etree

    for relation in etree.fromstring(archive.read("_rels/.rels")):
        if relation.get("Type", "").endswith("/officeDocument"):
            return relation.get("Target", "").lstrip("/")
    raise KeyError("no main document part")


def _docx_style_names(root) -> dict[str, str]:
    names: dict[str, str] = {}
    for style in root.iterfind(f"{_W}style"):
        name = style.find(f"{_W}name")
        if name is not None:
            names[style.get(f"{_W}styleId", "")] = name.get(_W_VAL, "")
    return names


def _docx_blocks(parent, styles: dict[str, str], parts: list[str]) -> None:
    for child in parent:
        tag = child.tag
        if tag == _W_P:
            text = " ".join(_docx_text(child).split())
            if text:
                parts.append(_styled(text, *_docx_paragraph_style(child, styles)))
        elif tag == _W_TBL:
            table = markdown_table(_docx_rows(child))
            if table:
                parts.append(table)
        elif tag in _W_WRAPPERS:
            _docx_blocks(child, styles, parts)


def _docx_text(node) -> str:
    """The text of a paragraph: its runs, and the runs inside links and tracked insertions."""
    out: list[str] = []
    for child in node:
        tag = child.tag
        if tag == _W_R:
            for piece in child:
                if piece.tag == _W_T:
                    out.append(piece.text or "")
                elif piece.tag in _W_BREAKS:
                    out.append(_W_BREAKS[piece.tag])
        elif tag in _W_WRAPPERS:
            out.append(_docx_text(child))
    return "".join(out)


def _docx_paragraph_style(paragraph, styles: dict[str, str]) -> tuple[str, bool]:
    """Style name of a paragraph, and whether Word numbers or bullets it."""
    properties = paragraph.find(f"{_W}pPr")
    if properties is None:
        return "", False
    style = properties.find(f"{_W}pStyle")
    name = styles.get(style.get(_W_VAL, ""), "") if style is not None else ""
    return name, properties.find(f"{_W}numPr") is not None


def _docx_rows(table) -> list[list[str]]:
    """Cell texts of a table. A merged cell repeats its text in every column and row it covers."""
    rows: list[list[str]] = []
    for row in _docx_children(table, _W_TR):
        cells: list[str] = []
        for cell in _docx_children(row, _W_TC):
            text = " ".join(
                " ".join(_docx_text(paragraph) for paragraph in _docx_children(cell, _W_P)).split()
            )
            span = 1
            properties = cell.find(f"{_W}tcPr")
            if properties is not None:
                grid_span = properties.find(f"{_W}gridSpan")
                if grid_span is not None and grid_span.get(_W_VAL, "").isdigit():
                    span = max(int(grid_span.get(_W_VAL)), 1)
                merge = properties.find(f"{_W}vMerge")
                if merge is not None and merge.get(_W_VAL, "continue") != "restart" and rows:
                    column = len(cells)
                    text = rows[-1][column] if column < len(rows[-1]) else text
            cells.extend([text] * span)
        rows.append(cells)
    return rows


def _docx_children(parent, wanted: str):
    """Direct children with this tag, looking through wrapper elements."""
    for child in parent:
        if child.tag == wanted:
            yield child
        elif child.tag in _W_WRAPPERS:
            yield from _docx_children(child, wanted)


def extract_xlsx(data: bytes) -> str:
    """An Excel workbook as Markdown: one section and one table per sheet, formulas as their values.

    calamine reads the values the file stores, so a formula gives its last
    computed result. A cell that holds an error (``#DIV/0!``) is left empty.
    """
    import io

    import python_calamine

    try:
        workbook = python_calamine.CalamineWorkbook.from_filelike(io.BytesIO(data))
        sheets = [
            (name, workbook.get_sheet_by_name(name).to_python(skip_empty_area=False))
            for name in workbook.sheet_names
        ]
    except Exception as exc:
        raise ValueError(f"Excel file could not be opened: {exc}") from exc
    parts: list[str] = []
    for name, values in sheets:
        table = markdown_table([[cell_text(value) for value in row] for row in values])
        if table:
            parts.append(f"## {name}\n\n{table}")
    return normalize_markdown("\n\n".join(parts))


def cell_text(value: object) -> str:
    """One spreadsheet value as text: whole numbers without ``.0``, dates in ISO form."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.10g}"
    if hasattr(value, "isoformat"):
        text = value.isoformat()
        return text[:10] if text.endswith("T00:00:00") else text.replace("T", " ")
    return " ".join(str(value).split())


def markdown_table(rows: list[list[str]]) -> str:
    """A pipe table from rows of cell texts. Empty rows and empty trailing columns are dropped."""
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""
    width = max(max((i + 1 for i, cell in enumerate(row) if cell), default=0) for row in rows)
    lines: list[str] = []
    for index, row in enumerate(rows):
        cells = [cell.replace("|", "\\|") for cell in row[:width]]
        cells += [""] * (width - len(cells))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("|" + " --- |" * width)
    return "\n".join(lines)


def _styled(text: str, style: str, listed: bool = False) -> str:
    name = style.lower()
    if name == "title":
        return f"# {text}"
    for prefix in ("heading ", "überschrift ", "titre "):
        if name.startswith(prefix) and name[len(prefix):].strip().isdigit():
            return "#" * min(int(name[len(prefix):]), 6) + f" {text}"
    if listed or name.startswith(("list", "aufzählung")):
        return f"- {text}"
    return text


def _join_blocks(parts: list[str]) -> str:
    """Blank line between blocks, except between two consecutive list items."""
    out: list[str] = []
    previous = ""
    for part in parts:
        if out:
            out.append("\n" if part.startswith("- ") and previous.startswith("- ") else "\n\n")
        out.append(part)
        previous = part
    return "".join(out)
