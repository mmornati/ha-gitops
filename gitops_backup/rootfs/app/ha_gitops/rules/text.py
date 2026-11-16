# SPDX-License-Identifier: Apache-2.0
"""Text helpers: log sanitising and git output parsing."""


def log_safe(value: object, limit: int = 200) -> str:
    """A value from outside (a command, an address), safe to put in a log line.

    Line breaks and other control characters are replaced, so the value
    cannot start a forged log entry, and it is cut to `limit` characters.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    return "".join(ch if ch.isprintable() else "?" for ch in text)[:limit]


def parse_name_status(raw: str) -> list[dict[str, str]]:
    """Parses `git diff --name-status -z` into path/status/old records."""
    names = {"A": "added", "D": "deleted", "R": "renamed", "C": "added"}
    tokens = list(raw.split("\0"))
    out, i = [], 0
    while i < len(tokens) and tokens[i]:
        code = tokens[i][:1]
        if code in ("R", "C") and i + 2 < len(tokens):
            old, path = tokens[i + 1], tokens[i + 2]
            i += 3
        elif i + 1 < len(tokens):
            old, path = "", tokens[i + 1]
            i += 2
        else:
            break
        out.append(
            {"path": path, "status": names.get(code, "modified"), "old": old if code == "R" else ""}
        )
    return out
