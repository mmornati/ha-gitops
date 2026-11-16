# SPDX-License-Identifier: Apache-2.0
"""Rules, settings and adapters, one small piece at a time."""

import fcntl
import json
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from ha_gitops.adapters import hass_ws
from ha_gitops.adapters.git import Git, GitError, git_env
from ha_gitops.adapters.github import GitHub, GitHubError, PullRequest
from ha_gitops.adapters.hass import HassError, HomeAssistant
from ha_gitops.adapters.http import HttpError, request_json
from ha_gitops.adapters.state import StateStore
from ha_gitops.engine import scan
from ha_gitops.rules import apply, ignore, pr_body
from ha_gitops.rules.secret_rules import CHUNK
from ha_gitops.settings import REF_RE, Settings, load_json

from tests.conftest import FakeApi, sh

# ---- rules -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "action"),
    [
        ("automations.yaml", "automation/reload"),
        ("automations/night.yaml", "automation/reload"),
        ("scripts.yaml", "script/reload"),
        ("scenes/evening.yaml", "scene/reload"),
        ("groups.yaml", "group/reload"),
        ("templates/power.yaml", "template/reload"),
        ("customize.yaml", "homeassistant/reload_core_config"),
        ("themes/dark.yaml", "frontend/reload_themes"),
        ("blueprints/automation/x/y.yaml", "automation/reload"),
        ("blueprints/script/x.yaml", "script/reload"),
        ("blueprints/template/x.yaml", "none"),
        ("python_scripts/x.py", "python_script/reload"),
        ("input_boolean.yaml", "homeassistant/reload_all"),
        ("dashboards/main.yaml", "dashboards"),
        ("dashboards/img/logo.png", "none"),
        ("ui-lovelace.yaml", "dashboards"),
        ("esphome/plug.yaml", "none"),
        ("zigbee2mqtt/configuration.yaml", "none"),
        ("configuration.yaml", "restart"),
        ("packages/energy.yaml", "restart"),
        ("custom_components/x/sensor.py", "restart"),
        ("README.md", "none"),
        ("www/logo.png", "none"),
        ("sensors.yaml", "restart"),
        ("notes.txt", "none"),
    ],
)
def test_changed_paths_map_to_the_smallest_action(path: str, action: str) -> None:
    assert apply.classify(path) == action


def test_apply_plan() -> None:
    auto = apply.plan("auto", ["automations.yaml", "automations/x.yaml", "ui-lovelace.yaml"])
    assert auto == apply.ApplyPlan(("automation/reload",), False, True)
    assert not apply.plan("auto", ["ui-lovelace.yaml"]).needs_config_check
    assert apply.plan("auto", ["README.md"]).empty
    assert apply.plan("restart", []).restart
    reload = apply.plan("reload", ["a.yaml"])
    assert reload.services == ("homeassistant/reload_all",)
    assert reload.refresh_dashboards
    assert not apply.plan("reload", ["a.py"]).refresh_dashboards


def test_pr_body_groups_files_and_stays_under_the_github_limit() -> None:
    """A backup that removes an integration touches thousands of files."""
    changes = [
        ("D", f"custom_components/hacs/frontend/chunk.{i}.long-hashed-name.js.gz")
        for i in range(3000)
    ]
    changes.append(("A", "automations.yaml"))
    body = pr_body.render(changes, when="2026-01-01 12:00", trigger="watcher")
    assert len(body) <= 65536
    assert "more files, not listed" in body
    assert "### Automations\n\n- `automations.yaml` (added)" in body
    assert "(trigger: watcher)" in body
    small = pr_body.render(
        [("M", "scripts.yaml"), ("D", "x.txt")], when="now", trigger="t", held=["mqtt.yaml"]
    )
    assert "more files" not in small
    assert "### Scripts\n\n- `scripts.yaml` (modified)" in small
    assert "### Other\n\n- `x.txt` (deleted)" in small
    assert small.endswith("- `mqtt.yaml`")
    assert "more files" not in pr_body.render([], when="now", trigger="t")


def test_ignore_lists_have_one_source() -> None:
    seeded = ignore.seed_gitignore().splitlines()
    for pattern in ignore.NEVER_COMMIT:
        assert pattern in seeded
    assert "*.db" in seeded
    once = ignore.managed_exclude("# mine\nlocal.txt\n")
    assert once.startswith("# mine\nlocal.txt\n" + ignore.EXCLUDE_BEGIN)
    assert ignore.managed_exclude(once) == once  # idempotent
    assert once.count("secrets.yaml\n") == 1


# ---- settings ----------------------------------------------------------------


def test_settings_are_clamped_and_defaulted(tmp_path: Path) -> None:
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps(
            {
                "github_repo": " o/r ",
                "github_token": "t",
                "interval_hours": 9999,
                "poll_seconds": "abc",
                "apply_after_pull": "bogus",
                "sync_mode": "pull",
                "signoff": "false",
                "dry_run": False,
            }
        )
    )
    env = {
        "OPTIONS": str(options),
        "DRY_RUN_OVERRIDE": "true",
        "APPLY_OVERRIDE": "off",
        "SUPERVISOR_URL": "https://sup.example/",
        "LOCK_WAIT": "x",
    }
    s = Settings.load(env)
    assert (s.github_repo, s.interval_hours, s.poll_seconds) == ("o/r", 168, 0)
    assert (s.apply_after_pull, s.sync_mode, s.signoff, s.dry_run) == ("off", "pull", False, True)
    assert s.lock_wait == 900
    assert s.ha_websocket == "wss://sup.example/core/websocket"
    assert s.ha_api == "https://sup.example/core/api"
    assert s.problem() is None
    assert "t" not in repr(s).split("github_repo")[1][:40]
    assert "github_token" not in repr(s)
    options.write_text("[1, 2]")
    assert Settings.load({"OPTIONS": str(options)}).problem() is not None
    assert load_json(tmp_path / "missing.json", {"d": 1}) == {"d": 1}


@pytest.mark.parametrize("ref", ["-x", "a..b", "a//b", "a b", "a/", "a.", "a;b", ""])
def test_unsafe_branch_names_are_refused(ref: str) -> None:
    assert not REF_RE.match(ref)
    assert REF_RE.match("auto-backup/2026-01-01-120000")


# ---- http, GitHub, Home Assistant -----------------------------------------------


def test_http_errors_are_one_exception_and_redirects_are_not_followed(api: FakeApi) -> None:
    headers = {"Authorization": "Bearer t"}
    with pytest.raises(HttpError) as refused:
        request_json("GET", f"{api.url}/gh/redirect", headers=headers)
    assert refused.value.status == 302
    assert api.count("GET", "/gh/elsewhere") == 0  # the token did not travel on
    with pytest.raises(HttpError, match="not JSON"):
        request_json("GET", f"{api.url}/gh/garbage", headers=headers)
    assert request_json("GET", f"{api.url}/gh/empty", headers=headers) is None
    with pytest.raises(HttpError, match="unsupported URL scheme"):
        request_json("GET", "file:///etc/passwd", headers=headers)
    with pytest.raises(HttpError) as down:
        request_json("GET", "http://127.0.0.1:9/x", headers=headers, timeout=2)
    assert down.value.status == 0
    api.fail = {"/gh/x"}
    with pytest.raises(HttpError, match="HTTP 500: boom"):
        request_json("GET", f"{api.url}/gh/x", headers=headers)


def test_github_client(api: FakeApi) -> None:
    gh = GitHub(f"{api.url}/gh", "local/test", "tok")
    with pytest.raises(GitHubError):
        gh.open_pulls("main")
    api.pulls = []
    assert gh.open_pulls("main") == []
    made = gh.create_pull("auto-backup/x", "main", "t", "b")
    assert made == PullRequest("auto-backup/x", "https://example/pr/1", "1")
    assert gh.check_runs("auto-backup/x") == {"check_runs": []}
    assert "auto-backup%2Fx" in api.calls[-1]["path"]
    api.check_runs = None
    assert gh.check_runs("auto-backup/x") is None  # GitHub answered: nothing for this ref
    assert gh.combined_status("auto-backup/x") == {"statuses": []}
    gh.merge_pull("1", "squash")
    assert api.bodies("PUT", "/pulls/1/merge") == [{"merge_method": "squash"}]
    api.create_ok = False
    with pytest.raises(GitHubError, match="Validation Failed"):
        gh.create_pull("b", "main", "t", "b")
    with pytest.raises(ValueError, match="invalid literal"):
        gh.close_pull("1/../../x")  # a PR number is always a number
    with pytest.raises(GitHubError):
        GitHub("http://127.0.0.1:9", "local/test", "tok", timeout=2).check_runs("x")
    assert api.calls[0]["auth"] == "Bearer tok"


def test_home_assistant_client(api: FakeApi) -> None:
    ha = HomeAssistant(f"{api.url}/sup", "sup-token")
    assert ha.check_config().valid
    api.check = "invalid"
    assert ha.check_config().errors == "boom"
    ha.call_service("automation/reload")
    assert api.calls[-1]["path"] == "/sup/core/api/services/automation/reload"
    assert ha.fire_event("applied", {"a": 1})
    assert api.calls[-1]["path"] == "/sup/core/api/events/ha_gitops_applied"
    assert ha.notify("t", "m")
    assert api.calls[-1]["body"]["notification_id"] == "ha_gitops"
    ha.set_state("sensor.ha_gitops_status", "success", {"x": 1})
    assert api.calls[-1]["body"] == {"state": "success", "attributes": {"x": 1}}
    with pytest.raises(HassError):  # the Supervisor offers no broker
        ha.mqtt_service()
    api.mqtt = {"host": "broker", "port": 1883}
    assert ha.mqtt_service() == {"host": "broker", "port": 1883}
    api.fail = {"/events/", "/persistent_notification/"}
    assert not ha.fire_event("applied")
    assert not ha.notify("t", "m")

    off = HomeAssistant(f"{api.url}/sup", "")
    assert not off.available
    assert off.mqtt_service() is None
    with pytest.raises(HassError):
        off.restart()
    assert not off.fire_event("x")


async def test_dashboard_refresh_asks_for_every_yaml_dashboard() -> None:
    asked: list[object] = []

    async def socket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        if auth.get("access_token") != "sup-token":
            await ws.send_json({"type": "auth_invalid"})
            return ws
        await ws.send_json({"type": "auth_ok"})
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                break
            data = json.loads(msg.data)
            await ws.send_json({"id": 999, "type": "event"})  # unrelated traffic
            if data["type"] == "lovelace/dashboards/list":
                result: object = [
                    {"url_path": "energy", "mode": "yaml"},
                    {"url_path": "ui", "mode": "storage"},
                ]
            else:
                asked.append(data["url_path"])
                result = {}
            await ws.send_json(
                {"id": data["id"], "type": "result", "success": True, "result": result}
            )
        return ws

    app = web.Application()
    app.add_routes([web.get("/core/websocket", socket)])
    async with TestServer(app) as server:
        url = str(server.make_url("/core/websocket")).replace("http", "ws", 1)
        assert await hass_ws.refresh_dashboards(url, "sup-token") == 1
        assert asked == [None, "energy"]
        with pytest.raises(OSError, match="rejected"):
            await hass_ws.refresh_dashboards(url, "wrong")


# ---- state on disk -----------------------------------------------------------


def test_state_store_round_trips(tmp_path: Path) -> None:
    store = StateStore(tmp_path, tmp_path)
    assert store.open_pr() is None
    assert store.read_status() == {}
    store.set_open_pr(PullRequest("auto-backup/x", "https://example/pr/1", "1"))
    assert store.open_pr() == PullRequest("auto-backup/x", "https://example/pr/1", "1")
    (tmp_path / "open_pr").write_text("auto-backup/y\n")  # written before a PR existed
    assert store.open_pr() == PullRequest("auto-backup/y", "", "")
    store.set_open_pr(None)
    assert store.open_pr() is None
    store.set_held(["a.yaml", "odd\tname.yaml"])
    assert store.held() == ["a.yaml", "odd\tname.yaml"]
    store.set_held([])
    assert store.held() == []
    store.set_applied_head("abc")
    store.set_invalid_head("def")
    assert (store.applied_head(), store.invalid_head()) == ("abc", "def")
    store.set_invalid_head(None)
    assert store.invalid_head() == ""
    store.set_controller_state({"paused": True})
    assert store.controller_state() == {"paused": True}
    (tmp_path / "status.json").write_text("[]")
    assert store.read_status() == {}
    assert store.tail_log(10) == []
    store.write_status({"state": "success", "detail": "synced", "extra": "abc"})
    assert (tmp_path / ".gitops_backup_status").read_text() == "success:synced:abc\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_run_lock_waits_then_gives_up(tmp_path: Path) -> None:
    store = StateStore(tmp_path, tmp_path)
    with store.run_lock(0) as first:
        assert first
        with (tmp_path / "gitops.lock").open("w") as other, pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with (tmp_path / "gitops.lock").open("w") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        with store.run_lock(0.2) as busy:
            assert not busy


# ---- git ---------------------------------------------------------------------


def repo(tmp_path: Path) -> Git:
    settings = Settings(config_dir=tmp_path, data_dir=tmp_path, github_token="tok")
    env = git_env(
        settings,
        {
            "PATH": __import__("os").environ["PATH"],
            "HOME": str(tmp_path),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "init.defaultBranch",
            "GIT_CONFIG_VALUE_0": "main",
        },
    )
    git = Git(tmp_path, env)
    git.run("init", "-q")
    return git


def test_git_wrapper(tmp_path: Path) -> None:
    git = repo(tmp_path)
    assert git.env["GIT_CONFIG_KEY_0"] == "init.defaultBranch"  # the caller's config is kept
    assert git.env["GIT_CONFIG_KEY_1"] == "safe.directory"
    assert git.env["GITOPS_TOKEN"] == "tok"
    assert git.out("branch", "--show-current") == "main"
    assert git.rev("HEAD") == ""
    assert not git.ok("rev-parse", "nope")
    with pytest.raises(GitError) as failed:
        git.run("checkout", "-q", "nope")
    assert failed.value.command == "checkout"
    assert "git checkout failed" in str(failed.value)
    git.with_paths("add", paths=[])  # nothing to do, no process
    (tmp_path / "-n").write_text("x")  # a name that looks like an option
    (tmp_path / "*.yaml").write_text("x")  # and one that looks like a pattern
    (tmp_path / "a.yaml").write_text("x")
    git.with_paths("add", paths=["-n", "*.yaml"])
    assert git.records("ls-files", "-z") == ["*.yaml", "-n"]
    bad = git_env(Settings(config_dir=tmp_path, data_dir=tmp_path), {"GIT_CONFIG_COUNT": "x"})
    assert bad["GIT_CONFIG_KEY_0"] == "safe.directory"


def test_askpass_hands_git_the_token_from_the_environment(tmp_path: Path) -> None:
    from ha_gitops.adapters.git import ASKPASS

    env = {"GITOPS_TOKEN": "tok", "PATH": "/usr/bin:/bin"}
    assert sh(str(ASKPASS), "Username for 'https://github.com': ", env=env) == "x-access-token"
    assert sh(str(ASKPASS), "Password for 'https://github.com': ", env=env) == "tok"


# ---- the content scan against a real index ----------------------------------------


def test_scan_reads_the_staged_blob_not_the_file_on_disk(tmp_path: Path) -> None:
    git = repo(tmp_path)
    odd = "odd\tname .yaml"
    (tmp_path / odd).write_text("password: SuperSecretPassw0rd\n")
    (tmp_path / "ok.yaml").write_text("password: !secret pw\n")
    (tmp_path / "swap.yaml").write_text("token: ghp_" + "a" * 36 + "\n")
    git.run("add", "-A")
    (tmp_path / "swap.yaml").write_text("clean\n")  # edited after staging
    found = scan.scan_index(git, [odd, "ok.yaml", "swap.yaml"])
    assert found == [(odd, "inline credential"), ("swap.yaml", "GitHub token")]
    assert scan.scan_index(git, []) == []


def test_scan_fails_closed_and_reads_large_files_in_chunks(tmp_path: Path) -> None:
    git = repo(tmp_path)
    (tmp_path / "big.yaml").write_bytes(b"#" * (CHUNK + 100) + b"\npassword: SuperSecretPassw0rd\n")
    git.run("add", "-A")
    # A nested repository is a gitlink: no content of its own is committed
    blob = git.out("hash-object", "-w", "big.yaml")
    git.run("update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},nested")
    tree = git.out("write-tree")
    git.run("update-index", "--add", "--cacheinfo", f"100644,{tree},tree-as-file")
    assert blob
    found = scan.scan_index(git, ["big.yaml", "not-in-index.yaml", "nested", "tree-as-file"])
    # What is not a readable blob is reported, so the caller holds it back
    assert found == [
        ("big.yaml", "inline credential"),
        ("not-in-index.yaml", scan.UNREADABLE),
        ("tree-as-file", scan.UNREADABLE),
    ]


def test_scan_stream_ending_early_is_an_error() -> None:
    import io

    with pytest.raises(OSError, match="ended early"):
        list(scan._read_exact(io.BytesIO(b"abc"), 10))


def test_engine_starts_without_loading_the_web_stack() -> None:
    """An engine run is a fresh process each time: it must stay cheap to start."""
    code = (
        "import sys, ha_gitops.engine.runner; "
        "sys.exit(any(m.startswith(('aiohttp', 'paho', 'asyncio')) for m in sys.modules))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        cwd=Path(__file__).parent.parent / "gitops_backup/rootfs/app",
    )
    assert done.returncode == 0, done.stderr
