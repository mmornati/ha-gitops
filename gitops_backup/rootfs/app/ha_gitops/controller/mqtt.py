# SPDX-License-Identifier: Apache-2.0
"""The HA GitOps device in Home Assistant, over MQTT discovery."""

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as paho
from paho.mqtt.enums import CallbackAPIVersion

from ha_gitops.adapters.hass import HassError, HomeAssistant
from ha_gitops.rules.discovery import discovery_configs

log = logging.getLogger("ha_gitops")
BASE = "ha_gitops"
DISCOVERY_PREFIX = "homeassistant"
CONNECT_WAIT = 15


def _make_client() -> paho.Client:
    return paho.Client(CallbackAPIVersion.VERSION2, client_id=BASE)


class MqttBridge:
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        version: str,
        commands: bool,
        on_command: Callable[[bytes], None],
        on_ha_start: Callable[[], None],
        client_factory: Callable[[], Any] = _make_client,
    ) -> None:
        self.hass = hass
        self.version = version
        self.commands = commands
        self._on_command = on_command
        self._on_ha_start = on_ha_start
        self._factory = client_factory
        self._client: Any = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def connect(self) -> bool:
        """Connects to the broker the Supervisor offers. False: use the REST sensor."""
        try:
            cfg = await asyncio.to_thread(self.hass.mqtt_service)
        except HassError as err:
            log.info("MQTT service lookup failed (%s); using the REST sensor", err)
            return False
        if cfg is None:
            log.info("No MQTT broker available; using the REST sensor")
            return False
        client = self._factory()
        if cfg.get("username"):
            client.username_pw_set(cfg["username"], cfg.get("password"))
        if cfg.get("ssl"):
            client.tls_set()
        client.will_set(f"{BASE}/availability", "offline", retain=True)
        ready = threading.Event()

        def on_connect(connected: Any, *_args: Any) -> None:
            self._announce(connected)
            ready.set()

        client.on_connect = on_connect
        client.on_message = self._message
        try:
            client.connect(str(cfg.get("host", "core-mosquitto")), int(cfg.get("port", 1883)))
        except (OSError, ValueError) as err:
            log.warning("MQTT connect failed (%s); using the REST sensor", err)
            return False
        client.loop_start()
        await asyncio.to_thread(ready.wait, CONNECT_WAIT)
        self._client = client
        log.info("MQTT discovery published (device: HA GitOps)")
        return True

    def _announce(self, client: Any) -> None:
        client.publish(f"{BASE}/availability", "online", retain=True)
        for topic, payload in discovery_configs(
            DISCOVERY_PREFIX, BASE, self.version, commands=self.commands
        ):
            # An empty config removes an entity (mqtt_commands turned off)
            client.publish(topic, json.dumps(payload) if payload is not None else "", retain=True)
        if self.commands:
            client.subscribe(f"{BASE}/cmd")
        client.subscribe(f"{DISCOVERY_PREFIX}/status")

    def _message(self, _client: Any, _userdata: Any, msg: Any) -> None:
        """Runs on paho's thread: only hands the message over."""
        if msg.topic == f"{DISCOVERY_PREFIX}/status":
            if msg.payload == b"online":  # HA restarted: republish state
                self._on_ha_start()
            return
        if getattr(msg, "retain", False):
            # A retained command would run again on every reconnect
            log.warning("Ignored a retained MQTT command")
            return
        self._on_command(bytes(msg.payload))

    def publish_state(self, state: dict[str, Any]) -> None:
        client = self._client
        client.publish(f"{BASE}/state", json.dumps(state), retain=True)
        client.publish(
            f"{BASE}/pr",
            json.dumps(
                {"url": state["pr_url"], "branch": state["pr_branch"], "checks": state["pr_checks"]}
            ),
            retain=True,
        )
        client.publish(
            f"{BASE}/update",
            json.dumps(
                {
                    "installed_version": (state["local_head"] or "unknown")[:7],
                    "latest_version": (state["remote_head"] or "unknown")[:7],
                    "release_url": state["pr_url"] or None,
                }
            ),
            retain=True,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.publish(f"{BASE}/availability", "offline", retain=True)
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
