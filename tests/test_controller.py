# SPDX-License-Identifier: Apache-2.0
"""The controller against a local repository: queue, triggers, panel, webhook."""

import asyncio
import hashlib
import hmac
import io
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer
from ha_gitops.adapters.github import PullRequest
from ha_gitops.controller import __main__ as entry
from ha_gitops.controller import mqtt, service, triggers, web
from ha_gitops.controller.app import Controller
from ha_gitops.controller.jobs import Job, JobQueue
from ha_gitops.rules.timing import Debounce
from ha_gitops.settings import Settings

from tests.conftest import Box, FakeApi

CSRF = {"X-Requested-With": "ha-gitops"}
BODY = json.dumps({"ref": "refs/heads/main"}).encode()


def signed(body: bytes = BODY, secret: bytes = b"s3cret") -> dict[str, str]:
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return {"X-GitHub-Event": "push", "X-Hub-Signature-256": f"sha256={digest}"}


@pytest.fixture
async def ctl(abox: Box, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Controller]:
    """A controller on a bootstrapped box, with fake GitHub and Home Assistant."""
    abox.options.update(
        signoff=False,
        webhook_secret="s3cret",
        sync_mode="both",
        poll_seconds=60,
        debounce_seconds=120,
        mqtt_discovery=False,
    )
    (abox.data / "options.json").write_text(json.dumps(abox.options))
    for key, value in abox.env.items():
        monkeypatch.setenv(key, value)
    made = Controller(Settings.load())
    made.recheck_delay = 0
    made.box = abox  # type: ignore[attr-defined]
    yield made
    for task in list(made._tasks):
        task.cancel()


async def panel(ctl: Controller) -> TestClient[Any, Any]:
    client = TestClient(TestServer(web.panel_app(ctl)))
    await client.start_server()
    return client


async def hook(ctl: Controller) -> TestClient[Any, Any]:
    client = TestClient(TestServer(web.webhook_app(ctl)))
    await client.start_server()
    return client


# ---- queue -------------------------------------------------------------------


async def test_queue_runs_the_most_important_job_and_full_covers_sync() -> None:
    queue = JobQueue()
    queue.request("sync", "poll")
    queue.request("full", "watcher")
    queue.request("full", "schedule")  # asked twice, queued once
    queue.request("discard", "panel")
    queue.request("revert", "panel", "abc1234")
    queue.request("revert", "panel", "abc1234")
    assert len(queue) == 4
    assert "revert" in queue
    assert "sync" in queue
    await asyncio.wait_for(queue.wait(), 1)
    assert queue.next() == Job("revert", "panel", {"REVERT_SHA": "abc1234"})
    assert queue.next() == Job("discard", "panel")
    assert queue.next() == Job("full", "watcher")
    assert queue.next() is None
    for n in range(50):
        queue.request("revert", "panel", f"{n:07x}")
    assert len(queue) == 20  # a flood of revert requests is capped


# ---- webhook -----------------------------------------------------------------


async def test_webhook_signature_gate(ctl: Controller) -> None:
    async with await hook(ctl) as client:
        bad = dict(signed(), **{"X-Hub-Signature-256": "sha256=deadbeef"})
        assert (await client.post("/webhook", data=BODY, headers=bad)).status == 401
        assert "sync" not in ctl.queue
        r = await client.post("/webhook", data=BODY, headers=signed())
        assert (r.status, await r.text()) == (202, "sync queued")
        assert "sync" in ctl.queue
        ping = dict(signed(b"{}"), **{"X-GitHub-Event": "ping"})
        assert await (await client.post("/webhook", data=b"{}", headers=ping)).text() == "pong"
        for junk in (b"{", b"[1]"):
            assert (await client.post("/webhook", data=junk, headers=signed(junk))).status == 400
        other = json.dumps({"ref": "refs/heads/other"}).encode()
        r = await client.post("/webhook", data=other, headers=signed(other))
        assert await r.text() == "ignored"
        assert (await client.get("/webhook")).status == 405
        assert (await client.get("/api/state")).status == 404  # the panel is not on this port


async def test_paused_webhook_is_ignored(ctl: Controller) -> None:
    ctl.flags["paused"] = True
    async with await hook(ctl) as client:
        r = await client.post("/webhook", data=BODY, headers=signed())
        assert await r.text() == "ignored"
    assert "sync" not in ctl.queue


async def test_bad_webhook_signatures_are_rate_limited(ctl: Controller) -> None:
    async with await hook(ctl) as client:
        bad = dict(signed(), **{"X-Hub-Signature-256": "sha256=bad"})
        codes = [(await client.post("/webhook", data=BODY, headers=bad)).status for _ in range(12)]
        assert codes == [401] * 10 + [429] * 2
        # A signed delivery still gets through
        assert (await client.post("/webhook", data=BODY, headers=signed())).status == 202


# ---- panel -------------------------------------------------------------------


async def test_panel_api(ctl: Controller) -> None:
    async with await panel(ctl) as client:
        state = await (await client.get("/api/state")).json()
        assert len(state["local_head"]) == 40
        assert state["repo"] == "local/test"
        assert state["webhook"] is True
        history = await (await client.get("/api/history")).json()
        assert history["commits"][-1]["subject"].startswith("chore: initial")
        assert history["repo_url"] == "https://github.com/local/test"
        log = await (await client.get("/api/log")).json()
        assert any("Bootstrap complete" in line for line in log["lines"])
        r = await client.post("/api/command", json={"command": "backup"})
        assert r.status == 403  # no CSRF header
        r = await client.post("/api/command", json={"command": "backup"}, headers=CSRF)
        assert r.status == 200
        assert "full" in ctl.queue
        r = await client.post("/api/command", json={"command": "rm"}, headers=CSRF)
        assert r.status == 400
        r = await client.post("/api/command", data=b"{", headers=CSRF)
        assert (await r.json())["result"] == "bad json"
        r = await client.post("/api/command", data=b"x" * 100_000, headers=CSRF)
        assert r.status == 413  # a command is small


async def test_panel_is_only_served_to_the_ingress_proxy(
    ctl: Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "INGRESS_PEERS", frozenset({"172.30.32.2"}))
    async with await panel(ctl) as client:
        assert (await client.get("/")).status == 403
        assert (await client.get("/api/state")).status == 403


async def test_panel_is_never_served_from_a_stale_cache(ctl: Controller) -> None:
    async with await panel(ctl) as client:
        page = await client.get("/")
        assert page.status == 200
        assert page.headers["Cache-Control"] == "no-cache"
        assert page.headers["X-Content-Type-Options"] == "nosniff"
        again = await client.get("/", headers={"If-None-Match": page.headers["ETag"]})
        assert again.status == 304  # so a revalidation is cheap
        assert again.headers["Cache-Control"] == "no-cache"
        assert (await client.get("/api/state")).headers["Cache-Control"] == "no-store"


async def test_drift_lists_new_files_and_hides_held_content(ctl: Controller) -> None:
    box: Box = ctl.box  # type: ignore[attr-defined]
    box.write("new file.yaml", "fresh: 1\n")
    box.write("mqtt.yaml", "password: SuperSecretPassw0rd\n")
    box.write("configuration.yaml", "homeassistant:\n  name: Edited\n")
    status = box.status()
    (box.data / "status.json").write_text(json.dumps(dict(status, held=["mqtt.yaml"])))
    async with await panel(ctl) as client:
        snap = await (await client.get("/api/diff")).json()
        state = await (await client.get("/api/state")).json()
    files = {f["path"]: f for f in snap["files"]}
    assert files["new file.yaml"]["status"] == "added"
    assert files["configuration.yaml"]["status"] == "modified"
    assert files["mqtt.yaml"]["held"]
    assert not files["new file.yaml"]["in_pr"]
    assert "+fresh: 1" in snap["diff"]  # untracked files get a diff
    assert "name: Edited" in snap["diff"]
    assert "SuperSecret" not in snap["diff"]  # held content never leaves the box
    assert (state["pending"], state["pending_new"], state["held"]) == (3, 2, ["mqtt.yaml"])
    # The scratch index is gone and the real one was not touched
    assert not (box.data / "panel.index").exists()
    assert box.git("diff", "--cached", "--name-only") == ""


async def test_drift_knows_what_the_open_pr_already_has(ctl: Controller) -> None:
    box: Box = ctl.box  # type: ignore[attr-defined]
    box.write("in_pr.yaml", "1\n")
    box.run()
    box.write("later.yaml", "2\n")
    await ctl.pulls.refresh()
    snap = await ctl.snapshot()
    files = {f["path"]: f["in_pr"] for f in snap["files"]}
    assert files == {"in_pr.yaml": True, "later.yaml": False}
    # Cached until something changes; never rebuilt while a run rewrites the tree
    box.write("third.yaml", "3\n")
    assert await ctl.snapshot() is snap
    ctl.running = True
    ctl.drift.touch()
    assert await ctl.snapshot() is snap
    ctl.running = False
    assert len((await ctl.snapshot())["files"]) == 3


async def test_drift_is_empty_without_a_repository_or_during_a_first_run(
    tmp_path: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = Box(tmp_path, api)
    (box.data / "options.json").write_text(json.dumps(box.options))
    for key, value in box.env.items():
        monkeypatch.setenv(key, value)
    ctl = Controller(Settings.load())
    assert (await ctl.snapshot())["files"] == []
    ctl.running = True
    assert (await ctl.snapshot())["files"] == []
    state = await ctl.build_state()
    assert (state["state"], state["local_head"]) == ("running", "")
    assert await ctl.git.run("status") == (1, "")


# ---- commands ----------------------------------------------------------------


async def test_commands(ctl: Controller) -> None:
    assert await ctl.command("pause", "test") == "ok"
    assert ctl.store.controller_state()["paused"] is True
    assert await ctl.command("resume", "test") == "ok"
    assert await ctl.command("auto_apply_off", "test") == "ok"
    assert ctl.flags["auto_apply"] is False
    assert await ctl.command("auto_apply_on", "test") == "ok"
    assert await ctl.command("refresh", "test") == "ok"
    assert await ctl.command("nonsense", "test") == "unknown command"
    assert await ctl.command({"command": "revert", "arg": "ABC1234"}, "panel") == "ok"
    assert ctl.queue.reverts == ["abc1234"]
    for cmd, mode in (("sync", "sync"), ("dry_run", "dry_run"), ("backup", "full")):
        assert await ctl.command(cmd, "stdin") == "ok"
        assert mode in ctl.queue
    # State is published as one REST sensor when there is no MQTT
    api: FakeApi = ctl.box.api  # type: ignore[attr-defined]
    sensor = api.bodies("POST", "/states/sensor.ha_gitops_status")[-1]
    assert sensor["attributes"]["friendly_name"] == "HA GitOps status"
    api.fail = {"/states/"}
    await ctl.publish()  # a failed publish is not an error


async def test_panel_only_commands_are_refused_over_mqtt(ctl: Controller) -> None:
    for cmd in ("discard", "merge", "restart"):
        assert await ctl.command(cmd, "mqtt") == "not allowed over MQTT"
    assert len(ctl.queue) == 0
    assert await ctl.command("discard", "panel") == "ok"
    job = ctl.queue.next()
    assert job is not None
    assert job.mode == "discard"


async def test_merge_from_the_panel(ctl: Controller) -> None:
    api: FakeApi = ctl.box.api  # type: ignore[attr-defined]
    assert await ctl.command("merge", "panel") == "there is no open backup PR"
    ctl.store.set_open_pr(PullRequest("auto-backup/x", "https://example/pr/5", "5"))
    api.check_runs = {"check_runs": [{"status": "completed", "conclusion": "failure"}]}
    assert "checks are failure" in await ctl.command("merge", "panel")
    assert api.count("PUT", "/merge") == 0
    api.check_runs = {"check_runs": [{"status": "completed", "conclusion": "success"}]}
    api.merge = (405, {"message": "Pull Request is not mergeable"})
    assert await ctl.command("merge", "panel") == (
        "GitHub refused the merge: Pull Request is not mergeable"
    )
    api.merge = (200, {"merged": True})
    assert (await ctl.build_state())["can_merge"] is True
    assert await ctl.command("merge", "panel") == "ok"
    assert api.bodies("PUT", "/pulls/5/merge")[-1] == {"merge_method": "merge"}
    assert "sync" in ctl.queue  # the merged PR is pulled right away
    # GitHub cannot be asked: the checks are unknown, and unknown blocks a merge
    api.fail = {"/check-runs"}
    await ctl.pulls.refresh()
    assert ctl.pulls.checks == "none"
    ctl.pulls.github._base = "http://127.0.0.1:9/repos/local/test"
    ctl.pulls.github._timeout = 2
    assert "checks are unknown" in await ctl.pulls.merge("merge", running=False)
    ctl.pulls.checks = "success"
    ctl.pulls.refresh = lambda: asyncio.sleep(0)  # type: ignore[method-assign]
    assert "could not reach GitHub" in await ctl.pulls.merge("merge", running=False)
    ctl.pulls.pull = PullRequest("auto-backup/x", "", "not-a-number")
    assert await ctl.pulls.merge("merge", running=False) == "there is no open backup PR"


async def test_restart_needs_a_valid_config(ctl: Controller) -> None:
    api: FakeApi = ctl.box.api  # type: ignore[attr-defined]
    ctl.flags["restart_required"] = True
    api.check = "invalid"
    assert "config check failed (boom)" in await ctl.command("restart", "panel")
    assert api.count("POST", "homeassistant/restart") == 0
    assert ctl.flags["restart_required"] is True
    api.check = "valid"
    api.fail = {"homeassistant/restart"}  # HA drops the call as it goes down
    assert await ctl.command("restart", "panel") == "ok"
    assert ctl.flags["restart_required"] is False
    api.fail = {"check_config"}
    assert "could not reach Home Assistant" in await ctl.restart_ha()
    ctl.hass.available = False
    assert await ctl.restart_ha() == "no access to the Home Assistant API"
    await ctl.publish()  # nowhere to publish to: quietly nothing


# ---- running the engine ------------------------------------------------------


async def test_engine_sync_run(ctl: Controller) -> None:
    """A merged change lands on origin/main; a sync run brings it down."""
    box: Box = ctl.box  # type: ignore[attr-defined]
    api: FakeApi = box.api  # type: ignore[assignment]
    box.dev_push("configuration.yaml", "homeassistant:\n  name: Merged\n")
    await ctl.command("auto_apply_off", "test")
    await ctl.run_job(Job("sync", "test"))
    assert "Merged" in box.read("configuration.yaml")
    status = box.status()
    assert (status["state"], status["detail"], status["applied"]) == ("success", "synced", "off")
    state = await ctl.build_state()
    assert (state["trigger"], state["running"]) == ("test", False)
    assert state["remote_head"] == state["local_head"]
    # With apply back on, a change that needs a restart raises the flag…
    await ctl.command("auto_apply_on", "test")
    box.dev_push("configuration.yaml", "homeassistant:\n  name: Again\n")
    await ctl.run_job(Job("sync", "test"))
    assert ctl.flags["restart_required"] is True
    # …and Home Assistant coming back up clears it
    await ctl.ha_started()
    assert ctl.flags["restart_required"] is False
    box.options["auto_restart"] = True
    (box.data / "options.json").write_text(json.dumps(box.options))
    ctl.flags["restart_required"] = True
    box.dev_push("configuration.yaml", "homeassistant:\n  name: Third\n")
    await ctl.run_job(Job("sync", "test"))
    assert ctl.flags["restart_required"] is False
    assert api.count("POST", "homeassistant/restart") == 1
    await ctl.run_job(Job("dry_run", "test"))
    assert box.status()["detail"] in ("dry_run", "no_changes")


async def test_worker_drains_the_queue(ctl: Controller) -> None:
    seen: list[str] = []

    async def fake(job: Job) -> None:
        seen.append(job.mode)

    ctl.run_job = fake  # type: ignore[method-assign]
    ctl.request("sync", "t")
    ctl.request("discard", "t")
    worker = asyncio.create_task(ctl.worker())
    await asyncio.sleep(0.05)
    worker.cancel()
    assert seen == ["discard", "sync"]


async def test_hung_run_is_stopped_and_backs_off(ctl: Controller) -> None:
    box: Box = ctl.box  # type: ignore[attr-defined]
    ctl.runner.command = ("sleep", "60")
    ctl.runner.timeout = 1
    started = time.monotonic()
    await ctl.run_job(Job("sync", "test"))
    assert time.monotonic() - started < 20
    status = box.status()
    assert (status["state"], status["detail"]) == ("error", "timeout")
    assert box.line() == "error:timeout:run stopped after 1s"
    assert not ctl.running
    assert ctl.failures == 1
    assert ctl.retry_at > time.monotonic() + 30  # the poller holds off
    assert (await ctl.build_state())["retry_in"] > 30


async def test_cancelled_run_stops_the_engine(ctl: Controller) -> None:
    ctl.runner.command = ("sleep", "60")
    task = asyncio.create_task(ctl.run_job(Job("sync", "test")))
    await asyncio.sleep(0.3)
    assert ctl.running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert not ctl.running


async def test_edit_made_during_a_run_queues_a_backup(ctl: Controller) -> None:
    box: Box = ctl.box  # type: ignore[attr-defined]
    box.write("during.yaml", "edited: while running\n")
    ctl.debounce = Debounce(10)
    # Only files the engine rewrote, all unchanged: nothing to do
    ctl.run_events = {"configuration.yaml"}
    await ctl.recheck_drift("sync")
    assert ctl.debounce.last is None
    # A real edit that the run did not back up
    ctl.run_events = {"during.yaml", "configuration.yaml"}
    await ctl.recheck_drift("sync")
    assert ctl.debounce.last is not None
    # Never from a dry run, which backs nothing up by design
    ctl.debounce.reset()
    ctl.run_events = {"during.yaml"}
    await ctl.recheck_drift("dry_run")
    assert ctl.debounce.last is None
    # Too many events to remember: any fresh drift counts
    ctl.run_overflow = True
    await ctl.recheck_drift("sync")
    assert ctl.debounce.last is not None
    # Never loops on something a run cannot back up
    ctl.debounce.reset()
    ctl.retriggers = 3
    ctl.run_events = {"during.yaml"}
    await ctl.recheck_drift("sync")
    assert ctl.debounce.last is None
    ctl.running = True
    ctl.run_events = {"during.yaml"}
    await ctl.recheck_drift("sync")
    assert ctl.run_events == {"during.yaml"}  # the next run's check takes these over


# ---- triggers ----------------------------------------------------------------


async def test_poll_queues_a_sync_when_the_remote_moved(ctl: Controller) -> None:
    box: Box = ctl.box  # type: ignore[attr-defined]
    await triggers.poll_once(ctl)
    assert "sync" not in ctl.queue
    assert ctl.remote_head == box.git("rev-parse", "main")
    sha = box.dev_push("automations.yaml", "[{alias: x}]\n")
    ctl.retry_at = time.monotonic() + 60  # after failed runs the poller holds off
    await triggers.poll_once(ctl)
    assert ctl.remote_head == sha
    assert "sync" not in ctl.queue
    ctl.retry_at = 0
    await triggers.poll_once(ctl)
    assert "sync" in ctl.queue


async def test_scheduler_and_poller_loops(ctl: Controller, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = 0
    real_sleep = asyncio.sleep

    async def fast(_seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks > 3:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(triggers.asyncio, "sleep", fast)
    object.__setattr__(ctl.settings, "run_at_start", True)
    with pytest.raises(asyncio.CancelledError):
        await triggers.scheduler(ctl)
    job = ctl.queue.next()
    assert job is not None
    assert job.trigger == "start"
    ticks = 0
    ctl.flags["paused"] = True
    with pytest.raises(asyncio.CancelledError):
        await triggers.scheduler(ctl)
    ticks = 0
    with pytest.raises(asyncio.CancelledError):
        await triggers.poller(ctl)
    assert ctl.remote_head


async def test_file_changes_are_debounced_into_one_backup(ctl: Controller) -> None:
    box: Box = ctl.box  # type: ignore[attr-defined]
    await triggers.file_changed(ctl, "automations.yaml")  # the watcher is off: ignored
    ctl.debounce = Debounce(0)
    for noise in (".storage/core.config", "home-assistant.log", "secrets.yaml", ""):
        await triggers.file_changed(ctl, noise)
    assert ctl.debounce.last is None
    rev = ctl.drift.rev
    await triggers.file_changed(ctl, "automations.yaml")
    await triggers.file_changed(ctl, "automations.yaml")  # answered from the cache
    assert ctl.drift.rev == rev + 2
    box.write(".gitignore", box.read(".gitignore") + "automations.yaml\n")
    await triggers.file_changed(ctl, ".gitignore")  # the ignore rules changed
    assert ctl.ignore_cache == {".gitignore": False}
    await triggers.debounce_tick(ctl)
    job = ctl.queue.next()
    assert job == Job("full", "watcher")
    ctl.flags["paused"] = True
    ctl.debounce.touch(0)
    await triggers.debounce_tick(ctl)
    assert ctl.queue.next() is None
    # While a run is on, changes are only noted
    ctl.flags["paused"] = False
    ctl.running = True
    await triggers.file_changed(ctl, "scripts.yaml")
    assert ctl.run_events == {"scripts.yaml"}
    ctl.run_events = {str(n) for n in range(triggers.MAX_RUN_EVENTS)}
    await triggers.file_changed(ctl, "scenes.yaml")
    assert ctl.run_overflow


async def test_watcher_follows_inotify_and_restarts(
    ctl: Controller, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    box: Box = ctl.box  # type: ignore[attr-defined]
    fake = tmp_path / "inotifywait"
    fake.write_text(f"#!/bin/sh\necho '{box.config}/scripts.yaml'\necho gone >&2\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{box.env['PATH']}")
    real_sleep = asyncio.sleep

    async def stop_after_first(seconds: float) -> None:
        if seconds == 60:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(triggers.asyncio, "sleep", stop_after_first)
    with pytest.raises(asyncio.CancelledError):
        await triggers.watcher(ctl)
    assert ctl.debounce is not None
    assert ctl.debounce.last is not None
    monkeypatch.setattr(triggers.shutil, "which", lambda _name: None)
    await triggers.watcher(ctl)  # no inotifywait: file watching is off
    object.__setattr__(ctl.settings, "sync_mode", "pull")
    await triggers.watcher(ctl)


# ---- MQTT --------------------------------------------------------------------


class FakeMqtt:
    def __init__(self, fail: bool = False) -> None:
        self.published: dict[str, Any] = {}
        self.subscribed: list[str] = []
        self.auth: tuple[Any, ...] | None = None
        self.tls = self.stopped = False
        self.fail = fail
        self.on_connect: Any = None
        self.on_message: Any = None

    def username_pw_set(self, *args: Any) -> None:
        self.auth = args

    def tls_set(self) -> None:
        self.tls = True

    def will_set(self, topic: str, payload: str, retain: bool = False) -> None:
        self.will = (topic, payload)

    def connect(self, host: str, port: int) -> None:
        if self.fail:
            raise OSError("refused")
        self.peer = (host, port)

    def loop_start(self) -> None:
        self.on_connect(self, None, None, 0, None)

    def loop_stop(self) -> None:
        self.stopped = True

    def disconnect(self) -> None:
        pass

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.published[topic] = payload

    def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)


async def test_mqtt_device_state_and_commands(ctl: Controller) -> None:
    api: FakeApi = ctl.box.api  # type: ignore[attr-defined]
    assert not await ctl.mqtt.connect()  # the Supervisor offers no broker
    api.mqtt = {"host": "broker", "port": 1884, "username": "u", "password": "p", "ssl": True}
    client = FakeMqtt()
    ctl.mqtt._factory = lambda: client
    assert await ctl.mqtt.connect()
    assert (client.peer, client.auth, client.tls) == (("broker", 1884), ("u", "p"), True)
    assert client.published["ha_gitops/availability"] == "online"
    assert client.will == ("ha_gitops/availability", "offline")
    assert client.subscribed == ["ha_gitops/cmd", "homeassistant/status"]
    assert "homeassistant/button/ha_gitops/sync/config" in client.published

    await ctl.publish()
    state = json.loads(client.published["ha_gitops/state"])
    assert state["mqtt"] is True
    assert json.loads(client.published["ha_gitops/update"])["installed_version"]

    def message(topic: str, payload: bytes, retain: bool = False) -> None:
        client.on_message(
            client, None, SimpleNamespace(topic=topic, payload=payload, retain=retain)
        )

    message("ha_gitops/cmd", b"sync")
    message("ha_gitops/cmd", b"backup", retain=True)  # would run again on every reconnect
    ctl.flags["restart_required"] = True
    message("homeassistant/status", b"offline")
    message("homeassistant/status", b"online")
    await asyncio.sleep(0.05)
    assert "sync" in ctl.queue
    assert "full" not in ctl.queue
    assert ctl.flags["restart_required"] is False

    ctl.mqtt.close()
    assert client.published["ha_gitops/availability"] == "offline"
    assert client.stopped
    assert not ctl.mqtt.connected
    ctl.mqtt.close()  # twice is fine


async def test_mqtt_falls_back_when_the_broker_is_unreachable(ctl: Controller) -> None:
    api: FakeApi = ctl.box.api  # type: ignore[attr-defined]
    api.mqtt = {"host": "broker"}
    ctl.mqtt._factory = lambda: FakeMqtt(fail=True)
    assert not await ctl.mqtt.connect()
    ctl.mqtt.commands = False
    quiet = FakeMqtt()
    ctl.mqtt._factory = lambda: quiet
    assert await ctl.mqtt.connect()
    assert quiet.subscribed == ["homeassistant/status"]
    assert quiet.published["homeassistant/button/ha_gitops/sync/config"] == ""  # removed
    assert isinstance(mqtt._make_client(), mqtt.paho.Client)


# ---- the whole service ---------------------------------------------------------


async def test_service_starts_serves_and_stops(
    ctl: Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "INGRESS_PORT", 0)
    monkeypatch.setattr(web, "WEBHOOK_PORT", 0)
    monkeypatch.setattr("sys.stdin", io.StringIO("pause\n"))
    monkeypatch.setattr(triggers.shutil, "which", lambda _name: None)
    object.__setattr__(ctl.settings, "mqtt_discovery", True)
    task = asyncio.create_task(service.run(ctl))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if ctl.paused:
            break
    assert ctl.paused  # the command typed on stdin arrived
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)

    object.__setattr__(ctl.settings, "webhook_secret", "")
    runners = await web.start(ctl)
    assert len(runners) == 1  # no secret, no webhook port
    await runners[0].cleanup()


async def test_entry_point_stops_cleanly_on_a_signal(
    monkeypatch: pytest.MonkeyPatch, abox: Box
) -> None:
    for key, value in abox.env.items():
        monkeypatch.setenv(key, value)
    started = asyncio.Event()

    async def forever(_ctl: Controller) -> None:
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(service, "run", forever)
    task = asyncio.create_task(entry.serve())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    # A cancelled service is a clean exit, not a traceback
    assert await asyncio.wait_for(task, 10) is None
