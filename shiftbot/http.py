from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class NetworkError(Exception):
    """Safe to log: contains no URL, credentials or upstream response body."""

    def __init__(self, status: int = 0, retry_after: int = 0):
        self.status, self.retry_after = status, retry_after
        super().__init__(f"HTTP {status}" if status else "Ошибка связи")


@dataclass(frozen=True)
class Response:
    status: int
    body: str

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, TypeError):
            raise NetworkError() from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials to a different URL.
        return None


class Http:
    async def request(self, method: str, url: str, *, params=None, payload=None, form=None, headers=None,
                      timeout: int = 20) -> Response:
        return await asyncio.to_thread(self._request, method, url, params, payload, form, headers, timeout)

    @staticmethod
    def _request(method: str, url: str, params, payload, form, headers, timeout: int) -> Response:
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        request_headers = {"Accept": "application/json", "User-Agent": "iiko-shift-monitor/2.0", **(headers or {})}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form, doseq=True).encode("ascii")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            request_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        opener = urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(req, timeout=timeout) as result:
                return Response(result.status, result.read().decode("utf-8-sig"))
        except urllib.error.HTTPError as error:
            retry_after = error.headers.get("Retry-After", "") if error.headers else ""
            error.close()
            raise NetworkError(error.code, int(retry_after) if retry_after.isdecimal() else 0) from None
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeError):
            raise NetworkError() from None
