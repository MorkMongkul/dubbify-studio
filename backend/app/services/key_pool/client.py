"""One Gemini request through the key pool: pick the key with the most room, send, report what it used, and — when
Google refuses that key — go on to the next. Callers keep their own prompts, models, timeouts and retries; they hand
over `send(headers)`, the call itself, and get Google's answer back. The key only ever travels in a header, never in a
URL (Movelo logs request URLs).

The pool must never be the reason a request fails: if it breaks (a locked database), the request goes out with the
first key as it did before there was a pool."""
import logging
from typing import Awaitable, Callable, Optional

import httpx

from app.core.config import settings
from app.services.key_pool import get_pool
from app.services.key_pool.pool import KeyPool, KeyReservation

logger = logging.getLogger(__name__)

MAX_KEYS_TRIED = 5
_OUTPUT_TOKENS_GUESS_CAP = 8000
_AUDIO_B64_CHARS_PER_TOKEN = 1000        # ~32 audio tokens a second ≈ 40 KB of base64 a second of FLAC
_GOOGLE = "https://generativelanguage.googleapis.com"


def estimate_tokens(payload: dict) -> int:
    """About how many tokens a request will use, for the pool's budget before it's sent (the real figure replaces it
    afterwards). Text counts ~3 characters a token; inline audio or images by their size, not as the base64 text they
    arrive as; the answer at most _OUTPUT_TOKENS_GUESS_CAP."""
    text_chars, media_tokens = 0, 0

    def walk(parts) -> None:
        nonlocal text_chars, media_tokens
        for part in parts or []:
            if not isinstance(part, dict):
                continue
            text_chars += len(part.get("text") or "")
            data = (part.get("inlineData") or part.get("inline_data") or {}).get("data") or ""
            media_tokens += len(data) // _AUDIO_B64_CHARS_PER_TOKEN

    for content in payload.get("contents") or []:
        walk(content.get("parts") if isinstance(content, dict) else None)
    system = payload.get("systemInstruction") or payload.get("system_instruction") or {}
    walk(system.get("parts") if isinstance(system, dict) else None)
    out = int((payload.get("generationConfig") or {}).get("maxOutputTokens") or 1024)
    return max(1, text_chars // 3 + media_tokens + min(out, _OUTPUT_TOKENS_GUESS_CAP))


def _headers(key: str) -> dict:
    return {"Content-Type": "application/json", "x-goog-api-key": key}


def _synthetic(status: int, message: str, retry_after: Optional[float] = None) -> httpx.Response:
    headers = {"retry-after": str(int(max(1, retry_after)))} if retry_after else {}
    return httpx.Response(
        status, headers=headers, request=httpx.Request("POST", _GOOGLE),
        json={"error": {"code": status, "message": message,
                        "status": "RESOURCE_EXHAUSTED" if status == 429 else "INVALID_ARGUMENT"}})


async def _reconcile(reservation: KeyReservation, resp: Optional[httpx.Response]) -> None:
    """Report the outcome; a failure to do so is logged, never raised."""
    try:
        if resp is None:
            await reservation.release_and_reconcile(success=False)
        elif resp.status_code == 200:
            try:
                tokens = int((resp.json().get("usageMetadata") or {}).get("totalTokenCount") or 0)
            except Exception:  # noqa: BLE001 — a body that isn't JSON still was a success
                tokens = 0
            await reservation.release_and_reconcile(actual_tokens=tokens, success=True)
        else:
            await reservation.release_and_reconcile(success=False, error_status=resp.status_code, error_text=resp.text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Couldn't report a Gemini request to the key pool: {e}")


async def with_key(send: Callable[[dict], Awaitable[httpx.Response]], est_tokens: int) -> httpx.Response:
    """`send(headers)` — one POST to Gemini with those headers — on a key from the pool. Returns Google's answer; a
    transport error propagates (after the key is freed). When no key has room, the answer is a 429 with Retry-After,
    and when every key is invalid, a 400 saying so — what Google itself would say."""
    try:
        pool: KeyPool = await get_pool()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Gemini key pool unavailable, using the first key: {e}")
        return await send(_headers((settings.parsed_gemini_keys() or [settings.GEMINI_API_KEY])[0]))
    if not pool.accounts:        # no key saved: as before, Google says what's wrong
        return await send(_headers(settings.GEMINI_API_KEY))

    tried: set = set()
    last: Optional[httpx.Response] = None
    for _ in range(min(MAX_KEYS_TRIED, len(pool.accounts))):
        try:
            reservation = await pool.acquire_reservation(est_tokens, exclude=tried)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Gemini key pool failed, using the first key: {e}")
            return await send(_headers(pool.accounts and next(iter(pool.accounts.values())).raw_key or ""))
        if reservation is None:
            break
        account = reservation.account
        resp: Optional[httpx.Response] = None
        try:
            resp = await send(_headers(account.raw_key))
        finally:
            await _reconcile(reservation, resp)
        last = resp
        # Quota, credits or an invalid key: another key may do. Anything else — an answer, or a Google-side
        # error — isn't about this key.
        if resp.status_code in (429, 402) or (resp.status_code in (400, 401, 403) and account.is_dead):
            tried.add(account.key_id)
            continue
        return resp

    if last is not None:         # every key we tried was refused: the last refusal is the answer
        return last
    resting = pool.soonest_resting_key()
    if resting is not None:      # all just resting after a per-minute 429: let Google say whether that has passed
        return await send(_headers(resting.raw_key))
    if pool.all_dead():
        return _synthetic(400, "API key not valid. Please pass a valid API key.")
    delay = await pool.get_next_available_delay()
    return _synthetic(429, "Every Gemini key is at its limit for now (Movelo spreads requests over your keys and "
                           "counts each key's limits); trying again shortly.", delay)
