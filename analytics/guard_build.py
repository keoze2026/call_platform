"""Avortyx Guard: which code is running, and which is waiting for a restart (CH-355).

The code is mounted into the containers, so `git pull` puts new code on disk
at once, but daphne and Celery keep running what they loaded at start until
the quiet-time restart. For hours the server can run CH-351 while CH-354 sits
on disk, and a change gets tested before it is live.

    running   what this process loaded when it started (sha + last CH)
    on_disk   what `git pull` has put there now
    pending   the CH ids on disk but not running = waiting for the restart

Read from .git and CHANGES.md: no git binary needed in the container.
"""
import re
from pathlib import Path

from django.conf import settings

BASE = Path(getattr(settings, 'BASE_DIR', '.'))
CH_RE = re.compile(r'^## (CH-\d+)', re.M)
TAIL = 200_000          # the newest entries are at the bottom of CHANGES.md


def _git_sha(base=BASE) -> str:
    git = base / '.git'
    try:
        head = (git / 'HEAD').read_text().strip()
        if not head.startswith('ref: '):
            return head[:40]
        ref = head[5:]
        f = git / ref
        if f.exists():
            return f.read_text().strip()[:40]
        packed = git / 'packed-refs'
        if packed.exists():
            for line in packed.read_text().splitlines():
                if line.endswith(' ' + ref):
                    return line.split()[0][:40]
    except OSError:
        pass
    return ''


def _ch_ids(base=BASE) -> list:
    """Every CH id in CHANGES.md's newest part, in file order."""
    try:
        with open(base / 'CHANGES.md', 'rb') as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - TAIL))
            text = fh.read().decode('utf-8', 'ignore')
    except OSError:
        return []
    return CH_RE.findall(text)


def snapshot(base=BASE) -> dict:
    ids = _ch_ids(base)
    return {'sha': _git_sha(base), 'ch': ids[-1] if ids else '', 'ids': ids}


RUNNING = snapshot()     # taken once, when this process loads the code


def status(base=BASE, running=None) -> dict:
    run = running or RUNNING
    disk = snapshot(base)
    known = set(run['ids'])
    pending = [c for c in disk['ids'] if c not in known]
    return {
        'running': {'sha': run['sha'][:7], 'ch': run['ch']},
        'on_disk': {'sha': disk['sha'][:7], 'ch': disk['ch']},
        'restart_needed': bool(disk['sha'] and run['sha'] and disk['sha'] != run['sha']) or bool(pending),
        'pending': pending,
    }


def ch_number(ch: str) -> int:
    m = re.match(r'CH-(\d+)', ch or '')
    return int(m.group(1)) if m else 0
