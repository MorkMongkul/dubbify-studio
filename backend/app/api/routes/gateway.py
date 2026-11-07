"""The Gemini key pool's monitor and gateway (0.6.0): GET /gateway is the Key Monitor page, /gateway/stats its figures,
and /gateway/translate and /gateway/v1/chat/completions let other local tools use the same keys. Like the rest of the
API this has no login and listens on 127.0.0.1 only, so any program on this Mac can spend the saved keys through it —
the same as it can already do through /api/v1. Nothing here ever returns a key: keys are shown as key_1, key_2 …
with their last four characters."""
import re
import time
import uuid
from typing import Annotated, Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.gateway_dashboard import DASHBOARD_HTML
from app.services import gemini_models, key_pool
from app.services.key_pool.client import estimate_tokens, with_key
from app.services.key_pool.gemini import Translator
from app.services.key_pool.pool import classify_failure

router = APIRouter(tags=["Gateway"])

MAX_SEGMENTS = 220
_MODEL = re.compile(r"[A-Za-z0-9._-]{1,100}")


@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML, headers={"Cache-Control": "no-cache"})


@router.get("/stats")
async def stats() -> Dict[str, Any]:
    pool = await key_pool.get_pool()
    return await pool.get_stats()


@router.post("/keys/{alias}/test")
async def test_key(alias: Annotated[str, Field(max_length=20)]) -> Dict[str, Any]:
    """A live ping on exactly this key (not the rotation's pick): ok + latency in ms, or why it failed."""
    pool = await key_pool.get_pool()
    account = next((a for a in pool.accounts.values() if a.alias == alias), None)
    if account is None:
        raise HTTPException(status_code=404, detail="No such key")
    model = gemini_models.current_model("translation")
    payload = {"contents": [{"parts": [{"text": "Reply with the single word OK."}]}],
               "generationConfig": {"maxOutputTokens": 64}}
    headers = {"Content-Type": "application/json", "x-goog-api-key": account.raw_key}
    started = time.perf_counter()
    try:
        resp = await gemini_models._post(gemini_models.endpoint(model), payload, headers,
                                         httpx.Timeout(30.0, connect=10.0))
    except httpx.HTTPError as e:
        return {"ok": False, "alias": alias, "message": f"Couldn't reach Google ({type(e).__name__})"}
    latency = round((time.perf_counter() - started) * 1000)
    if resp.status_code == 200:
        return {"ok": True, "alias": alias, "latency_ms": latency, "model": model}
    kind, seconds = classify_failure(resp.status_code, resp.text)
    await account.rest(kind, seconds)
    return {"ok": False, "alias": alias, "latency_ms": latency,
            "message": f"{resp.status_code}: {gemini_models.error_message(resp.text)[:160]}"}


class Segment(BaseModel):
    index: int
    text: Annotated[str, Field(max_length=4000)]


class TranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_id: Annotated[str, Field(max_length=200)] = ""
    target_lang: Annotated[str, Field(min_length=2, max_length=30, pattern=r"^[A-Za-z][A-Za-z0-9 _-]*$")]
    source_lang: Annotated[Optional[str], Field(max_length=30)] = None
    glossary: Optional[Dict[str, str]] = None
    segments: Annotated[List[Segment], Field(min_length=1)]


@router.post("/translate")
async def translate(req: TranslateRequest, wait: bool = Query(True)) -> Dict[str, Any]:
    """Translate numbered segments (indices 0…n-1 in order, at most MAX_SEGMENTS). Done in this request — `wait` is
    accepted for the monitor page's sake and changes nothing. Segments that couldn't be done come back with
    `text: null` and are listed in `failed_indices`."""
    if len(req.segments) > MAX_SEGMENTS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_SEGMENTS} segments per request")
    if [s.index for s in req.segments] != list(range(len(req.segments))):
        raise HTTPException(status_code=422, detail="Segment indices must be 0 … n-1, in order, with no gaps")
    if req.glossary is not None and len(req.glossary) > 200:
        raise HTTPException(status_code=422, detail="At most 200 glossary entries")
    pool = await key_pool.get_pool()
    store = key_pool.get_store()
    job_id = uuid.uuid4().hex
    segments = [s.model_dump() for s in req.segments]
    try:
        await store.create_job(job_id, req.video_id, req.target_lang, req.source_lang or "", len(segments), {})
        await store.update_job(job_id, "processing")
    except Exception:  # noqa: BLE001 — the history is an extra
        pass
    results, failed = await Translator(store).all(segments, req.target_lang, req.glossary)
    status = "done" if not failed else ("failed" if len(failed) == len(results) else "partial")
    try:
        await store.update_job(job_id, status, result_payload=results, failed_indices=failed)
    except Exception:  # noqa: BLE001
        pass
    if status == "failed" and pool.all_dead():
        raise HTTPException(status_code=401, detail="API key not valid. Check the keys in Settings.")
    return {"job_id": job_id, "status": status, "segments": results, "failed_indices": failed}


@router.get("/translate/jobs/{job_id}")
async def job(job_id: Annotated[str, Field(max_length=64)]) -> Dict[str, Any]:
    await key_pool.get_pool()
    found = await key_pool.get_store().get_job(job_id)
    if not found:
        raise HTTPException(status_code=404, detail="No such job")
    found.pop("request_payload", None)
    return found


class ChatMessage(BaseModel):
    role: str
    content: Annotated[str, Field(max_length=200_000)]


class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: Annotated[List[ChatMessage], Field(max_length=200)]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = Field(default=None, ge=1, le=65536)


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest) -> Dict[str, Any]:
    """OpenAI-style chat completions on the pool's keys."""
    model = req.model or gemini_models.current_model("translation")
    if not _MODEL.fullmatch(model):
        raise HTTPException(status_code=422, detail="Invalid model name")
    system, contents = None, []
    for m in req.messages:
        if m.role.lower() == "system":
            system = {"parts": [{"text": m.content}]}
        else:
            contents.append({"role": "user" if m.role.lower() == "user" else "model", "parts": [{"text": m.content}]})
    payload: Dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": "Hello"}]}]}
    if system:
        payload["system_instruction"] = system
    config = {k: v for k, v in (("temperature", req.temperature), ("maxOutputTokens", req.max_tokens)) if v is not None}
    if config:
        payload["generationConfig"] = config
    try:
        resp = await with_key(lambda headers: gemini_models._post(gemini_models.endpoint(model), payload, headers,
                                                                  httpx.Timeout(120.0, connect=15.0)),
                              estimate_tokens(payload))
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Couldn't reach Google ({type(e).__name__})")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code if resp.status_code in (400, 401, 403, 404, 429) else 502,
                            detail=gemini_models.error_message(resp.text)[:200])
    data = resp.json()
    usage = data.get("usageMetadata", {})
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}]
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": parts[0].get("text", "")},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": usage.get("promptTokenCount", 0), "completion_tokens": usage.get("candidatesTokenCount", 0),
                  "total_tokens": usage.get("totalTokenCount", 0)},
    }
