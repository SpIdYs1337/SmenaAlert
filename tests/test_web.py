import asyncio
import imaplib
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

try:
    import flask  # noqa: F401
    import cryptography  # noqa: F401
except ImportError as error:
    raise unittest.SkipTest("Install requirements-web.txt to test the panel.") from error

from shiftbot.config import DAYS, MSK
from shiftbot.domain import Shift
from shiftbot.iiko import IikoError
from shiftbot.web.app import create_app
from shiftbot.web.mail import Mailbox, Mailer, MailError
from shiftbot.web.service import Service
from shiftbot.web.settings import Vault
from shiftbot.web.storage import Database


class FixtureMail:
    def __init__(self):
        self.messages = []
        self.rejected = set()

    def send(self, settings, password, recipient, subject, body, *, key):
        if recipient in self.rejected:
            raise MailError("Адрес временно недоступен.", retryable=True)
        self.messages.append((recipient, subject, body, password))
        return f"<test-{key}@example.org>"


class FixtureMailbox:
    def __init__(self):
        self.received = set()
        self.error = None

    def probe(self, _settings, _password):
        if self.error is not None:
            raise self.error

    def contains(self, settings, password, message_id):
        self.probe(settings, password)
        return message_id in self.received


class WebPanelTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        directory = Path(folder.name)
        self.db = Database(directory / "panel.sqlite3")
        self.vault = Vault(directory / "key")
        self.smtp = FixtureMail()
        self.imap = FixtureMailbox()
        self.current = datetime(2026, 11, 18, 9, 10, tzinfo=MSK)
        source = self

        class FixtureIiko:
            def __init__(self, *_):
                pass

            async def shifts(self, outlet, _now):
                if outlet.name == "Сбой":
                    raise IikoError("Нет ответа iiko.")
                return source.shifts.get(outlet.name, [])

        self.shifts = {}
        self.service = Service(self.db, self.vault, mailer=self.smtp, mailbox=self.imap,
                               iiko_factory=FixtureIiko, now=lambda: self.current)
        self.app = create_app(self.service, admin="admin", password="fixture-panel-password")
        self.client = self.app.test_client()

    def login(self):
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)
        form = self.client.get("/login").get_data(as_text=True)
        csrf = re.search('name="csrf" value="([^"]+)"', form)[1]
        self.assertEqual(self.client.post("/login", data={"csrf": csrf, "login": "admin", "password": "fixture-panel-password"}).status_code, 302)
        page = self.client.get("/").get_data(as_text=True)
        self.token = re.search('name="csrf" content="([^"]+)"', page)[1]

    def write(self, method, path, data, expected=200):
        response = self.client.open("/api" + path, method=method, json=data, headers={"X-CSRF-Token": self.token})
        self.assertEqual(response.status_code, expected, response.get_data(as_text=True))
        return response.get_json()

    def configure(self):
        self.login()
        self.write("PUT", "/smtp", {"host": "smtp.example.org", "port": 587, "security": "starttls",
            "login": "alerts@example.org", "sender": "alerts@example.org", "sender_name": "Контроль", "password": "fixture-smtp-secret"})
        server = self.write("POST", "/servers", {"name": "Chain", "base_url": "https://chain.example.org/resto", "timezone": "Europe/Moscow",
            "login": "api", "password": "fixture-iiko-secret"}, 201)["id"]
        return server

    def point(self, server, name, people, uuid_suffix):
        return self.write("POST", "/points", {"server": server, "name": name, "department_id": f"22222222-2222-4222-8222-{uuid_suffix:012}",
            "week": dict.fromkeys(DAYS, "09:00"), "people": people}, 201)["id"]

    def add_person(self, name, email, points=()):
        return self.write("POST", "/people", {"name": name, "email": email, "points": list(points)}, 201)["id"]

    def test_auto_alert_only_goes_to_assigned_people_and_has_follow_up(self):
        server = self.configure()
        first = self.add_person("Анна", "anna@example.org")
        self.add_person("Другой сотрудник", "other@example.org")
        arbat = self.point(server, "Арбат", [first], 1)
        self.point(server, "Сбой", [first], 2)
        self.write("PUT", "/monitor", {"enabled": True})
        for _ in range(2):
            asyncio.run(self.service.check())
            asyncio.run(self.service.deliver())
        self.assertEqual(len(self.smtp.messages), 1)
        self.assertEqual(self.smtp.messages[0][0], "anna@example.org")
        self.assertIn("Опоздание с открытием", self.smtp.messages[0][1])
        self.assertEqual(self.db.checks("2026-11-18")[arbat]["status"], "not_open")
        self.shifts["Арбат"] = [Shift(1, self.current.replace(minute=7))]
        asyncio.run(self.service.check())
        asyncio.run(self.service.deliver())
        self.assertEqual(len(self.smtp.messages), 2)
        self.assertIn("09:07:00", self.smtp.messages[1][2])
        self.assertEqual({item["kind"] for item in self.db.history()}, {"auto", "followup"})

    def test_date_changes_are_per_point_and_never_alert_before_opening_time(self):
        server = self.configure()
        person = self.add_person("Анна", "anna@example.org")
        later = self.point(server, "Арбат", [person], 1)
        closed = self.point(server, "Сокол", [person], 2)
        today = self.current.date().isoformat()
        edited = self.db.get("points", later)
        edited["week"]["wed"] = "off"
        self.write("PUT", f"/points/{later}", {**edited, "people": [person], "exceptions": {today: "10:00"}})
        self.write("PUT", f"/points/{closed}", {
            **self.db.get("points", closed), "people": [person], "exceptions": {today: "off"},
        })
        self.write("PUT", "/monitor", {"enabled": True})

        self.current = self.current.replace(minute=59, second=59)
        asyncio.run(self.service.check())
        asyncio.run(self.service.deliver())
        self.assertEqual(self.db.history(), [])
        self.assertEqual(self.smtp.messages, [])
        states = {point["id"]: point["today"]["status"] for point in self.service.views()}
        self.assertEqual(states, {later: "waiting", closed: "day_off"})

        self.current = self.current.replace(hour=10, minute=0, second=0)
        asyncio.run(self.service.check())
        asyncio.run(self.service.deliver())
        self.assertEqual(len(self.smtp.messages), 1)
        self.assertIn("Арбат", self.smtp.messages[0][1])
        self.assertEqual(self.db.checks(today)[later]["status"], "not_open")

        self.current = self.current.replace(day=19, hour=8)
        for point in self.service.views():
            self.assertEqual(datetime.fromisoformat(point["today"]["deadline"]).hour, 9)
        self.current = self.current.replace(day=25)
        states = {point["id"]: point["today"]["status"] for point in self.service.views()}
        self.assertEqual(states, {later: "day_off", closed: "waiting"})

    def test_ui_config_and_manual_outbox_use_only_authorized_assignments(self):
        server = self.configure()
        person = self.add_person("Анна", "anna@example.org")
        outsider = self.add_person("Денис", "denis@example.org")
        point = self.point(server, "Арбат", [person], 1)
        api = self.client.get("/api/dashboard").get_data(as_text=True)
        self.assertNotIn("fixture-smtp-secret", api)
        self.assertNotIn("fixture-iiko-secret", api)
        self.assertNotIn('"secret"', api)
        self.assertNotIn(b"fixture-smtp-secret", self.db.path.read_bytes())
        self.assertEqual(self.client.post("/api/smtp/test", json={"email": "anna@example.org"}).status_code, 403)
        self.write("POST", "/smtp/test", {"email": "invalid@"}, 400)
        self.write("POST", f"/points/{point}/send", {"people": [outsider]}, 400)
        letter = self.write("POST", f"/points/{point}/preview", {"note": "Пилот"})
        self.assertIn("Пилот", letter["body"])
        self.write("POST", f"/points/{point}/send", {"people": [person], "note": "Пилот"}, 202)
        test = self.write("POST", "/smtp/test", {"email": "demo@example.org"}, 202)
        asyncio.run(self.service.deliver())
        self.assertEqual([message[0] for message in self.smtp.messages], ["anna@example.org", "demo@example.org"])
        self.assertEqual(self.db.delivery(test["id"])["status"], "sent")
        self.assertEqual(self.smtp.messages[0][3], "fixture-smtp-secret")
        queued = self.write("POST", f"/points/{point}/send", {"people": [person]}, 202)["ids"][0]
        self.write("PUT", f"/people/{person}", {"name": "Анна", "email": "anna@example.org", "points": []})
        asyncio.run(self.service.deliver())
        self.assertEqual(self.db.delivery(queued)["status"], "cancelled")
        self.assertEqual(len(self.smtp.messages), 2)

    def test_retry_keeps_sent_recipients_and_rechecks_changed_assignments(self):
        server = self.configure()
        good = self.add_person("Анна", "anna@example.org")
        bad = self.add_person("Ольга", "olga@example.org")
        point = self.point(server, "Арбат", [good, bad], 1)
        self.write("PUT", "/monitor", {"enabled": True})
        self.smtp.rejected.add("olga@example.org")
        asyncio.run(self.service.check())
        asyncio.run(self.service.deliver())
        results = {record["recipient"]: record["status"] for record in self.db.history()}
        self.assertEqual(results, {"anna@example.org": "sent", "olga@example.org": "queued"})
        self.write("PUT", f"/people/{bad}", {"name": "Ольга", "email": "olga@example.org", "points": []})
        self.current = self.current.replace(minute=15)
        asyncio.run(self.service.check())
        asyncio.run(self.service.deliver())
        results = {record["recipient"]: record["status"] for record in self.db.history()}
        self.assertEqual(results, {"anna@example.org": "sent", "olga@example.org": "cancelled"})
        self.assertEqual(len(self.smtp.messages), 1)

    def test_starttls_authenticates_after_tls_and_sends_standard_message(self):
        cfg = {"host": "smtp.example.org", "port": 587, "security": "starttls", "login": "alerts@example.org",
               "sender": "alerts@example.org", "sender_name": "Контроль открытия"}
        with patch("shiftbot.web.mail.smtplib.SMTP") as smtp:
            message_id = Mailer().send(cfg, "test-credential", "tom@example.org", "Проверка", "Тестовое письмо.", key=1)
            methods = [call[0] for call in smtp.return_value.method_calls]
            self.assertLess(methods.index("starttls"), methods.index("login"))
            message = smtp.return_value.send_message.call_args[0][0]
            self.assertEqual(str(message["To"]), "tom@example.org")
            self.assertIn("alerts@example.org", str(message["From"]))
            self.assertEqual(message_id, str(message["Message-ID"]))

    def test_imap_settings_and_receipt_of_specific_test_letter(self):
        self.configure()
        self.service.start()
        self.addCleanup(self.service.close)
        self.write("POST", "/imap/probe", {}, 400)
        config = {"host": "imap.example.org", "port": 993, "security": "ssl", "login": "alerts@example.org",
                  "email": "alerts@example.org", "password": "fixture-imap-secret"}
        self.assertEqual(self.client.put("/api/imap", json=config).status_code, 403)
        self.write("PUT", "/imap", config | {"security": "plain"}, 400)
        self.write("PUT", "/imap", config)
        self.write("PUT", "/imap", config | {"host": "other.example.org", "password": ""}, 400)
        self.imap.error = MailError("Папка «Входящие» недоступна по IMAP.")
        self.write("POST", "/imap/probe", {}, 502)
        self.assertFalse(self.service.snapshot()["imap"]["health"]["ok"])
        self.imap.error = None
        self.write("POST", "/imap/probe", {})
        overview = self.client.get("/api/dashboard")
        self.assertTrue(overview.get_json()["imap"]["health"]["ok"])
        self.assertNotIn(b"fixture-imap-secret", overview.data)
        self.assertNotIn(b"fixture-imap-secret", self.db.path.read_bytes())
        test = self.write("POST", "/smtp/test", {"email": config["email"]}, 202)["id"]
        other = self.write("POST", "/smtp/test", {"email": "other@example.org"}, 202)["id"]
        self.service.call(self.service.deliver)
        self.assertTrue(self.client.get(f"/api/history/{test}").get_json()["can_check_imap"])
        self.assertFalse(self.client.get(f"/api/history/{other}").get_json()["can_check_imap"])
        self.write("POST", f"/history/{other}/imap", {}, 400)
        self.imap.received.add(f"<test-{other}@example.org>")
        self.assertEqual(self.write("POST", f"/history/{test}/imap", {}), {"found": False})
        self.imap.received.add(f"<test-{test}@example.org>")
        self.assertEqual(self.write("POST", f"/history/{test}/imap", {}), {"found": True})
        self.assertEqual(self.db.delivery(test)["status"], "sent")

    def test_imap_starttls_search_opens_inbox_readonly(self):
        config = {"host": "imap.example.org", "port": 143, "security": "starttls", "login": "alerts@example.org"}
        imap_class = imaplib.IMAP4
        with patch("shiftbot.web.mail.imaplib.IMAP4", error=imap_class.error, abort=imap_class.abort) as imap:
            connection = imap.return_value
            connection.select.return_value = ("OK", [b"3"])
            connection.search.return_value = ("OK", [b"2"])
            self.assertTrue(Mailbox().contains(config, "app-password", "<test@example.org>"))
            self.assertEqual([call[0] for call in connection.method_calls], ["starttls", "login", "select", "search", "logout"])
            self.assertTrue(connection.starttls.call_args.kwargs["ssl_context"].check_hostname)
            connection.select.assert_called_once_with("INBOX", readonly=True)
            connection.search.assert_called_once_with(None, "HEADER", "Message-ID", '"<test@example.org>"')
            connection.reset_mock()
            connection.starttls.side_effect = imap_class.error("fixture server text")
            with self.assertRaisesRegex(MailError, "STARTTLS"):
                Mailbox().probe(config, "app-password")
            connection.login.assert_not_called()

    def test_upgrade_keeps_letters_without_message_id(self):
        self.configure()
        test = self.service.queue_test("alerts@example.org")
        self.db.finish(test, "sent", 1)
        with self.db.connection() as db:
            db.execute("ALTER TABLE deliveries DROP COLUMN message_id")
        upgraded = Database(self.db.path)
        self.assertEqual(upgraded.delivery(test)["status"], "sent")
        self.assertIsNone(upgraded.delivery(test)["message_id"])
