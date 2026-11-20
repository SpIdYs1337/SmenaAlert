"""Editable knowledge tree and atomic document imports."""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .settings import InputError, identifier, number, text

DOCUMENT_LIMIT = 5 * 1024 * 1024
MAX_TEXT = 180_000
MAX_NODES = 300
MAX_DEPTH = 24
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OUTLINE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){1,7}[.)]?|\d{1,3}[.)])\s+(.+)$")


def numbered_item(line):
    match = OUTLINE.fullmatch(line.strip())
    if match:
        return match[1].rstrip(".)").count(".") + 1, match[2]
    return None


def body_text(value):
    if not isinstance(value, str) or len(value) > MAX_TEXT or any(ord(c) < 32 and c not in "\r\n\t" for c in value):
        raise InputError("Проверьте текст статьи. Максимум 180 000 символов.")
    return value.replace("\r\n", "\n").replace("\r", "\n")


class DocumentXML(ET.TreeBuilder):
    def doctype(self, *_args):
        raise InputError("Документ содержит неподдерживаемое объявление XML.")


def word_xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > 10 * 1024 * 1024:
        raise InputError("Текстовая часть DOCX слишком велика.")
    return ET.fromstring(archive.read(info), parser=ET.XMLParser(target=DocumentXML()))


def word_lines(raw):
    def paragraph_text(paragraph):
        pieces = []
        for element in paragraph.iter():
            if element.tag == WORD + "t":
                pieces.append(element.text or "")
            elif element.tag == WORD + "tab":
                pieces.append("\t")
            elif element.tag == WORD + "br":
                pieces.append("\n")
        return "".join(pieces)

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            document = word_xml(archive, "word/document.xml")
            styles = {}
            if "word/styles.xml" in archive.namelist():
                for style in word_xml(archive, "word/styles.xml").iter(WORD + "style"):
                    level = style.find(f"{WORD}pPr/{WORD}outlineLvl")
                    if level is not None:
                        styles[style.get(WORD + "styleId")] = level.get(WORD + "val")
            heading_depth = 0
            for paragraph in document.iter(WORD + "p"):
                value = paragraph_text(paragraph)
                style = paragraph.find(f"{WORD}pPr/{WORD}pStyle")
                direct = paragraph.find(f"{WORD}pPr/{WORD}outlineLvl")
                name = style.get(WORD + "val", "") if style is not None else ""
                level = direct.get(WORD + "val") if direct is not None else styles.get(name)
                match = re.fullmatch(r"(?:Heading|Заголовок)\s*([1-9])", name, re.I)
                heading = int(match[1]) if match else 0
                if level in tuple("012345678"):
                    heading = int(level) + 1
                if heading:
                    heading_depth = heading
                    yield heading, value
                    continue
                list_style = paragraph.find(f"{WORD}pPr/{WORD}numPr")
                if list_style is not None:
                    number_id = list_style.find(WORD + "numId")
                    indent = list_style.find(WORD + "ilvl")
                    depth = indent.get(WORD + "val", "0") if indent is not None else "0"
                    if number_id is not None and number_id.get(WORD + "val") != "0" and depth in tuple("012345678"):
                        yield heading_depth + 1 + int(depth), value
                        continue
                numbered = numbered_item(value)
                if numbered:
                    yield heading_depth + numbered[0], numbered[1]
                else:
                    yield 0, value
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError, NotImplementedError, OSError):
        raise InputError("Не удалось прочитать DOCX. Сохраните документ в формате Word DOCX заново.") from None


def plain_lines(raw, markdown):
    try:
        contents = body_text(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise InputError("Сохраните текстовый файл в кодировке UTF-8.") from None
    fence = ""
    for line in contents.split("\n"):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line) if markdown else None
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence):
                fence = ""
            yield 0, line
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line) if markdown and not fence else None
        numbered = numbered_item(line) if not fence else None
        if heading:
            yield len(heading[1]), heading[2]
        elif numbered:
            yield numbered
        else:
            yield 0, line


def pdf_lines(raw):
    if not raw.startswith(b"%PDF-"):
        raise InputError("Не удалось распознать PDF.")
    # Bound the external reader independently of the web worker and of compressed PDF size.
    with tempfile.TemporaryFile() as extracted:
        try:
            result = subprocess.run(
                ["prlimit", "--as=402653184", "--cpu=8", "--fsize=1048576", "--",
                 "pdftotext", "-enc", "UTF-8", "-nopgbrk", "-", "-"],
                input=raw, stdout=extracted, stderr=subprocess.DEVNULL, timeout=12,
            )
        except FileNotFoundError:
            raise InputError("Для импорта PDF установите poppler-utils и util-linux на сервере.") from None
        except subprocess.TimeoutExpired:
            raise InputError("Чтение PDF заняло слишком много времени. Разделите файл на части.") from None
        if result.returncode:
            raise InputError("PDF защищён, повреждён или слишком велик для импорта.")
        extracted.seek(0)
        return plain_lines(extracted.read(), False)


def parse_document(filename, raw):
    name = text({"name": (filename or "").replace("\\", "/").split("/")[-1]}, "name", "Имя файла", limit=180)
    if not raw or len(raw) > DOCUMENT_LIMIT:
        raise InputError("Выберите непустой файл размером до 5 МБ.")
    extension = Path(name).suffix.lower()
    root_title = Path(name).stem.strip() or name
    entries = [{"title": root_title, "lines": [], "parent": None}]
    if extension == ".pdf":
        lines = pdf_lines(raw)
    elif extension == ".docx":
        lines = word_lines(raw)
    elif extension in (".txt", ".md"):
        lines = plain_lines(raw, extension == ".md")
    else:
        raise InputError("Выберите DOCX, Markdown, TXT или PDF.")
    stack = [(0, 0)]
    length = 0
    for level, line in lines:
        length += len(line) + 1
        if length > MAX_TEXT:
            raise InputError("Текст документа превышает 180 000 символов.")
        if level and line.strip():
            title = " ".join(body_text(line).split())
            body = []
            if len(title) > 240:
                body.append(line)
                title = title[:239].rstrip() + "…"
            while stack[-1][0] >= level:
                stack.pop()
            if len(entries) >= MAX_NODES:
                raise InputError("За один импорт можно создать до 300 пунктов.")
            entries.append({"title": title, "lines": body, "parent": stack[-1][1]})
            stack.append((level, len(entries) - 1))
        else:
            entries[stack[-1][1]]["lines"].append(line)
    return name, [(entry["title"], body_text("\n".join(entry["lines"]).strip()), entry["parent"]) for entry in entries]


class Knowledge:
    def __init__(self, db):
        self.db = db

    def tree(self):
        with self.db.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT id,parent,title FROM articles ORDER BY rowid")]

    def get(self, item):
        with self.db.connection() as conn:
            row = conn.execute("SELECT a.*,d.name AS filename FROM articles a LEFT JOIN documents d ON d.article=a.id "
                               "WHERE a.id=?", (item,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def check_parent(conn, parent, item=None, height=1):
        depth = 0
        while parent is not None:
            if parent == item:
                raise InputError("Нельзя перенести пункт внутрь его подпункта.")
            row = conn.execute("SELECT parent FROM articles WHERE id=?", (parent,)).fetchone()
            if not row:
                raise InputError("Выберите существующий родительский пункт.")
            parent = row[0]
            depth += 1
        if depth + height > MAX_DEPTH:
            raise InputError(f"Допускается до {MAX_DEPTH} уровней вложенности.")

    def save(self, data, now, author, item=None):
        version = number(data.get("version"), "Версия пункта", 1, 2**63-1) if item else None
        title = text(data, "title", "Название пункта", limit=240)
        body = body_text(data.get("body", ""))
        parent = None
        if data.get("parent") is not None:
            parent = text(data, "parent", "Родительский пункт", required=False) or None
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            height = 1
            if item:
                height = conn.execute("""
                    WITH RECURSIVE tree(id,depth) AS (SELECT ?,1 UNION ALL SELECT a.id,depth+1
                        FROM articles a JOIN tree ON a.parent=tree.id) SELECT max(depth) FROM tree
                """, (item,)).fetchone()[0]
            self.check_parent(conn, parent, item, height)
            if item:
                updated = conn.execute("UPDATE articles SET parent=?,title=?,body=?,edited_at=?,edited_by=?,version=version+1 "
                                       "WHERE id=? AND version=?", (parent, title, body, now, author, item, version))
                if not updated.rowcount:
                    raise InputError("Пункт уже изменился. Откройте свежую версию перед сохранением.")
            else:
                item = identifier()
                conn.execute("INSERT INTO articles(id,parent,title,body,edited_at,edited_by) VALUES (?,?,?,?,?,?)",
                             (item, parent, title, body, now, author))
        return item

    def import_file(self, filename, raw, parent, now, author):
        filename, entries = parse_document(filename, raw)
        ids = [identifier() for _ in entries]
        depths = []
        for _, _, ancestor in entries:
            depths.append(1 if ancestor is None else depths[ancestor] + 1)
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.check_parent(conn, parent, height=max(depths))
            for item, (title, body, ancestor) in zip(ids, entries):
                conn.execute("INSERT INTO articles(id,parent,title,body,edited_at,edited_by) VALUES (?,?,?,?,?,?)",
                             (item, parent if ancestor is None else ids[ancestor], title, body, now, author))
            conn.execute("INSERT INTO documents VALUES (?,?,?)", (ids[0], filename, raw))
        return ids[0], len(ids)

    def original(self, item):
        with self.db.connection() as conn:
            return conn.execute("SELECT name,content FROM documents WHERE article=?", (item,)).fetchone()
