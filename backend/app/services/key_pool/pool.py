"""The key pool (from the Gemini Key-Pool Gateway): a pre-flight budget for each Gemini key — requests in the last 60
seconds (RPM), requests and tokens today (RPD, TPD; Google's day ends at midnight Pacific) — and a selector that gives
each request to the key with the most room, the least recently used among equals, so keys take turns. A key that
Google refuses is rested (cooling for a minute, exhausted until midnight Pacific, billing problems for ten minutes) or,
when it's invalid, dead; the request goes on to the next key. Caps come from Settings (GEMINI_*_PER_KEY; 0 = no cap).

Keys are known by key_id, a short hash of the key: their counters and health are saved under it, so reordering or
replacing keys never gives one key another's history. `alias` (key_1, key_2 …) is only their place in the list, for
the monitor."""
import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.key_pool.store import SQLiteStore

logger = logging.getLogger(__name__)

MAX_CONCURRENT_PER_KEY = 16
COOLING_SECS = 60.0
BILLING_COOLING_SECS = 600.0     # credits run out until someone tops them up: look again every ten minutes
HEADROOM_RPM_WEIGHT, HEADROOM_RPD_WEIGHT = 1000.0, 10.0

try:
    from zoneinfo import ZoneInfo
    PACIFIC_TZ: tzinfo = ZoneInfo("America/Los_Angeles")
except Exception:  # noqa: BLE001 — no time-zone database: Pacific standard time, an hour off for half the year
    PACIFIC_TZ = timezone(timedelta(hours=-8))


def _now() -> float:
    return time.time()


def key_id_of(raw_key: str) -> str:
    """A short, stable name for a key that reveals nothing of it."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:12]


def get_current_pacific_day() -> str:
    return datetime.fromtimestamp(_now(), PACIFIC_TZ).strftime("%Y-%m-%d")


def get_pacific_day_start_timestamp() -> float:
    start = datetime.fromtimestamp(_now(), PACIFIC_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def get_next_midnight_pacific_timestamp() -> float:
    tomorrow = (datetime.fromtimestamp(_now(), PACIFIC_TZ) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.timestamp()


def get_next_reset_utc_iso() -> str:
    return datetime.fromtimestamp(get_next_midnight_pacific_timestamp(), timezone.utc).isoformat()


def caps() -> Tuple[int, int, int]:
    """(requests per minute, requests per day, tokens per day) for each key; 0 = no cap."""
    return settings.GEMINI_RPM_PER_KEY, settings.GEMINI_RPD_PER_KEY, settings.GEMINI_TPD_PER_KEY


def _over(used: float, cap: int) -> bool:
    return cap > 0 and used >= cap


def _remaining_ratio(used: float, cap: int) -> float:
    return 1.0 if cap <= 0 else max(0.0, (cap - used) / cap)


_BILLING = ("prepay", "prepayment", "credits are depleted", "credit balance", "billing", "payment required")
_DAILY = re.compile(r"per[\s_-]*day|\bdaily\b")
_RETRY_DELAY = re.compile(r'retry[\s_]?delay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)s', re.IGNORECASE)


def classify_failure(status: int, text: str) -> Tuple[str, float]:
    """What a refusal from Google means for the key that got it: ("cooling" | "exhausted" | "dead" | "none", seconds)."""
    lower = (text or "").lower()
    if status == 402 or (status == 429 and any(word in lower for word in _BILLING)):
        return "cooling", BILLING_COOLING_SECS
    if status == 429:
        if _DAILY.search(lower):
            return "exhausted", 0.0
        match = _RETRY_DELAY.search(text or "")
        wait = float(match.group(1)) + 1.0 if match else COOLING_SECS
        return "cooling", min(300.0, max(5.0, wait))
    if status in (400, 401, 403) and ("api key not valid" in lower or "api_key_invalid" in lower or "key invalid" in lower):
        return "dead", 0.0
    return "none", 0.0


class KeyState(str, Enum):
    HEALTHY = "healthy"
    COOLING = "cooling"
    EXHAUSTED = "exhausted"
    DEAD = "dead"


class KeyAccount:
    """The state and counters of one Gemini key."""

    def __init__(self, alias: str, raw_key: str, store: SQLiteStore) -> None:
        self.alias = alias
        self.raw_key = raw_key
        self.key_id = key_id_of(raw_key)
        self.store = store
        self.state: KeyState = KeyState.HEALTHY
        self.cooling_until: Optional[float] = None
        self.exhausted_until: Optional[float] = None
        self.is_dead: bool = False
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_PER_KEY)
        self.rpm_window: deque[float] = deque()      # when each request of the last 60 s was started
        self.pacific_day: str = get_current_pacific_day()
        self.rpd: int = 0
        self.tpd: int = 0
        self.last_used_at: float = 0.0
        self.in_flight_requests: int = 0
        self.in_flight_tokens: int = 0
        self._lock = asyncio.Lock()

    @property
    def hint(self) -> str:
        """The last four characters, to tell keys apart in the monitor."""
        return "…" + self.raw_key[-4:]

    async def sync_from_db(self) -> None:
        async with self._lock:
            meta = await self.store.get_key_metadata(self.key_id)
            self.is_dead = meta["is_dead"]
            self.cooling_until = meta["cooling_until"]
            self.exhausted_until = meta["exhausted_until"]
            self.pacific_day = get_current_pacific_day()
            self.rpd, self.tpd = await self.store.get_daily_usage(self.key_id, self.pacific_day)
            self._update_state_internal(_now())

    def _clean_rpm_window(self, now: float) -> None:
        while self.rpm_window and self.rpm_window[0] <= now - 60.0:
            self.rpm_window.popleft()

    def _update_state_internal(self, now: float) -> None:
        if self.is_dead:
            self.state = KeyState.DEAD
            return
        today = get_current_pacific_day()
        if today != self.pacific_day:                # a new Pacific day: the daily counters start again
            self.pacific_day = today
            self.rpd = self.tpd = 0
            self.exhausted_until = None
            self.state = KeyState.HEALTHY
            return
        if self.exhausted_until is not None:
            if now >= self.exhausted_until:
                self.exhausted_until = None
                self.rpd = self.tpd = 0
                self.state = KeyState.HEALTHY
            else:
                self.state = KeyState.EXHAUSTED
                return
        _, rpd_cap, tpd_cap = caps()
        if _over(self.rpd + self.in_flight_requests, rpd_cap) or _over(self.tpd + self.in_flight_tokens, tpd_cap):
            self.state = KeyState.EXHAUSTED
            self.exhausted_until = get_next_midnight_pacific_timestamp()
            return
        if self.cooling_until is not None:
            if now >= self.cooling_until:
                self.cooling_until = None
                self.state = KeyState.HEALTHY
            else:
                self.state = KeyState.COOLING
                return
        self.state = KeyState.HEALTHY

    async def get_state_and_headroom(self) -> Tuple[KeyState, float, Dict[str, Any]]:
        """The state, and how much room is left (0 when the key can't take a request now), with the figures."""
        async with self._lock:
            now = _now()
            self._clean_rpm_window(now)
            self._update_state_internal(now)
            rpm_cap, rpd_cap, tpd_cap = caps()
            rpm_used = len(self.rpm_window)
            rpd_used = self.rpd + self.in_flight_requests
            tpd_used = self.tpd + self.in_flight_tokens
            details = {
                "alias": self.alias, "hint": self.hint, "state": self.state.value,
                "rpm_used": rpm_used, "rpm_cap": rpm_cap, "rpd_used": rpd_used, "rpd_cap": rpd_cap,
                "tpd_used": tpd_used, "tpd_cap": tpd_cap,
                "cooling_until": self.cooling_until, "exhausted_until": self.exhausted_until,
            }
            if self.state != KeyState.HEALTHY or _over(rpm_used, rpm_cap) or _over(rpd_used, rpd_cap) or _over(tpd_used, tpd_cap):
                return self.state, 0.0, details
            headroom = (_remaining_ratio(rpm_used, rpm_cap) * HEADROOM_RPM_WEIGHT
                        + _remaining_ratio(rpd_used, rpd_cap) * HEADROOM_RPD_WEIGHT)
            return self.state, headroom, details

    async def rest(self, kind: str, seconds: float = 0.0) -> None:
        """Rest the key after a refusal (classify_failure)."""
        async with self._lock:
            if kind == "cooling":
                self.cooling_until = _now() + seconds
                self.state = KeyState.COOLING
                await self.store.set_key_cooling(self.key_id, self.cooling_until)
                logger.warning(f"Gemini {self.alias} rests for {seconds:.0f}s")
            elif kind == "exhausted":
                self.exhausted_until = get_next_midnight_pacific_timestamp()
                self.state = KeyState.EXHAUSTED
                await self.store.set_key_exhausted(self.key_id, self.exhausted_until)
                logger.warning(f"Gemini {self.alias} is out of quota for today (until midnight Pacific)")
            elif kind == "dead":
                self.is_dead = True
                self.state = KeyState.DEAD
                await self.store.set_key_dead(self.key_id)
                logger.error(f"Gemini {self.alias} was refused as not valid")

    async def reserve(self, est_tokens: int) -> bool:
        """Take a request's share of the budget before asking Google; False when this key has no room."""
        async with self._lock:
            now = _now()
            self._clean_rpm_window(now)
            self._update_state_internal(now)
            rpm_cap, rpd_cap, tpd_cap = caps()
            if self.state != KeyState.HEALTHY or _over(len(self.rpm_window), rpm_cap):
                return False
            if _over(self.rpd + self.in_flight_requests, rpd_cap):
                return False
            if tpd_cap > 0 and self.tpd + self.in_flight_tokens + est_tokens > tpd_cap:
                return False
            self.in_flight_requests += 1
            self.in_flight_tokens += est_tokens
            self.last_used_at = now
            self.rpm_window.append(now)
            return True

    async def reconcile(self, est_tokens: int, actual_tokens: int, success: bool,
                        error_status: Optional[int] = None, error_text: Optional[str] = None) -> None:
        """Replace the reservation with what the request really used, and rest the key if Google refused it."""
        async with self._lock:
            self.in_flight_requests = max(0, self.in_flight_requests - 1)
            self.in_flight_tokens = max(0, self.in_flight_tokens - est_tokens)
            tokens = actual_tokens if actual_tokens > 0 else (est_tokens if success else 0)
            self.pacific_day = get_current_pacific_day()
            try:
                self.rpd, self.tpd = await self.store.add_daily_usage(self.key_id, self.pacific_day, 1, tokens)
            except Exception as e:  # noqa: BLE001 — the counters are only a guide: keep them in memory
                logger.warning(f"Couldn't save {self.alias}'s usage: {e}")
                self.rpd, self.tpd = self.rpd + 1, self.tpd + tokens
        if error_status is not None:
            kind, seconds = classify_failure(error_status, error_text or "")
            if kind == "cooling" and error_status == 429 and caps()[1] > 0 and self.rpd >= caps()[1] * 0.9 \
                    and "resource_exhausted" in (error_text or "").lower():
                kind = "exhausted"
            if kind != "none":
                try:
                    await self.rest(kind, seconds)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Couldn't save {self.alias}'s state: {e}")
        async with self._lock:
            self._update_state_internal(_now())


class KeyReservation:
    """A reserved share of one key's budget; release it exactly once, however the request ended."""

    def __init__(self, account: KeyAccount, est_tokens: int) -> None:
        self.account = account
        self.est_tokens = est_tokens
        self.reconciled = False

    async def release_and_reconcile(self, actual_tokens: int = 0, success: bool = True,
                                    error_status: Optional[int] = None, error_text: Optional[str] = None) -> None:
        if self.reconciled:
            return
        self.reconciled = True
        try:
            await self.account.reconcile(self.est_tokens, actual_tokens, success, error_status, error_text)
        finally:
            self.account.semaphore.release()


class KeyPool:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self.accounts: Dict[str, KeyAccount] = {}     # by key_id, in the order of the keys in Settings
        self.signature: Tuple[str, ...] = ()
        self._sync_lock = asyncio.Lock()

    async def sync_keys(self, keys: List[str]) -> None:
        """Make the pool's keys these (Settings changed): new keys get their saved history, removed ones go."""
        async with self._sync_lock:
            wanted = {key_id_of(k): k for k in keys}
            ordered: Dict[str, KeyAccount] = {}
            for position, (kid, raw) in enumerate(wanted.items(), 1):
                account = self.accounts.get(kid)
                if account is None:
                    account = KeyAccount(f"key_{position}", raw, self.store)
                    await account.sync_from_db()
                account.alias = f"key_{position}"
                ordered[kid] = account
            self.accounts = ordered
            self.signature = tuple(keys)
            logger.info(f"Gemini key pool: {len(self.accounts)} key(s)")

    def all_dead(self) -> bool:
        return bool(self.accounts) and all(a.is_dead for a in self.accounts.values())

    async def get_stats(self) -> Dict[str, Any]:
        """The pool's figures for the monitor: no key itself, only their place, last four characters and state."""
        key_stats, requests, tokens, healthy = [], 0, 0, 0
        for account in self.accounts.values():
            state, headroom, details = await account.get_state_and_headroom()
            details["headroom"] = round(headroom, 2)
            key_stats.append(details)
            requests += details["rpd_used"]
            tokens += details["tpd_used"]
            healthy += 1 if state == KeyState.HEALTHY and headroom > 0 else 0
        return {
            "keys": key_stats,
            "next_reset_utc": get_next_reset_utc_iso(),
            "totals": {
                "requests_today": requests, "tokens_today": tokens,
                "videos_translated_today": await self.store.get_completed_jobs_count_today(get_pacific_day_start_timestamp()),
                "keys_available": healthy, "total_keys": len(self.accounts),
            },
            "recent_jobs": await self.store.get_recent_jobs(limit=20),
        }

    async def acquire_reservation(self, est_tokens: int, exclude: Optional[set] = None) -> Optional[KeyReservation]:
        """Reserve `est_tokens` on the key with the most room (key ids in `exclude` — keys this request already
        tried — are left out); None when no key can take it now."""
        skip = exclude or set()
        candidates: List[Tuple[float, float, KeyAccount]] = []
        for kid, account in self.accounts.items():
            if kid in skip:
                continue
            state, headroom, _ = await account.get_state_and_headroom()
            if state == KeyState.HEALTHY and headroom > 0:
                candidates.append((headroom, -account.last_used_at, account))
        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        for _, _, account in candidates:
            try:
                await asyncio.wait_for(account.semaphore.acquire(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            if await account.reserve(est_tokens):
                return KeyReservation(account, est_tokens)
            account.semaphore.release()
        if candidates:        # every candidate was busy: wait a little for the one with most room
            account = candidates[0][2]
            try:
                await asyncio.wait_for(account.semaphore.acquire(), timeout=3.0)
            except asyncio.TimeoutError:
                return None
            if await account.reserve(est_tokens):
                return KeyReservation(account, est_tokens)
            account.semaphore.release()
        return None

    def soonest_resting_key(self) -> Optional[KeyAccount]:
        """When every usable key is only resting after an ordinary per-minute 429 (not out of credit or quota for
        the day), the one that recovers first: a request may still go to Google on it, which decides — as it did
        before the pool, and a single key's callers keep their own retry timing."""
        now = _now()
        resting = []
        for account in self.accounts.values():
            if account.is_dead:
                continue
            if account.state != KeyState.COOLING or account.cooling_until - now > BILLING_COOLING_SECS / 2:
                return None
            resting.append(account)
        return min(resting, key=lambda a: a.cooling_until) if resting else None

    async def get_next_available_delay(self) -> float:
        """Seconds until some key may take a request again."""
        now = _now()
        delays = []
        for account in self.accounts.values():
            if account.is_dead:
                continue
            for until in (account.cooling_until, account.exhausted_until):
                if until and until > now:
                    delays.append(until - now)
            if account.state == KeyState.HEALTHY:
                delays.append(max(1.0, (account.rpm_window[0] + 60.0 - now) if account.rpm_window else 1.0))
        return max(1.0, min(delays)) if delays else 60.0
