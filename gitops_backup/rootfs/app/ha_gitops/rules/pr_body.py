# SPDX-License-Identifier: Apache-2.0
"""The markdown description of a backup PR."""

from collections.abc import Iterable, Sequence

# GitHub rejects PR bodies over 65536 characters.
BODY_MAX = 60_000
_RESERVE = 200  # room for the "…and N more files" note

_GROUPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("automations.yaml", "automations/"), "Automations"),
    (("scripts.yaml", "scripts/"), "Scripts"),
    (("scenes.yaml", "scenes/"), "Scenes"),
    (("dashboards/", "ui-lovelace", "www/"), "Dashboards and frontend"),
    (("packages/",), "Packages"),
    (("custom_components/",), "Custom components"),
    (("blueprints/",), "Blueprints"),
    (("configuration.yaml", "customize", "templates/"), "Core configuration"),
)
_STATUS = {"A": "added", "D": "deleted"}


def group_of(path: str) -> str:
    for prefixes, name in _GROUPS:
        if path.startswith(prefixes):
            return name
    return "Other"


def intro(when: str, trigger: str) -> str:
    return (
        "Automated Home Assistant config backup, opened by the HA GitOps app.\n"
        f"Last update: {when} (trigger: {trigger}). Review and merge to keep it. "
        "Closing this PR does not undo the changes on the box: use Discard local "
        "changes in the HA GitOps panel for that.\n"
    )


def file_list(changes: Iterable[tuple[str, str]], max_chars: int = BODY_MAX) -> str:
    """Changed files grouped by area. ``changes`` is (status letter, path).

    Stops listing once the text would pass ``max_chars`` (a big integration
    update touches thousands of files); the PR diff still has them all.
    """
    rows = sorted((group_of(path), status[:1], path) for status, path in changes)
    out: list[str] = []
    used = skipped = 0
    current = None
    for index, (group, status, path) in enumerate(rows):
        piece = "" if group == current else f"\n### {group}\n\n"
        piece += f"- `{path}` ({_STATUS.get(status, 'modified')})\n"
        size = len(piece.encode())
        if used + size > max_chars - _RESERVE:
            skipped = len(rows) - index
            break
        current = group
        used += size
        out.append(piece)
    if skipped:
        out.append(
            f"\n…and {skipped} more files, not listed here to keep this description "
            "under the GitHub limit. See the Files changed tab.\n"
        )
    return "".join(out)


def held_section(held: Sequence[str]) -> str:
    if not held:
        return ""
    lines = "\n".join(f"- `{path}`" for path in held)
    return (
        "\n\n### Held back\n\nNot in this PR, because the content looks like a secret. "
        "Move the value to `secrets.yaml` and use `!secret`, or add the file to "
        f"`.gitignore`.\n\n{lines}"
    )


def render(
    changes: Iterable[tuple[str, str]],
    *,
    when: str,
    trigger: str,
    held: Sequence[str] = (),
    max_chars: int = BODY_MAX,
) -> str:
    return (intro(when, trigger) + file_list(changes, max_chars)).rstrip("\n") + held_section(held)
