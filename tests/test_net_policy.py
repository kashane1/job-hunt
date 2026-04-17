from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from job_hunt.net_policy import (
    DomainRateLimiter,
    RobotsCache,
    registered_domain,
)


class RegisteredDomainTest(unittest.TestCase):
    def test_basic_etld1(self) -> None:
        self.assertEqual(registered_domain("https://www.example.com/foo"), "example.com")
        self.assertEqual(registered_domain("https://example.com/"), "example.com")

    def test_shared_platform_domains(self) -> None:
        self.assertEqual(
            registered_domain("https://boards.greenhouse.io/co/jobs/1"),
            "greenhouse.io",
        )
        self.assertEqual(
            registered_domain("https://jobs.lever.co/startup/123"),
            "lever.co",
        )

    def test_ip_url_bucketed_whole(self) -> None:
        self.assertEqual(registered_domain("http://1.2.3.4/path"), "1.2.3.4")

    def test_ipv6_url_bucketed_whole(self) -> None:
        self.assertEqual(registered_domain("http://[2001:db8::1]/p"), "2001:db8::1")

    def test_empty_hostname_raises(self) -> None:
        with self.assertRaises(ValueError):
            registered_domain("http:///path")

    def test_idn_normalized(self) -> None:
        # IDN hostnames should normalize via idna — do not explode on Unicode.
        out = registered_domain("https://exämple.com/path")
        # Punycode-normalized form sorts under .com
        self.assertTrue(out.endswith(".com"))


class DomainRateLimiterSerializesSameDomainTest(unittest.TestCase):
    def test_three_calls_serialize(self) -> None:
        limiter = DomainRateLimiter(default_interval_s=0.1)
        start = time.monotonic()
        for _ in range(3):
            limiter.acquire("https://example.com/a")
        elapsed = time.monotonic() - start
        # Slots are at T=0, T=0.1, T=0.2 → ~0.2s cumulative wait
        self.assertGreaterEqual(elapsed, 0.18)

    def test_distinct_domains_parallelize(self) -> None:
        limiter = DomainRateLimiter(default_interval_s=0.5)
        start = time.monotonic()
        limiter.acquire("https://example.com/")
        limiter.acquire("https://other.com/")
        elapsed = time.monotonic() - start
        # Distinct domains have independent slots → ~0s cumulative wait
        self.assertLess(elapsed, 0.1)

    def test_no_thundering_herd(self) -> None:
        limiter = DomainRateLimiter(default_interval_s=0.05)
        finish_times: list[float] = []
        start = time.monotonic()

        def worker() -> None:
            limiter.acquire("https://busy.example/")
            finish_times.append(time.monotonic() - start)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        finish_times.sort()
        # Finish times should be staggered ~0.05s apart, not all clustered.
        spread = finish_times[-1] - finish_times[0]
        self.assertGreaterEqual(spread, 0.15)


class RobotsCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = Path(self._tmp.name) / "robots.json"
        self.limiter = DomainRateLimiter(default_interval_s=0.0)

    def test_disallow_all_blocks(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str):
            calls.append(url)
            return True, "User-agent: *\nDisallow: /\n"

        cache = RobotsCache(
            self.cache_path, self.limiter, "job-hunt/0.3", fetch_fn=fake_fetch,
        )
        self.assertFalse(cache.can_fetch("https://example.com/jobs"))

    def test_allow_all_allows(self) -> None:
        def fake_fetch(url: str):
            return True, "User-agent: *\nDisallow:\n"

        cache = RobotsCache(
            self.cache_path, self.limiter, "job-hunt/0.3", fetch_fn=fake_fetch,
        )
        self.assertTrue(cache.can_fetch("https://example.com/jobs"))

    def test_5xx_treated_as_disallow(self) -> None:
        def fake_fetch(url: str):
            return False, ""  # simulate 5xx / network failure

        cache = RobotsCache(
            self.cache_path, self.limiter, "job-hunt/0.3", fetch_fn=fake_fetch,
        )
        self.assertFalse(cache.can_fetch("https://fivex.example.com/jobs"))

    def test_stampede_prevention(self) -> None:
        call_count = 0
        lock = threading.Lock()

        def fake_fetch(url: str):
            nonlocal call_count
            with lock:
                call_count += 1
            # Simulate slow fetch so threads pile up on the inflight Event
            time.sleep(0.05)
            return True, "User-agent: *\nDisallow:\n"

        cache = RobotsCache(
            self.cache_path, self.limiter, "job-hunt/0.3", fetch_fn=fake_fetch,
        )
        threads = [
            threading.Thread(
                target=lambda: cache.can_fetch("https://stampede.example.com/x"),
            )
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(call_count, 1)

    def test_persistent_cache_skips_refetch(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str):
            calls.append(url)
            return True, "User-agent: *\nDisallow:\n"

        cache = RobotsCache(
            self.cache_path, self.limiter, "job-hunt/0.3", fetch_fn=fake_fetch,
        )
        cache.can_fetch("https://persist.example/a")
        # New instance reusing the same cache file
        cache2 = RobotsCache(
            self.cache_path, self.limiter, "job-hunt/0.3", fetch_fn=fake_fetch,
        )
        cache2.can_fetch("https://persist.example/b")
        self.assertEqual(len(calls), 1)

    def test_disallow_ttl_shorter_than_allow(self) -> None:
        self.assertLess(RobotsCache._DISALLOW_TTL_S, RobotsCache._ALLOW_TTL_S)


if __name__ == "__main__":
    unittest.main()
