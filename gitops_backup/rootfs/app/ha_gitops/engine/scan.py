# SPDX-License-Identifier: Apache-2.0
"""Content-level backstop: keeps secret-like files out of a backup commit.

What is scanned is the blob in the git index, which is exactly what would be
committed, not the file on disk (it can change between ``git add`` and the
scan). Fails closed: if the scan cannot run or a file cannot be unstaged, the
caller must not push.
"""

from collections.abc import Iterator
from typing import IO

from ha_gitops.adapters.git import Git, GitError
from ha_gitops.engine.context import RunContext
from ha_gitops.rules import secret_rules
from ha_gitops.rules.secret_rules import CHUNK

UNREADABLE = "content could not be read"
GITLINK = "160000"  # a nested repository: no file content is committed


class ScanError(Exception):
    """The scan could not run to the end; nothing may be pushed."""


def _read_exact(stream: IO[bytes], size: int) -> Iterator[bytes]:
    while size > 0:
        data = stream.read(min(size, CHUNK))
        if not data:
            raise OSError("git cat-file ended early")
        size -= len(data)
        yield data


def scan_index(git: Git, paths: list[str]) -> list[tuple[str, str]]:
    """(path, rule label) for each path whose staged content trips a rule.

    Files of any size are scanned, in chunks, through one ``cat-file`` process.
    A path that cannot be read from the index is reported too.
    """
    if not paths:
        return []
    entries: dict[str, tuple[str, str]] = {}
    for record in git.records("ls-files", "-s", "-z"):
        meta, _, path = record.partition("\t")
        mode, oid, stage = meta.split()
        if stage == "0":
            entries[path] = (mode, oid)
    found: list[tuple[str, str]] = []
    cat = git.popen("cat-file", "--batch")
    if cat.stdin is None or cat.stdout is None:  # pragma: no cover - both are pipes
        raise OSError("git cat-file has no pipes")
    try:
        for path in paths:
            mode, oid = entries.get(path, ("", ""))
            if mode == GITLINK:
                continue
            label: str | None = UNREADABLE
            if oid:
                cat.stdin.write(oid.encode() + b"\n")
                cat.stdin.flush()
                header = cat.stdout.readline().split()
                if len(header) == 3 and header[1] == b"blob":
                    label = secret_rules.scan_chunks(_read_exact(cat.stdout, int(header[2])))
                    cat.stdout.read(1)  # the newline after the content
                elif len(header) == 3:  # not a blob: skip its content
                    for _ in _read_exact(cat.stdout, int(header[2]) + 1):
                        pass
            if label:
                found.append((path, label))
    finally:
        cat.stdin.close()
        cat.stdout.close()
        cat.wait()
    return found


def hold_back_secrets(ctx: RunContext) -> list[str]:
    """Unstages every staged file that looks like it holds a secret.

    Everything that differs from the base branch is scanned, not only what
    this run adds: on top of an open backup PR that includes the files the PR
    already has, which an older version may have pushed unscanned.
    """
    git, base = ctx.git, ctx.settings.base_ref
    against = [base] if git.rev(base) else []
    try:
        # Every staged entry that is not a deletion: a filter on "added or
        # modified" would skip a symlink replaced by a regular file, which git
        # reports as a type change. --no-renames: a file renamed and edited at
        # once counts as added, with its new content.
        staged = git.records(
            "diff", "--cached", "--no-renames", "--name-only", "--diff-filter=d", "-z", *against
        )
        found = scan_index(git, staged)
    except (GitError, OSError, ValueError) as err:
        ctx.log(f"❌ Secret scan could not run; not backing up ({type(err).__name__})")
        raise ScanError from err

    held = [path for path, _ in found]
    in_base = {path: git.rev(f"{base}:{path}") if against else "" for path in held}
    for path, label in found:
        # Only the rule label and the path are logged, never the match
        ctx.log(f"⚠ Secret-like content ({label}) in {path} — held back from backup")
    try:
        # Back to what the base branch has, or out of the index when the base
        # branch does not have the file. On top of an open backup PR this also
        # takes a file out of the PR if an earlier commit put it there.
        git.with_paths("reset", "-q", base, paths=[p for p in held if in_base[p]])
        git.with_paths(
            "rm", "-q", "--cached", "--ignore-unmatch", paths=[p for p in held if not in_base[p]]
        )
        # A held file that is somehow still staged must never be pushed
        for path in held:
            entry = git.out("--literal-pathspecs", "ls-files", "-s", "--", path).split()
            if (entry[1] if len(entry) > 1 else "") != in_base[path]:
                ctx.log(f"❌ Could not unstage {path}; not backing up")
                raise ScanError
    except GitError as err:
        ctx.log("❌ Could not unstage a held file; not backing up")
        raise ScanError from err
    # Remembered for the panel, also across runs that do not scan (sync)
    ctx.state.set_held(held)
    ctx.report.held = held
    return held


def report_held(ctx: RunContext) -> None:
    """Tells Home Assistant which files a run held back, if any."""
    held = ctx.report.held
    if not held:
        return
    listing = "\n".join(f"- {path}" for path in held)
    ctx.hass.notify(
        "HA GitOps: files held back",
        "These files look like they contain a secret, so they were NOT backed up to GitHub:\n\n"
        f"{listing}\n\nMove the value to secrets.yaml and reference it with !secret, or add "
        "the file to .gitignore.",
    )
    ctx.hass.fire_event("secrets_held", {"files": held})
