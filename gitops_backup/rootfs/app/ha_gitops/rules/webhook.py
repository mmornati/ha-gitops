# SPDX-License-Identifier: Apache-2.0
"""GitHub webhook deliveries: signature check and what they ask for."""

import hashlib
import hmac
from typing import Any


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Checks GitHub's X-Hub-Signature-256 header against the shared secret."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256=") :])


def webhook_action(event: str | None, payload: dict[str, Any], base_branch: str) -> str | None:
    """What a verified GitHub delivery asks for: "ping", "sync" or nothing."""
    if event == "ping":
        return "ping"
    if event == "push" and payload.get("ref") == f"refs/heads/{base_branch}":
        return "sync"
    if (
        event == "pull_request"
        and payload.get("action") == "closed"
        and (payload.get("pull_request") or {}).get("merged")
        and ((payload.get("pull_request") or {}).get("base") or {}).get("ref") == base_branch
    ):
        return "sync"
    return None
