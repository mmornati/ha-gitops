# SPDX-License-Identifier: Apache-2.0
"""Tests for the content rules of the secret scan."""

import unittest

from ha_gitops.rules import secret_rules as secret_scan


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
        self.assertEqual(
            secret_scan.scan(b"\0\0 -----BEGIN RSA PRIVATE KEY-----"), "private key block"
        )
        self.assertIsNone(secret_scan.scan(b"\0\0\npassword: SuperSecretPassw0rd\n"))

    def test_match_across_a_chunk_boundary(self):
        token = b"ghp_" + b"a" * 36
        self.assertEqual(
            secret_scan.scan_chunks(iter([b"x " + token[:20], token[20:] + b" y"])), "GitHub token"
        )


if __name__ == "__main__":
    unittest.main()
