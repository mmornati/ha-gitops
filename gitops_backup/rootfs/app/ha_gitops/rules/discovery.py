# SPDX-License-Identifier: Apache-2.0
"""MQTT discovery payloads for the HA GitOps device."""

from typing import Any


def _short_sha(key: str) -> str:
    return f"{{{{ value_json.{key}[:7] if value_json.{key} else 'unknown' }}}}"


def discovery_configs(
    prefix: str, base: str, version: str, commands: bool = True
) -> list[tuple[str, dict[str, Any] | None]]:
    """MQTT discovery payloads for the HA GitOps device.

    With `commands` off, the buttons, switches and the update entity are
    listed with a None payload: publishing an empty config removes them.
    """
    device = {
        "identifiers": ["ha_gitops"],
        "name": "HA GitOps",
        "manufacturer": "mmornati/ha-gitops",
        "model": "GitOps config backup app",
        "sw_version": version,
    }
    common = {
        "device": device,
        "availability_topic": f"{base}/availability",
        "state_topic": f"{base}/state",
    }

    def entity(component: str, key: str, name: str, **extra: Any) -> tuple[str, dict[str, Any]]:
        cfg = dict(common)
        cfg.update({"unique_id": f"ha_gitops_{key}", "object_id": f"ha_gitops_{key}", "name": name})
        cfg.update(extra)
        return f"{prefix}/{component}/ha_gitops/{key}/config", cfg

    attrs = {"json_attributes_topic": f"{base}/state"}
    out: list[tuple[str, dict[str, Any] | None]] = [
        entity(
            "sensor",
            "status",
            "Status",
            value_template="{{ value_json.state }}",
            icon="mdi:source-branch-check",
            **attrs,
        ),
        entity(
            "sensor",
            "last_run",
            "Last run",
            value_template="{{ value_json.last_run }}",
            device_class="timestamp",
            entity_category="diagnostic",
        ),
        entity(
            "sensor",
            "local_head",
            "Local commit",
            value_template=_short_sha("local_head"),
            icon="mdi:source-commit-local",
            entity_category="diagnostic",
        ),
        entity(
            "sensor",
            "remote_head",
            "Remote commit",
            value_template=_short_sha("remote_head"),
            icon="mdi:source-commit",
            entity_category="diagnostic",
        ),
        entity(
            "sensor",
            "pending_changes",
            "Pending changes",
            value_template="{{ value_json.pending }}",
            unit_of_measurement="files",
            state_class="measurement",
            icon="mdi:file-document-edit",
        ),
        entity(
            "sensor",
            "open_pr",
            "Open PR",
            value_template="{{ value_json.pr_number or 'none' }}",
            json_attributes_topic=f"{base}/pr",
            icon="mdi:source-pull",
        ),
        entity(
            "sensor",
            "pr_checks",
            "PR checks",
            value_template="{{ value_json.pr_checks }}",
            icon="mdi:check-decagram",
        ),
        entity(
            "sensor",
            "last_apply",
            "Last apply",
            value_template="{{ value_json.applied or 'none' }}",
            icon="mdi:reload",
        ),
        entity(
            "sensor",
            "held_files",
            "Held back files",
            value_template="{{ value_json.held | count }}",
            unit_of_measurement="files",
            state_class="measurement",
            icon="mdi:file-lock",
        ),
        entity(
            "binary_sensor",
            "running",
            "Running",
            value_template="{{ 'ON' if value_json.running else 'OFF' }}",
            device_class="running",
            entity_category="diagnostic",
        ),
        entity(
            "binary_sensor",
            "restart_required",
            "Restart required",
            value_template="{{ 'ON' if value_json.restart_required else 'OFF' }}",
            icon="mdi:restart-alert",
        ),
        entity(
            "button",
            "sync",
            "Sync now",
            command_topic=f"{base}/cmd",
            payload_press="sync",
            icon="mdi:cloud-download",
        ),
        entity(
            "button",
            "backup",
            "Back up now",
            command_topic=f"{base}/cmd",
            payload_press="backup",
            icon="mdi:cloud-upload",
        ),
        entity(
            "button",
            "dry_run",
            "Dry run",
            command_topic=f"{base}/cmd",
            payload_press="dry_run",
            icon="mdi:test-tube",
            entity_category="diagnostic",
        ),
        entity(
            "switch",
            "paused",
            "Pause automatic sync",
            command_topic=f"{base}/cmd",
            payload_on="pause",
            payload_off="resume",
            value_template="{{ 'pause' if value_json.paused else 'resume' }}",
            icon="mdi:pause-circle",
        ),
        entity(
            "switch",
            "auto_apply",
            "Apply merged changes",
            command_topic=f"{base}/cmd",
            payload_on="auto_apply_on",
            payload_off="auto_apply_off",
            value_template="{{ 'auto_apply_on' if value_json.auto_apply else 'auto_apply_off' }}",
            icon="mdi:auto-fix",
        ),
    ]
    upd = dict(common)
    upd.update(
        {
            "unique_id": "ha_gitops_config",
            "object_id": "ha_gitops_config",
            "name": "Config",
            "state_topic": f"{base}/update",
            "command_topic": f"{base}/cmd",
            "payload_install": "sync",
            "title": "Home Assistant config",
            "icon": "mdi:source-merge",
        }
    )
    out.append((f"{prefix}/update/ha_gitops/config/config", upd))
    if not commands:
        out = [(topic, None if cfg and "command_topic" in cfg else cfg) for topic, cfg in out]
    return out
