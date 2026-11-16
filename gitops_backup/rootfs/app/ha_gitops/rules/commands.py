# SPDX-License-Identifier: Apache-2.0
"""Commands accepted from stdin, MQTT and the panel."""

import json

from ha_gitops.settings import SHA_RE

COMMANDS = {
    "sync",
    "backup",
    "dry_run",
    "pause",
    "resume",
    "auto_apply_on",
    "auto_apply_off",
    "revert",
    "refresh",
    "discard",
    "merge",
    "restart",
}
# These change the box or the repository for good. They are taken from the
# panel (behind Home Assistant's login) and from stdin, never from MQTT, where
# any device with broker credentials could send them.
PANEL_ONLY = {"discard", "merge", "restart"}


def parse_command(raw: object) -> tuple[str, str] | None:
    """Parses a command from stdin, MQTT or the panel.

    hassio.addon_stdin JSON-encodes its input, so `"sync"` and
    `{"command": "revert", "arg": "abc1234"}` are both accepted, as is the
    plain text `revert abc1234`.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode(errors="replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except ValueError:
            raw = text
    if isinstance(raw, dict):
        cmd = str(raw.get("command", "")).strip().lower()
        arg = str(raw.get("arg", "") or "").strip()
    elif isinstance(raw, str):
        parts = raw.strip().split(None, 1)
        if not parts:
            return None
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
    else:
        return None
    if cmd not in COMMANDS:
        return None
    if cmd == "revert":
        arg = arg.lower()
        if not SHA_RE.match(arg):
            return None
    else:
        arg = ""  # no other command takes an argument: never carry free text along
    return cmd, arg
