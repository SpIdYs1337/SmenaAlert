import io
import re
import json
import shutil
import subprocess
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

try:
    import flask
except ImportError as error:
    raise unittest.SkipTest("Install requirements-web.txt to test the panel.") from error

from PIL import Image

from shiftbot.config import MSK
from shiftbot.web.app import create_app
from shiftbot.web.knowledge import DOCUMENT_LIMIT
from shiftbot.web.service import Service
from shiftbot.web.settings import Vault
from shiftbot.web.storage import Database


def csrf(response):
    match = re.search(r'name="csrf" (?:content|value)="([^"]+)"', response.get_data(as_text=True))
    assert match, response.status_code
    return match[1]


def word_document():
    document = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Приёмка</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Номер</w:t></w:r></w:p>
        <w:p><w:r><w:t>Сверьте маркировку.</w:t></w:r></w:p></w:body></w:document>"""
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("word/document.xml", document.encode())
    return result.getvalue()


def pdf_document():
    stream = b"BT /F1 12 Tf 20 60 Td (Knowledge) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 120 90] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"]
    doc = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, value in enumerate(objects, 1):
        offsets.append(len(doc))
        doc.extend(b"%d 0 obj\n" % i + value + b"\nendobj\n")
    offset = len(doc)
    doc.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for start in offsets[1:]:
        doc.extend(b"%010d 00000 n \n" % start)
    doc.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % offset)
    return bytes(doc)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        self.db = Database(base / "panel.sqlite3")
        now = datetime(2026, 11, 20, 22, 3, tzinfo=MSK)
        service = Service(self.db, Vault(base / ".panel-key"), now=lambda: now)
        self.app = create_app(service, admin="admin", password="installation-owner-secret")
        self.owner = self.app.test_client()
        self.login(self.owner, "admin", "installation-owner-secret")
        self.token = csrf(self.owner.get("/"))

    def login(self, client, login, password):
        token = csrf(client.get("/login"))
        return client.post("/login", data={"csrf": token, "login": login, "password": password})

    def write(self, path, data, *, client=None, token=None, method="POST", expect=201):
        response = (client or self.owner).open("/api/work/" + path, method=method, json=data,
                                               headers={"X-CSRF-Token": token or self.token})
        self.assertEqual(response.status_code, expect, response.get_data(as_text=True))
        return response.get_json()

    def staff(self, login="engineer", grants=("knowledge.read",)):
        return self.write("employees", {"name": login, "login": login, "password": "temporary-code-123",
                                        "grants": list(grants)})["id"]

    def activate(self, login):
        client = self.app.test_client()
        self.assertEqual(self.login(client, login, "temporary-code-123").status_code, 302)
        result = client.get("/", follow_redirects=True)
        self.assertIn("Первый вход", result.get_data(as_text=True))
        self.assertEqual(client.get("/api/work/knowledge").status_code, 403)
        result = client.post("/account/password", data={"csrf": csrf(result), "password": "temporary-code-123",
                                                        "new_password": "personal-code-456", "confirm_password": "personal-code-456"})
        self.assertEqual(result.status_code, 302)
        return client, csrf(client.get("/"))

    def upload(self, name, raw, expect=201, parent=""):
        response = self.owner.post("/api/work/knowledge/import", headers={"X-CSRF-Token": self.token},
                                   data={"file": (io.BytesIO(raw), name), "parent": parent})
        response.request.environ["wsgi.input"].close()
        self.assertEqual(response.status_code, expect, response.get_data(as_text=True))
        return response.get_json()

    def test_profile_only_shows_the_current_employees_permitted_equipment(self):
        first = self.staff("first", ["inventory.read"])
        second = self.staff("second", ["inventory.read"])
        reader = self.staff("reader", ["knowledge.read"])
        for employee in (first, second, reader):
            item = self.write("inventory", {"name": "Ноутбук", "tag": employee, "day": "2026-11-20"})["id"]
            path = "/api/work/inventory/" + item
            version = self.owner.get(path).json["version"]
            self.write("inventory/" + item + "/events", {"action": "assign", "employee": employee,
                       "day": "2026-11-20", "version": version, "service_months": 36})
        user, _ = self.activate("first")
        profile = user.get("/api/work/profile", query_string={"employee": second}).json
        self.assertEqual(profile["person"]["id"], first)
        self.assertNotIn("password_hash", profile["person"])
        self.assertEqual([item["tag"] for item in profile["assigned"]], [first])
        restricted, _ = self.activate("reader")
        self.assertEqual(restricted.get("/api/work/profile").json["assigned"], [])
        self.assertEqual(self.app.test_client().get("/api/work/profile").status_code, 401)

    def test_first_login_module_permissions_reset_and_deactivation(self):
        employee = self.staff()
        user, token = self.activate("engineer")
        self.assertEqual(user.get("/api/work/knowledge").status_code, 200)
        self.assertEqual(user.get("/api/work/inventory").status_code, 403)
        self.assertEqual(user.get("/api/work/employees").status_code, 403)
        self.assertEqual(user.get("/api/statistics").status_code, 403)
        self.assertEqual(set(user.get("/api/dashboard").json), {"now"})
        self.write("knowledge", {"title": "Запись"}, client=user, token=token, expect=403)
        edit = self.owner.get("/api/work/employees").json["items"][0]
        self.assertFalse(edit["must_change"])
        self.assertNotIn("password_hash", edit)
        self.write("employees/" + employee, {**edit, "password": "new-temporary-code"}, method="PUT", expect=200)
        self.assertEqual(user.get("/api/work/knowledge").status_code, 401)
        self.assertEqual(self.login(self.app.test_client(), "engineer", "personal-code-456").status_code, 401)
        reentry = self.app.test_client()
        self.assertEqual(self.login(reentry, "ENGINEER", "new-temporary-code").status_code, 302)
        self.assertIn("Первый вход", reentry.get("/", follow_redirects=True).get_data(as_text=True))
        edit = self.owner.get("/api/work/employees").json["items"][0]
        self.write("employees/" + employee, {**edit, "active":False}, method="PUT", expect=200)
        self.assertEqual(reentry.get("/api/work/knowledge").status_code, 401)
        self.assertEqual(self.login(self.app.test_client(), "engineer", "new-temporary-code").status_code, 401)

    def test_accounts_do_not_add_mail_recipients_and_cannot_collide_with_owner(self):
        self.staff()
        self.assertEqual(self.db.list("people"), [])
        data = {"name": "Имя", "password": "temporary-code-123", "grants": ["inventory.write"]}
        self.write("employees", {**data, "login": "ADMIN"}, expect=400)
        self.write("employees", {**data, "login": "ENGINEER"}, expect=409)
        self.write("employees", {**data, "login": "new", "grants":["invented"]}, expect=400)
        self.assertEqual(self.app.test_client().get("/api/work/inventory").status_code,401)

    def test_knowledge_edit_hierarchy_move_cycles_and_lost_edits(self):
        root = self.write("knowledge", {"title": "IT"})["id"]
        child = self.write("knowledge", {"title": "Кассы", "parent": root})["id"]
        leaf = self.write("knowledge", {"title": "Проверка", "body": "Сверьте номер", "parent":child})["id"]
        self.write("knowledge/"+root, {"title":"IT", "parent":leaf, "version":1}, method="PUT", expect=400)
        self.write("knowledge/"+leaf, {"title":"Проверка", "parent":root, "body":"Новый текст", "version":1},
                   method="PUT", expect=200)
        self.write("knowledge/"+leaf, {"title":"Потеря", "parent":root, "version":1}, method="PUT", expect=400)
        self.assertEqual(self.owner.get("/api/work/knowledge/"+leaf).json["body"], "Новый текст")

    def test_import_editable_structure_and_original(self):
        samples = (("Приёмка.md", "# Приёмка\n## Номер\nСверьте маркировку.".encode()),
                   ("Приёмка.docx", word_document()),
                   ("Приёмка.txt", "1. Приёмка\n1.1 Номер\nСверьте маркировку.".encode()))
        for name, raw in samples:
            with self.subTest(name=name):
                result = self.upload(name, raw)
                root = result["id"]
                self.assertEqual(result["count"], 3)
                items = self.owner.get("/api/work/knowledge").json["items"]
                section = next(item for item in items if item["parent"] == root)
                sub = next(item for item in items if item["parent"] == section["id"])
                self.assertEqual(sub["title"], "Номер")
                self.assertEqual(self.owner.get("/api/work/knowledge/"+sub["id"]).json["body"], "Сверьте маркировку.")
                response = self.owner.get("/api/work/knowledge/"+root+"/original")
                self.assertIn("attachment", response.headers["Content-Disposition"])
                self.assertEqual(response.data, raw)
        pdf = self.upload("Памятка.pdf", pdf_document())
        self.assertEqual(self.owner.get("/api/work/knowledge/"+pdf["id"]).json["body"], "Knowledge")

    def test_word_multilevel_numbering_becomes_editable_children(self):
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        long_title = "Проверьте устройство. " * 20
        for level, line in ((0, "Осмотр"), (1, long_title), (0, "Передача")):
            xml += (f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="3"/></w:numPr></w:pPr>'
                    f'<w:r><w:t>{line}</w:t></w:r></w:p>')
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w") as zipped:
            zipped.writestr("word/document.xml", (xml+"</w:body></w:document>").encode())
        root = self.upload("Регламент.docx", result.getvalue())["id"]
        tree = self.owner.get("/api/work/knowledge").json["items"]
        sections = [item for item in tree if item["parent"] == root]
        self.assertEqual([s["title"] for s in sections], ["Осмотр", "Передача"])
        leaf = next(item for item in tree if item["parent"] == sections[0]["id"])
        self.assertLessEqual(len(leaf["title"]), 240)
        self.assertEqual(self.owner.get("/api/work/knowledge/"+leaf["id"]).json["body"], long_title.strip())

    def test_bad_import_rolls_back_and_file_limits_are_enforced(self):
        self.upload("broken.docx", b"bad", 400)
        self.upload("empty.txt", b"", 400)
        self.upload("unknown.txt", b"abc", 400, parent="missing")
        self.upload("large.txt", b"a" * (DOCUMENT_LIMIT + 1), 400)
        self.upload("too-big.txt", b"a" * (DOCUMENT_LIMIT + 150 * 1024), 413)
        self.assertEqual(self.owner.get("/api/work/knowledge").json["items"], [])

    def test_movements_hold_history_dates_and_concurrent_updates(self):
        first = self.staff("one", ["inventory.write"])
        second = self.staff("two")
        user, token = self.activate("one")
        data = {"tag":"IT-014", "name":"Ноутбук", "serial":"ABC", "day":"2026-11-16"}
        asset = self.write("inventory", data, client=user, token=token)["id"]
        self.write("inventory", {**data, "tag":"it-014"}, expect=409)
        path = f"inventory/{asset}"
        def details():
            return user.get("/api/work/"+path).json
        for action, employee, day in (("commission", None, "2026-11-17"), ("assign", first, "2026-11-18"),
                                      ("transfer", second, "2026-11-19"), ("return", None, "2026-11-20")):
            version = details()["version"]
            data = {"action":action,"employee":employee,"day":day,"version":version, "service_months": 12}
            self.write(path+"/events", data, client=user, token=token)
            self.write(path+"/events", data, client=user, token=token, expect=400)
        events = details()["events"]
        self.assertEqual([e["action"] for e in reversed(events)], ["receive","commission","assign","transfer","return"])
        self.assertTrue(all(e["actor_name"] == "one" and e["recorded_at"].endswith("+03:00") for e in events))
        self.assertEqual(events[1]["employee"], second)
        row = user.get("/api/work/inventory").json["items"][0]
        self.assertEqual((row["state"],row["employee"],row["received_on"]), ("stock",None,"2026-11-16"))
        for day in ("2026-11-19","2026-11-21"):
            self.write(path+"/events", {"version":details()["version"], "action":"assign", "employee":first, "day":day}, expect=400)

    def test_reader_can_download_but_cannot_import_or_change_monitor(self):
        doc = self.upload("Руководство.txt", "Инструкция".encode())["id"]
        self.staff("reader")
        reader, token = self.activate("reader")
        self.assertEqual(reader.get(f"/api/work/knowledge/{doc}/original").data, "Инструкция".encode())
        upload = reader.post("/api/work/knowledge/import", data={"file": (io.BytesIO(b"note"), "x.txt")},
                             headers={"X-CSRF-Token": token})
        self.assertEqual(upload.status_code, 403)
        self.assertEqual(reader.put("/api/monitor", json={"enabled": True},
                                    headers={"X-CSRF-Token": token}).status_code, 403)
        self.assertFalse(self.db.preferences()["enabled"])
        self.assertEqual(self.owner.post("/api/work/inventory", json={}).status_code, 403)

    def test_disabled_holder_remains_in_history_and_can_return_device(self):
        employee = self.staff()
        asset = self.write("inventory", {"tag": "IT-2", "name": "Монитор", "day": "2026-11-20"})["id"]
        path = "inventory/" + asset
        def version():
            return self.owner.get("/api/work/" + path).json["version"]
        self.write(path + "/events", {"action": "assign", "employee": employee, "version": version(), "day": "2026-11-20",
                                      "service_months": 12})
        held = self.owner.get("/api/work/inventory", query_string={"employee": employee}).json["items"]
        self.assertEqual([item["id"] for item in held], [asset])
        entry = self.owner.get("/api/work/employees").json["items"][0]
        self.write("employees/" + employee, {**entry, "active": False, "name": "Архив"}, method="PUT", expect=200)
        self.assertEqual(self.owner.get("/api/work/inventory/assignees").json["items"], [])
        self.write(path + "/events", {"action": "return", "version": version(), "day": "2026-11-20"})
        self.write(path + "/events", {"action": "assign", "employee": employee, "version": version(),
                                      "day": "2026-11-20"}, expect=400)
        events = self.owner.get("/api/work/" + path).json["events"]
        self.assertEqual(events[1]["employee_name"], "engineer")

    def test_directory_employee_can_hold_equipment_without_an_account(self):
        employee = self.write("employees", {"name": "Сотрудник склада"})["id"]
        self.assertEqual(self.owner.get("/api/work/inventory/assignees").json["items"][0]["id"], employee)
        entry = self.owner.get("/api/work/employees").json["items"][0]
        self.assertEqual((entry["login"], entry["login_enabled"], entry["grants"]), ("", False, []))
        item = self.write("inventory", {"name": "Сканер", "tag": "DIR-1", "day": "2026-10-28"})["id"]
        path = "inventory/" + item
        version = self.owner.get("/api/work/" + path).json["version"]
        self.write(path + "/events", {"version": version, "day": "2026-10-28", "action": "assign", "employee": employee,
                                      "service_months": 12})
        enabled = {**entry, "login_enabled": True, "login": "warehouse", "grants": ["knowledge.read"]}
        self.write("employees/" + employee, enabled, method="PUT", expect=400)
        self.write("employees/" + employee, {**enabled, "password": "temporary-code-123"}, method="PUT", expect=200)
        user, _ = self.activate("warehouse")
        active = self.owner.get("/api/work/employees").json["items"][0]
        self.write("employees/" + employee, {**active, "login_enabled": False}, method="PUT", expect=200)
        self.assertEqual(user.get("/api/work/profile").status_code, 401)
        self.assertEqual(self.login(self.app.test_client(), "warehouse", "personal-code-456").status_code, 401)
        self.assertEqual(self.owner.get("/api/work/" + path).json["events"][0]["employee"], employee)
        self.assertEqual(self.owner.get("/api/work/inventory/assignees").json["items"][0]["id"], employee)

    def test_service_dates_warning_filter_data_and_commission_required(self):
        for start, end, status in (
            ("2026-01-31", "2026-02-28", "expired"),
            ("2026-10-20", "2026-11-20", "today"),
            ("2026-10-30", "2026-11-30", "soon"),
            ("2026-11-20", "2026-12-20", "soon"),
        ):
            with self.subTest(start=start):
                item = self.write("inventory", {"name": "Терминал", "tag": start, "day": start})["id"]
                path = "inventory/" + item
                row = self.owner.get("/api/work/" + path).json
                self.assertEqual(row["service_status"], "not_started")
                movement = {"version": row["version"], "day": start, "action": "commission"}
                self.write(path + "/events", movement, expect=400)
                self.write(path + "/events", {**movement, "service_months": 1})
                row = self.owner.get("/api/work/" + path).json
                self.assertEqual((row["service_started_on"], row["service_expires_on"], row["service_status"]),
                                 (start, end, status))
                edit = {"service_revision": row["service_revision"], "service_months": 24}
                self.write(path + "/service", edit, method="PUT", expect=200)
                self.write(path + "/service", edit, method="PUT", expect=400)
                self.assertEqual(self.owner.get("/api/work/" + path).json["service_status"], "valid")

    def test_device_location_history_relocates_without_reassigning(self):
        employee = self.write("employees", {"name":"Сотрудник склада"})["id"]
        device = self.write("inventory", {"tag":"MOVE-17", "name":"Терминал", "day":"2026-11-01",
                                          "location":"Приёмный склад", "note":"Накладная 17"})["id"]
        path = "inventory/" + device

        def record(action, day, place, expect=201, **details):
            row = self.owner.get("/api/work/" + path).json
            self.write(path + "/events", {"version":row["version"], "action":action, "day":day, "location":place,
                                          **details}, expect=expect)

        record("relocate", "2026-11-02", "Стеллаж №1")
        record("assign", "2026-11-03", "Стойка 1", employee=employee, service_months=36)
        record("relocate", "2026-11-04", "стойка 1", expect=400)
        record("relocate", "2026-11-04", "Стойка 2")
        detail = self.owner.get("/api/work/" + path).json
        self.assertEqual(detail["events"][0]["employee"], employee)
        self.assertEqual(detail["service_started_on"], "2026-11-03")
        self.assertEqual([event["location"] for event in reversed(detail["events"])],
                         ["Приёмный склад", "Стеллаж №1", "Стойка 1", "Стойка 2"])
        self.assertEqual(detail["events"][-1]["note"], "Накладная 17")
        record("return", "2026-11-05", "")
        detail = self.owner.get("/api/work/" + path).json
        self.assertIsNone(detail["events"][0]["employee"])
        self.assertEqual((detail["events"][0]["location"], detail["service_months"]), ("Склад",36))

    @staticmethod
    def sample_photo():
        raw = io.BytesIO()
        Image.new("RGB", (60, 30), color="#29483d").save(raw, format="PNG")
        return raw.getvalue()

    def send_files(self, path, fields, *, method="POST", expect=201):
        result = self.owner.open("/api/work/" + path, data=fields, method=method, content_type="multipart/form-data",
                                 headers={"X-CSRF-Token": self.token})
        self.assertEqual(result.status_code, expect, result.get_data(as_text=True))
        return result.json

    def test_asset_optional_photo_is_scoped_and_validated(self):
        fields = {"name": "Стойка", "tag": "PHOTO-1", "day": "2026-11-20"}
        self.send_files("inventory", {**fields, "photo": (io.BytesIO(b"<svg></svg>"), "photo.png")}, expect=400)
        item = self.send_files("inventory", {**fields, "photo": (io.BytesIO(self.sample_photo()), "photo.png")})["id"]
        path = f"/api/work/inventory/{item}/photo"
        response = self.owner.get(path)
        self.assertEqual(response.mimetype, "image/webp")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.app.test_client().get(path).status_code, 401)
        self.staff("documents")
        user, _ = self.activate("documents")
        self.assertEqual(user.get(path).status_code, 403)
        self.write("inventory/" + item + "/photo", {}, method="DELETE", expect=200)
        self.assertEqual(self.owner.get(path).status_code, 404)

    def test_article_editor_persists_clean_markup_and_media_atomically(self):
        parent = self.write("knowledge", {"title": "Памятки", "kind": "section"})["id"]
        self.write("knowledge", {"kind": "section", "title": "Ошибка", "body": "Текст"}, expect=400)
        media = "abc00000a123eeee"
        raw = (f'<h2>Техника</h2><p><strong>Шаг</strong><a href="javascript:alert(1)">ссылка</a>'
               f'<script>alert(1)</script></p><figure data-media="{media}"></figure>')
        entry = {"title":"Приёмка", "kind":"article", "parent":parent, "html":raw}
        self.write("knowledge", entry, expect=400)
        fields = {"record": json.dumps(entry), "media:" + media: (io.BytesIO(self.sample_photo()), "photo.png")}
        item = self.send_files("knowledge", fields)["id"]
        path = "knowledge/" + item
        data = self.owner.get("/api/work/" + path).json
        self.assertEqual((data["kind"], data["parent"]), ("article", parent))
        self.assertIn("<strong>Шаг</strong>", data["html"])
        self.assertNotIn("javascript", data["html"])
        self.assertNotIn("alert(1)", data["html"])
        self.assertEqual(data["media"][0]["id"], media)
        self.assertEqual(self.owner.get("/api/work/" + path + "/media/" + media).mimetype, "image/webp")
        self.assertEqual(self.owner.get(f"/api/work/knowledge/{parent}/media/{media}").status_code,404)
        self.write(path, {**entry, "version":1, "html":"<p>Новая редакция</p>"}, method="PUT", expect=200)
        self.assertEqual(self.owner.get("/api/work/" + path + "/media/" + media).status_code,404)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is needed for real videos")
    def test_video_duration_is_validated_on_the_server_and_streams_by_range(self):
        def clip(seconds):
            return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s=64x64:r=4:d={seconds}",
                                   "-an", "-c:v", "libx264", "-threads", "1", "-movflags", "frag_keyframe+empty_moov",
                                   "-f", "mp4", "pipe:1"], timeout=15, capture_output=True, check=True).stdout
        media = "aa0aa000a123eeee"
        article = json.dumps({"title": "Видео", "html": f'<figure data-media="{media}"></figure>'})
        fields = {"record": article, "media:" + media: (io.BytesIO(clip(21)), "guide.mp4")}
        self.send_files("knowledge", fields, expect=400)
        self.assertEqual(self.owner.get("/api/work/knowledge").json["items"], [])
        raw = clip(20)
        item = self.send_files("knowledge", {**fields, "media:" + media: (io.BytesIO(raw), "guide.mp4")})["id"]
        response = self.owner.get(f"/api/work/knowledge/{item}/media/{media}", headers={"Range":"bytes=16-47"})
        self.assertEqual((response.status_code, response.mimetype, response.data), (206, "video/mp4", raw[16:48]))

    def test_upgrade_keeps_legacy_monitor_volume_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"old.sqlite"
            with sqlite3.connect(path) as conn:
                conn.executescript("CREATE TABLE settings(key TEXT PRIMARY KEY,data TEXT NOT NULL);"
                                   """INSERT INTO settings VALUES('monitor','{"enabled":true}');""")
            for _ in range(2):
                database = Database(path)
                self.assertTrue(database.preferences()["enabled"])
                with database.connection() as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM employees").fetchone()[0],0)
