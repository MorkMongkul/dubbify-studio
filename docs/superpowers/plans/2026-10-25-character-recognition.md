# Characters Recognised by Voice and Face — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every analysed line arrives on a *character* recognised by the actor's own voice and talking face (local models), with uncertain lines marked "Who is this?" in the transcript, so voices stop being re-assigned every episode.

**Architecture:** A new backend package `app/services/characters/` computes per-line prints (ERes2NetV2 voice print; YuNet+SFace faces, Light-ASD lip-sync for the speaking face), stores them in `line_prints`, builds per-character profiles from *confirmed* lines, and decides each line (sure / to check). It runs in Stage 2 between transcription and translation (new status `recognizing`), powers Detect speakers without Gemini, upgrades existing projects once, and suggests merges. The editor shows option A (amber rows with two guesses in the transcript) and characters with faces.

**Tech Stack:** FastAPI + async SQLAlchemy (SQLite WAL / Postgres), alembic + `add_missing_columns`; sherpa-onnx (voice prints), onnxruntime (Light-ASD), opencv-python-headless (YuNet/SFace DNN only — frames come from the bundled ffmpeg), numpy; React + TanStack Query + Zustand; PyInstaller sidecar; GitHub Actions (macos-15 + macos-15-intel).

**Spec:** `docs/superpowers/specs/2026-10-25-character-recognition-design.md` (read it first; its *Measured* section gives every parameter's origin).

## Global Constraints

- No PyTorch in the app; models run with sherpa-onnx / onnxruntime / OpenCV DNN. Torch only in the dev-time export script (Task 1).
- Video frames are decoded with the bundled LGPL ffmpeg through `app/core/binaries.py` — never with OpenCV's own video I/O.
- Faces and prints never leave the Mac.
- No DB session open while models run (SQLite has one writer): read → commit → compute (in `asyncio.to_thread`) → write.
- Routes take `Depends(get_db, scope="function")`.
- A column on an existing table needs an alembic migration **and** an `add_missing_columns` entry (`app/core/database.py`); new tables come from `create_all`.
- Types in sync: models → schemas → route → `src/types/index.ts` → `src/api/client.ts` → `src/hooks/useApi.ts`.
- Never change a confirmed line (user pick, hand change, voiced/approved/finished and not "to check") — not analysis re-runs, not identify, not merges, not the upgrade's background work.
- No automatic merges; "Same character?" is only a suggestion.
- The app stays well under 1 GB; DMG growth is reported in Task 2.
- Parameters fixed by the spec (copy exactly): voice print = ERes2NetV2 192-d on `vocals.wav` 16 kHz mono, L2-normalised, none for lines < 0.3 s; faces = YuNet score ≥ 0.7, side ≥ 40 px, every frame at 25 fps; tracks by IoU ≥ 0.3 with gaps ≤ 0.09 s, ≥ 10 frames; Light-ASD crop padded by 0.40 → 224 → grey centre 112×112, 13 MFCCs at 100 Hz (winlen 0.025, winstep 0.010) from int16 audio; speaking face = track with highest mean score if mean > 0; face print = mean SFace (128-d) on every 6th frame, L2-normalised; **sure** = face and voice picks agree, or no speaking face and voice margin (best − second cosine) ≥ 0.10.
- Copy: amber row label "Who is this?"; "Someone else…"; header "N to check" and "Next to check ▸"; suggestion "Same character?" with "Merge" and "Not the same"; warnings "N lines still to check".

## Review Focus

1. **A Mac with 8 GB of RAM analysing a 150 s episode** — memory must stay bounded: faces are cropped the moment they're seen and frames are never kept (the spike was killed at exit 137 holding frames). Test: Task 3 `test_prints_keep_no_frames` (tracemalloc peak < 300 MB on a 60 s 720×1280 synthetic video).
2. **Episodes with no faces, an off-screen speaker, or a missing `vocals.wav`** — prints degrade to voice-only or none; the decision then follows Gemini's grouping or marks "to check"; nothing crashes. Tests: Task 3 `test_a_video_with_no_faces_gives_voice_only`, Task 6 `test_analysis_without_vocals_keeps_gemini_speakers`.
3. **Lines shorter than 0.3 s, silent, or overlapping** — no voice print; such a line is "to check" when profiles exist (never "sure" on no evidence). Test: Task 4 `test_a_line_without_prints_is_to_check`.
4. **The user edits a line's Khmer while its re-translation is in flight** — the user's text wins. Test: Task 8 `test_a_khmer_edit_made_during_retranslation_wins`.
5. **The app quits during the upgrade or background print computation** — next start resumes only the missing prints, never duplicates or re-moves lines. Test: Task 9 `test_upgrade_runs_once_and_resumes_prints`.

---

### Task 1: Light-ASD as ONNX, and MFCCs in numpy

**Files:**
- Create: `scripts/export-light-asd.py` (dev-only; needs torch and Light-ASD at commit pinned in the script, MIT)
- Create: `backend/app/assets/models/lightasd-ava.onnx` (committed, ~4 MB), `backend/app/assets/models/LICENSES.md` (Light-ASD MIT text + the others' licences)
- Create: `backend/app/services/characters/__init__.py`, `backend/app/services/characters/mfcc.py`, `backend/app/services/characters/lips.py`
- Create: `backend/app/tests/fixtures/lightasd_parity.npz`, `backend/app/tests/fixtures/mfcc_reference.npz` (written by the export script)
- Test: `backend/app/tests/test_characters_lips.py`

**Interfaces:**
- Produces: `mfcc(signal: np.ndarray, rate: int = 16000) -> np.ndarray` (shape `(frames, 13)`, python_speech_features defaults: nfilt 26, nfft 512, preemph 0.97, ceplifter 22, appendEnergy, rectangular window); `speaking_scores(audio_int16: np.ndarray, crops: np.ndarray) -> np.ndarray | None` (crops `(T,112,112)` uint8 at 25 fps; returns per-frame scores, `None` if < 5 frames; durations 1, 2, 4 s averaged as the spike did).

- [ ] **Step 1:** Write `scripts/export-light-asd.py`: builds Light-ASD's `ASD_Model` + `lossAV.FC` from the pinned commit, loads `pretrain_AVA_CVPR.model` with `weights_only=True`, exports one ONNX graph `(audio [1, 4T, 13] float32, video [1, T, 112, 112] float32) → scores [T]` with dynamic T, then writes the two fixtures: seeded random input (T = 10) with torch's output, and `python_speech_features.mfcc` of a seeded 1 s int16 signal. It asserts ONNX vs torch max abs diff < 1e-4 before writing. Run it once in a scratch venv; commit only its outputs.
- [ ] **Step 2: Write the failing tests**
```python
def test_mfcc_matches_the_reference_implementation():
    ref = np.load(FIXTURES / "mfcc_reference.npz")
    assert np.allclose(mfcc(ref["signal"]), ref["mfcc"], atol=1e-3)

def test_light_asd_onnx_matches_torch():
    ref = np.load(FIXTURES / "lightasd_parity.npz")
    assert np.allclose(lips.run_onnx(ref["audio"], ref["video"]), ref["scores"], atol=1e-4)

def test_fewer_than_five_frames_give_no_scores():
    assert speaking_scores(np.zeros(16000, np.int16), np.zeros((4, 112, 112), np.uint8)) is None
```
- [ ] **Step 3:** Run `cd backend && venv/bin/python -m pytest app/tests/test_characters_lips.py -v` — Expected: FAIL (module missing).
- [ ] **Step 4:** Implement `mfcc.py` (numpy only — no scipy: DCT-II ortho as a matrix) and `lips.py` (onnxruntime session loaded once, lazily, from `app/assets/models/lightasd-ava.onnx`; `run_onnx(audio, video)` the raw call; `speaking_scores` slices by duration like the spike's `asd_scores`).
- [ ] **Step 5:** Run the tests — Expected: 3 passed.
- [ ] **Step 6: Commit** `feat(characters): Light-ASD lip-sync as ONNX, MFCCs without scipy`

### Task 2: The models inside the app

**Files:**
- Create: `backend/app/assets/models/models.json` — manifest `[{name, file, url, sha256, bytes, licence}]`:
  - `eres2netv2`: `3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx`, `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx`, sha256 `bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239`, 71441526 bytes
  - `sface`: `face_recognition_sface_2021dec.onnx`, `https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx`, sha256 `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`, 38696353 bytes
  - `yunet`: committed `face_detection_yunet_2023mar.onnx`, sha256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`, 232589 bytes
  - `lightasd`: committed `lightasd-ava.onnx` (sha256 of Task 1's file)
- Create: `scripts/fetch-models.sh` (downloads missing files, verifies SHA-256, fails loudly)
- Create: `backend/app/services/characters/models.py`
- Modify: `backend/requirements.txt` (pin `sherpa-onnx`, `onnxruntime`, `opencv-python-headless` to versions with cp311 macOS arm64 **and** x86_64 wheels — the spike used sherpa-onnx 2.0.0, OpenCV 5.0.0), `.gitignore` (the two fetched files), `backend/sidecar_entry.py` (self-test), `.github/workflows/desktop-release.yml` (run `scripts/fetch-models.sh` before `build_sidecar.sh`, cached by `hashFiles('backend/app/assets/models/models.json')`) (bundling already includes `app/assets`); the release notes' *Included software* gains the models' licences from `LICENSES.md`
- Test: `backend/app/tests/test_characters_models.py`

**Interfaces:**
- Produces: `models.path(name: str) -> Path` (raises `ModelMissing` with "run scripts/fetch-models.sh"); `models.self_test() -> str` (loads all four and runs one tiny input each; returns "Character models: ok").

- [ ] **Step 1: Failing tests:** `test_every_model_matches_its_manifest` (each file exists and its SHA-256 equals the manifest), `test_the_models_load_and_run` (`models.self_test() == "Character models: ok"`). Tests that need fetched files skip with the reason "run scripts/fetch-models.sh" when absent.
- [ ] **Step 2:** Run — Expected: FAIL.
- [ ] **Step 3:** Implement manifest, fetch script, `models.py`; extend `self_test()` in `sidecar_entry.py` to print and require "Character models: ok"; CI fetch + cache step; pins.
- [ ] **Step 4:** `scripts/fetch-models.sh && cd backend && venv/bin/pip install -r requirements.txt && venv/bin/python -m pytest app/tests/test_characters_models.py -v` — Expected: 2 passed. Then `backend/build_sidecar.sh` locally — Expected: "Character models: ok" in its self-test; note the backend folder size before/after in the commit message.
- [ ] **Step 5: Commit** `feat(characters): bundle the voice, face and lip models`

### Task 3: Prints for a line

**Files:**
- Create: `backend/app/services/characters/faces.py`, `backend/app/services/characters/voiceprint.py`, `backend/app/services/characters/prints.py`
- Test: `backend/app/tests/test_characters_prints.py`

**Interfaces:**
- Consumes: Task 1 `speaking_scores`, `mfcc`; Task 2 `models.path`.
- Produces:
  - `@dataclass LinePrints: voice: np.ndarray | None; face: np.ndarray | None; speaking_score: float | None; face_crop: bytes | None` (JPEG of the speaking face)
  - `prints_for_spans(video_path: str, vocals_path: str | None, spans: list[tuple[float, float]], with_faces: bool = True) -> list[LinePrints]` (blocking; call via `asyncio.to_thread`)
  - `faces.frames(video_path, start, end, fps=25) -> Iterator[tuple[float, np.ndarray]]` (bundled ffmpeg, `-ss/-to`, rawvideo bgr24 pipe; yields one frame at a time)
- [ ] **Step 1: Failing tests** (synthetic media made with ffmpeg in the test, as `test_media_assets.py` does):
```python
async def test_a_video_with_no_faces_gives_voice_only(tmp_path):        # testsrc2 + a 2 s tone in vocals
    [p] = prints_for_spans(video, vocals, [(0.5, 2.5)])
    assert p.voice is not None and p.voice.shape == (192,) and abs(np.linalg.norm(p.voice) - 1) < 1e-5
    assert p.face is None and p.speaking_score is None

def test_a_line_shorter_than_0_3_s_has_no_voice_print(tmp_path):
    assert prints_for_spans(video, vocals, [(1.0, 1.25)])[0].voice is None

def test_no_vocals_file_gives_no_voice_print(tmp_path):
    assert prints_for_spans(video, None, [(0.5, 2.5)])[0].voice is None

def test_faces_are_followed_by_overlap_and_the_most_speaking_track_wins(monkeypatch):
    # detector and lip scorer stubbed: two tracks, the one scoring > 0 is chosen; < 10 frames ignored
    ...assert chosen track's print is the mean of its every-6th-frame SFace features, L2-normalised

def test_prints_keep_no_frames(tmp_path):                                # Review Focus 1
    tracemalloc.start(); prints_for_spans(video_60s_720x1280, vocals, spans_covering_60s)
    assert tracemalloc.get_traced_memory()[1] < 300 * 1024**2
```
- [ ] **Step 2:** Run `venv/bin/python -m pytest app/tests/test_characters_prints.py -v` — Expected: FAIL.
- [ ] **Step 3:** Implement: `voiceprint.py` (sherpa-onnx `SpeakerEmbeddingExtractor`, 16 kHz mono from `vocals.wav`); `faces.py` (YuNet `FaceDetectorYN` with input size set per video, SFace `FaceRecognizerSF.alignCrop/feature`, IoU tracking, `asd_crop` made immediately per frame); `prints.py` (per span: frames → tracks with crops and every-6th-frame features → `speaking_scores` on the span's int16 audio → speaking track if mean > 0; keep its best-scored face crop as JPEG ≤ 160 px). `with_faces=False` skips faces entirely (Task 11).
- [ ] **Step 4:** Run — Expected: 5 passed.
- [ ] **Step 5: Commit** `feat(characters): voice and speaking-face prints for a line`

### Task 4: Profiles and the decision

**Files:**
- Create: `backend/app/services/characters/decide.py` (pure, no DB)
- Test: `backend/app/tests/test_characters_decide.py`

**Interfaces:**
- Produces:
  - `SURE_VOICE_MARGIN = 0.10`; `UNKNOWN_VOICE_FLOOR: float` (set in Task 14 from ForbiddenMountain; start at 0.35 and keep the test table in step)
  - `@dataclass Profile: voice: np.ndarray | None; face: np.ndarray | None; lines: int`
  - `profile_from(prints: list[LinePrints]) -> Profile` (normalised means of the non-None prints)
  - `@dataclass Decision: speaker_id: str | None; sure: bool; guesses: list[tuple[str, str]]` (≤ 2 `(speaker_id, basis)`, basis ∈ `"voice + face" | "face" | "voice"`)
  - `decide(p: LinePrints, profiles: dict[str, Profile]) -> Decision`
- [ ] **Step 1: Failing tests** (unit vectors built in the test):
```python
def test_face_and_voice_agree_is_sure(): ...assert d.speaker_id == "A" and d.sure and d.guesses == []
def test_face_wins_when_they_disagree_and_both_are_guesses():
    assert d.speaker_id == "B" and not d.sure and d.guesses == [("B", "face"), ("A", "voice")]
def test_off_screen_voice_leading_by_0_10_is_sure(): ...margin 0.10 → sure; 0.09 → to check with the voice's top two
def test_no_profiles_decides_nothing(): assert decide(p, {}) == Decision(None, False, [])
def test_an_unknown_voice_with_no_face_match_is_nobody():   # best voice cosine < UNKNOWN_VOICE_FLOOR
    assert d.speaker_id is None and not d.sure
def test_a_line_without_prints_is_to_check():               # Review Focus 3
    d = decide(LinePrints(None, None, None, None), profiles); assert not d.sure and d.speaker_id is None
def test_a_profile_is_the_normalised_mean_of_its_prints(): ...
```
- [ ] **Step 2:** Run — Expected: FAIL. **Step 3:** Implement. **Step 4:** Run — Expected: 7 passed.
- [ ] **Step 5: Commit** `feat(characters): profiles and the sure / to-check decision`

### Task 5: Data

**Files:**
- Modify: `backend/app/models/models.py` — new `LinePrint` (`line_prints`: `segment_id` PK FK segments, `job_id` FK jobs, `voice_print` LargeBinary null, `face_print` LargeBinary null, `speaking_score` Float null, `face_crop_path` String null, `model_version` String, `made_at`); `Segment.who_check` Text null (JSON), `Segment.who_confirmed` Boolean default False, `Segment.khmer_machine` Text null; `Speaker.face_crop_path` String null; `Project.characters_version` Integer null, `Project.character_dismissals` Text null (JSON list of `[a, b]` sorted pairs); `JobStatus.RECOGNIZING = "recognizing"`
- Create: `backend/alembic/versions/<rev>_characters.py` (down_revision `7b8c9d0e1f2a`; columns; on Postgres `ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'recognizing'` — skip on SQLite)
- Modify: `backend/app/core/database.py` (`add_missing_columns`: segments `who_check TEXT, who_confirmed BOOLEAN, khmer_machine TEXT`; speakers `face_crop_path VARCHAR`; projects `characters_version INTEGER, character_dismissals TEXT` — comment `# 0.5.0 characters`), `backend/app/core/data_folder.py` (`PATH_COLUMNS` += `LinePrint.face_crop_path`, `Speaker.face_crop_path`), job/project/segment deletes (delete `line_prints` first — follow the `stem_pieces` pattern in `routes/jobs.py`, `routes/projects.py`, segment delete/split/replace in `pipeline_common.replace_job_segments`), `pipeline_common` running-status set (`_ORPHANABLE_STATUSES` += RECOGNIZING)
- Modify: `backend/app/schemas/schemas.py` (`SegmentResponse.who_check: list[{speaker_id, basis}] | None`, `who_confirmed: bool`; `SegmentUpdate.who_check`, `who_confirmed`; `SpeakerResponse.face_crop_url` computed like media URLs), `frontend/src/types/index.ts` (same fields; `JobStatus` += `'recognizing'`), `frontend/src/lib/utils.ts` `isJobRunning`
- Test: `backend/app/tests/test_characters_data.py`
- [ ] **Step 1: Failing tests:** `test_an_old_desktop_db_gets_the_new_columns` (like the media task: an SQLite DB without the columns → `init_db` → columns exist); `test_line_prints_go_with_their_segment_job_and_project` (3 deletes leave no rows); `test_segment_response_carries_who_check` (JSON round trip); `test_a_job_left_recognizing_is_recovered` (`recover_orphaned_jobs` settles it like `diarizing`).
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement. **Step 4:** Run the four plus `app/tests/test_media_tables.py` — Expected: pass.
- [ ] **Step 5: Commit** `feat(characters): tables and columns for prints, checks and characters`

### Task 6: Recognition in the analysis

**Files:**
- Create: `backend/app/services/characters/store.py`
- Modify: `backend/app/services/pipeline.py` (`run_analysis_pipeline`), `backend/app/services/subtitle_pipeline.py`
- Test: `backend/app/tests/test_characters_pipeline.py`

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces:
  - `store.is_confirmed(seg: Segment, job: Job) -> bool` — `seg.who_confirmed` or (`seg.who_check is None` and (`seg.tts_audio_path` or `seg.is_approved` or `job.finished_at`))
  - `store.project_profiles(db, project_id: str, exclude_job_id: str | None = None) -> dict[str, Profile]` (from stored prints of confirmed lines; speakers with no confirmed prints are left out)
  - `store.save_prints(db, job_id: str, segment_ids: list[str], prints: list[LinePrints], crops_dir: Path) -> None`
  - `async recognise(db, job: Job, spans, gemini_labels: list[str]) -> tuple[list[str], list[list[dict] | None], list[LinePrints]]` — per line the label to use (a known character's `Speaker.label`, else Gemini's), its `who_check`, and the prints; commits before computing, runs prints in `asyncio.to_thread`
- [ ] **Step 1: Failing tests** (Gemini, translation and prints patched at the service boundary — `pipeline.transcribe_audio`, `pipeline.translate_batch_with_report`, `characters.prints.prints_for_spans`):
```python
async def test_known_characters_get_their_lines_before_translation(...):
    # project with confirmed lines of characters A and B (prints stored); new episode's prints match A on line 0 (sure)
    # and are ambiguous on line 1 → translation's cast is built from labels [A.label, ...]; line 0 who_check None,
    # line 1 who_check == [{"speaker_id": B.id, "basis": "face"}, {"speaker_id": A.id, "basis": "voice"}]
async def test_the_first_episode_keeps_gemini_speakers_and_gets_prints(...):  # no profiles → labels as Gemini's, who_check None, line_prints rows exist
async def test_analysis_without_vocals_keeps_gemini_speakers(...):          # Review Focus 2
async def test_status_goes_recognizing_between_transcribing_and_translating(...)
async def test_khmer_machine_is_the_translation_written(...)                # after analysis, and after Translate missing lines (run_translation)
async def test_reanalysis_keeps_confirmed_characters_on_overlapping_lines(...)  # old confirmed line 2.0–4.0 s on C → new line 2.1–3.9 s on C, who_confirmed True
async def test_subtitle_analysis_recognises_too(...)
```
- [ ] **Step 2:** Run — FAIL. **Step 3:** Implement: in `run_analysis_pipeline` after `transcribe_audio` set `JobStatus.RECOGNIZING` (progress 60), call `recognise`, use its labels for `translation_cast`, `segments_context`, `ensure_speakers`; set `who_check`, `khmer_machine` (also in `run_translation` for the lines it fills); after the swap `save_prints` with the new ids; overlap carry-over for confirmed old lines before the swap (map by `labels_for_spans`-style overlap on actor spans). Same in `subtitle_pipeline`.
- [ ] **Step 4:** Run the tests plus `app/tests/test_api.py app/tests/test_speaker_pipeline.py` — Expected: pass.
- [ ] **Step 5: Commit** `feat(characters): recognise characters before translating`

### Task 7: Detect speakers without Gemini

**Files:** Modify `backend/app/services/pipeline.py` (`run_speaker_detection` → profiles + decisions; prints only for lines lacking them), `backend/app/api/routes/jobs.py` (`/detect-speakers` unchanged URL). Test: `backend/app/tests/test_characters_identify.py`
- [ ] **Step 1: Failing tests:** `test_detect_speakers_never_calls_gemini` (patched `transcribe_audio` asserts not called); `test_detect_speakers_never_changes_a_confirmed_line`; `test_detect_speakers_marks_uncertain_lines` (who_check set); `test_detect_speakers_computes_only_missing_prints` (prints patched: called with the 2 spans lacking rows).
- [ ] **Step 2:** FAIL. **Step 3:** Implement (status `RECOGNIZING`; first episode with no profiles → keep current Gemini path). **Step 4:** pass. **Step 5: Commit** `feat(characters): Detect speakers checks lines against the characters`

### Task 8: Picking a character (and re-translating that line)

**Files:** Modify `backend/app/api/routes/segments.py` (new `POST /segments/{id}/character`; PATCH semantics), `backend/app/services/translator.py` or `pipeline.py` (one-line re-translation reusing `translate_batch_with_report` with `translation_cast`, `translation_story`, `translation_memory` and up to `EARLIER_LINES` earlier lines), schemas (`CharacterPick {speaker_id}`, `CharacterPicked {segment, retranslated: bool, previous_khmer: str}`). Test: `backend/app/tests/test_characters_pick.py`

**Interfaces:**
- Produces: `POST /api/v1/segments/{segment_id}/character` body `{speaker_id}` → `CharacterPicked`; sets `speaker_id`, `who_confirmed=True`, `who_check=None`; re-translates when `khmer_machine` is not None and `khmer_text == khmer_machine`, writing both; PATCH with a changed `speaker_id` and no explicit `who_confirmed`/`who_check` also confirms; PATCH accepts explicit `who_check`/`who_confirmed` (undo restores them).
- [ ] **Step 1: Failing tests:** `test_a_pick_confirms_the_line_and_clears_its_check`; `test_a_pick_retranslates_an_unedited_line` (translation patched; `retranslated` True, `previous_khmer` old text); `test_a_pick_never_rewrites_an_edited_line` (khmer_text ≠ khmer_machine → untouched, `retranslated` False); `test_a_line_from_before_0_5_is_treated_as_edited` (khmer_machine None → untouched); `test_a_khmer_edit_made_during_retranslation_wins` (Review Focus 4: PATCH khmer_text while the patched translator awaits → final text is the user's); `test_patch_restores_who_check_for_undo`; `test_changing_a_lines_voice_is_casting_only` (PATCH `voice_id` alone leaves `speaker_id`, `who_confirmed` and `who_check` as they were).
- [ ] **Step 2:** FAIL. **Step 3:** Implement (commit before the Gemini call; re-read the segment fresh before writing, as `run_translation` does). **Step 4:** pass. **Step 5: Commit** `feat(characters): pick a character — confirms it and fixes the line's Khmer`

### Task 9: Upgrading existing projects

**Files:** Create `backend/app/services/characters/upgrade.py`; modify `backend/app/main.py` lifespan (after `convert_old_jobs`; the prints work runs as a background task held in `_background`, like `resume_interrupted`). Test: `backend/app/tests/test_characters_upgrade.py`

**Interfaces:**
- Produces: `async convert_voice_overrides(db, project_id) -> int` (lines moved); `async upgrade_projects(sessionmaker) -> None` (projects with `characters_version` NULL: convert, set version 1, commit; then prints for every segment lacking a `line_prints` row, job by job, sessions closed while computing; then `refresh_faces`); `async refresh_faces(db, project_id)` (each character's `face_crop_path` = best-scored crop among its confirmed lines).
- [ ] **Step 1: Failing tests:** `test_a_line_voiced_by_another_characters_voice_moves_to_that_character` (one speaker cast with it → moved, `voice_id` cleared, `who_confirmed` True); `test_with_several_characters_on_that_voice_the_one_with_most_lines_wins`; `test_a_voice_no_character_has_becomes_a_new_character` (new Speaker, label not taken, `voice_id` set, empty `display_name`); `test_nothing_else_changes` (text, times, tts paths identical); `test_upgrade_runs_once_and_resumes_prints` (Review Focus 5: second run moves nothing; prints computed only for missing rows — patched `prints_for_spans` call count).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** pass. **Step 5: Commit** `feat(characters): existing projects — one-line voices become characters`

### Task 10: "Same character?" suggestions

**Files:** Create `backend/app/services/characters/suggestions.py`; routes in `backend/app/api/routes/segments.py` (`GET /projects/{id}/character-suggestions`, `POST /projects/{id}/character-suggestions/dismiss {a, b}`); schemas `CharacterSuggestion {a, b, reason: "same voice" | "same voice and face"}`. Test: `backend/app/tests/test_characters_suggestions.py`
- [ ] **Step 1: Failing tests:** `test_two_characters_cast_with_one_voice_are_suggested` (reason "same voice"); `test_profiles_that_match_in_voice_and_face_are_suggested` (thresholds `SAME_VOICE`, `SAME_FACE` constants, start 0.75 / 0.6, tuned in Task 14); `test_a_dismissed_pair_never_comes_back`; `test_nothing_is_merged_by_itself` (suggestions route leaves speakers unchanged).
- [ ] **Step 2–4:** FAIL → implement → pass. **Step 5: Commit** `feat(characters): Same character? suggestions`

### Task 11: The face step on slow Macs

**Files:** Create `backend/app/services/characters/speed.py`; modify `characters/store.py` callers to pass `with_faces=face_step_enabled()`; add `scripts/bench-characters.py` and a CI step on both chips printing its estimate. Test: `backend/app/tests/test_characters_speed.py`

**Interfaces:**
- Produces: `FACE_STEP_BUDGET_SECS = 180` (per 120 s episode); `face_step_enabled() -> bool` — first call times a fixed synthetic workload (YuNet on 25 frames 720×1280, SFace ×5, Light-ASD on 50 frames, ERes2NetV2 on 5 s) and extrapolates to a 120 s episode (spike's mix: 50% of time with 1.5 faces on screen); cached in `DUBIFY_DATA_DIR/character-speed.json` per app version.
- [ ] **Step 1: Failing tests:** `test_a_slow_estimate_turns_faces_off` (patched timer → estimate 200 s → False); `test_the_estimate_is_cached_per_app_version`.
- [ ] **Step 2–4:** FAIL → implement → pass; run `scripts/bench-characters.py` locally (Apple chip) and record its estimate in the commit message. **Step 5: Commit** `feat(characters): voice only where the face step would be too slow`

### Task 12: "Who is this?" in the transcript

**Files:** Create `frontend/src/lib/whoCheck.ts` (pure); modify `frontend/src/api/client.ts` (`segments.pickCharacter`), `frontend/src/hooks/useApi.ts` (`usePickCharacter`, mutateAsync), `frontend/src/lib/historyHelpers.ts` (`recordCharacterPick(qc, jobId, before: {speaker_id, who_check, who_confirmed, khmer_text}, after)`), `frontend/src/store/editorStore.ts` (`checkingLineId`), `frontend/src/components/transcript/TranscriptPanel.tsx` (header chip + Next; amber row with two guesses + "Someone else…" using the existing speaker picker / New speaker box; keys 1/2; the line's existing speaker select also goes through the pick); Test: `frontend/tests/whoCheck.test.ts`

**Interfaces:**
- Produces: `toCheck(segments: Segment[]) -> Segment[]` (who_check set, in time order); `nextToCheck(segments, afterId: string | null) -> Segment | null` (wraps); `guessChips(seg, speakers) -> {speaker, basis, key: 1 | 2}[]`.
- [ ] **Step 1: Failing tests** (`node --test`): `toCheck` order and count; `nextToCheck` wraps and skips confirmed; `guessChips` maps bases and keys and drops a deleted speaker.
- [ ] **Step 2:** `cd frontend && npm test` — FAIL. **Step 3:** Implement the pure lib, then the UI per option A (`.superpowers/brainstorm/…/who-check.html`): amber `inset 3px` rule, chips with face thumbnail (`face_crop_url`, else the speaker colour initial), basis, key; header "N to check" + "Next to check ▸" (selects `checkingLineId`, scrolls, seeks the playhead to the line); keys 1/2 apply to `checkingLineId` only, never while typing (`isTypingTarget`); a pick shows "Khmer rewritten for <name>" when `retranslated`, and ⌘Z restores speaker, check and Khmer in one step.
- [ ] **Step 4:** `npm run lint && npx tsc -b && npm test` — pass. **Step 5: Commit** `feat(characters): Who is this? in the transcript`

### Task 13: Characters, suggestions and warnings

**Files:** Modify `frontend/src/components/panels/SpeakersPanel.tsx` (face thumbnail on each card; a "Same character?" list: both faces, ▶ a line of each, **Merge** = existing merge + `recordSpeakerMerge`, **Not the same** = dismiss), `client.ts`/`useApi.ts` (suggestions + dismiss), `TranscriptPanel.tsx` (Voice all: "N lines still to check" confirm when `toCheck` non-empty), `frontend/src/components/panels/StoryPanel.tsx` (Mark finished: same warning), `frontend/src/lib/jobProgress.ts` (`recognizing` step and wording "Recognizing characters…"; ring: `diarizing` 0–60, `recognizing` 60–80 with `usualSecs: (v) => 10 + 0.6 * v`, then as today), `frontend/tests/jobProgress.test.ts`.
- [ ] **Step 1: Failing test:** `jobProgress` — a job `recognizing` for 30 s of a 100 s video shows between 60 and 80 and the title "Recognizing characters…".
- [ ] **Step 2–4:** FAIL → implement → `npm run lint && npx tsc -b && npm test` pass. **Step 5: Commit** `feat(characters): characters with faces, Same character?, warnings`

### Task 14: Measured on the owner's project, docs, end to end

**Files:** Create `scripts/measure-characters.py` (args: data-folder copy, project id, optional corrections JSON `{segment_id: speaker_label}`; runs the shipped `prints_for_spans`/`decide`/upgrade on the copy; "as it happens"; prints overall right %, sure coverage, sure accuracy; exits 1 below 68% / 50% / 83%); modify `CLAUDE.md` (a *Characters (0.5.0)* paragraph under Architecture: prints, confirmed rule, decision, statuses, upgrade, suggestions, Intel rule, measurement command), `decide.UNKNOWN_VOICE_FLOOR` and `suggestions.SAME_VOICE/SAME_FACE` (set from the run; keep the unit-test tables in step).
- [ ] **Step 1:** Make a read-only copy of the owner's data (`sqlite3 -readonly … .backup`, uploads cloned with `/bin/cp -Rc`), corrections file with ep10's line at 1:26.3 → Meng Piseth's character. Run `backend/venv/bin/python -I scripts/measure-characters.py <copy> e4e45ca2-8638-4f3b-85bb-14d0faf0a7e8 --corrections <file>` — Expected: ≥ 68% right, sure on ≥ 50% at ≥ 83%. Tune the floor/thresholds, re-run, record the numbers in the commit message; delete the copy.
- [ ] **Step 2:** End to end in WebKit on a scratch backend with the copied project (no keys except Gemini for the one-line re-translate if available): analyse ep11 → "N to check", chips, keys 1/2, Next to check, a pick rewrites Khmer and ⌘Z restores it, Same character? Merge / Not the same, Voice all warning. Screenshots to the scratchpad.
- [ ] **Step 3:** Full suites: `cd backend && venv/bin/python -m pytest app/tests -q`, `cd frontend && npm run lint && npm test && npm run build` — Expected: all pass.
- [ ] **Step 4: Commit** `docs: characters recognised by voice and face`
