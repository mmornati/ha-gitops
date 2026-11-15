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
        ):
            self.assertIsNone(secret_scan.scan(data), data)

    def test_binary_content_still_checked_for_keys_and_tokens(self):
        self.assertEqual(secret_scan.scan(b"\0\0 -----BEGIN RSA PRIVATE KEY-----"),
                         "private key block")
        self.assertIsNone(secret_scan.scan(b"\0\0\npassword: SuperSecretPassw0rd\n"))

    def test_match_across_a_chunk_boundary(self):
        token = b"ghp_" + b"a" * 36
        self.assertEqual(secret_scan.scan_chunks(iter([b"x " + token[:20], token[20:] + b" y"])),
                         "GitHub token")

    def cli(self, tmp, stdin: bytes) -> bytes:
        return subprocess.run([sys.executable, str(APP / "secret_scan.py")], cwd=tmp,
                              check=True, input=stdin, capture_output=True).stdout

    def repo(self, tmp):
        env = dict(os.environ, HOME=tmp, GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "-q", tmp], check=True, env=env)
        return lambda *a: subprocess.run(["git", *a], cwd=tmp, check=True, env=env)

    def test_cli_scans_the_staged_blob_not_the_file_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            git = self.repo(tmp)
            odd = "odd\tname .yaml"  # tab inside, trailing-blank-prone name
            Path(tmp, odd).write_text("password: SuperSecretPassw0rd\n")
            Path(tmp, "ok.yaml").write_text("password: !secret pw\n")
            Path(tmp, "swap.yaml").write_text("token: ghp_" + "a" * 36 + "\n")
            git("add", "-A")
            # Edited after staging: the index still holds the secret
            Path(tmp, "swap.yaml").write_text("clean\n")
            out = self.cli(tmp, os.fsencode(odd) + b"\0ok.yaml\0swap.yaml\0")
        self.assertEqual(out, os.fsencode(odd) + b"\0inline credential\0"
                              b"swap.yaml\0GitHub token\0")
        self.assertNotIn(b"SuperSecret", out)

    def test_cli_fails_closed_and_scans_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            git = self.repo(tmp)
            Path(tmp, "big.yaml").write_bytes(
                b"#" * (secret_scan.CHUNK + 100) + b"\npassword: SuperSecretPassw0rd\n")
            git("add", "-A")
            out = self.cli(tmp, b"big.yaml\0not-in-index.yaml\0")
        self.assertEqual(out, b"big.yaml\0inline credential\0"
                              b"not-in-index.yaml\0content could not be read\0")


if __name__ == "__main__":
    unittest.main()
