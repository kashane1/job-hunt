"""Network-policy primitives: per-domain rate limiting + robots.txt cache.

Owns stateful cross-module infrastructure. utils.py stays primitives-only.
Both `ingestion.py` (batch 2) and `discovery.py` (batch 3) import from here.

Core invariants:
- `DomainRateLimiter.acquire()` reserves the next slot inside the lock and
  sleeps unlocked — no thundering herd when N threads call concurrently.
- `RobotsCache` has differentiated TTLs (allow 24h, disallow 1h), stores the
  resolved IP per entry, and invalidates on re-resolve mismatch. Stampede-safe
  via per-host `threading.Event`.
- Robots fetch is capped at 500KB, BOM-tolerant, and spec-correct on 5xx
  (treat as disallow per RFC 9309, not as allow).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .utils import now_iso, read_json, write_json

logger = logging.getLogger(__name__)


# =============================================================================
# Registered-domain bucketing
# =============================================================================

KNOWN_SHARED_DOMAINS: Final = frozenset({
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "recruitee.com",
    "personio.de",
    "personio.com",
})


def registered_domain(url: str) -> str:
    """Return eTLD+1 for rate-limit bucketing.

    Edge cases:
    - IP URLs (`http://1.2.3.4/`) are bucketed by the whole IP, not sliced.
    - Empty hostnames raise ValueError — never return "".
    - IDN/Punycode hostnames are normalized via idna encoding.
    """
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"URL has no hostname: {url!r}")

    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass

    for known in KNOWN_SHARED_DOMAINS:
        if host == known or host.endswith(f".{known}"):
            return known

    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


# =============================================================================
# DomainRateLimiter — reserve-first (no thundering herd)
# =============================================================================

@dataclass
class _DomainBudget:
    min_interval_s: float
    next_slot_at: float = 0.0


class DomainRateLimiter:
    """Thread-safe per-domain minimum-interval limiter.

    `acquire()` reads and writes `next_slot_at` under the lock, then sleeps
    unlocked. N threads that all enter acquire() simultaneously land at
    staggered slots (T=0, T=interval, T=2*interval, ...) rather than all
    clustering at T=interval.
    """

    def __init__(self, default_interval_s: float = 0.5):
        self._lock = threading.Lock()
        self._budgets: dict[str, _DomainBudget] = {}
        self._default = default_interval_s

    def set_interval(self, domain: str, seconds: float) -> None:
        with self._lock:
            budget = self._budgets.setdefault(domain, _DomainBudget(seconds))
            budget.min_interval_s = seconds

    def acquire(self, url: str) -> float:
        """Block until this domain's next slot. Returns seconds slept."""
        domain = registered_domain(url)
        with self._lock:
            budget = self._budgets.setdefault(
                domain, _DomainBudget(min_interval_s=self._default)
            )
            now = time.monotonic()
            slot = max(now, budget.next_slot_at)
            budget.next_slot_at = slot + budget.min_interval_s
            wait = slot - now
        if wait > 0:
            time.sleep(wait)
        return wait


# =============================================================================
# RobotsCache — persistent, stampede-safe, poison-resistant
# =============================================================================

_BOM_BYTES = b"\xef\xbb\xbf"


def _resolve_first_ip(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None
    for family, *_rest, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ipaddress.ip_address(ip_str)
            return ip_str
        except ValueError:
            continue
    return None


class RobotsCache:
    """Cache robots.txt decisions with differentiated TTLs.

    Each cache entry is keyed by registered domain and records:
        {
          "fetched_at": monotonic timestamp (seconds),
          "resolved_ip": str,
          "rules_text": str,            # the full robots.txt body (or "" on error)
          "fetch_failed": bool,         # True when the last fetch errored
        }

    `can_fetch(url)` uses the cached rules when fresh; otherwise fetches once
    per host per TTL, coordinated across threads by a per-host Event.
    """

    _ROBOTS_MAX_BYTES: Final = 500_000
    _ALLOW_TTL_S: Final = 24 * 60 * 60
    _DISALLOW_TTL_S: Final = 60 * 60

    def __init__(
        self,
        cache_path: Path,
        rate_limiter: DomainRateLimiter,
        user_agent: str,
        fetch_fn=None,
        clock=time.monotonic,
    ):
        self._cache_path = cache_path
        self._rate_limiter = rate_limiter
        self._user_agent = user_agent
        self._fetch_fn = fetch_fn  # Injected for testing; defaults to urllib
        self._clock = clock
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        self._cache: dict[str, dict] = self._load()

    def _load(self) -> dict:
        if not self._cache_path.exists():
            return {}
        try:
            payload = read_json(self._cache_path)
        except Exception as exc:
            logger.warning("robots cache load failed (%s); starting empty", exc)
            return {}
        entries = payload.get("entries", {})
        if not isinstance(entries, dict):
            return {}
        return entries

    def _save(self) -> None:
        payload = {"schema_version": 1, "entries": self._cache}
        try:
            write_json(self._cache_path, payload)
        except OSError as exc:
            logger.warning("robots cache save failed: %s", exc)

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
        self._save()

    def _fetch_robots(self, robots_url: str) -> tuple[bool, str]:
        """Fetch robots.txt; returns (ok, body_text). ok=False for 5xx/network."""
        if self._fetch_fn is not None:
            return self._fetch_fn(robots_url)
        # Default: use urllib directly. We intentionally do NOT go through the
        # pinned opener from ingestion.py — robots is a cache-once-per-host
        # operation and the extra validation round-trip is overkill. The body
        # cap and BOM handling below are the real safety net.
        import urllib.error  # noqa: PLC0415  (keep stdlib imports local)
        import urllib.request
        req = urllib.request.Request(robots_url, headers={"User-Agent": self._user_agent})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read(self._ROBOTS_MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if 500 <= exc.code < 600:
                return False, ""
            # 4xx — per convention: 404/410 → allow all, 401/403 → disallow all
            if exc.code in (401, 403):
                return True, "User-agent: *\nDisallow: /"
            return True, ""
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            return False, ""
        if raw.startswith(_BOM_BYTES):
            raw = raw[len(_BOM_BYTES):]
        if len(raw) > self._ROBOTS_MAX_BYTES:
            raw = raw[: self._ROBOTS_MAX_BYTES]
        return True, raw.decode("utf-8", errors="replace")

    def _entry_is_fresh(self, entry: dict, now: float) -> bool:
        fetched_at = entry.get("fetched_at", 0.0)
        if not isinstance(fetched_at, (int, float)):
            return False
        if entry.get("fetch_failed", False):
            ttl = self._DISALLOW_TTL_S
        else:
            ttl = self._ALLOW_TTL_S
        return (now - float(fetched_at)) < ttl

    def _parse_rules(self, rules_text: str, robots_url: str):
        parser = urllib.robotparser.RobotFileParser(url=robots_url)
        parser.parse(rules_text.splitlines())
        return parser

    def can_fetch(self, url: str) -> bool:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if not host:
            return False
        scheme = parsed.scheme or "https"
        port_suffix = f":{parsed.port}" if parsed.port else ""
        robots_url = f"{scheme}://{host}{port_suffix}/robots.txt"
        domain_key = registered_domain(url)
        now = self._clock()

        with self._lock:
            entry = self._cache.get(domain_key)
            if entry and self._entry_is_fresh(entry, now):
                # IP-change invalidation
                resolved = _resolve_first_ip(host)
                if resolved and entry.get("resolved_ip") and resolved != entry["resolved_ip"]:
                    logger.info(
                        "robots cache invalidated for %s (IP %s -> %s)",
                        domain_key, entry.get("resolved_ip"), resolved,
                    )
                else:
                    if entry.get("fetch_failed"):
                        return False
                    parser = self._parse_rules(entry.get("rules_text", ""), robots_url)
                    return parser.can_fetch(self._user_agent, url)
            # Stampede coordination
            event = self._inflight.get(domain_key)
            if event is not None:
                should_wait = True
            else:
                should_wait = False
                event = threading.Event()
                self._inflight[domain_key] = event

        if should_wait:
            event.wait(timeout=30)
            with self._lock:
                entry = self._cache.get(domain_key)
            if entry is None:
                return False
            if entry.get("fetch_failed"):
                return False
            parser = self._parse_rules(entry.get("rules_text", ""), robots_url)
            return parser.can_fetch(self._user_agent, url)

        # We are the fetcher
        try:
            self._rate_limiter.acquire(robots_url)
            ok, body = self._fetch_robots(robots_url)
            resolved_ip = _resolve_first_ip(host) or ""
            new_entry = {
                "fetched_at": now,
                "resolved_ip": resolved_ip,
                "rules_text": body,
                "fetch_failed": not ok,
            }
            with self._lock:
                self._cache[domain_key] = new_entry
            self._save()
            if not ok:
                return False
            parser = self._parse_rules(body, robots_url)
            return parser.can_fetch(self._user_agent, url)
        finally:
            with self._lock:
                in_flight = self._inflight.pop(domain_key, None)
            if in_flight is not None:
                in_flight.set()
