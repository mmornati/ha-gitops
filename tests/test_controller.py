# SPDX-License-Identifier: Apache-2.0
"""Controller tests against a local repo (needs aiohttp; no MQTT, no GitHub).

Exercises the webhook signature gate, the ingress API, the command queue and
a real engine run in sync mode.
"""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "gitops_backup/rootfs/app"


def sh(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True)


class ControllerTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.config, cls.data, cls.origin = root / "config", root / "data", root / "origin.git"
        cls.config.mkdir()
        cls.data.mkdir()
        cls.env = dict(os.environ, HOME=str(root))
        sh("git", "init", "-q", "--bare", "-b", "main", str(cls.origin))
        sh("git", "config", "--global", f"url.{cls.origin}.insteadOf",
           "https://github.com/local/test.git", env=cls.env)
        (cls.config / "configuration.yaml").write_text("homeassistant:\n")
        (cls.data / "options.json").write_text(json.dumps({
            "github_repo": "local/test", "github_token": "test-token", "base_branch": "main",
            "branch_prefix": "auto-backup", "dry_run": False, "commit_name": "T",
            "commit_email": "t@localhost", "signoff": False, "interval_hours": 24,
            "sync_mode": "both", "poll_seconds": 60, "debounce_seconds": 120,
            "webhook_secret": "s3cret", "mqtt_discovery": False}))
        os.environ.update(HOME=str(root), OPTIONS=str(cls.data / "options.json"),
                          DATA_DIR=str(cls.data), CONFIG_DIR=str(cls.config),
                          GITHUB_API="http://127.0.0.1:9")
        os.environ.pop("SUPERVISOR_TOKEN", None)
        # Bootstrap /config through the engine itself
        subprocess.run(["bash", str(APP / "gitops_backup.sh")], env=dict(os.environ),
                       check=True, capture_output=True)
        sys.path.insert(0, str(APP))
        global controller, web, TestClient, TestServer
        import controller  # noqa: F401
        from aiohttp import web  # noqa: F401
        from aiohttp.test_utils import TestClient, TestServer  # noqa: F401

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    async def asyncSetUp(self):
        self.ctl = controller.Controller(json.loads((self.data / "options.json").read_text()))

    def hook_app(self):
        app = web.Application()
        app.router.add_post("/webhook", self.ctl.handle_webhook)
        return app

    def panel_app(self):
        app = web.Application(middlewares=[self.ctl.ingress_only])
        app.router.add_get("/api/state", self.ctl.api_state)
        app.router.add_get("/api/history", self.ctl.api_history)
        app.router.add_get("/api/diff", self.ctl.api_diff)
        app.router.add_get("/api/log", self.ctl.api_log)
        app.router.add_post("/api/command", self.ctl.api_command)
        return app

    async def test_webhook_signature_gate(self):
        body = json.dumps({"ref": "refs/heads/main"}).encode()
        sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        async with TestClient(TestServer(self.hook_app())) as client:
            r = await client.post("/webhook", data=body, headers={
                "X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=deadbeef"})
            self.assertEqual(r.status, 401)
            self.assertNotIn("sync", self.ctl.jobs)
            r = await client.post("/webhook", data=body, headers={
                "X-GitHub-Event": "push", "X-Hub-Signature-256": sig})
            self.assertEqual(r.status, 202)
            self.assertIn("sync", self.ctl.jobs)

    async def test_paused_webhook_is_ignored(self):
        self.ctl.state["paused"] = True
        body = json.dumps({"ref": "refs/heads/main"}).encode()
        sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        async with TestClient(TestServer(self.hook_app())) as client:
            r = await client.post("/webhook", data=body, headers={
                "X-GitHub-Event": "push", "X-Hub-Signature-256": sig})
            self.assertEqual(await r.text(), "ignored")
        self.assertNotIn("sync", self.ctl.jobs)

    async def test_panel_api(self):
        async with TestClient(TestServer(self.panel_app())) as client:
            state = await (await client.get("/api/state")).json()
            self.assertEqual(len(state["local_head"]), 40)
            history = await (await client.get("/api/history")).json()
            self.assertTrue(history["commits"][-1]["subject"].startswith("chore: initial"))
            r = await client.post("/api/command", json={"command": "backup"})
            self.assertEqual(r.status, 403)  # no CSRF header
            r = await client.post("/api/command", json={"command": "backup"},
                                  headers={"X-Requested-With": "ha-gitops"})
            self.assertEqual(r.status, 200)
            self.assertIn("full", self.ctl.jobs)
            r = await client.post("/api/command", json={"command": "rm"},
                                  headers={"X-Requested-With": "ha-gitops"})
            self.assertEqual(r.status, 400)

    async def test_queue_full_covers_sync(self):
        self.ctl.request("sync", "poll")
        self.ctl.request("full", "watcher")
        mode, trigger, _ = self.ctl.next_job()
        self.assertEqual((mode, trigger), ("full", "watcher"))
        self.assertIsNone(self.ctl.next_job())

    async def test_engine_sync_run(self):
        # A merged change lands on origin/main; a sync run brings it down
        work = Path(self.tmp.name) / "work"
        sh("git", "clone", "-q", str(self.origin), str(work), env=self.env)
        (work / "automations.yaml").write_text("[]\n")
        sh("git", "add", ".", cwd=work, env=self.env)
        sh("git", "-c", "user.name=T", "-c", "user.email=t@l", "commit", "-q", "-m", "add",
           cwd=work, env=self.env)
        sh("git", "push", "-q", "origin", "main", cwd=work, env=self.env)
        await self.ctl.command("pause", "test")
        self.assertTrue(json.loads((self.data / "controller_state.json").read_text())["paused"])
        await self.ctl.command("resume", "test")
        await self.ctl.run_engine("sync", "test", {})
        self.assertTrue((self.config / "automations.yaml").exists())
        status = json.loads((self.data / "status.json").read_text())
        self.assertEqual((status["state"], status["detail"]), ("success", "synced"))
        state = await self.ctl.build_state()
        self.assertEqual(state["trigger"], "test")
        self.assertFalse(state["running"])


    async def test_drift_lists_new_files_and_hides_held_content(self):
        new = self.config / "new file.yaml"
        held = self.config / "mqtt.yaml"
        new.write_text("fresh: 1\n")
        held.write_text("password: SuperSecretPassw0rd\n")
        (self.config / "configuration.yaml").write_text("homeassistant:\n  name: Edited\n")
        status = self.data / "status.json"
        before = status.read_text()
        status.write_text(json.dumps(dict(json.loads(before), held=["mqtt.yaml"])))
        try:
            async with TestClient(TestServer(self.panel_app())) as client:
                snap = await (await client.get("/api/diff")).json()
                state = await (await client.get("/api/state")).json()
        finally:
            new.unlink()
            held.unlink()
            sh("git", "checkout", "--", "configuration.yaml", cwd=self.config, env=self.env)
            status.write_text(before)
        files = {f["path"]: f for f in snap["files"]}
        self.assertEqual(files["new file.yaml"]["status"], "added")
        self.assertEqual(files["configuration.yaml"]["status"], "modified")
        self.assertTrue(files["mqtt.yaml"]["held"])
        self.assertFalse(files["new file.yaml"]["in_pr"])
        self.assertIn("+fresh: 1", snap["diff"])           # untracked files get a diff
        self.assertIn("name: Edited", snap["diff"])
        self.assertNotIn("SuperSecret", snap["diff"])      # held content never leaves the box
        self.assertEqual(state["pending"], 3)
        self.assertEqual(state["pending_new"], 2)
        self.assertEqual(state["held"], ["mqtt.yaml"])
        # The scratch index is gone and the real one was not touched
        self.assertFalse((self.data / "panel.index").exists())
        out = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.config,
                             env=self.env, capture_output=True, text=True).stdout
        self.assertEqual(out, "")

    async def test_panel_only_commands_are_refused_over_mqtt(self):
        for cmd in ("discard", "merge", "restart"):
            self.assertEqual(await self.ctl.command(cmd, "mqtt"), "not allowed over MQTT")
        self.assertEqual(self.ctl.jobs, {})
        self.assertEqual(await self.ctl.command("discard", "panel"), "ok")
        self.assertEqual(self.ctl.next_job()[0], "discard")

    async def test_bad_webhook_signatures_are_rate_limited(self):
        body = json.dumps({"ref": "refs/heads/main"}).encode()
        good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        async with TestClient(TestServer(self.hook_app())) as client:
            codes = []
            for _ in range(12):
                r = await client.post("/webhook", data=body, headers={
                    "X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=bad"})
                codes.append(r.status)
            self.assertEqual(codes, [401] * 10 + [429] * 2)
            r = await client.post("/webhook", data=body, headers={
                "X-GitHub-Event": "push", "X-Hub-Signature-256": good})
            self.assertEqual(r.status, 202)  # a signed delivery still gets through

    async def test_hung_run_is_stopped_and_backs_off(self):
        slow = Path(self.tmp.name) / "slow.sh"
        slow.write_text("#!/bin/bash\nsleep 60\n")
        engine, timeout = controller.ENGINE, controller.ENGINE_TIMEOUT
        status = self.data / "status.json"
        before = status.read_text()
        controller.ENGINE, controller.ENGINE_TIMEOUT = slow, 1
        try:
            started = time.monotonic()
            await self.ctl.run_engine("sync", "test", {})
        finally:
            controller.ENGINE, controller.ENGINE_TIMEOUT = engine, timeout
        self.assertLess(time.monotonic() - started, 20)
        after = json.loads(status.read_text())
        status.write_text(before)
        self.assertEqual((after["state"], after["detail"]), ("error", "timeout"))
        self.assertFalse(self.ctl.running)
        self.assertEqual(self.ctl.failures, 1)
        self.assertGreater(self.ctl.retry_at, time.monotonic() + 30)  # poller holds off

    async def fake_server(self, routes):
        app = web.Application()
        app.add_routes(routes)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return str(server.make_url("")).rstrip("/")

    async def test_merge_from_the_panel(self):
        calls, checks = [], {"conclusion": "success"}

        async def runs(_request):
            return web.json_response({"check_runs": [dict(checks, status="completed")]})

        async def statuses(_request):
            return web.json_response({"statuses": []})

        async def merge(request):
            calls.append(await request.json())
            return web.json_response({"merged": True})

        base = await self.fake_server([
            web.get("/repos/local/test/commits/{ref:.+}/check-runs", runs),
            web.get("/repos/local/test/commits/{ref:.+}/status", statuses),
            web.put("/repos/local/test/pulls/5/merge", merge)])
        api, controller.GITHUB_API = controller.GITHUB_API, base
        open_pr = self.data / "open_pr"
        try:
            self.assertEqual(await self.ctl.merge_pr(), "there is no open backup PR")
            open_pr.write_text("auto-backup/x\thttps://example/pr/5\t5\n")
            checks["conclusion"] = "failure"
            self.assertIn("checks are failure", await self.ctl.merge_pr())
            self.assertEqual(calls, [])
            checks["conclusion"] = "success"
            self.assertEqual(await self.ctl.command("merge", "panel"), "ok")
        finally:
            controller.GITHUB_API = api
            open_pr.unlink(missing_ok=True)
        self.assertEqual(calls, [{"merge_method": "merge"}])
        self.assertIn("sync", self.ctl.jobs)  # the merged PR is pulled right away

    async def test_restart_needs_a_valid_config(self):
        calls, result = [], {"result": "invalid", "errors": "bad yaml"}

        async def check(_request):
            return web.json_response(result)

        async def restart(_request):
            calls.append("restart")
            return web.json_response([])

        async def states(_request):
            return web.json_response({})

        base = await self.fake_server([
            web.post("/core/api/config/core/check_config", check),
            web.post("/core/api/services/homeassistant/restart", restart),
            web.post("/core/api/states/{entity}", states)])
        supervisor, controller.SUPERVISOR = controller.SUPERVISOR, base
        os.environ["SUPERVISOR_TOKEN"] = "sup"
        self.ctl.state["restart_required"] = True
        try:
            self.assertIn("config check failed", await self.ctl.restart_ha())
            self.assertEqual(calls, [])
            self.assertTrue(self.ctl.state["restart_required"])
            result["result"] = "valid"
            self.assertEqual(await self.ctl.restart_ha(), "ok")
        finally:
            controller.SUPERVISOR = supervisor
            os.environ.pop("SUPERVISOR_TOKEN", None)
        self.assertEqual(calls, ["restart"])
        self.assertFalse(self.ctl.state["restart_required"])

    async def test_edit_made_during_a_run_queues_a_backup(self):
        delay, controller.RECHECK_DELAY = controller.RECHECK_DELAY, 0
        edited = self.config / "during.yaml"
        edited.write_text("edited: while running\n")
        self.ctl.debounce = controller.core.Debounce(10)
        try:
            # Only files the engine rewrote, all unchanged: nothing to do
            self.ctl.run_events = {"configuration.yaml"}
            await self.ctl.recheck_drift("sync")
            self.assertIsNone(self.ctl.debounce.last)
            # A real edit that the run did not back up
            self.ctl.run_events = {"during.yaml", "configuration.yaml"}
            await self.ctl.recheck_drift("sync")
            self.assertIsNotNone(self.ctl.debounce.last)
            # Never from a dry run, which backs nothing up by design
            self.ctl.debounce.reset()
            self.ctl.run_events = {"during.yaml"}
            await self.ctl.recheck_drift("dry_run")
            self.assertIsNone(self.ctl.debounce.last)
        finally:
            controller.RECHECK_DELAY = delay
            edited.unlink()


if __name__ == "__main__":
    unittest.main()
