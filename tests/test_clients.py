import hashlib
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from shiftbot.config import MSK, Server
from shiftbot.http import NetworkError, NoRedirect, Response
from shiftbot.iiko import Iiko, IikoError, parse_shifts
from shiftbot.messenger import Messenger, MessengerError
from tests.helpers import CHAT_ID, now, outlet


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return Response(200, result)


class IikoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"LOGIN": "service", "PASSWORD": "local_test_password"})
        self.env.start()
        self.server = Server("main", "https://example.org/resto", "LOGIN", "PASSWORD", MSK)
        self.key = "a" * 32

    def tearDown(self):
        self.env.stop()

    async def test_date_filter_department_and_any_status(self):
        raw = [{"cashRegNumber": 1, "openDate": "2026-11-18T08:55:00.123", "closeDate": "2026-11-18T09:00:00"}]
        http = FakeHttp([self.key, json.dumps(raw), "[]", "true"])
        client = Iiko(self.server, http)
        point = outlet(department_id="11111111-1111-4111-8111-111111111111", group_id="22222222-2222-4222-8222-222222222222")
        rows = await client.shifts(point, now())
        self.assertEqual(rows[0].opened_at.tzinfo, MSK)
        request = http.calls[1][2]["params"]
        self.assertEqual(request["status"], "ANY")
        self.assertEqual(request["openDateFrom"], "2026-11-18")
        self.assertEqual(request["openDateTo"], "2026-11-18")
        self.assertEqual(request["departmentId"], point.department_id)
        self.assertEqual(request["groupId"], point.group_id)
        auth = http.calls[0][2]["params"]
        self.assertEqual(http.calls[0][0], "POST")
        self.assertEqual(auth["pass"], hashlib.sha1(b"local_test_password").hexdigest())
        await client.shifts(point, now())
        self.assertEqual(sum(call[1].endswith("/auth") for call in http.calls), 1)
        await client.close()
        self.assertTrue(http.calls[-1][1].endswith("/logout"))

    async def test_401_reauthenticates_once(self):
        http = FakeHttp([self.key, NetworkError(401), "b" * 32, "[]"])
        client = Iiko(self.server, http)
        self.assertEqual(await client.shifts(outlet(), now()), [])
        self.assertEqual(sum(call[1].endswith("/auth") for call in http.calls), 2)
        self.assertEqual(http.calls[-1][2]["params"]["key"], "b" * 32)

    async def test_auth_uses_get_only_if_post_not_allowed(self):
        http = FakeHttp([NetworkError(405), self.key, "[]"])
        await Iiko(self.server, http).shifts(outlet(), now())
        self.assertEqual([call[0] for call in http.calls], ["POST", "GET", "GET"])

    async def test_connection_outage_is_coalesced_across_outlets(self):
        http = FakeHttp([self.key, NetworkError()])
        client = Iiko(self.server, http)
        with self.assertRaises(IikoError):
            await client.shifts(outlet(), now())
        with self.assertRaises(IikoError):
            await client.shifts(outlet(id="another"), now())
        self.assertEqual(len(http.calls), 2)

    async def test_rate_limit_is_honored(self):
        http = FakeHttp([self.key, NetworkError(429, 600)])
        client = Iiko(self.server, http)
        with self.assertRaises(IikoError):
            await client.shifts(outlet(), now())
        with self.assertRaises(IikoError):
            await client.shifts(outlet(), now())
        self.assertEqual(len(http.calls), 2)

    async def test_server_timezone_converts_moscow_query_window(self):
        server = Server("main", self.server.base_url, "LOGIN", "PASSWORD", ZoneInfo("Asia/Yekaterinburg"))
        http = FakeHttp([self.key, "[]"])
        await Iiko(server, http).shifts(outlet(), now())
        params = http.calls[-1][2]["params"]
        self.assertEqual((params["openDateFrom"], params["openDateTo"]), ("2026-11-18", "2026-11-19"))

    def test_open_date_alone_is_enough_without_register_filter(self):
        payload = [{"openDate": "2026-11-18T08:54:00"}]
        self.assertEqual(parse_shifts(payload, MSK)[0].opened_at.hour, 8)
        with self.assertRaises(IikoError):
            parse_shifts(payload, MSK, require_register=True)

    def test_bad_schema_is_not_empty_shifts(self):
        for payload in ({"error": "denied"}, None, [None], [{"openDate": "oops", "cashRegNumber": 1}],
                        [{"openDate": "2026-11-18", "cashRegNumber": 1}]):
            with self.subTest(payload=payload), self.assertRaises(IikoError):
                parse_shifts(payload, MSK)

    def test_explicit_timestamp_offset_takes_priority(self):
        rows = parse_shifts([{"cashRegNumber": 1, "openDate": "2026-11-17T21:05:00Z"}], MSK)
        self.assertEqual(rows[0].opened_at.astimezone(MSK).day, 18)
        self.assertEqual(rows[0].opened_at.astimezone(MSK).hour, 0)

    def test_network_exception_does_not_contain_request_secrets(self):
        self.assertEqual(str(NetworkError(403)), "HTTP 403")
        self.assertEqual(str(NetworkError()), "Ошибка связи")

    def test_http_redirects_are_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))


class MessengerTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_message_uses_oauth_and_thread(self):
        http = FakeHttp(['{"ok":true,"message_id":1}'])
        messenger = Messenger("local_test_token", CHAT_ID, http, thread_id=42)
        await messenger.send("**Смена**", important=True)
        payload = http.calls[-1][2]["payload"]
        self.assertEqual(http.calls[-1][2]["headers"], {"Authorization": "OAuth local_test_token"})
        self.assertTrue(http.calls[-1][1].endswith("/messages/sendText/"))
        self.assertEqual(payload["chat_id"], CHAT_ID)
        self.assertEqual(payload["thread_id"], 42)
        self.assertTrue(payload["important"])

    async def test_alert_retry_id_is_stable_but_personal_route_is_distinct(self):
        http = FakeHttp(['{"ok":true,"message_id":1}'] * 3)
        messenger = Messenger("local_test_token", CHAT_ID, http, thread_id=42)
        await messenger.send("Изначальная тревога", event_id="arbat/2026-11-18/late")
        await messenger.send("Повторная попытка", event_id="arbat/2026-11-18/late")
        await messenger.send("Личная копия", login="manager@example.org", event_id="arbat/2026-11-18/late")
        first, retry, private = (call[2]["payload"] for call in http.calls)
        self.assertEqual(first["payload_id"], retry["payload_id"])
        self.assertNotEqual(first["payload_id"], private["payload_id"])
        self.assertEqual(private["login"], "manager@example.org")
        self.assertNotIn("chat_id", private)
        self.assertNotIn("thread_id", private)

    async def test_updates_follow_yandex_envelope_and_request(self):
        http = FakeHttp(['{"ok":true,"updates":[{"update_id":120,"text":"/points"}]}'])
        updates = await Messenger("local_test_token", CHAT_ID, http).updates(119)
        self.assertEqual(updates, [{"update_id": 120, "text": "/points"}])
        self.assertEqual(http.calls[0][0], "GET")
        self.assertTrue(http.calls[0][1].endswith("/messages/getUpdates/"))
        self.assertEqual(http.calls[0][2]["params"], {"limit": 100, "offset": 119})

    async def test_messenger_429_is_not_retried_before_retry_after(self):
        http = FakeHttp([NetworkError(429, 120)])
        messenger = Messenger("local_test_token", CHAT_ID, http)
        with self.assertRaises(MessengerError):
            await messenger.send("Test")
        with self.assertRaises(MessengerError):
            await messenger.send("Test")
        self.assertEqual(len(http.calls), 1)

    async def test_large_status_is_split_on_lines(self):
        http = FakeHttp(['{"ok":true,"message_id":1}'] * 4)
        messenger = Messenger("local_test_token", CHAT_ID, http)
        lines = ["**" + "x" * 400 + "**"] * 20
        await messenger.send_lines(lines)
        self.assertGreater(len(http.calls), 1)
        self.assertTrue(all(len(call[2]["payload"]["text"]) <= 6000 for call in http.calls))


if __name__ == "__main__":
    unittest.main()
