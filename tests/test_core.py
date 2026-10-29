# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the controller's pure helpers (stdlib only)."""

import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gitops_backup/rootfs/app"))

import gitops_core as core  # noqa: E402


class SignatureTest(unittest.TestCase):
    def sign(self, secret, body):
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_valid(self):
        body = b'{"ref":"refs/heads/main"}'
        self.assertTrue(core.verify_signature("s3cret", body, self.sign("s3cret", body)))

    def test_wrong_secret_or_body(self):
        body = b"{}"
        self.assertFalse(core.verify_signature("s3cret", body, self.sign("other", body)))
        self.assertFalse(core.verify_signature("s3cret", b"{ }", self.sign("s3cret", body)))

    def test_missing(self):
        self.assertFalse(core.verify_signature("s3cret", b"{}", None))
        self.assertFalse(core.verify_signature("", b"{}", self.sign("", b"{}")))
        self.assertFalse(core.verify_signature("s3cret", b"{}", "sha1=abc"))


class WebhookActionTest(unittest.TestCase):
    def test_push_to_base(self):
        self.assertEqual(core.webhook_action("push", {"ref": "refs/heads/main"}, "main"), "sync")

    def test_push_elsewhere(self):
        self.assertIsNone(core.webhook_action("push", {"ref": "refs/heads/auto-backup/x"}, "main"))

    def test_ping(self):
        self.assertEqual(core.webhook_action("ping", {}, "main"), "ping")

    def test_merged_pr(self):
        payload = {"action": "closed", "pull_request": {"merged": True, "base": {"ref": "main"}}}
        self.assertEqual(core.webhook_action("pull_request", payload, "main"), "sync")
        payload["pull_request"]["merged"] = False
        self.assertIsNone(core.webhook_action("pull_request", payload, "main"))


class ParseCommandTest(unittest.TestCase):
    def test_plain_and_json(self):
        self.assertEqual(core.parse_command("sync"), ("sync", ""))
        self.assertEqual(core.parse_command('"backup"\n'), ("backup", ""))
        self.assertEqual(core.parse_command(b"pause"), ("pause", ""))
        self.assertEqual(core.parse_command(json.dumps({"command": "revert", "arg": "ABC1234"})),
                         ("revert", "abc1234"))
        self.assertEqual(core.parse_command("revert abc1234def"), ("revert", "abc1234def"))

    def test_rejects(self):
        self.assertIsNone(core.parse_command("rm -rf /"))
        self.assertIsNone(core.parse_command(""))
        self.assertIsNone(core.parse_command("revert ../../etc"))
        self.assertIsNone(core.parse_command("revert"))
        self.assertIsNone(core.parse_command(42))


class WatcherIgnoreTest(unittest.TestCase):
    def test_ignored(self):
        for path in (".storage/core.entity_registry", ".git/index", "home-assistant_v2.db-wal",
                     "home-assistant.log", "home-assistant.log.1", "deps/x/y.py",
                     "custom_components/foo/__pycache__/a.pyc", "zigbee2mqtt/log/2026/log.log",
                     "gitops_backup.log", "automations.yaml.swp"):
            self.assertTrue(core.watcher_ignores(path), path)

    def test_kept(self):
        for path in ("automations.yaml", "packages/heating.yaml", "dashboards/home.yaml",
                     "custom_components/foo/sensor.py", "www/card.js"):
            self.assertFalse(core.watcher_ignores(path), path)


class DebounceTest(unittest.TestCase):
    def test_quiet_period(self):
        d = core.Debounce(120)
        self.assertFalse(d.due(0))
        d.touch(0)
        d.touch(100)
        self.assertFalse(d.due(200))
        self.assertTrue(d.due(220))
        d.reset()
        self.assertFalse(d.due(1000))

    def test_max_wait(self):
        d = core.Debounce(120, max_wait=600)
        for t in range(0, 700, 30):  # never quiet for 120 s
            d.touch(t)
            if t >= 600:
                self.assertTrue(d.due(t))
                break
            self.assertFalse(d.due(t))


class ChecksTest(unittest.TestCase):
    def test_summary(self):
        ok = {"check_runs": [{"status": "completed", "conclusion": "success"}]}
        bad = {"check_runs": [{"status": "completed", "conclusion": "failure"}]}
        running = {"check_runs": [{"status": "in_progress"}]}
        self.assertEqual(core.summarize_checks(ok, None), "success")
        self.assertEqual(core.summarize_checks(ok, {"statuses": [{"state": "pending"}]}), "pending")
        self.assertEqual(core.summarize_checks(running, None), "pending")
        self.assertEqual(core.summarize_checks(bad, {"statuses": [{"state": "success"}]}), "failure")
        self.assertEqual(core.summarize_checks(None, None), "none")


class DiscoveryTest(unittest.TestCase):
    def test_entities(self):
        configs = dict(core.discovery_configs("homeassistant", "ha_gitops", "0.4.0"))
        ids = {cfg["unique_id"] for cfg in configs.values()}
        for key in ("status", "pending_changes", "open_pr", "sync", "backup", "paused",
                    "auto_apply", "config", "running"):
            self.assertIn(f"ha_gitops_{key}", ids)
        for topic, cfg in configs.items():
            self.assertTrue(topic.startswith("homeassistant/") and topic.endswith("/config"), topic)
            self.assertEqual(cfg["device"]["identifiers"], ["ha_gitops"])
            self.assertEqual(cfg["availability_topic"], "ha_gitops/availability")
            json.dumps(cfg)
        button = configs["homeassistant/button/ha_gitops/sync/config"]
        self.assertEqual((button["command_topic"], button["payload_press"]), ("ha_gitops/cmd", "sync"))


if __name__ == "__main__":
    unittest.main()
