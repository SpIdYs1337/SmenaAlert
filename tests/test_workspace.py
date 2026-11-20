import io
import re
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
            data = {"action":action,"employee":employee,"day":day,"version":version}
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
