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


if __name__ == "__main__":
    unittest.main()
