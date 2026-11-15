# SPDX-License-Identifier: Apache-2.0
"""Tests for the content-level secret scan (secret_scan.py)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "gitops_backup" / "rootfs" / "app"
sys.path.insert(0, str(APP))

import secret_scan  # noqa: E402


class ScanTest(unittest.TestCase):
    def test_flags_real_looking_secrets(self):
        cases = {
            "private key block": b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n",
            "GitHub token": b"token = ghp_" + b"a" * 36 + b"\n",
            "AWS access key": b"id: AKIAABCDEFGHIJKLMNOP\n",
            "inline credential": b"mqtt:\n  password: SuperSecretPassw0rd\n",
        }
        for label, data in cases.items():
            self.assertEqual(secret_scan.scan(data), label)
        self.assertEqual(secret_scan.scan(b'  "api_key": "abcd1234efgh"\n'), "inline credential")

    def test_leaves_normal_config_alone(self):
        for data in (
            b"mqtt:\n  password: !secret mqtt_password\n",
            b"password: '{{ states(\"input_text.pw\") }}'\n",
            b"password: ${MQTT_PASSWORD}\n",
            b"api_key: changeme\n",
            b"api_key: CHANGE_ME\n",
            b"password: short\n",
            b"- alias: Reset password reminder\n  trigger: []\n",
            b"\0\0binary -----BEGIN PRIVATE KEY-----",
        ):
            self.assertIsNone(secret_scan.scan(data), data)

    def test_cli_reports_label_and_path_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "bad name.yaml").write_text("password: SuperSecretPassw0rd\n")
            Path(tmp, "ok.yaml").write_text("password: !secret pw\n")
            out = subprocess.run(
                [sys.executable, str(APP / "secret_scan.py")], cwd=tmp, check=True,
                input=b"bad name.yaml\0ok.yaml\0gone.yaml\0", capture_output=True).stdout
        self.assertEqual(out, b"inline credential\tbad name.yaml\0")
        self.assertNotIn(b"SuperSecret", out)

    def test_large_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp, "big.yaml")
            big.write_bytes(b"password: SuperSecretPassw0rd\n" + b"#" * secret_scan.MAX_BYTES)
            out = subprocess.run(
                [sys.executable, str(APP / "secret_scan.py")], cwd=tmp, check=True,
                input=os.fsencode("big.yaml") + b"\0", capture_output=True).stdout
        self.assertEqual(out, b"")


if __name__ == "__main__":
    unittest.main()
