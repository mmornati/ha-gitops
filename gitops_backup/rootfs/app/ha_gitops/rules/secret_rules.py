# SPDX-License-Identifier: Apache-2.0
"""Content rules for the secret scan.

The never-commit path patterns cannot know about a secret that lives in an
ordinary file: an inline ``password: hunter2``, a pasted PEM block, a GitHub
token in a shell script. These rules look at content. The matched text itself
is never returned, only the label of the rule. The rules come from upstream
askb/ha-gitops (0.5.2).
"""

import re
from collections.abc import Iterable

INLINE = "inline credential"
RULES: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private key block", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("GitHub fine-grained PAT", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{80,}\b")),
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Slack token", re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "JWT / HA token",
        re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        INLINE,
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
CHUNK = 4 * 1024 * 1024
OVERLAP = 8192  # carried between chunks, so a match across the cut is seen


def scan(data: bytes, binary: bool | None = None) -> str | None:
    """Label of the first rule the content trips, or None.

    Binary content skips only the inline `key: value` rule, which is
    meaningless there; key blocks and tokens are still looked for.
    """
    if binary is None:
        binary = b"\0" in data[:8000]
    for label, rx in RULES:
        if binary and label == INLINE:
            continue
        if rx.search(data):
            return label
    return None


def scan_chunks(chunks: Iterable[bytes]) -> str | None:
    """scan() over a stream, without holding a large file in memory."""
    tail = b""
    binary: bool | None = None
    found: str | None = None
    for chunk in chunks:  # always drained, the caller's stream must stay in sync
        if found:
            continue
        if binary is None:
            binary = b"\0" in chunk[:8000]
        buf = tail + chunk
        found = scan(buf, binary)
        tail = buf[-OVERLAP:]
    return found
