# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: a throwaway Home Assistant box and a fake GitHub + HA API.

Everything runs locally. "GitHub" is a bare repository reached through a git
``insteadOf`` rule, and the two HTTP APIs are one small server on localhost.
"""

import json
import os
import subprocess
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from ha_gitops.engine import runner
from ha_gitops.engine.context import RunContext

REPO = "local/test"
CLOSED_PORT = "http://127.0.0.1:9"  # nothing listens there: calls fail fast


def sh(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True)
    return done.stdout.strip()


class FakeApi:
    """GitHub under ``/gh`` and the Supervisor under ``/sup``; records every call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.check = "valid"
        self.pulls: list[dict[str, Any]] | None = None  # None: listing fails (502)
        self.create_ok = True
        self.merge: tuple[int, dict[str, Any]] = (200, {"merged": True})
        self.check_runs: Any = {"check_runs": []}
        self.statuses: Any = {"statuses": []}
        self.mqtt: dict[str, Any] | None = None
        self.fail: set[str] = set()  # path fragments answered with 500
        self._number = 0
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def _answer(self, status: int, payload: Any, raw: bytes | None = None) -> None:
                data = raw if raw is not None else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                api.calls.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": body,
                        "auth": self.headers.get("Authorization", ""),
                    }
                )
                status, payload = api.route(self.command, self.path, body)
                if status == 302:
                    self.send_response(302)
                    self.send_header("Location", payload)
                    self.end_headers()
                elif isinstance(payload, bytes):
                    self._answer(status, None, payload)
                else:
                    self._answer(status, payload)

            do_GET = do_POST = do_PATCH = do_PUT = _handle  # noqa: N815

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def route(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        if any(fragment in path for fragment in self.fail):
            return 500, {"message": "boom"}
        if path.startswith("/gh/redirect"):
            return 302, f"{self.url}/gh/elsewhere"
        if path.startswith("/gh/garbage"):
            return 200, b"not json"
        if path.startswith("/gh/empty"):
            return 200, b""
        base = f"/gh/repos/{REPO}"
        if path.startswith(f"{base}/pulls?"):
            return (502, {"message": "bad gateway"}) if self.pulls is None else (200, self.pulls)
        if path == f"{base}/pulls" and method == "POST":
            if not self.create_ok:
                return 422, {"message": "Validation Failed"}
            self._number += 1
            url = f"https://example/pr/{self._number}"
            if self.pulls is not None:
                self.pulls.append(
                    {
                        "head": {"ref": body["head"], "repo": {"full_name": REPO}},
                        "html_url": url,
                        "number": self._number,
                        "created_at": f"2026-01-01T00:00:{self._number:02d}Z",
                    }
                )
            return 201, {"html_url": url, "number": self._number}
        if path.startswith(f"{base}/pulls/") and method == "PUT":
            return self.merge
        if path.startswith(f"{base}/pulls/") and method == "PATCH":
            if body.get("state") == "closed" and self.pulls is not None:
                number = int(path.rsplit("/", 1)[1])
                self.pulls[:] = [p for p in self.pulls if p["number"] != number]
            return 200, {}
        if path.endswith("/check-runs"):
            return (200, self.check_runs) if self.check_runs is not None else (404, {})
        if path.endswith("/status") and path.startswith(base):
            return (200, self.statuses) if self.statuses is not None else (404, {})
        if path == "/sup/services/mqtt":
            return (200, {"data": self.mqtt}) if self.mqtt is not None else (404, {})
        if path == "/sup/core/api/config/core/check_config":
            return 200, {"result": self.check, "errors": "boom"}
        return 200, {}

    def count(self, method: str, fragment: str) -> int:
        return sum(1 for c in self.calls if c["method"] == method and fragment in c["path"])

    def bodies(self, method: str, fragment: str) -> list[Any]:
        return [c["body"] for c in self.calls if c["method"] == method and fragment in c["path"]]


class Box:
    """One Home Assistant box: /config, /data, and its GitHub remote."""

    def __init__(self, root: Path, api: FakeApi | None = None, **options: Any) -> None:
        self.root = root
        self.config, self.data = root / "config", root / "data"
        self.home, self.origin = root / "home", root / "origin.git"
        for folder in (self.config, self.data, self.home):
            folder.mkdir()
        self.api = api
        self.clock = datetime(2026, 1, 1, 12, 0, 0)
        self.options: dict[str, Any] = {
            "github_repo": REPO,
            "github_token": "test-token",
            "base_branch": "main",
            "branch_prefix": "auto-backup",
            "dry_run": False,
            "commit_name": "Test",
            "commit_email": "test@localhost",
            "signoff": True,
            **options,
        }
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "OPTIONS": str(self.data / "options.json"),
            "CONFIG_DIR": str(self.config),
            "DATA_DIR": str(self.data),
            "LOCK_WAIT": "30",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GITHUB_API": f"{api.url}/gh" if api else CLOSED_PORT,
            "SUPERVISOR_URL": f"{api.url}/sup" if api else CLOSED_PORT,
        }
        if api:
            self.env["SUPERVISOR_TOKEN"] = "sup-token"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        sh(
            "git",
            "config",
            "--global",
            f"url.{self.origin}.insteadOf",
            f"https://github.com/{REPO}.git",
            env=self.env,
        )
        sh("git", "config", "--global", "user.name", "Dev", env=self.env)
        sh("git", "config", "--global", "user.email", "dev@localhost", env=self.env)
        self.write("configuration.yaml", "homeassistant:\n  name: Home\n")
        self.write("automations.yaml", "[]\n")
        self.write("secrets.yaml", "wifi: abc\n")
        self.last: RunContext | None = None

    # ---- the engine --------------------------------------------------------

    def run(self, mode: str = "full", **env: str) -> int:
        """One engine run, in this process (so coverage sees it)."""
        self.data.joinpath("options.json").write_text(json.dumps(self.options))
        ctx = runner.build_context({**self.env, "GITOPS_MODE": mode, **env})
        self.clock += timedelta(minutes=1)
        now = self.clock
        ctx.now = lambda: now
        ctx.echo = lambda _line: None
        self.last = ctx
        return runner.run(ctx)

    # ---- files and git -----------------------------------------------------

    def write(self, name: str, content: str) -> Path:
        path = self.config / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def read(self, name: str) -> str:
        return (self.config / name).read_text()

    def git(self, *args: str) -> str:
        return sh("git", *args, cwd=self.config, env=self.env)

    def ogit(self, *args: str) -> str:
        return sh("git", "-c", "safe.bareRepository=all", *args, cwd=self.origin, env=self.env)

    def remote_branches(self, prefix: str = "auto-backup/") -> list[str]:
        refs = self.ogit("for-each-ref", "--format=%(refname:short)", "refs/heads").split()
        return sorted(r for r in refs if r.startswith(prefix))

    def remote_files(self, ref: str) -> list[str]:
        return self.ogit("ls-tree", "-r", "--name-only", ref).splitlines()

    @property
    def dev(self) -> Path:
        """A developer's clone, standing in for changes made on GitHub."""
        path = self.root / "dev"
        if not path.exists():
            sh("git", "clone", "-q", str(self.origin), str(path), env=self.env)
        return path

    def dgit(self, *args: str) -> str:
        return sh("git", *args, cwd=self.dev, env=self.env)

    def dev_push(self, name: str, content: str) -> str:
        """A change merged on GitHub; returns its commit id."""
        self.dgit("pull", "-q", "origin", "main")
        (self.dev / name).write_text(content)
        self.dgit("add", "-A")
        self.dgit("commit", "-q", "-m", f"change {name}")
        self.dgit("push", "-q", "origin", "main")
        return self.dgit("rev-parse", "HEAD")

    def merge_backup(self) -> None:
        """Merges the open backup branch into main, as the PR merge would."""
        branch = self.open_pr()[0]
        self.dgit("pull", "-q", "origin", "main")
        self.dgit("fetch", "-q", "origin")
        self.dgit("merge", "-q", "--no-ff", "-m", "Merge backup PR", f"origin/{branch}")
        self.dgit("push", "-q", "origin", "main")

    # ---- state -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads((self.data / "status.json").read_text())
        return loaded

    def line(self) -> str:
        return (self.config / ".gitops_backup_status").read_text().strip()

    def log(self) -> str:
        return (self.data / "gitops_backup.log").read_text()

    def open_pr(self) -> list[str]:
        return (self.data / "open_pr").read_text().strip().split("\t")

    def settled(self) -> None:
        """No stash and no unmerged index entry is left behind."""
        assert self.git("stash", "list") == ""
        assert self.git("ls-files", "-u") == ""


@pytest.fixture
def api() -> Iterator[FakeApi]:
    server = FakeApi()
    yield server
    server.close()


@pytest.fixture
def box(tmp_path: Path) -> Box:
    """A bootstrapped box whose GitHub API is unreachable."""
    made = Box(tmp_path)
    assert made.run() == 0
    return made


@pytest.fixture
def abox(tmp_path: Path, api: FakeApi) -> Box:
    """A bootstrapped box with a working GitHub API and Home Assistant."""
    made = Box(tmp_path, api)
    assert made.run() == 0
    return made
