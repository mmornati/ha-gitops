# SPDX-License-Identifier: Apache-2.0
"""GitHub check results folded into one word, and the merge gate."""

from typing import Any


def can_merge(pr_number: object, checks: str, running: bool) -> bool:
    """The open backup PR may be merged from the panel.

    "none" means the repository has no checks at all; "unknown" (GitHub could
    not be asked), "pending" and "failure" all block.
    """
    return bool(pr_number) and not running and checks in ("success", "none")


def summarize_checks(check_runs: dict[str, Any] | None, status: dict[str, Any] | None) -> str:
    """Folds GitHub check runs + commit statuses into one word."""
    states = []
    for run in (check_runs or {}).get("check_runs", []):
        if run.get("status") != "completed":
            states.append("pending")
        elif run.get("conclusion") in ("success", "neutral", "skipped"):
            states.append("success")
        else:
            states.append("failure")
    for st in (status or {}).get("statuses", []):
        states.append({"success": "success", "pending": "pending"}.get(st.get("state"), "failure"))
    if not states:
        return "none"
    if "failure" in states:
        return "failure"
    if "pending" in states:
        return "pending"
    return "success"
