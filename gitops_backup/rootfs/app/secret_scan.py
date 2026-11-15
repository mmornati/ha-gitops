#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Anil Belur
"""Content-level secret scan for the backup engine.

The never-commit path patterns cannot know about a secret that lives in an
ordinary file: an inline `password: hunter2`, a pasted PEM block, a GitHub
token in a shell script. This reads NUL-separated paths (relative to the
repository in the current directory) on stdin and prints one
`path<NUL>label<NUL>` record for each one whose *staged* content looks like a
secret. The engine then leaves those files out of the backup commit.

What is scanned is the blob in the git index, which is exactly what would be
committed, not the file on disk (it can change between `git add` and the
scan). A path that cannot be read from the index is reported too, so the
engine fails closed. Files of any size are scanned, in chunks.

The matched text itself is never printed. Stdlib only. The rules come from
upstream askb/ha-gitops (0.5.2).
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import BinaryIO, Iterator

INLINE = "inline credential"
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
UNREADABLE = "content could not be read"
GITLINK = "160000"  # a nested repository: no file content is committed


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


def scan_chunks(chunks: Iterator[bytes]) -> str | None:
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


def read_exact(stream: BinaryIO, size: int) -> Iterator[bytes]:
    while size > 0:
        data = stream.read(min(size, CHUNK))
        if not data:
            raise OSError("git cat-file ended early")
        size -= len(data)
        yield data


def index_entries() -> dict[bytes, tuple[bytes, bytes]]:
    """path -> (mode, object id) for every stage-0 entry in the index."""
    out = subprocess.run(["git", "ls-files", "-s", "-z"], check=True,
                         capture_output=True).stdout
    entries = {}
    for rec in filter(None, out.split(b"\0")):
        meta, path = rec.split(b"\t", 1)
        mode, oid, stage = meta.split()
        if stage == b"0":
            entries[path] = (mode, oid)
    return entries


def main() -> int:
    paths = [p for p in sys.stdin.buffer.read().split(b"\0") if p]
    if not paths:
        return 0
    entries = index_entries()
    out = sys.stdout.buffer
    cat = subprocess.Popen(["git", "cat-file", "--batch"],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert cat.stdin and cat.stdout
    try:
        for path in paths:
            mode, oid = entries.get(path, (b"", b""))
            if mode == GITLINK.encode():
                continue
            label: str | None = UNREADABLE
            if oid:
                cat.stdin.write(oid + b"\n")
                cat.stdin.flush()
                header = cat.stdout.readline().split()
                if len(header) == 3 and header[1] == b"blob":
                    label = scan_chunks(read_exact(cat.stdout, int(header[2])))
                    cat.stdout.read(1)  # the newline after the content
                elif len(header) == 3:  # not a blob: skip its content
                    for _ in read_exact(cat.stdout, int(header[2]) + 1):
                        pass
            if label:
                out.write(path + b"\0" + label.encode() + b"\0")
    finally:
        cat.stdin.close()
        cat.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
