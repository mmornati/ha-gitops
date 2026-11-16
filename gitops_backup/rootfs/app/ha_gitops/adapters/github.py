# SPDX-License-Identifier: Apache-2.0
"""The GitHub REST calls the app makes, in one place."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ha_gitops.adapters.http import HttpError, request_json

GitHubError = HttpError


@dataclass(frozen=True, slots=True)
class PullRequest:
    branch: str
    url: str
    number: str


class GitHub:
    def __init__(self, api: str, repo: str, token: str, timeout: float = 20) -> None:
        self.repo = repo
        self._base = f"{api.rstrip('/')}/repos/{repo}"
        self._timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ha-gitops",
        }

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        return request_json(
            method, self._base + path, headers=self._headers, body=body, timeout=self._timeout
        )

    def open_pulls(self, base: str) -> list[dict[str, Any]]:
        found = self._call("GET", f"/pulls?state=open&base={quote(base, safe='')}&per_page=100")
        return [p for p in found if isinstance(p, dict)] if isinstance(found, list) else []

    def create_pull(self, head: str, base: str, title: str, body: str) -> PullRequest:
        made = self._call(
            "POST", "/pulls", {"title": title, "body": body, "head": head, "base": base}
        )
        if not isinstance(made, dict) or not made.get("html_url") or not made.get("number"):
            raise GitHubError(0, "unexpected answer when opening the pull request")
        return PullRequest(head, str(made["html_url"]), str(made["number"]))

    def update_pull(self, number: str, title: str, body: str) -> None:
        self._call("PATCH", f"/pulls/{int(number)}", {"title": title, "body": body})

    def close_pull(self, number: str) -> None:
        self._call("PATCH", f"/pulls/{int(number)}", {"state": "closed"})

    def merge_pull(self, number: str, method: str) -> None:
        self._call("PUT", f"/pulls/{int(number)}/merge", {"merge_method": method})

    def check_runs(self, ref: str) -> dict[str, Any] | None:
        return self._dict(f"/commits/{quote(ref, safe='')}/check-runs")

    def combined_status(self, ref: str) -> dict[str, Any] | None:
        return self._dict(f"/commits/{quote(ref, safe='')}/status")

    def _dict(self, path: str) -> dict[str, Any] | None:
        try:
            found = self._call("GET", path)
        except GitHubError as err:
            if err.status:  # GitHub answered: no data for this ref
                return None
            raise
        return found if isinstance(found, dict) else None
