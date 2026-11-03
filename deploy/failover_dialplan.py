#!/usr/bin/env python3
"""Server A: ring a destination for a set time, then try the next buyer (CH-514).

WHY (Sir, 4 Nov)
  "If a destination doesn't answer within about 15 seconds, the call moves to
   the next buyer straight away. The caller is never lost."
  Dial() in [from-carrier] used ${MAX_DURATION} (3600) as its ring time, so a
  destination that did not answer rang until the sending platform gave up and
  dialled us again - the "redials" (Q06: 143 on 3 Nov).

WHAT THIS CHANGES  (only inside [from-carrier] of /etc/asterisk/extensions.conf)
  1. Dial() rings ${RING_TIMEOUT} seconds when Server B sends one, else
     ${MAX_DURATION} as before.
  2. When the destination did not answer and B said NEXT_ON_FAIL=1, it runs
     AGI(route_call.py,next), which asks B for the next buyer, and dials that
     one. No next buyer: the call ends exactly as it does today.
  With FAILOVER_RING_SECONDS=0 on B (the default), B sends neither variable and
  every call runs as it does today, so this can go in before the switch is on.

  Needs route_call.py from CH-514 (it understands "next"):
      bash scripts/fallback/install_a.sh   (run it first)

USAGE  (on Server A, as root, from /opt/call_platform)
  python3 deploy/failover_dialplan.py             dry run: prints the change, edits nothing
  python3 deploy/failover_dialplan.py --apply     back up, edit, reload, verify
  python3 deploy/failover_dialplan.py --rollback  put the newest backup back and reload

SAFETY
  - Refuses while calls are active (override: --even-if-busy).
  - Refuses if the installed route_call.py does not know "next".
  - Stops, changing nothing, if [from-carrier] is not shaped as expected.
  - Backs up to /root/extensions.conf.bak-failover-<timestamp>; restores it by
    itself if the reload fails or the new lines are not loaded.
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
AGI_DIRS = [d for d in os.environ.get(
    "AGI_DIRS", "/var/lib/asterisk/agi-bin:/usr/share/asterisk/agi-bin").split(":") if d]
ASTERISK = os.environ.get("ASTERISK_BIN", "asterisk")
BACKUP_GLOB = "extensions.conf.bak-failover-*"
MARK = "failover-next-buyer"

RE_SECTION = re.compile(r"^\s*\[([^\]]+)\]")
RE_SET_DIALNUM = re.compile(r"^(\s*same\s*=>\s*)n(\s*,\s*Set\(DIAL_NUM=)", re.I)
RE_DIAL = re.compile(r"^\s*same\s*=>\s*n\s*,\s*Dial\(PJSIP/\$\{DIAL_NUM\}@carrier", re.I)
RE_ANSWERED = re.compile(r'^\s*same\s*=>\s*n\s*,\s*GotoIf\(\$\["\$\{DIALSTATUS\}"\s*=\s*"ANSWER"\]\?finished\)', re.I)
RE_FAIL_HANGUP = re.compile(r"^(\s*same\s*=>\s*)n(\s*,\s*Hangup\(\$\{IF\()", re.I)
RING = '${IF($["${RING_TIMEOUT}" != ""]?${RING_TIMEOUT}:${MAX_DURATION})}'


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


def check_agi():
    found = []
    for d in AGI_DIRS:
        found += glob.glob(os.path.join(d, "**", "route_call.py"), recursive=True)
    if not found:
        raise Stop("route_call.py was not found in: " + ", ".join(AGI_DIRS))
    for path in found:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if "route-next" not in fh.read():
                raise Stop(path + " is the old route_call.py (no \"next\").\n"
                           "Run first:  bash scripts/fallback/install_a.sh")
    return found


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
    nl = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    start, end = find_section(lines)
    block = lines[start:end]

    def one(regex, what):
        hits = [i for i, ln in enumerate(block) if regex.search(ln)]
        if len(hits) != 1:
            raise Stop("Expected exactly one %s line in [from-carrier], found %d. "
                       "Send me the section:  asterisk -rx 'dialplan show from-carrier'" % (what, len(hits)))
        return hits[0]

    i_set = one(RE_SET_DIALNUM, "Set(DIAL_NUM=...)")
    i_dial = one(RE_DIAL, "Dial(PJSIP/${DIAL_NUM}@carrier,...)")
    i_ans = one(RE_ANSWERED, 'GotoIf(DIALSTATUS = "ANSWER"?finished)')
    i_hang = one(RE_FAIL_HANGUP, "Hangup(${IF(...HANGUPCAUSE...)})")
    if not (i_set < i_dial < i_ans < i_hang) or i_ans != i_dial + 1 or i_hang != i_ans + 1:
        raise Stop("[from-carrier] is not Set(DIAL_NUM) .. Dial .. GotoIf(ANSWER) .. Hangup(cause) in that order.")
    if "${MAX_DURATION}" not in block[i_dial]:
        raise Stop("The Dial() line has no ${MAX_DURATION}:\n  " + block[i_dial].strip())

    block[i_set] = RE_SET_DIALNUM.sub(r"\1n(redial)\2", block[i_set], count=1)
    block[i_dial] = block[i_dial].replace("${MAX_DURATION}", RING, 1)
    block[i_hang] = RE_FAIL_HANGUP.sub(r"\1n(nofailover)\2", block[i_hang], count=1)
    add = [
        ' same => n,GotoIf($["${NEXT_ON_FAIL}" != "1"]?nofailover)   ; ' + MARK + " (CH-514)" + nl,
        " same => n,AGI(route_call.py,next)" + nl,
        ' same => n,GotoIf($["${ROUTE_ACTION}" = "dial"]?redial)' + nl,
    ]
    block[i_ans + 1:i_ans + 1] = add
    return "".join(lines[:start] + block + lines[end:])


def reload_and_verify():
    rc, out = run("dialplan reload")
    print(out.strip())
    if rc != 0:
        return False
    rc, out = run("dialplan show from-carrier")
    print(out.strip())
    return rc == 0 and "route_call.py,next" in out and "RING_TIMEOUT" in out


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
            print("Rolled back. Make one test call.")
            return 0

        for p in check_agi():
            print("AGI script checked:", p)
        busy = active_calls()
        if busy and not args.even_if_busy:
            raise Stop("%d call(s) active. Wait for 0 active calls, then run again (or --even-if-busy)." % busy)
        with open(CONF, encoding="utf-8", newline="") as fh:
            old = fh.read()
        new = transform(old)
        print("".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                           "extensions.conf (now)", "extensions.conf (after)", n=2)))
        if not args.apply:
            print("\nDry run only. Nothing was changed. Add --apply to do it.")
            return 0
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(BACKUP_DIR, "extensions.conf.bak-failover-" + stamp)
        shutil.copy2(CONF, backup)
        print("Backup saved:", backup)
        write_conf(new)
        if reload_and_verify():
            print("\nDONE. Server A asks for the next buyer when B switches failover on "
                  "(FAILOVER_RING_SECONDS on Server B). Make one test call.")
            print("Undo any time:  python3 deploy/failover_dialplan.py --rollback")
            return 0
        print("\nThe reload failed or the new lines are not loaded. Restoring the backup.")
        shutil.copy2(backup, CONF)
        print(run("dialplan reload")[1].strip())
        return 1
    except Stop as why:
        print("STOPPED:", why)
        return 2


if __name__ == "__main__":
    sys.exit(main())
