"""Minimal Word and Excel files, written by hand so the tests need no Office library."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006"


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def paragraph(text: str, style: str | None = None, listed: bool = False) -> str:
    properties = ""
    if style:
        properties += f'<w:pStyle w:val="{style}"/>'
    if listed:
        properties += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
    if properties:
        properties = f"<w:pPr>{properties}</w:pPr>"
    return f"<w:p>{properties}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def table(rows: list[list[str]]) -> str:
    """Cells are texts. ``"<2>text"`` spans two columns; ``"^"`` continues the cell above."""
    out = ["<w:tbl>"]
    for row in rows:
        out.append("<w:tr>")
        for cell in row:
            properties = ""
            if cell.startswith("<2>"):
                cell, properties = cell[3:], '<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
            elif cell == "^":
                cell, properties = "", "<w:tcPr><w:vMerge/></w:tcPr>"
            out.append(f"<w:tc>{properties}{paragraph(cell)}</w:tc>")
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def docx(*blocks: str) -> bytes:
    styles = (
        f'<w:styles xmlns:w="{_W}">'
        '<w:style w:type="paragraph" w:styleId="Titel"><w:name w:val="Title"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="berschrift2"><w:name w:val="heading 2"/></w:style>'
        "</w:styles>"
    )
    return _zip(
        {
            "[Content_Types].xml": f'<Types xmlns="{_PKG}/content-types"/>',
            "word/document.xml": f'<w:document xmlns:w="{_W}"><w:body>{"".join(blocks)}</w:body></w:document>',
            "word/styles.xml": styles,
        }
    )


def xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    """One sheet per entry. A cell is a text, a number, or None."""
    parts = {
        "[Content_Types].xml": (
            f'<Types xmlns="{_PKG}/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType='
                '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for index in range(1, len(sheets) + 1)
            )
            + "</Types>"
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{_PKG}/relationships">'
            f'<Relationship Id="rId1" Type="{_R}/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
    }
    entries, relations = [], []
    for index, (name, rows) in enumerate(sheets.items(), start=1):
        entries.append(f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>')
        relations.append(
            f'<Relationship Id="rId{index}" Type="{_R}/worksheet" Target="worksheets/sheet{index}.xml"/>'
        )
        body = []
        for row_number, row in enumerate(rows, start=1):
            cells = []
            for column, value in enumerate(row):
                reference = f"{chr(ord('A') + column)}{row_number}"
                if value is None:
                    continue
                if isinstance(value, (int, float)):
                    cells.append(f'<c r="{reference}"><v>{value}</v></c>')
                else:
                    cells.append(f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
            body.append(f'<row r="{row_number}">{"".join(cells)}</row>')
        parts[f"xl/worksheets/sheet{index}.xml"] = (
            f'<worksheet xmlns="{_S}"><sheetData>{"".join(body)}</sheetData></worksheet>'
        )
    parts["xl/workbook.xml"] = (
        f'<workbook xmlns="{_S}" xmlns:r="{_R}"><sheets>{"".join(entries)}</sheets></workbook>'
    )
    parts["xl/_rels/workbook.xml.rels"] = (
        f'<Relationships xmlns="{_PKG}/relationships">{"".join(relations)}</Relationships>'
    )
    return _zip(parts)
