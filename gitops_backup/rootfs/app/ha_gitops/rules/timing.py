# SPDX-License-Identifier: Apache-2.0
"""Small time-based policies: debounce, backoff, rate limit."""


class Debounce:
    """Fires once nothing has changed for `quiet` seconds.

    `max_wait` caps the delay, so a file rewritten every few seconds still
    gets backed up eventually.
    """

    def __init__(self, quiet: float, max_wait: float | None = None):
        self.quiet = quiet
        self.max_wait = max_wait if max_wait is not None else quiet * 10
        self.first: float | None = None
        self.last: float | None = None

    def touch(self, now: float) -> None:
        if self.first is None:
            self.first = now
        self.last = now

    def due(self, now: float) -> bool:
        if self.last is None:
            return False
        first = self.last if self.first is None else self.first
        return now - self.last >= self.quiet or now - first >= self.max_wait

    def reset(self) -> None:
        self.first = self.last = None


def backoff_seconds(failures: int, base: float = 60, cap: float = 3600) -> float:
    """How long to wait before the poller retries after `failures` failed runs."""
    if failures <= 0:
        return 0
    return float(min(cap, base * 2 ** (failures - 1)))


class RateLimiter:
    """Counts events per key inside a sliding window."""

    def __init__(self, limit: int = 10, window: float = 60, max_keys: int = 1000):
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self.events: dict[str, list[float]] = {}

    def hit(self, key: str, now: float) -> int:
        """Records one event and returns how many the key has in the window."""
        if len(self.events) >= self.max_keys and key not in self.events:
            self.events.clear()
        recent = [t for t in self.events.get(key, []) if now - t < self.window]
        recent.append(now)
        self.events[key] = recent
        return len(recent)
