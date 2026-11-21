"""Editable knowledge tree and atomic document imports."""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import nh3

from .settings import InputError, identifier, number, text

DOCUMENT_LIMIT = 5 * 1024 * 1024
MAX_TEXT = 180_000
KNOWLEDGE_JSON_LIMIT = 2 * 1024 * 1024
MAX_MEDIA = 12
MAX_MEDIA_BYTES = 64 * 1024 * 1024
MEDIA_ID = re.compile(r"[0-9a-f]{16}")
BLOCKS = {"p", "div", "h2", "h3", "h4", "blockquote", "ul", "ol", "li", "figure", "pre", "br", "hr"}
EDITOR_TAGS = BLOCKS | {"strong", "b", "em", "i", "u", "s", "strike", "code", "a"}
EDITOR_FORMAT = {
    tag: {"data-align": {"left", "center", "right"}}
    for tag in BLOCKS - {"br", "hr"}
}
EDITOR_FORMAT["figure"]["data-width"] = {"25", "50", "75", "100"}
MAX_NODES = 300
MAX_DEPTH = 24
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OUTLINE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){1,7}[.)]?|\d{1,3}[.)])\s+(.+)$")
SUBTREE = """
    WITH RECURSIVE subtree(id,depth) AS (
        SELECT id,1 FROM articles WHERE id=?
        UNION ALL
        SELECT a.id,subtree.depth+1 FROM articles a JOIN subtree ON a.parent=subtree.id
    )
"""


def numbered_item(line):
    match = OUTLINE.fullmatch(line.strip())
    if match:
        return match[1].rstrip(".)").count(".") + 1, match[2]
    return None


def body_text(value):
    if not isinstance(value, str) or len(value) > MAX_TEXT or any(ord(c) < 32 and c not in "\r\n\t" for c in value):
        raise InputError("Проверьте текст статьи. Максимум 180 000 символов.")
    return value.replace("\r\n", "\n").replace("\r", "\n")


class ArticleMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.lines = []
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "figure":
            item = dict(attrs).get("data-media", "")
            if not MEDIA_ID.fullmatch(item or ""):
                raise InputError("В статье есть вложение без файла.")
            self.references.append(item)
        if tag in {"br", "hr"}:
            self.lines.append("\n")

    def handle_endtag(self, tag):
        if tag in BLOCKS and self.lines and self.lines[-1] != "\n":
            self.lines.append("\n")

    def handle_data(self, data):
        self.lines.append(data)


def clean_article(value):
    if not isinstance(value, str) or len(value) > MAX_TEXT * 2:
        raise InputError("Текст статьи слишком большой.")
    html = nh3.clean(
        value, tags=EDITOR_TAGS, attributes={"a": {"href"}, "figure": {"data-media"}},
        tag_attribute_values=EDITOR_FORMAT,
        clean_content_tags={"script", "style", "iframe", "object", "svg", "math", "template", "audio", "video"},
        url_schemes={"http", "https", "mailto"},
    )
    article = ArticleMarkup(html)
    if len(article.references) > MAX_MEDIA or len(set(article.references)) != len(article.references):
        raise InputError("В статье может быть до 12 вложений.")
    return html, body_text("".join(article.lines).strip()), set(article.references)


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

    def tree(self, *, archived=False):
        with self.db.connection() as conn:
            rows = conn.execute("""
                SELECT a.id,CASE WHEN a.archive_root IS NULL OR p.archive_root IS NOT NULL THEN a.parent END AS parent,
                       a.title,a.kind
                FROM articles a LEFT JOIN articles p ON p.id=a.parent
                WHERE (a.archive_root IS NOT NULL)=? ORDER BY a.rowid
            """, (archived,))
            return [dict(row) for row in rows]

    def get(self, item):
        with self.db.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("""
                SELECT a.*,d.name AS filename,p.archive_root IS NOT NULL AS parent_archived
                FROM articles a LEFT JOIN documents d ON d.article=a.id LEFT JOIN articles p ON p.id=a.parent
                WHERE a.id=?
            """, (item,)).fetchone()
            if not row:
                return None
            media = conn.execute("SELECT id,name,mime,seconds FROM article_media WHERE article=? ORDER BY rowid", (item,))
            return {**dict(row), "media": [dict(file) for file in media]}

    @staticmethod
    def check_parent(conn, parent, item=None, height=1):
        depth = 0
        while parent is not None:
            if parent == item:
                raise InputError("Нельзя перенести пункт внутрь его подпункта.")
            row = conn.execute("SELECT parent,archive_root FROM articles WHERE id=?", (parent,)).fetchone()
            if not row:
                raise InputError("Выберите существующий родительский пункт.")
            if row["archive_root"]:
                raise InputError("Сначала восстановите родительский пункт из архива.")
            parent = row["parent"]
            depth += 1
        if depth + height > MAX_DEPTH:
            raise InputError(f"Допускается до {MAX_DEPTH} уровней вложенности.")

    def save(self, data, now, author, item=None, uploads=None):
        version = number(data.get("version"), "Версия пункта", 1, 2**63-1) if item else None
        title = text(data, "title", "Название", limit=240)
        uploads = uploads or {}
        markup = clean_article(data["html"]) if "html" in data else None
        parent = None
        if data.get("parent") is not None:
            parent = text(data, "parent", "Родительский пункт", required=False) or None
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT kind,body,html,version,archive_root FROM articles WHERE id=?",
                                    (item,)).fetchone()
            if item and (not previous or previous["version"] != version):
                raise InputError("Пункт уже изменился. Откройте свежую версию перед сохранением.")
            if previous and previous["archive_root"]:
                raise InputError("Сначала восстановите пункт из архива.")
            kind = data.get("kind", previous["kind"] if previous else "article")
            if kind not in ("section", "article") or (previous and kind != previous["kind"]):
                raise InputError("Выберите раздел или статью при создании.")
            height = 1
            if item:
                height = conn.execute(SUBTREE + "SELECT max(depth) FROM subtree", (item,)).fetchone()[0]
            self.check_parent(conn, parent, item, height)

            references = None
            if markup is not None:
                html, body, references = markup
            elif previous and "body" not in data:
                html, body = previous["html"], previous["body"]
            else:
                html, body, references = None, body_text(data.get("body", "")), set()
            if kind == "section" and (body or html or uploads):
                raise InputError("Для текста и медиа создайте статью в разделе.")

            if item:
                conn.execute("UPDATE articles SET parent=?,title=?,body=?,html=?,edited_at=?,edited_by=?,version=version+1 "
                             "WHERE id=?", (parent, title, body, html, now, author, item))
            else:
                item = identifier()
                conn.execute("INSERT INTO articles(id,parent,title,body,html,kind,edited_at,edited_by) "
                             "VALUES (?,?,?,?,?,?,?,?)", (item, parent, title, body, html, kind, now, author))
            if references is not None or uploads:
                self.save_media(conn, item, references or set(), uploads)
        return item

    @staticmethod
    def archive_record(conn, item, data):
        version = number(data.get("version"), "Версия пункта", 1, 2**63-1)
        row = conn.execute("""
            SELECT a.archive_root,a.version,p.archive_root IS NOT NULL AS parent_archived
            FROM articles a LEFT JOIN articles p ON p.id=a.parent WHERE a.id=?
        """, (item,)).fetchone()
        if not row or row["version"] != version:
            raise InputError("Пункт уже изменился. Откройте свежую версию.")
        return row

    def set_archived(self, item, data):
        archived = data.get("archived")
        if type(archived) is not bool:
            raise InputError("Выберите архивирование или восстановление.")
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.archive_record(conn, item, data)
            if archived:
                if row["archive_root"]:
                    raise InputError("Пункт уже в архиве.")
                conn.execute(SUBTREE + """
                    UPDATE articles SET archive_root=?,version=version+1
                    WHERE archive_root IS NULL AND id IN (SELECT id FROM subtree)
                """, (item, item))
            else:
                if row["parent_archived"]:
                    raise InputError("Сначала восстановите родительский пункт из архива.")
                if row["archive_root"] != item:
                    raise InputError("Пункт уже в базе знаний.")
                conn.execute("UPDATE articles SET archive_root=NULL,version=version+1 WHERE archive_root=?", (item,))

    def delete(self, item, data):
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self.archive_record(conn, item, data)["archive_root"]:
                raise InputError("Сначала переместите пункт в архив.")
            # Delete children first to satisfy the parent reference.
            rows = conn.execute(SUBTREE + "SELECT id FROM subtree ORDER BY depth DESC", (item,)).fetchall()
            conn.executemany("DELETE FROM articles WHERE id=?", rows)

    @staticmethod
    def save_media(conn, item, references, uploads):
        previous = dict(conn.execute("SELECT id,length(content) FROM article_media WHERE article=?", (item,)))
        if references - previous.keys() != uploads.keys():
            raise InputError("Часть вложений недоступна. Добавьте файлы в статью заново.")
        size = sum(previous[key] for key in references & previous.keys()) + sum(len(file.content) for file in uploads.values())
        if size > MAX_MEDIA_BYTES:
            raise InputError("Общий размер вложений в статье должен быть не более 64 МБ.")
        conn.executemany("DELETE FROM article_media WHERE article=? AND id=?",
                         ((item, key) for key in previous.keys() - references))
        conn.executemany("INSERT INTO article_media (id,article,name,mime,seconds,content) VALUES (?,?,?,?,?,?)",
                         ((key, item, f.name, f.mime, f.seconds, f.content) for key, f in uploads.items()))

    def media(self, item, media):
        with self.db.connection() as conn:
            return conn.execute("SELECT name,mime,content FROM article_media WHERE article=? AND id=?",
                                (item, media)).fetchone()

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
