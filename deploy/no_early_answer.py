#!/usr/bin/env python3
"""Stop answering the caller before the buyer does.

WHY
  [from-carrier] runs Answer() before it even asks Django where to send the
  call, so the upstream platform sees a CONNECTED call the instant it arrives,
  whatever the buyer does next. When the buyer declines (503 / 603) or never
  picks up, our portal shows Missed while the upstream still shows Connected.

WHAT THIS CHANGES  (only inside [from-carrier] of /etc/asterisk/extensions.conf)
  1. Removes the early Answer(). Dial() answers the caller itself at the
     moment the buyer answers, so "answered" now means a buyer really picked up.
  2. After Dial(): if the buyer never answered, hang up with the buyer's own
     hangup cause (a decline stays a decline, busy stays busy, no answer stays
     no answer) so the upstream platform receives a failed call, not a
     connected one.
  3. Optional, --reject-nobuyer: a call with no buyer to send it to is rejected
     instead of answered and played "goodbye". Playback() answers the caller, so
     without this every call refused before dialing (over the concurrent-call
     cap, blocked caller, duplicate, no buyer, inactive campaign) still shows as
     CONNECTED upstream for a second while Avortyx shows it missed. Callers hear
     a rejection instead of the goodbye message. It can be done in the same run
     or later: once step 1 is applied, run again with only --reject-nobuyer.

USAGE  (on the server, as root, from /opt/call_platform)
  python3 deploy/no_early_answer.py              dry run: prints the change, edits nothing
  python3 deploy/no_early_answer.py --apply      back up, edit, reload, verify
  python3 deploy/no_early_answer.py --apply --reject-nobuyer
                                                 same, and reject calls with no buyer
                                                 (after step 1 is in: only this part)
  python3 deploy/no_early_answer.py --rollback   put the newest backup back and reload

SAFETY
  - Refuses to run while calls are active (override: --even-if-busy).
  - Refuses if route_call.py talks to the caller (needs an answered channel)
    or cannot be found (override: --skip-agi-check).
  - Backs up the file to /root/extensions.conf.bak-noearly-<timestamp> first.
  - If the reload fails, or Answer() is still in the loaded dialplan, it puts
    the backup back by itself.
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
AGI_DIRS = [
    d for d in os.environ.get(
        "AGI_DIRS",
        "/var/lib/asterisk/agi-bin:/usr/share/asterisk/agi-bin:/etc/asterisk",
    ).split(":") if d
]
ASTERISK = os.environ.get("ASTERISK_BIN", "asterisk")
BACKUP_GLOB = "extensions.conf.bak-noearly-*"
MARK = "no-early-answer"

RE_SECTION = re.compile(r"^\s*\[([^\]]+)\]")
RE_ANSWER = re.compile(r"^\s*same\s*=>\s*n\s*,\s*Answer\(\s*\)\s*(;.*)?$", re.I)
RE_AGI = re.compile(r"AGI\(\s*route_call\.py", re.I)
RE_DIAL = re.compile(r"^\s*same\s*=>\s*n\s*,\s*Dial\(PJSIP/\$\{DIAL_NUM\}@carrier", re.I)
RE_HANGUP = re.compile(r"^\s*same\s*=>\s*n\s*,\s*Hangup\(\s*\)\s*(;.*)?$", re.I)
RE_NOBUYER = re.compile(
    r"^\s*same\s*=>\s*n\(nobuyer\)\s*,\s*Playback\(vm-goodbye\)\s*(;.*)?$", re.I
)
RE_NOBUYER_DONE = re.compile(
    r"^\s*same\s*=>\s*n\(nobuyer\)\s*,\s*Hangup\(\s*21\s*\)\s*(;.*)?$", re.I
)
# AGI commands that need an answered channel (the script talks to the caller).
RE_AGI_SPEAKS = re.compile(
    r"ANSWER\b|STREAM FILE|EXEC\s+(Playback|Background|Answer|SayDigits|SayNumber|Read)\b"
)


class Stop(Exception):
    pass


def run(*args):
    p = subprocess.run(
        [ASTERISK, "-rx", " ".join(args)], capture_output=True, text=True, timeout=30
    )
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
        raise Stop(
            "route_call.py was not found in: " + ", ".join(AGI_DIRS) + "\n"
            "Find it with:  find / -name route_call.py 2>/dev/null\n"
            "then send me its contents, or rerun with --skip-agi-check."
        )
    for path in found:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                if line.lstrip().startswith("#"):
                    continue
                if RE_AGI_SPEAKS.search(line):
                    raise Stop(
                        "%s line %d talks to the caller, which needs an answered "
                        "channel:\n  %s\nSend me that file before going on."
                        % (path, n, line.strip())
                    )
    return found


def find_section(lines):
    start = None
    for i, ln in enumerate(lines):
        m = RE_SECTION.match(ln)
        if m and m.group(1).strip() == "from-carrier":
            start = i
            break
    if start is None:
        raise Stop("No [from-carrier] section in " + CONF)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if RE_SECTION.match(lines[j]):
            end = j
            break
    return start, end


def next_code(block, i):
    """Index of the next line after i that is not blank or a comment."""
    j = i + 1
    while j < len(block) and (not block[j].strip() or block[j].lstrip().startswith(";")):
        j += 1
    return j


def reject_nobuyer_lines(out, nl):
    """Turn the goodbye playback into a rejection, in place.

    Playback() answers the channel, so a refused call used to be connected
    upstream for the length of the message. Hangup(21) refuses it instead.
    """
    n = next((i for i, l in enumerate(out) if RE_NOBUYER.match(l)), None)
    if n is None:
        if any(RE_NOBUYER_DONE.match(l) for l in out):
            raise Stop("--reject-nobuyer: already applied, calls with no buyer are rejected.")
        raise Stop("--reject-nobuyer: no 'n(nobuyer),Playback(vm-goodbye)' line found.")
    k2 = next_code(out, n)
    if k2 >= len(out) or not RE_HANGUP.match(out[k2]):
        raise Stop("--reject-nobuyer: the line after the goodbye is not a plain Hangup().")
    ind2 = re.match(r"\s*", out[n]).group(0)
    out[n] = "%ssame => n(nobuyer),Hangup(21)%s" % (ind2, nl)
    out[k2] = ""


def transform(text, reject_nobuyer):
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    s, e = find_section(lines)
    block = lines[s:e]
    body = "".join(block)

    if MARK in body and not any(RE_ANSWER.match(l) for l in block):
        # The early answer is already gone. Only the no-buyer part can still be asked for.
        if not reject_nobuyer:
            raise Stop("Already applied: [from-carrier] has no early Answer() and carries the "
                       "marker. To also reject calls with no buyer, run with --reject-nobuyer.")
        out = list(block)
        reject_nobuyer_lines(out, nl)
        return "".join(lines[:s] + out + lines[e:])
    if re.search(r"n\(finished\)", body):
        raise Stop("[from-carrier] already has a 'finished' label; not touching it.")

    a = next((i for i, l in enumerate(block) if RE_ANSWER.match(l)), None)
    if a is None:
        raise Stop("No 'same => n,Answer()' line in [from-carrier]; nothing to remove.")
    k = next_code(block, a)
    if k >= len(block) or not RE_AGI.search(block[k]):
        raise Stop("Answer() is not directly followed by AGI(route_call.py); the file is not "
                   "in the shape I expected. Send it to me.")

    d = next((i for i, l in enumerate(block) if i > a and RE_DIAL.match(l)), None)
    if d is None:
        raise Stop("No Dial(PJSIP/${DIAL_NUM}@carrier ...) line after the AGI call.")
    h = next_code(block, d)
    if h >= len(block) or not RE_HANGUP.match(block[h]):
        raise Stop("The line after Dial() is not a plain Hangup(); the file is not in the "
                   "shape I expected. Send it to me.")

    ind = re.match(r"\s*", block[h]).group(0)
    out = list(block)
    out[a] = "%ssame => n,NoOp(%s: Dial() answers the caller when the buyer answers)%s" % (ind, MARK, nl)
    out[h] = (
        '%ssame => n,GotoIf($["${DIALSTATUS}" = "ANSWER"]?finished)%s'
        '%ssame => n,Hangup(${IF($["${HANGUPCAUSE}" != ""]?${HANGUPCAUSE}:34)})%s'
        '%ssame => n(finished),Hangup()%s'
    ) % (ind, nl, ind, nl, ind, nl)

    if reject_nobuyer:
        reject_nobuyer_lines(out, nl)

    return "".join(lines[:s] + out + lines[e:])


def reload_and_verify():
    rc, out = run("dialplan reload")
    print(out.strip())
    if rc != 0:
        return False
    rc, out = run("dialplan show from-carrier")
    print(out.strip())
    return rc == 0 and "Answer(" not in out


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


def newest_backup():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, BACKUP_GLOB)))
    return files[-1] if files else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--reject-nobuyer", action="store_true")
    ap.add_argument("--even-if-busy", action="store_true")
    ap.add_argument("--skip-agi-check", action="store_true")
    args = ap.parse_args()

    try:
        if args.rollback:
            b = newest_backup()
            if not b:
                raise Stop("No backup named %s in %s" % (BACKUP_GLOB, BACKUP_DIR))
            if active_calls() and not args.even_if_busy:
                raise Stop("Calls are active. Wait for 0 active calls (or --even-if-busy).")
            shutil.copy2(b, CONF)
            print("Restored", b, "->", CONF)
            ok = run("dialplan reload")
            print(ok[1].strip())
            print("Rolled back. Make one test call.")
            return 0

        if not args.skip_agi_check:
            for p in check_agi():
                print("AGI script checked:", p)
        busy = active_calls()
        if busy and not args.even_if_busy:
            raise Stop("%d call(s) active. Wait for 0 active calls, then run again "
                       "(or --even-if-busy)." % busy)

        with open(CONF, encoding="utf-8", newline="") as fh:
            old = fh.read()
        new = transform(old, args.reject_nobuyer)
        diff = "".join(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            "extensions.conf (now)", "extensions.conf (after)", n=2))
        print(diff or "(no change)")
        if not args.apply:
            print("\nDry run only. Nothing was changed. Add --apply to do it.")
            return 0

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(BACKUP_DIR, "extensions.conf.bak-noearly-" + stamp)
        shutil.copy2(CONF, backup)
        print("Backup saved:", backup)
        write_conf(new)
        if reload_and_verify():
            if args.reject_nobuyer:
                print("\nDONE. Calls with no buyer are now rejected, not answered. Make one test call.")
            else:
                print("\nDONE. The caller is no longer answered early. Make one test call.")
            print("Undo any time:  python3 deploy/no_early_answer.py --rollback")
            return 0
        print("\nThe reload failed or Answer() is still loaded. Restoring the backup.")
        shutil.copy2(backup, CONF)
        print(run("dialplan reload")[1].strip())
        return 1
    except Stop as why:
        print("STOPPED:", why)
        return 2


if __name__ == "__main__":
    sys.exit(main())
