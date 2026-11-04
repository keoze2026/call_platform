#!/usr/bin/env python3
"""Server A: record from the moment the buyer is dialled, not from the answer (CH-535).

WHY (Sir, 4 Nov)
  "When anyone gets 0 sec connected calls we missed recording link, so buyer
   does not agree to pay without recording."
  [from-carrier] starts the recorder with MixMonitor(${RECFILE},b) - the "b"
  means "only once the two sides are bridged". A call the buyer never
  answered (Missed With Conversion: paid on Incoming Call) or answered as the
  caller hung up (late answer, Completed 1 s) is never bridged, so its file is
  a 44-byte empty WAV (checked on Server A, 4 Nov: all 7 such calls).

WHAT THIS CHANGES  (one line inside [from-carrier] of /etc/asterisk/extensions.conf)
  MixMonitor(${RECFILE},b)  ->  MixMonitor(${RECFILE})
  Recording starts when the call reaches the Dial to the buyer: whatever the
  buyer's side sends before answering (ringing, a greeting, the pick-up) is in
  the file, then the conversation as today. Same file, same name, same link;
  failover tries to the next buyer go into the same file. RECORD_CALL=0 still
  turns recording off.
  Answered calls' recordings now begin with the few seconds of ringing.

USAGE  (on Server A, as root, from the folder holding this file)
  python3 record_from_dial.py              dry run: prints the change, edits nothing
  python3 record_from_dial.py --apply      back up, edit, reload, verify
  python3 record_from_dial.py --rollback   put the newest backup back and reload

SAFETY
  - --apply / --rollback refuse while calls are active (override:
    --even-if-busy). The dry run works any time.
  - Stops, changing nothing, unless [from-carrier] has exactly one
    MixMonitor(${RECFILE},b).
  - Backs up to /root/extensions.conf.bak-recdial-<timestamp>; restores it by
    itself if the reload fails or the new line is not loaded.
"""
import argparse
import datetime
import difflib
import glob
import os
import re
import shutil
import subprocess
import sys

CONF = os.environ.get("EXTENSIONS_CONF", "/etc/asterisk/extensions.conf")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/root")
ASTERISK = os.environ.get("ASTERISK_BIN", "asterisk")
BACKUP_GLOB = "extensions.conf.bak-recdial-*"
MARK = "record-from-dial"
RE_SECTION = re.compile(r"^\s*\[([^\]]+)\]")
RE_MIX_B = re.compile(r"MixMonitor\(\$\{RECFILE\},b\)")


class Stop(Exception):
    pass


def run(*args):
    p = subprocess.run([ASTERISK, "-rx", " ".join(args)], capture_output=True, text=True, timeout=30)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def active_calls():
    rc, out = run("core show channels")
    m = re.search(r"(\d+)\s+active call", out)
    if rc != 0 or not m:
        raise Stop("Could not read the active call count from Asterisk:\n" + out)
    return int(m.group(1))


def find_section(lines):
    start = next((i for i, ln in enumerate(lines)
                  if (m := RE_SECTION.match(ln)) and m.group(1).strip() == "from-carrier"), None)
    if start is None:
        raise Stop("No [from-carrier] section in " + CONF)
    end = next((j for j in range(start + 1, len(lines)) if RE_SECTION.match(lines[j])), len(lines))
    return start, end


def transform(text):
    if MARK in text:
        raise Stop("Already applied (" + MARK + " is in " + CONF + ").")
    lines = text.splitlines(keepends=True)
    start, end = find_section(lines)
    hits = [i for i in range(start, end) if RE_MIX_B.search(lines[i])]
    if len(hits) != 1:
        raise Stop("Expected exactly one MixMonitor(${RECFILE},b) in [from-carrier], found %d. "
                   "Send me:  asterisk -rx 'dialplan show from-carrier'" % len(hits))
    i = hits[0]
    line = lines[i]
    body, nl = (line[:-2], "\r\n") if line.endswith("\r\n") else ((line[:-1], "\n") if line.endswith("\n") else (line, ""))
    body = RE_MIX_B.sub("MixMonitor(${RECFILE})", body, count=1)
    lines[i] = body.rstrip() + "   ; " + MARK + " (CH-535)" + nl
    return "".join(lines)


def reload_and_verify():
    rc, out = run("dialplan reload")
    print(out.strip())
    if rc != 0:
        return False
    rc, out = run("dialplan show from-carrier")
    print(out.strip())
    return rc == 0 and "MixMonitor(${RECFILE})" in out and "MixMonitor(${RECFILE},b)" not in out


def write_conf(new_text):
    st = os.stat(CONF)
    tmp = CONF + ".new"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)
    os.chmod(tmp, st.st_mode)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except (PermissionError, AttributeError):
        pass
    os.replace(tmp, CONF)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--even-if-busy", action="store_true")
    args = ap.parse_args()
    try:
        if args.rollback:
            files = sorted(glob.glob(os.path.join(BACKUP_DIR, BACKUP_GLOB)))
            if not files:
                raise Stop("No backup named %s in %s" % (BACKUP_GLOB, BACKUP_DIR))
            if active_calls() and not args.even_if_busy:
                raise Stop("Calls are active. Wait for 0 active calls (or --even-if-busy).")
            shutil.copy2(files[-1], CONF)
            print("Restored", files[-1], "->", CONF)
            print(run("dialplan reload")[1].strip())
            print("Rolled back: recording starts at the answer again. Make one test call.")
            return 0

        with open(CONF, encoding="utf-8", newline="") as fh:
            old = fh.read()
        new = transform(old)
        print("".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                           "extensions.conf (now)", "extensions.conf (after)", n=1)))
        if not args.apply:
            print("\nDry run only. Nothing was changed. Add --apply to do it (with 0 active calls).")
            return 0
        # CH-536: only the real change waits for 0 active calls; the dry run
        # (reads the file, changes nothing) runs any time.
        busy = active_calls()
        if busy and not args.even_if_busy:
            raise Stop("%d call(s) active. Wait for 0 active calls, then run again (or --even-if-busy)." % busy)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(BACKUP_DIR, "extensions.conf.bak-recdial-" + stamp)
        shutil.copy2(CONF, backup)
        print("Backup saved:", backup)
        write_conf(new)
        if reload_and_verify():
            print("\nDONE. New calls record from the moment the buyer is dialled. Make one test call.")
            print("Undo any time:  python3 record_from_dial.py --rollback")
            return 0
        print("\nThe reload failed or the new line is not loaded. Restoring the backup.")
        shutil.copy2(backup, CONF)
        print(run("dialplan reload")[1].strip())
        return 1
    except Stop as why:
        print("STOPPED:", why)
        return 2


if __name__ == "__main__":
    sys.exit(main())
