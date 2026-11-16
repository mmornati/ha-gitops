# SPDX-License-Identifier: Apache-2.0
"""JSON over HTTP on the standard library, for the GitHub and HA clients."""

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


class HttpError(Exception):
    """The request failed, or the server answered with an error status."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}" if status else message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends the Authorization header to wherever a redirect points.

    Neither API redirects in normal use, so a redirect is treated as an error
    instead of following it with a token attached.
    """

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


# No proxy from the environment either: both peers are fixed, known hosts.
_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
MAX_RESPONSE = 16 * 1024 * 1024


def request_json(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: Any = None,
    timeout: float = 20,
) -> Any:
    """Sends one request and returns the decoded JSON answer (None if empty)."""
    if urlsplit(url).scheme not in ("http", "https"):
        raise HttpError(0, "unsupported URL scheme")
    data = None
    send = dict(headers)
    if body is not None:
        data = json.dumps(body).encode()
        send["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=send, method=method)  # noqa: S310
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as err:
        raise HttpError(err.code, _error_message(err)) from None
    except (OSError, ValueError) as err:
        raise HttpError(0, type(err).__name__ if not str(err) else str(err)) from None
    if len(raw) > MAX_RESPONSE:
        raise HttpError(0, "response too large")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise HttpError(0, "response is not JSON") from None


def _error_message(err: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(err.read(65536))
    except OSError, ValueError:
        return str(err.reason)
    message = payload.get("message") if isinstance(payload, dict) else None
    return str(message or err.reason)
