"""Exercise HTTP serialization against a local iiko and Yandex Bot API fixture."""

import asyncio
import json
import tempfile
import threading
import unittest
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from shiftbot.commands import Commands
from shiftbot.config import MSK, Server
from shiftbot.http import Http
from shiftbot.iiko import Iiko
from shiftbot.messenger import Messenger
from shiftbot.monitor import Monitor
from shiftbot.state import Store
from tests.helpers import ADMIN_LOGIN, CHAT_ID, now, settings


class HttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_assignment_alert_recovery_and_restart_via_http(self):
        sent, queries, data = [], [], {"shifts": []}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, body, content_type="application/json"):
                raw = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                request = urlsplit(self.path)
                query = parse_qs(request.query)
                if request.path == "/resto/api/v2/cashshifts/list":
                    if query.get("key") != ["a" * 32]:
                        self.send_error(401)
                        return
                    queries.append(query)
                    self.respond(json.dumps(data["shifts"]))
                elif request.path == "/resto/api/corporation/departments":
                    if not self.headers.get("Accept", "").startswith("application/xml"):
                        self.send_error(406)
                        return
                    self.respond("<list><department><id>north</id><name>Север</name></department></list>",
                                 "application/xml")
                elif request.path.endswith("/logout"):
                    if not self.headers.get("Accept", "").startswith("text/plain"):
                        self.send_error(406)
                        return
                    data["logged_out"] = True
                    self.respond("true", "text/plain")
                elif request.path == "/bot/v1/messages/getUpdates/":
                    if self.headers.get("Authorization") != "OAuth fixture-token":
                        self.send_error(401)
                        return
                    self.respond(json.dumps({"ok": True, "updates": [data["update"]]}))
                else:
                    self.send_error(404)

            def do_POST(self):
                request = urlsplit(self.path)
                if request.path == "/resto/api/auth":
                    if not self.headers.get("Accept", "").startswith("text/plain"):
                        self.send_error(406)
                        return
                    if self.headers.get_content_type() != "application/x-www-form-urlencoded" or request.query:
                        self.send_error(400)
                        return
                    form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode("ascii"))
                    if form != {"login": ["fixture"], "pass": [sha1(b"fixture").hexdigest()]}:
                        self.send_error(401)
                        return
                    self.respond("a" * 32, "text/plain")
                elif request.path == "/bot/v1/messages/sendText/":
                    if self.headers.get("Authorization") != "OAuth fixture-token":
                        self.send_error(401)
                        return
                    sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                    self.respond('{"ok": true, "message_id": 1}')
                else:
                    self.send_error(404)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        http = Http()
        client = Iiko(Server("main", base + "/resto", "LOGIN", "PASSWORD", MSK), http)
        messenger = Messenger("fixture-token", CHAT_ID, http)
        messenger._base = base + "/bot/v1/"
        try:
            with patch.dict("os.environ", {"LOGIN": "fixture", "PASSWORD": "fixture"}):
                departments = await client.discover("departments")
                self.assertEqual(departments, [{
                    "id": "north", "name": "Север", "parentId": None, "departmentId": None,
                }])
                with tempfile.TemporaryDirectory() as folder:
                    path = str(Path(folder) / "state.sqlite3")
                    store = Store(path)
                    monitor = Monitor(settings(), store, {"main": client}, messenger)
                    commands = Commands(settings(), store, monitor, messenger, "ShiftBot")
                    data["update"] = {
                        "update_id": 321, "timestamp": int(datetime.now(timezone.utc).timestamp()),
                        "text": "/assign arbat", "chat": {"id": CHAT_ID, "type": "group"},
                        "from": {"login": ADMIN_LOGIN, "robot": False},
                        "reply_to_message": {"from": {"login": "anna@example.org", "display_name": "Анна"}},
                    }
                    for update in await messenger.updates(0):
                        await commands.handle(update)
                    await monitor.tick(now())
                    await monitor.tick(now(minute=6))
                    data["shifts"] = [{"openDate": "2026-11-18T09:06:00"}]
                    await monitor.tick(now(minute=7))
                    store.close()
                    store = Store(path)
                    try:
                        await Monitor(settings(), store, {"main": client}, messenger).tick(now(minute=8))
                    finally:
                        store.close()
                await client.close()
            self.assertTrue(data.get("logged_out"))
            self.assertEqual(len(sent), 3)  # Assignment acknowledgement, initial alert, resolution.
            self.assertEqual(sent[1]["chat_id"], CHAT_ID)
            self.assertIn("Анна (anna@example.org)", sent[1]["text"])
            self.assertIn("Опоздание с открытием", sent[1]["text"])
            self.assertIn("открыта с опозданием", sent[2]["text"])
            self.assertEqual(queries[0]["status"], ["ANY"])
            self.assertEqual(queries[0]["openDateFrom"], ["2026-11-18"])
            self.assertEqual(queries[0]["openDateTo"], ["2026-11-18"])
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
