#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Anil Belur
"""Content-level secret scan for the backup engine.

The never-commit path patterns cannot know about a secret that lives in an
ordinary file: an inline `password: hunter2`, a pasted PEM block, a GitHub
token in a shell script. This reads NUL-separated paths (relative to the
current directory) on stdin and prints one `label<TAB>path<NUL>` record for
each file whose content looks like a secret. The engine then leaves those
files out of the backup commit.

The matched text itself is never printed. Stdlib only. The rules come from
upstream askb/ha-gitops (0.5.2).
"""

from __future__ import annotations

import re
import sys

RULES: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private key block", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("GitHub fine-grained PAT", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{80,}\b")),
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Slack token", re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT / HA token",
     re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "inline credential",
        # key: literal — skips HA `!secret name`, templates, env refs and
        # placeholders. Only plain literals of 8+ chars trip it.
        re.compile(
            rb"(?im)^\s*[\"']?(?:password|passwd|api[_-]?key|access[_-]?token|auth[_-]?token"
            rb"|client[_-]?secret|secret[_-]?key|private[_-]?key|bearer)[\"']?\s*[:=]\s*[\"']?"
            rb"(?!!secret\b|\{\{|\$\{?[A-Z_]|<|change[_-]?me|your[_-]|example|redacted|xxx|\*\*\*)"
            rb"[^\s\"'#]{8,}"
        ),
    ),
)
MAX_BYTES = 1_000_000


def scan(data: bytes) -> str | None:
    """Label of the first rule the content trips, or None."""
    if b"\0" in data[:8000]:
        return None  # binary; the path patterns cover key blobs
    for label, rx in RULES:
        if rx.search(data):
            return label
    return None


def main() -> int:
    out = sys.stdout.buffer
    for raw in filter(None, sys.stdin.buffer.read().split(b"\0")):
        try:
            with open(raw, "rb") as fh:
                data = fh.read(MAX_BYTES + 1)
        except OSError:
            continue  # deleted since staging, or unreadable
        if len(data) > MAX_BYTES:
            continue
        label = scan(data)
        if label:
            out.write(label.encode() + b"\t" + raw + b"\0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
