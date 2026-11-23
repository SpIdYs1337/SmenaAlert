"""Create a Word handover form from a recorded inventory operation."""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from .settings import text

ACT_OPERATIONS = ("receive", "assign", "transfer", "return")
ACT_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ACT_FIELDS = {
    "act_number": ("Номер акта", 64),
    "act_from": ("Передал", 160),
    "act_to": ("Принял", 160),
    "act_contents": ("Комплектация", 400),
    "act_condition": ("Внешнее и техническое состояние", 400),
}
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PACKAGE_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
BLANK = "______________________"


def act_fields(data):
    return {key: text(data, key, label, required=False, limit=limit)
            for key, (label, limit) in ACT_FIELDS.items()}


def paragraph(value, *, alignment="left", size=24, bold=False):
    value = escape(XML_INVALID.sub("", value))
    weight = "<w:b/>" if bold else ""
    return (
        f'<w:p><w:pPr><w:spacing w:after="160"/><w:jc w:val="{alignment}"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
        f'{weight}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
        f'<w:t xml:space="preserve">{value}</w:t></w:r></w:p>'
    )


def signature_cell(label, person):
    content = paragraph(label, bold=True) + paragraph(person) + paragraph("________________ / __________________")
    return f'<w:tc><w:tcPr><w:tcW w:w="4510" w:type="dxa"/></w:tcPr>{content}</w:tc>'


def make_act(operation, fields=None):
    fields = fields or {}
    action = operation["action"]
    receiver = operation["employee_name"] if action in ("assign", "transfer") else operation["actor_name"]
    sender = (operation["previous_holder"] if action in ("transfer", "return")
              else operation["actor_name"] if action == "assign" else "")
    receiver = fields.get("act_to") or receiver or BLANK
    sender = fields.get("act_from") or sender or BLANK
    number = fields.get("act_number") or str(operation["id"])
    day = date.fromisoformat(operation["day"])
    signed_on = f"«{day.day:02d}» {MONTHS[day.month - 1]} {day.year} г."
    equipment = (f'Оборудование — {operation["name"]}. Инвентарный номер — {operation["tag"]}. '
                 f'Серийный номер — {operation["serial"] or BLANK}.')
    contents = fields.get("act_contents") or BLANK
    condition = fields.get("act_condition") or BLANK
    place = paragraph(f'Место передачи — {operation["location"]}.') if operation["location"] else ""
    signatures = (
        '<w:tbl><w:tblPr><w:tblW w:w="9020" w:type="dxa"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="4510"/><w:gridCol w:w="4510"/></w:tblGrid><w:tr>'
        + signature_cell("Передал", sender) + signature_cell("Принял", receiver)
        + '</w:tr></w:tbl>'
    )
    document = (
        f'<w:document xmlns:w="{WORD}"><w:body>'
        + paragraph(f"АКТ ПРИЁМА-ПЕРЕДАЧИ ОБОРУДОВАНИЯ № {number}", alignment="center", size=28, bold=True)
        + paragraph(signed_on, alignment="center")
        + paragraph(equipment)
        + place
        + paragraph(f"Комплектация — {contents}.")
        + paragraph(f"Внешнее и техническое состояние — {condition}.")
        + paragraph("")
        + signatures
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'
        '</w:sectPr></w:body></w:document>'
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", (
            f'<Types xmlns="{PACKAGE_TYPES}"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Override PartName="/word/document.xml" ContentType="{ACT_MIME}.main+xml"/>'
            '</Types>'
        ))
        archive.writestr("_rels/.rels", (
            f'<Relationships xmlns="{PACKAGE_RELATIONSHIPS}"><Relationship Id="rId1" '
            f'Type="{OFFICE_DOCUMENT}" Target="word/document.xml"/></Relationships>'
        ))
        archive.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + document)
    return output.getvalue()
