#!/usr/bin/env bash
#
# Converts finished call recordings from WAV to MP3, about five times smaller
# (CH-281, Sir 26 Oct: increase server capacity for recordings).
#
# Runs on the HOST, like asterisk_channel_sync.sh: the recordings are in the
# host's /var/spool/asterisk/recordings, which the containers cannot see.
# Restarts nothing and does not touch Asterisk or the calls.
#
# For each WAV not written to for 5 minutes (a live call's file is written to
# all the time, so it is never picked up):
#   1. ffmpeg makes <id>.mp3.part at low priority (nice / ionice)
#   2. the MP3's length is checked against the WAV's (within 2 seconds)
#   3. it is renamed to <id>.mp3, readable by nginx
#   4. the call's link in the database is switched to .mp3 (manage.py
#      recordings_to_mp3)
#   5. only then is the WAV deleted
# A failure at any step keeps the WAV, and the next run tries again.
# Old .wav links already sent out keep working: nginx serves the .mp3 for them
# (docs/nginx/rec.v0l1.com.conf).
#
# Install once (as root):
#   apt-get install -y ffmpeg
#   chmod +x /opt/call_platform/scripts/recordings_to_mp3.sh
#   ( crontab -l 2>/dev/null; echo "*/5 * * * * /opt/call_platform/scripts/recordings_to_mp3.sh >> /var/log/recordings_to_mp3.log 2>&1" ) | crontab -
#
# Dry run (converts nothing, deletes nothing, lists what it would do):
#   DRY_RUN=1 /opt/call_platform/scripts/recordings_to_mp3.sh
set -uo pipefail

DIR="${RECORDINGS_DIR:-/var/spool/asterisk/recordings}"
APP="${APP_DIR:-/opt/call_platform}"
BATCH="${BATCH:-100}"           # files per run; the backlog clears over a few hours
MIN_AGE_MIN="${MIN_AGE_MIN:-5}" # untouched this long = the call is over
SILENCE_MIN_AGE_MIN="${SILENCE_MIN_AGE_MIN:-15}" # CH-661: an empty file this old is a finished call
BITRATE="${BITRATE:-24k}"       # phone audio is 8 kHz mono; 24k keeps the voice clear
MANAGE="${MANAGE:-docker compose exec -T web python manage.py}"
DRY_RUN="${DRY_RUN:-}"

exec 9>/tmp/recordings_to_mp3.lock
flock -n 9 || { echo "$(date -Is) previous run still going, skipped"; exit 0; }

command -v ffmpeg >/dev/null || { echo "$(date -Is) ffmpeg not installed: apt-get install -y ffmpeg" >&2; exit 1; }
[ -d "$DIR" ] || { echo "$(date -Is) $DIR not found" >&2; exit 1; }

# Length in whole seconds, measured by decoding the audio. Asterisk's WAV
# headers can carry no length at all, so reading the header alone ("ffprobe
# duration") gives N/A and every file failed the check (CH-282).
secs() { nice -n 19 ffmpeg -nostdin -v error -i "$1" -f null -progress pipe:1 - 2>/dev/null | awk -F= '/^out_time_us=/{if ($2 ~ /^[0-9]+$/) t=$2} END{printf "%d", t/1000000}'; }

# CH-661 (Sir, 10 Nov: "any complete call should have a recording"): a call
# that recorded nothing - the caller gone before anyone spoke, e.g. a buyer's
# answer caught by the cancel hold - leaves a WAV header with no audio (44
# bytes) that plays nothing. It becomes 1 second of silence, the truth of that
# call, keeping its time, and is converted below like any other recording.
silenced=0
while IFS= read -r -d '' wav; do
    # Still open (a call ringing a long time writes nothing yet): never touched.
    if command -v fuser >/dev/null && fuser -s "$wav" 2>/dev/null; then continue; fi
    if [ -n "$DRY_RUN" ]; then
        echo "would make 1 second of silence: $wav"
        continue
    fi
    if nice -n 19 ffmpeg -nostdin -v error -y -f lavfi -i anullsrc=r=8000:cl=mono -t 1 \
            -c:a pcm_s16le -f wav "$wav.part" && touch -r "$wav" "$wav.part" && chmod 644 "$wav.part" \
            && mv -f "$wav.part" "$wav"; then
        silenced=$((silenced+1))
    else
        echo "$(date -Is) could not make silence for $wav" >&2; rm -f "$wav.part"
    fi
done < <(find "$DIR" -maxdepth 1 -type f -name '*.wav' -mmin +"$SILENCE_MIN_AGE_MIN" -size -2k -print0 | head -z -n "$BATCH")
[ $silenced -gt 0 ] && echo "$(date -Is) $silenced empty recordings made 1 second of silence"

converted=()
failed=0
while IFS= read -r -d '' wav; do
    id=$(basename "$wav" .wav)
    mp3="$DIR/$id.mp3"
    if [ -n "$DRY_RUN" ]; then
        echo "would convert $wav"
        continue
    fi
    if [ ! -s "$mp3" ]; then
        if ! nice -n 19 ionice -c3 ffmpeg -nostdin -v error -y -i "$wav" -ac 1 -codec:a libmp3lame -b:a "$BITRATE" -f mp3 "$mp3.part"; then
            echo "$(date -Is) convert failed: $wav" >&2; rm -f "$mp3.part"; failed=$((failed+1)); continue
        fi
        a=$(secs "$wav"); b=$(secs "$mp3.part")
        # Under one second of audio measures 0s on both sides (CH-303: 30 such
        # WAVs per run were kept for ever and piled up). A WAV under 32 KB is at
        # most 2 seconds of phone audio: 0s and 0s is a match, not a failure.
        short=""
        if [ "${a:-0}" -eq 0 ] && [ "${b:-0}" -eq 0 ] && [ -s "$mp3.part" ] && [ "$(stat -c %s "$wav")" -lt 32768 ]; then short=1; fi
        if [ -z "$short" ] && { [ "${a:-0}" -le 0 ] || [ "${b:-0}" -le 0 ] || [ $(( a - b )) -gt 2 ] || [ $(( b - a )) -gt 2 ]; }; then
            echo "$(date -Is) length mismatch ${a:-?}s vs ${b:-?}s, kept WAV: $wav" >&2; rm -f "$mp3.part"; failed=$((failed+1)); continue
        fi
        chmod 644 "$mp3.part"
        mv -f "$mp3.part" "$mp3"
    fi
    converted+=("$id")
done < <(find "$DIR" -maxdepth 1 -type f -name '*.wav' -mmin +"$MIN_AGE_MIN" -size +2k -print0 | head -z -n "$BATCH")
# -size +2k: a WAV of a few bytes is a header with no audio (a call that
# recorded nothing). Since CH-661 those are made 1 second of silence above
# first (16 KB), so here they are converted like any recording.

[ -n "$DRY_RUN" ] && exit 0
[ ${#converted[@]} -eq 0 ] && { [ $failed -gt 0 ] && echo "$(date -Is) nothing converted, $failed failed"; exit 0; }

# Switch the links in the database, then delete only the WAVs it confirmed.
cd "$APP" || exit 1
out=$($MANAGE recordings_to_mp3 "${converted[@]}" 2>/dev/null)
rc=$?
if [ $rc -ne 0 ]; then
    echo "$(date -Is) database update failed (rc=$rc); kept ${#converted[@]} WAVs, will retry" >&2
    exit 1
fi
deleted=0
while read -r state id _; do
    case "$state" in
        ok|none)
            if [ -s "$DIR/$id.mp3" ] && [ -f "$DIR/$id.wav" ]; then rm -f "$DIR/$id.wav" && deleted=$((deleted+1)); fi ;;
    esac
done <<< "$out"
echo "$(date -Is) converted ${#converted[@]}, deleted $deleted WAVs, failed $failed, $(df -h "$DIR" | awk 'NR==2{print $5" disk used"}')"
