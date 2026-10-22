#!/bin/bash
# Checks that the previous release's data opens in a new one, before the new
# release is published:
#   gh release download v0.2.6 -D <old folder> -p Movelo-Studio-Apple-Silicon.dmg
#   scripts/check-upgrade.sh <old folder>/Movelo-Studio-Apple-Silicon.dmg \
#       <draft folder>/Movelo-Studio-Apple-Silicon.dmg <work dir> ["text only the new editor has"]
# (on an Intel Mac, use the Intel DMGs).
#
# The OLD app's backend starts on a fresh data folder and gets one voice of
# the user's own, a speaker with a chosen built-in voice and an Assistant
# message (apps before 0.2.6 saved it unanswered without an AI key; later ones
# save nothing). Then the NEW app's backend starts on the same folder, and the
# database upgrade, that voice, that speaker, the built-in voices' sections,
# the project's (empty) story notes, the old app's chats, and the editor page it
# serves are checked; the last argument proves the page is the new editor.
# Everything happens in <work dir> (no window, its own HOME and port), so the
# real app's data is never touched. SPEAKER_VOICE picks another built-in voice.
set -euo pipefail
OLD_DMG=$1 NEW_DMG=$2 W=$3 MUST_SHIP=${4:-}
PORT=51991
SPEAKER_VOICE=${SPEAKER_VOICE:-builtin-03-cold-ceo-a}
A="http://127.0.0.1:$PORT/api/v1"

if [ -e "$W" ] && [ ! -e "$W/.check-upgrade" ]; then
  echo "$W already exists and isn't a folder this script made; give a new one"; exit 1
fi
rm -rf "$W"; mkdir -p "$W"/data "$W"/logs "$W"/run "$W"/home; touch "$W/.check-upgrade"

attach() { hdiutil attach -nobrowse -readonly -mountpoint "$2" "$1" >/dev/null; }
detach() { hdiutil detach -quiet "$1" 2>/dev/null || hdiutil detach -force -quiet "$1"; }
cleanup() {
  exec 3>&-
  for m in "$W/mnt-old" "$W/mnt-new"; do [ -d "$m/Movelo Studio.app" ] && detach "$m"; done; true
}
trap cleanup EXIT

start_backend() {   # $1 = mounted volume, $2 = log file
  local exe="$1/Movelo Studio.app/Contents/Resources/backend/movelo-backend"
  rm -f "$W/stdin"; mkfifo "$W/stdin"
  (cd "$W/run" && exec env -i HOME="$W/home" PATH=/usr/bin:/bin:/usr/sbin:/sbin PORT=$PORT \
      DUBIFY_DATA_DIR="$W/data" DUBIFY_LOG_DIR="$W/logs" DUBIFY_INSTANCE_ID=upgrade-check DUBIFY_EXIT_WITH_PARENT=1 \
      DUBIFY_APP_VERSION=check "$exe" < "$W/stdin" > "$2" 2>&1) &
  BACKEND=$!
  exec 3>"$W/stdin"            # keep stdin open: the backend exits when it closes
  for _ in $(seq 90); do
    curl -sf "http://127.0.0.1:$PORT/health/ready" 2>/dev/null | grep -q upgrade-check && return 0
    sleep 1
  done
  echo "backend never became ready:"; tail -20 "$2"; return 1
}
stop_backend() { exec 3>&-; wait "$BACKEND" 2>/dev/null || true; }

python3 -I -c "
import math, struct, wave
with wave.open('$W/ref.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
    w.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(i / 7))) for i in range(48000)))"

echo "== old app: fresh data folder, one voice of my own, a speaker with a chosen voice"
attach "$OLD_DMG" "$W/mnt-old"
start_backend "$W/mnt-old" "$W/old.log"
curl -s -o "$W/created.json" -w "create voice: HTTP %{http_code}\n" -X POST "$A/voices/" \
  -F "name=Old voice" -F mode=clone -F "reference_audio=@$W/ref.wav"
PROJECT=$(curl -s -X POST "$A/projects/" -H 'Content-Type: application/json' -d '{"name":"Upgrade check"}' \
  | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s -o "$W/speaker.json" -w "create speaker: HTTP %{http_code}\n" -X POST "$A/projects/$PROJECT/speakers" \
  -H 'Content-Type: application/json' -d "{\"display_name\":\"Doctor Yun\",\"voice_id\":\"$SPEAKER_VOICE\"}"
curl -s -o /dev/null -w "Assistant message without an AI key: HTTP %{http_code}\n" -X POST \
  "$A/chat/$PROJECT/messages" -H 'Content-Type: application/json' -d '{"text":"How was 老公 translated?"}'
# What the old app kept: its chat list (0.2.6 on), or its one chat per project (before)
curl -s -o "$W/old-threads.json" -w "%{http_code}" "$A/chat/$PROJECT/threads" > "$W/old-threads.code"
curl -s -o "$W/old-messages.json" -w "%{http_code}" "$A/chat/$PROJECT/messages" > "$W/old-messages.code"
stop_backend; detach "$W/mnt-old"

echo "== new app: same data folder"
attach "$NEW_DMG" "$W/mnt-new"
cp "$W/mnt-new/Movelo Studio.app/Contents/Resources/backend/_internal/app/assets/builtin_voices/manifest.json" \
  "$W/new-manifest.json"
start_backend "$W/mnt-new" "$W/new.log"
curl -s "$A/voices/" > "$W/voices.json"
curl -s "$A/projects/$PROJECT/speakers" > "$W/speakers.json"
curl -s "$A/projects/$PROJECT/story" > "$W/story.json"
curl -s "$A/chat/$PROJECT/threads" > "$W/threads.json"
curl -s -D "$W/page-headers.txt" -o "$W/page.html" "http://127.0.0.1:$PORT/"
grep -i '^cache-control' "$W/page-headers.txt" | tr -d '\r' | sed 's/^/editor page caching: /'
: > "$W/editor-code.js"
for js in $(grep -o 'src="/assets/[^"]*\.js"' "$W/page.html" | sed 's/^src="//; s/"$//'); do
  curl -s "http://127.0.0.1:$PORT$js" >> "$W/editor-code.js"
done
stop_backend; detach "$W/mnt-new"

python3 -I - "$W" "$SPEAKER_VOICE" "$MUST_SHIP" <<'EOF'
import json, sys
from pathlib import Path
w, speaker_voice, must_ship = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
load = lambda name: json.loads((w / name).read_text())


def old_chats():
    if (w / "old-threads.code").read_text() == "200":
        return [(t["title"], t["message_count"]) for t in load("old-threads.json")]
    if (w / "old-messages.code").read_text() == "200":      # before 0.2.6: one chat per project
        shown = [m for m in load("old-messages.json")
                 if m["text"] and not m.get("function_call") and not m.get("function_response")]
        first = next((m["text"] for m in shown if m["role"] == "user"), None)
        return [(first[:60], len(shown))] if first else []
    raise SystemExit("the old app answered neither chat list")


manifest = load("new-manifest.json")
builtins = len(manifest["voices"]) + (speaker_voice in manifest.get("retired", []))   # a used voice stays
voices = load("voices.json")
own = [v for v in voices if not v["builtin"]]
built = [v for v in voices if v["builtin"]]
assert [v["name"] for v in own] == ["Old voice"], own
assert len(built) == builtins and all(v["section"] for v in built), (len(built), builtins, [v["id"] for v in built if not v["section"]])
speakers = load("speakers.json")
assert [(s["display_name"], s["voice_id"], s["voice_auto"]) for s in speakers] == [("Doctor Yun", speaker_voice, False)], speakers
story = load("story.json")
assert (story["intro"], story["characters"], story["learning"]["state"]) == ("", [], "idle"), story
chats = [(t["title"], t["message_count"]) for t in load("threads.json")]
assert chats == old_chats(), (chats, old_chats())
headers = (w / "page-headers.txt").read_text().lower()
assert "cache-control: no-cache" in headers, headers
code = (w / "editor-code.js").read_text(errors="replace")
assert code, "the editor page loaded no code"
if must_ship:
    assert must_ship in code, f"the new editor's code doesn't contain {must_ship!r}"
print(f"upgrade OK: 'Old voice' kept; {len(built)} built-in voices in sections; the speaker keeps its voice; "
      f"story notes open (empty); "
      + (f"the old app's {len(chats)} chat(s) kept; " if chats else "no chat in the old app (it needs a key since 0.2.6); ")
      + "the editor page is checked every time" + (f" and is the new one ({must_ship!r})" if must_ship else ""))
EOF
echo "== columns the new app added at start-up (none expected when the database didn't change):"
grep -ho "Added column [a-z_]* to the [a-z_]* table" "$W/new.log" "$W"/logs/* 2>/dev/null | sort -u || true
