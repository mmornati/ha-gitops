# SPDX-License-Identifier: Apache-2.0
"""What applying a changed file to the running Home Assistant needs."""

from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase

NONE = "none"
RESTART = "restart"
DASHBOARDS = "dashboards"

# First match wins. A pattern is an exact name, a ``dir/`` prefix or a glob.
_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("automations.yaml", "automations/"), "automation/reload"),
    (("scripts.yaml", "scripts/"), "script/reload"),
    (("scenes.yaml", "scenes/"), "scene/reload"),
    (("groups.yaml",), "group/reload"),
    (("templates/", "template.yaml", "templates.yaml"), "template/reload"),
    (("customize.yaml", "customize/"), "homeassistant/reload_core_config"),
    (("themes/",), "frontend/reload_themes"),
    (("blueprints/automation/",), "automation/reload"),
    (("blueprints/script/",), "script/reload"),
    (("blueprints/",), NONE),
    (("python_scripts/",), "python_script/reload"),
    (("input_*.yaml",), "homeassistant/reload_all"),
    (("dashboards/*.yaml", "dashboards/*.yml", "ui-lovelace*.yaml"), DASHBOARDS),
    # Read by their own apps, not by Home Assistant Core
    (("esphome/", "zigbee2mqtt/"), NONE),
    (("configuration.yaml", "packages/", "custom_components/", "integrations/"), RESTART),
    (
        (
            "www/",
            "docs/",
            "tests/",
            ".github/",
            "*.md",
            ".gitignore",
            ".yamllint",
            "dashboards/",
            "*.fake.*",
        ),
        NONE,
    ),
    (("*.yaml", "*.yml"), RESTART),
)


def _matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if "*" in pattern:
        return fnmatchcase(path, pattern)
    return path == pattern


def classify(path: str) -> str:
    """A reload service (``domain/service``), ``restart``, ``dashboards`` or ``none``."""
    for patterns, action in _RULES:
        if any(_matches(path, p) for p in patterns):
            return action
    return NONE


@dataclass(frozen=True, slots=True)
class ApplyPlan:
    services: tuple[str, ...] = ()
    restart: bool = False
    refresh_dashboards: bool = False

    @property
    def empty(self) -> bool:
        return not (self.services or self.restart or self.refresh_dashboards)

    @property
    def needs_config_check(self) -> bool:
        """A dashboard-only change loads no configuration: nothing to check."""
        return bool(self.services) or self.restart


def plan(mode: str, paths: Iterable[str]) -> ApplyPlan:
    """The smallest set of actions that loads ``paths`` (``mode`` is not ``off``)."""
    if mode == "restart":
        return ApplyPlan(restart=True)
    if mode == "reload":
        yaml_changed = any(p.endswith((".yaml", ".yml")) for p in paths)
        return ApplyPlan(services=("homeassistant/reload_all",), refresh_dashboards=yaml_changed)
    services: list[str] = []
    restart = refresh = False
    for path in paths:
        action = classify(path)
        if action == RESTART:
            restart = True
        elif action == DASHBOARDS:
            refresh = True
        elif action != NONE and action not in services:
            services.append(action)
    return ApplyPlan(tuple(services), restart, refresh)
