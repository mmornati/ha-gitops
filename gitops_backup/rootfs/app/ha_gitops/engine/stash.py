# SPDX-License-Identifier: Apache-2.0
"""Settles what a failed ``git stash pop`` left behind."""

import contextlib
from pathlib import Path

from ha_gitops.adapters.git import Git


def _blobs(git: Git, tree: str) -> dict[str, str]:
    """path -> blob id for every file in a tree, in one git call."""
    out: dict[str, str] = {}
    for record in git.records("ls-tree", "-r", "-z", tree):
        meta, _, path = record.partition("\t")
        out[path] = meta.split()[2]
    return out


def reconcile(git: Git, stash: str) -> bool:
    """Makes the live (stashed) version win, path by path.

    ``git stash pop`` failed and kept the entry. That usually means nothing is
    wrong: a merged backup PR brought back the very files that were stashed,
    so the untracked ones "already exist" and a moved file looks like a
    rename/delete conflict. Files pop merged cleanly are left alone.

    Returns True when a live file really differs from what upstream has.
    Raises ``GitError`` or ``OSError`` if a live file could not be restored:
    the caller must then keep the stash, the only copy left.
    """
    diverged = False
    head = _blobs(git, "HEAD")
    tracked = _blobs(git, stash)
    untracked = _blobs(git, f"{stash}^3") if git.rev(f"{stash}^3") else {}

    # Paths from ls-files, not git diff: diff refreshes the index as it exits,
    # and that lock would make the checkouts below fail. Each unmerged path
    # has one record per stage.
    unmerged = dict.fromkeys(r.partition("\t")[2] for r in git.records("ls-files", "-u", "-z"))
    for path in unmerged:
        source = stash if path in tracked else f"{stash}^3" if path in untracked else ""
        if not source:
            # Deleted on the box
            diverged = diverged or path in head
            with contextlib.suppress(FileNotFoundError):
                Path(git.repo, path).unlink()
            continue
        blob = tracked[path] if path in tracked else untracked[path]
        diverged = diverged or blob != head.get(path)
        git.run("--literal-pathspecs", "checkout", "-q", source, "--", path)

    for path, blob in untracked.items():
        if blob == head.get(path):
            continue
        diverged = diverged or path in head
        git.run("--literal-pathspecs", "checkout", "-q", f"{stash}^3", "--", path)
    return diverged
