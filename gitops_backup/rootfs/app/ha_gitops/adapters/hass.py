# SPDX-License-Identifier: Apache-2.0
"""Home Assistant Core, reached through the Supervisor proxy."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ha_gitops.adapters.http import HttpError, request_json

HassError = HttpError
EVENT_PREFIX = "ha_gitops_"
NOTIFICATION_ID = "ha_gitops"


@dataclass(frozen=True, slots=True)
class ConfigCheck:
    valid: bool
    errors: str


class HomeAssistant:
    def __init__(self, supervisor_url: str, token: str, timeout: float = 120) -> None:
        self._supervisor = supervisor_url.rstrip("/")
        self._api = f"{self._supervisor}/core/api"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout
        self.available = bool(token)

    def _post(self, path: str, body: Any = None, timeout: float | None = None) -> Any:
        if not self.available:
            raise HassError(0, "no access to the Home Assistant API")
        return request_json(
            "POST",
            self._api + path,
            headers=self._headers,
            body={} if body is None else body,
            timeout=timeout or self._timeout,
        )

    def check_config(self) -> ConfigCheck:
        answer = self._post("/config/core/check_config")
        answer = answer if isinstance(answer, dict) else {}
        return ConfigCheck(
            answer.get("result") == "valid", str(answer.get("errors") or "unknown error")
        )

    def call_service(self, service: str) -> None:
        """``service`` is ``domain/service``, as in the REST path."""
        domain, _, name = service.partition("/")
        self._post(f"/services/{quote(domain, safe='')}/{quote(name, safe='')}")

    def restart(self) -> None:
        self.call_service("homeassistant/restart")

    def fire_event(self, name: str, data: dict[str, Any] | None = None) -> bool:
        """Best effort: an event that cannot be sent never fails a run."""
        try:
            self._post(f"/events/{EVENT_PREFIX}{quote(name, safe='')}", data, timeout=20)
        except HassError:
            return False
        return True

    def notify(self, title: str, message: str) -> bool:
        try:
            self._post(
                "/services/persistent_notification/create",
                {"title": title, "message": message, "notification_id": NOTIFICATION_ID},
                timeout=20,
            )
        except HassError:
            return False
        return True

    def set_state(self, entity_id: str, state: str, attributes: dict[str, Any]) -> None:
        self._post(
            f"/states/{quote(entity_id, safe='.')}",
            {"state": state, "attributes": attributes},
            timeout=10,
        )

    def mqtt_service(self) -> dict[str, Any] | None:
        """The broker the Supervisor offers this app, if there is one."""
        if not self.available:
            return None
        answer = request_json(
            "GET", f"{self._supervisor}/services/mqtt", headers=self._headers, timeout=10
        )
        data = answer.get("data") if isinstance(answer, dict) else None
        return data if isinstance(data, dict) else None
