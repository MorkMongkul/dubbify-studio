"""
Which Gemini model each feature uses, and what happens when Google retires one.

Google retires model versions on its own schedule. A retired model answers
generateContent with 404 "This model ... is no longer available to new users"
— while still appearing in the model list, so the list alone can't be trusted.
Pinning one model name per feature therefore breaks the app every time a
version is retired (translation went down this way with gemini-2.5-flash-lite,
and the Assistant earlier with gemini-2.5-flash).

So each feature has an ordered list of models: the one configured in Settings,
then Google's "-latest" aliases (which Google keeps pointed at a current
model). When a model answers "not available", the feature moves to the next
one; when the known names are all gone, it asks Google for the newest listed
model of the same kind. The switch is remembered for the rest of the session
and logged, so the setting can be updated.

Only "this model isn't available" errors switch models. Rate limits, server
errors and bad requests go back to the caller, which owns its retry policy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


@dataclass(frozen=True)
class _Purpose:
    setting: str                 # Settings attribute holding the configured model
    fallbacks: tuple[str, ...]   # tried in order after the configured model
    family: re.Pattern           # what a replacement from the model list must look like
    choices: re.Pattern          # what Settings offers for this job (a wider choice than `family`)
    aliases: tuple[str, ...]     # Google's maintained aliases Settings offers first


_VERSION = r"gemini-\d+(?:\.\d+)?"
_TTS = re.compile(rf"^{_VERSION}-flash(?:-lite)?(?:-preview)?-tts(?:-preview)?$")
PURPOSES: dict[str, _Purpose] = {
    "translation": _Purpose(
        "GEMINI_MODEL", ("gemini-flash-lite-latest", "gemini-flash-latest"),
        re.compile(rf"^{_VERSION}-flash-lite$"), re.compile(rf"^{_VERSION}-flash(?:-lite)?$"),
        ("gemini-flash-lite-latest", "gemini-flash-latest")),
    "asr": _Purpose(
        "GEMINI_ASR_MODEL", ("gemini-flash-latest",),
        re.compile(rf"^{_VERSION}-flash$"), re.compile(rf"^{_VERSION}-(?:flash|pro)$"),
        ("gemini-flash-latest", "gemini-pro-latest")),
    "chat": _Purpose(
        "GEMINI_CHAT_MODEL", ("gemini-flash-latest",),
        re.compile(rf"^{_VERSION}-flash$"), re.compile(rf"^{_VERSION}-(?:flash|pro)$"),
        ("gemini-flash-latest", "gemini-pro-latest")),
    # Google has no "-latest" alias for speech models — the model list is the fallback.
    "tts": _Purpose("GEMINI_TTS_MODEL", (), _TTS, _TTS, ()),
}

# Phrases Google uses (with 400/403) for a model that doesn't exist or can't be used.
_UNAVAILABLE_HINTS = (
    "no longer available",
    "is not found",
    "not found for api version",
    "not supported for generatecontent",
    "does not exist",
    "has been deprecated",
    "has been retired",
    "unknown model",
    "model not found",
)

# Bound on model switches within one request (configured + aliases + discovered).
_MAX_SWITCHES = 6


@dataclass
class _State:
    configured: str
    current: str
    unavailable: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_states: dict[str, _State] = {}


def reset() -> None:
    """Forget every switch (tests; the next request starts from Settings again)."""
    _states.clear()


def default_model(purpose: str) -> str:
    """The built-in choice — what "Automatic" in Settings (an empty value) means."""
    return type(settings).model_fields[PURPOSES[purpose].setting].default


def _state(purpose: str) -> _State:
    configured = (getattr(settings, PURPOSES[purpose].setting, "") or "").strip() or default_model(purpose)
    state = _states.get(purpose)
    # A different model chosen in Settings is a fresh start.
    if state is None or state.configured != configured:
        state = _State(configured=configured, current=configured)
        _states[purpose] = state
    return state


def current_model(purpose: str) -> str:
    """The model this feature should use right now."""
    return _state(purpose).current


def in_use() -> dict[str, str]:
    """Models that replaced the configured one this session, for diagnostics."""
    return {p: s.current for p, s in _states.items() if s.current != s.configured}


def endpoint(model: str) -> str:
    return f"{API_BASE}/models/{model}:generateContent"


def error_message(body: str) -> str:
    """Google's error message from a response body, or the start of the body."""
    try:
        return str(json.loads(body).get("error", {}).get("message") or body)[:300]
    except (ValueError, AttributeError):
        return (body or "")[:300]


def is_model_unavailable(status_code: int, body: str) -> bool:
    """True when the error means this model can't be used — retired, renamed or unknown."""
    if status_code == 404:
        return True
    if status_code in (400, 403):
        text = (body or "").lower()
        return any(hint in text for hint in _UNAVAILABLE_HINTS)
    return False


def _version_key(name: str) -> tuple:
    match = re.match(r"gemini-(\d+)(?:\.(\d+))?", name)
    major, minor = (int(match.group(1)), int(match.group(2) or 0)) if match else (0, 0)
    # Newest first; a stable model beats a preview, a full model beats a lite one.
    return major, minor, "preview" not in name, "lite" not in name


async def _list_models() -> list[dict]:
    """Every model this API key can see (name + supported methods). [] when the list can't be read."""
    models: list[dict] = []
    token = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
            while True:
                params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
                resp = await client.get(f"{API_BASE}/models", params=params,
                                        headers={"x-goog-api-key": settings.GEMINI_API_KEY})
                if resp.status_code != 200:
                    logger.warning(f"Couldn't list Gemini models ({resp.status_code}): {error_message(resp.text)}")
                    return models
                data = resp.json()
                models += data.get("models", [])
                token = data.get("nextPageToken")
                if not token:
                    return models
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Couldn't list Gemini models: {type(e).__name__}")
        return models


async def list_names() -> list[str]:
    """Models this key can generate with (listed ≠ usable: retired ones can stay listed)."""
    return [
        m.get("name", "").removeprefix("models/")
        for m in await _list_models()
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


async def _discover(purpose: str, exclude: set[str]) -> str | None:
    """The newest listed model of this feature's kind that hasn't already failed."""
    family = PURPOSES[purpose].family
    candidates = sorted((n for n in await list_names() if family.match(n) and n not in exclude),
                        key=_version_key, reverse=True)
    return candidates[0] if candidates else None


def options_for(purpose: str, listed: list[str]) -> list[str]:
    """What Settings offers for this job: Google's maintained aliases first, then newest first."""
    spec = PURPOSES[purpose]
    aliases = [a for a in spec.aliases if a in listed]
    versions = sorted((n for n in listed if spec.choices.match(n)), key=_version_key, reverse=True)
    return aliases + versions


async def probe(purpose: str, model: str) -> tuple[bool, str]:
    """Ask `model` for a tiny answer — the only way to know a listed model really works."""
    if purpose == "tts":
        payload = {
            "contents": [{"parts": [{"text": "Read this out loud: OK."}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}},
        }
    else:
        payload = {"contents": [{"parts": [{"text": "Reply with the single word OK."}]}],
                   "generationConfig": {"maxOutputTokens": 64}}
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY}
    try:
        resp = await _post(endpoint(model), payload, headers, httpx.Timeout(60.0, connect=10.0))
    except httpx.HTTPError as e:
        return False, f"Couldn't reach Google ({type(e).__name__}) — check your connection."
    if resp.status_code == 200:
        return True, f"{model} works with your key."
    return False, f"{model} didn't work ({resp.status_code}): {error_message(resp.text)}"


async def switch_model(purpose: str, failed_model: str, reason: str = "") -> str | None:
    """
    `failed_model` turned out to be unavailable: pick the next model for this
    feature and remember it. Returns None when nothing usable is left.
    """
    state = _state(purpose)
    async with state.lock:
        if state.current != failed_model and state.current not in state.unavailable:
            return state.current   # a concurrent request already switched
        state.unavailable.add(failed_model)
        candidates = [state.configured, *PURPOSES[purpose].fallbacks]
        replacement = next((m for m in candidates if m and m not in state.unavailable), None)
        if replacement is None:
            replacement = await _discover(purpose, state.unavailable)
        if replacement is None:
            logger.error(
                f"No usable Gemini model left for {purpose} — tried {sorted(state.unavailable)}. "
                f"Last error: {error_message(reason)}"
            )
            return None
        state.current = replacement
        logger.warning(
            f"Gemini model {failed_model} isn't available for {purpose} ({error_message(reason)}) — "
            f"using {replacement} instead. Set it in Settings to make this permanent."
        )
        return replacement


_http_client: httpx.AsyncClient | None = None


async def _post(url: str, payload: dict, headers: dict, timeout) -> httpx.Response:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient()
    return await _http_client.post(url, json=payload, headers=headers, timeout=timeout)


async def generate_content(purpose: str, payload: dict, *, timeout) -> httpx.Response:
    """
    POST generateContent for this feature, moving to another model when the
    current one is unavailable. Any other response — success or error — is
    returned as it is; transport errors propagate.
    """
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY}
    model = current_model(purpose)
    resp = await _post(endpoint(model), payload, headers, timeout)
    for _ in range(_MAX_SWITCHES):
        if resp.status_code == 200 or not is_model_unavailable(resp.status_code, resp.text):
            return resp
        replacement = await switch_model(purpose, model, resp.text)
        if replacement is None:
            return resp
        model = replacement
        resp = await _post(endpoint(model), payload, headers, timeout)
    return resp
