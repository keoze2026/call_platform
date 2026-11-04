#!/usr/bin/env python3
"""Server A: record caller and agent as separate files too (CH-543).

WHY (Sir, 4 Nov - AI audit upgrade)
  "Record every call in dual channel (caller left, agent right). The AI
   analyses each side separately." One mixed recording cannot say WHICH side
  was silent: "agent gave the opening, the customer never spoke" and "nobody
  spoke" look alike, and beeps on the caller's line hide under the agent.

WHAT THIS CHANGES  (one line inside [from-carrier] of /etc/asterisk/extensions.conf)
  MixMonitor(${RECFILE})  ->
  MixMonitor(${RECFILE},nr(<split>/${CALL_LOG_ID}-caller.wav)t(<split>/${CALL_LOG_ID}-agent.wav))
  - the mixed recording ${RECFILE} is unchanged: same file, same link, same
    MP3 / B2 path, what buyers hear;
  - r(...) = what Asterisk receives from the caller's line  -> the caller;
    t(...) = what it sends to the caller (the buyer's agent) -> the agent;
  - n keeps the two files in step (silence inserted), so "time to first
    caller speech" is measured right. Used only if this Asterisk has it.
  The two files go to /var/spool/asterisk/recordings/split/. The MP3 and B2
  jobs only look at the top folder, so they never touch them. The voice check
  on Server B reads them over https (nginx serves /recordings/split/), and a
  cron here deletes them after 12 hours: the evidence is the
  normal recording; the split files are only for the AI.
  A "b" (record only once bridged) already on the line is kept.

USAGE  (on Server A, as root, from /opt/call_platform)
  python3 deploy/split_channels.py              dry run: checks and prints the change
  python3 deploy/split_channels.py --apply      back up, edit, reload, verify
  python3 deploy/split_channels.py --rollback   put the newest backup back and reload

SAFETY
  - Stops, changing nothing, unless [from-carrier] has exactly one
    MixMonitor(${RECFILE}) or MixMonitor(${RECFILE},b), and this Asterisk's
    MixMonitor documents the r() and t() options.
  - --apply / --rollback refuse while calls are active (--even-if-busy to
    override: a reload never drops a call in progress; calls already running
    keep their recorder, new calls get the new one).
  - Backs up to /root/extensions.conf.bak-split-<time>; restores it by itself
    if the reload fails or the new line is not loaded.
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
RECORDINGS = os.environ.get("RECORDINGS_DIR", "/var/spool/asterisk/recordings")
SPLIT_DIR = os.path.join(RECORDINGS, "split")
BACKUP_GLOB = "extensions.conf.bak-split-*"
MARK = "split-channels"
RE_SECTION = re.compile(r"^\s*\[([^\]]+)\]")
RE_MIX = re.compile(r"MixMonitor\(\$\{RECFILE\}(,b)?\)")


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


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def words(text):
    """Plain lower-case words: Asterisk's help carries colour codes and marks
    around words ("*receive*", "<r>") when a program reads it (CH-545)."""
    return " " + re.sub(r"[^a-z0-9]+", " ", ANSI.sub("", text).lower()) + " "


def asterisk_major():
    rc, out = run("core show version")
    m = re.search(r"Asterisk\s+(\d+)\.", ANSI.sub("", out))
    return int(m.group(1)) if rc == 0 and m else 0


def mixmonitor_options():
    """(has r()/t(), has n) from this Asterisk's own MixMonitor help; r()/t()
    also when the version has had them for years (13 and later)."""
    rc, out = run("core show application MixMonitor")
    if rc != 0:
        raise Stop("Could not read MixMonitor's help from Asterisk:\n" + out)
    w = words(out)
    has_rt = " receive audio feed " in w and " transmit audio feed " in w
    has_n = " insert silence " in w
    if not has_rt and asterisk_major() >= 13:
        has_rt = True
    return has_rt, has_n


def find_section(lines):
    start = next((i for i, ln in enumerate(lines)
                  if (m := RE_SECTION.match(ln)) and m.group(1).strip() == "from-carrier"), None)
    if start is None:
        raise Stop("No [from-carrier] section in " + CONF)
    end = next((j for j in range(start + 1, len(lines)) if RE_SECTION.match(lines[j])), len(lines))
    return start, end


def transform(text, with_n=True, split_dir=SPLIT_DIR):
    if MARK in text:
        raise Stop("Already applied (" + MARK + " is in " + CONF + ").")
    lines = text.splitlines(keepends=True)
    start, end = find_section(lines)
    hits = [i for i in range(start, end) if RE_MIX.search(lines[i])]
    if len(hits) != 1:
        raise Stop("Expected exactly one MixMonitor(${RECFILE}) in [from-carrier], found %d. "
                   "Send me:  asterisk -rx 'dialplan show from-carrier'" % len(hits))
    i = hits[0]
    line = lines[i]
    body, nl = (line[:-2], "\r\n") if line.endswith("\r\n") else ((line[:-1], "\n") if line.endswith("\n") else (line, ""))
    keep_b = "b" if RE_MIX.search(body).group(1) else ""
    opts = (keep_b + ("n" if with_n else "")
            + "r(" + split_dir + "/${CALL_LOG_ID}-caller.wav)"
            + "t(" + split_dir + "/${CALL_LOG_ID}-agent.wav)")
    body = RE_MIX.sub(lambda m: "MixMonitor(${RECFILE}," + opts + ")", body, count=1)
    lines[i] = body.rstrip() + "   ; " + MARK + " (CH-543)" + nl
    return "".join(lines)


def make_split_dir():
    st = os.stat(RECORDINGS)
    os.makedirs(SPLIT_DIR, exist_ok=True)
    os.chown(SPLIT_DIR, st.st_uid, st.st_gid)
    os.chmod(SPLIT_DIR, 0o755)


def reload_and_verify():
    rc, out = run("dialplan reload")
    print(out.strip())
    if rc != 0:
        return False
    rc, out = run("dialplan show from-carrier")
    print(out.strip())
    return rc == 0 and "-caller.wav" in out and "-agent.wav" in out


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
            print("Rolled back: one mixed recording per call again. Make one test call.")
            return 0

        has_rt, has_n = mixmonitor_options()
        if not has_rt:
            raise Stop("This Asterisk's MixMonitor has no r()/t() options. Send me:  "
                       "asterisk -rx 'core show version'")
        print("MixMonitor: r()/t() supported; n (keep files in step) %s" % ("supported" if has_n else "NOT supported - files kept without it"))
        with open(CONF, encoding="utf-8", newline="") as fh:
            old = fh.read()
        new = transform(old, with_n=has_n)
        print("".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                           "extensions.conf (now)", "extensions.conf (after)", n=1)))
        if not args.apply:
            print("\nDry run only. Nothing was changed. Add --apply to do it (with 0 active calls).")
            return 0
        busy = active_calls()
        if busy and not args.even_if_busy:
            raise Stop("%d call(s) active. Wait for 0 active calls, then run again (or --even-if-busy)." % busy)
        make_split_dir()
        print("Folder ready:", SPLIT_DIR)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(BACKUP_DIR, "extensions.conf.bak-split-" + stamp)
        shutil.copy2(CONF, backup)
        print("Backup saved:", backup)
        write_conf(new)
        if reload_and_verify():
            print("\nDONE. New calls are recorded mixed (as before) AND caller / agent apart.")
            print("Next: the clean-up cron, then one test call (see the guide).")
            print("Undo any time:  python3 deploy/split_channels.py --rollback")
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
