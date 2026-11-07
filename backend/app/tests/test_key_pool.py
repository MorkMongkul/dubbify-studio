"""
The Gemini key pool (0.5.8, app/services/key_pool; from the Gemini Key-Pool Gateway): several keys share the work, each
is counted against its own caps BEFORE Google is asked, a key that is out of quota is skipped and the request moves to
the next one, and nothing here can break a request when the pool itself fails. Google is replaced by a fake `send`;
the keys are made up.
"""
import json

import httpx
import pytest

from app.core.config import settings
from app.services import key_pool
from app.services.key_pool import pool as pool_module
from app.services.key_pool.client import estimate_tokens, with_key

pytestmark = pytest.mark.asyncio

K1, K2, K3 = ("AIzaSy" + c * 33 for c in "ABC")


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", f"{K1}\n{K2}\n{K3}")
    monkeypatch.setattr(settings, "GEMINI_RPM_PER_KEY", 12)
    monkeypatch.setattr(settings, "GEMINI_RPD_PER_KEY", 450)
    monkeypatch.setattr(settings, "GEMINI_TPD_PER_KEY", 900_000)


def _ok(tokens=100):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}],
                                     "usageMetadata": {"totalTokenCount": tokens}})


def _error(status, message="", **extra):
    return httpx.Response(status, json={"error": {"code": status, "message": message, **extra}})


class Google:
    """A fake Google: which key was used each time, and what each key answers."""

    def __init__(self, answers=None):
        self.used: list[str] = []
        self.answers = answers or {}

    async def __call__(self, headers):
        key = headers["x-goog-api-key"]
        self.used.append(key)
        answer = self.answers.get(key, _ok)
        return answer() if callable(answer) else answer


async def test_requests_rotate_evenly_over_the_keys():
    google = Google()
    for _ in range(9):
        assert (await with_key(google, 500)).status_code == 200
    assert [google.used.count(k) for k in (K1, K2, K3)] == [3, 3, 3]


async def test_a_key_that_is_out_of_quota_is_skipped_and_the_request_moves_on():
    google = Google({K1: lambda: _error(429, "Quota exceeded for metric 'generate_content requests per minute'")})
    resp = await with_key(google, 500)
    assert resp.status_code == 200
    assert google.used[0] != google.used[-1] or len(google.used) == 1   # the request ended on another key
    pool = await key_pool.get_pool()
    states = {a.alias: a.state.value for a in pool.accounts.values()}
    assert "cooling" in states.values()
    google.used.clear()
    for _ in range(6):
        await with_key(google, 500)
    cooled = next(a.raw_key for a in pool.accounts.values() if a.state.value == "cooling")
    assert cooled not in google.used          # not asked again while it cools


async def test_a_daily_limit_rests_the_key_until_pacific_midnight():
    google = Google({K1: lambda: _error(429, "Quota exceeded for metric GenerateRequestsPerDayPerProjectPerModel-FreeTier")})
    for _ in range(4):
        await with_key(google, 500)
    pool = await key_pool.get_pool()
    first = next(a for a in pool.accounts.values() if a.raw_key == K1)
    assert first.state.value == "exhausted" and first.exhausted_until > pool_module._now()


async def test_depleted_credits_move_on_and_are_tried_again_later_not_every_minute():
    google = Google({K1: lambda: _error(402, "Prepay credits depleted. Purchase credits to resume service.")})
    resp = await with_key(google, 500)
    assert resp.status_code == 200
    pool = await key_pool.get_pool()
    first = next(a for a in pool.accounts.values() if a.raw_key == K1)
    assert first.state.value == "cooling" and first.cooling_until - pool_module._now() > 300
    depleted = lambda: _error(429, "RESOURCE_EXHAUSTED: your prepayment credits are depleted")  # noqa: E731
    billing = Google({K2: depleted, K3: depleted})
    await with_key(billing, 500)
    second = next(a for a in pool.accounts.values() if a.raw_key == billing.used[0])
    assert second.cooling_until - pool_module._now() > 300


async def test_an_invalid_key_is_dead_for_good_but_a_new_key_in_its_place_is_not(monkeypatch):
    google = Google({K1: lambda: _error(400, "API key not valid. Please pass a valid API key.")})
    for _ in range(3):
        await with_key(google, 500)
    pool = await key_pool.get_pool()
    assert next(a for a in pool.accounts.values() if a.raw_key == K1).state.value == "dead"
    # the owner replaces it: same position, other key — nothing of the old one's mark carries over
    monkeypatch.setattr(settings, "GEMINI_API_KEY", f"{'AIzaSy' + 'Z' * 33}\n{K2}\n{K3}")
    fresh = Google()
    for _ in range(6):
        await with_key(fresh, 500)
    assert ("AIzaSy" + "Z" * 33) in fresh.used


async def test_when_every_key_is_invalid_the_answer_says_so_not_that_it_is_busy():
    google = Google({k: (lambda: _error(400, "API key not valid. Please pass a valid API key.")) for k in (K1, K2, K3)})
    for _ in range(4):
        resp = await with_key(google, 500)
    assert resp.status_code == 400 and "API key not valid" in resp.text


async def test_keys_at_their_caps_answer_429_with_retry_after_and_google_is_not_asked(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", K1)
    monkeypatch.setattr(settings, "GEMINI_RPM_PER_KEY", 2)
    google = Google()
    for _ in range(2):
        assert (await with_key(google, 100)).status_code == 200
    resp = await with_key(google, 100)
    assert resp.status_code == 429 and int(resp.headers["retry-after"]) >= 1
    assert len(google.used) == 2
    assert "credit" not in resp.text.lower() and "billing" not in resp.text.lower()   # translator reads those as "wait won't help"


async def test_a_daily_token_cap_is_checked_before_asking(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", K1)
    monkeypatch.setattr(settings, "GEMINI_TPD_PER_KEY", 1000)
    google = Google({K1: lambda: _ok(500)})
    assert (await with_key(google, 600)).status_code == 200
    assert (await with_key(google, 600)).status_code == 429          # 500 used (reported) + 600 > 1000
    assert len(google.used) == 1


async def test_a_cap_of_zero_is_no_cap(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", K1)
    for name in ("GEMINI_RPM_PER_KEY", "GEMINI_RPD_PER_KEY", "GEMINI_TPD_PER_KEY"):
        monkeypatch.setattr(settings, name, 0)
    google = Google()
    for _ in range(40):
        assert (await with_key(google, 50_000)).status_code == 200


async def test_counters_follow_the_key_through_a_reorder_and_a_restart(monkeypatch):
    google = Google()
    for _ in range(6):
        await with_key(google, 500)
    pool = await key_pool.get_pool()
    before = {a.raw_key: a.rpd for a in pool.accounts.values()}
    assert sum(before.values()) == 6
    key_pool.reset()                                           # the app restarted ...
    monkeypatch.setattr(settings, "GEMINI_API_KEY", f"{K3}\n{K1}\n{K2}")   # ... with the keys in another order
    pool = await key_pool.get_pool()
    assert {a.raw_key: a.rpd for a in pool.accounts.values()} == before
    assert [a.alias for a in pool.accounts.values()] == ["key_1", "key_2", "key_3"]


async def test_adding_and_removing_keys_takes_effect_without_a_restart(monkeypatch):
    google = Google()
    await with_key(google, 100)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", K2)
    google.used.clear()
    for _ in range(3):
        await with_key(google, 100)
    assert set(google.used) == {K2}


async def test_no_keys_sends_the_request_as_before(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    google = Google()
    resp = await with_key(google, 100)
    assert resp.status_code == 200 and google.used == [""]


async def test_a_pool_that_breaks_never_breaks_the_request(monkeypatch):
    pool = await key_pool.get_pool()

    async def broken(*args, **kwargs):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(pool.store, "add_daily_usage", broken)
    monkeypatch.setattr(pool.store, "get_daily_usage", broken)
    google = Google()
    resp = await with_key(google, 100)
    assert resp.status_code == 200 and google.used


async def test_a_transport_error_is_reported_to_the_caller_and_frees_the_key():
    async def offline(headers):
        raise httpx.ConnectError("no network")
    with pytest.raises(httpx.ConnectError):
        await with_key(offline, 100)
    pool = await key_pool.get_pool()
    assert all(a.in_flight_requests == 0 and a.in_flight_tokens == 0 for a in pool.accounts.values())


async def test_the_statistics_name_keys_by_position_and_never_show_one():
    await with_key(Google(), 100)
    stats = await (await key_pool.get_pool()).get_stats()
    dump = json.dumps(stats)
    assert all(k not in dump for k in (K1, K2, K3))
    assert [k["alias"] for k in stats["keys"]] == ["key_1", "key_2", "key_3"]
    assert stats["keys"][0]["hint"] == "…" + K1[-4:] and stats["totals"]["total_keys"] == 3


async def test_the_midnight_rollover_starts_a_new_day(monkeypatch):
    google = Google()
    for _ in range(3):
        await with_key(google, 500)
    monkeypatch.setattr(pool_module, "get_current_pacific_day", lambda: "2099-01-01")
    stats = await (await key_pool.get_pool()).get_stats()
    assert stats["totals"]["requests_today"] == 0


async def test_audio_is_estimated_as_audio_not_as_its_base64_text():
    audio = "A" * 4_000_000                                   # ~3 MB of audio sent inline
    payload = {"contents": [{"parts": [{"text": "x" * 3000}, {"inlineData": {"mimeType": "audio/flac", "data": audio}}]}],
               "generationConfig": {"maxOutputTokens": 32768}}
    assert 5_000 < estimate_tokens(payload) < 30_000
    assert estimate_tokens({}) >= 1
