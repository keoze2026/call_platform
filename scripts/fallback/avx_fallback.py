#!/usr/bin/env python3
"""Routing on Server A while Server B cannot be reached (CH-464, Sir 2 Nov).

Sir's conditions: A keeps routing when B is down, buyer caps and concurrency
limits still apply, and every call made meanwhile is logged and synced to B so
billing stays accurate.

How it fits together on Server A:

  route_call.py (AGI)  asks B as always. Only when B does not answer - no
                       connection, a timeout, a 5xx - it calls decide() here
                       instead of hanging up. Every call B routes is also
                       noted in the journal (note_b_call), so A knows which
                       calls are live and how far into each cap it is.
  fallback_sync.py     a service on A: every 5 seconds sends the calls A
                       routed on its own to B, which writes them with A's
                       call ids (their hang-up reports wait in call_ended.sh
                       until then, so every call is billed as usual); every
                       minute fetches the plan from B (numbers, campaigns,
                       rules, buyers, lines, caps used, blocked and allowed
                       callers) into plan.sqlite3.

decide() follows routing/asterisk_handler.py and routing/engine.py step by
step: blocked callers, whitelist, anonymous block, duplicate block, campaign
caps and concurrency, balance, duplicate handling (Original / Different),
buyer blocks, the campaign's rules by priority, each line's buyer status,
on/off switch, buyer caps, buyer and line concurrency, line caps, priority
then weight, then the buyer's own TFN or its live TFNs in rotation.

What it cannot know while B is away, and how it behaves:
  - calls before A started keeping the journal: duplicate handling and the
    duplicate block see only calls A has handled (all calls, once A is live);
  - which calls were answered: the duplicate block and Original count every
    call sent to a line (stricter than B, never looser);
  - caps: B's counts at the last plan plus every call B had not counted yet;
    a day or month that rolls over during the outage starts again from zero;
  - balance: as it was at the last plan; a plan older than 48 hours is not
    used at all (the call is refused, as it is today when B is down);
  - RTB campaigns and number-pool spam rules: not run (RTB calls are refused,
    pool rules let the call through, as they do on any error today).

Standard library only: it runs inside the AGI, under the asterisk user.
"""
import json
import os
import random
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone

STATE_DIR = os.environ.get('AVX_FALLBACK_DIR', '/var/lib/avortyx/fallback')
PLAN_DB = os.path.join(STATE_DIR, 'plan.sqlite3')
JOURNAL_DB = os.path.join(STATE_DIR, 'journal.sqlite3')
PENDING_DIR = os.path.join(STATE_DIR, 'pending')

BLOCKED_CALLER_LABEL = 'CallerId Blocked'
INSUFFICIENT_FUNDS_LABEL = 'Insufficient Funds'
AUTO_BLOCK_REASONS = ('auto_detected', 'spam', 'fraud')
ANONYMOUS_VALUES = {'', 'anonymous', 'private', 'unknown'}
LIVE_WINDOW = 4 * 3600          # B counts live calls from the last four hours
ORIGINAL_MEMORY = 365 * 86400   # ORIGINAL_MEMORY_HOURS
# A plan older than this is not routed with: it would be days of other
# people's changes (paused buyers, lines switched off, new blocks).
MAX_PLAN_AGE = float(os.environ.get('FALLBACK_MAX_PLAN_AGE', str(48 * 3600)))


# ── numbers ──────────────────────────────────────────────────────────────────

def d10(number):
    return ''.join(c for c in str(number or '') if c.isdigit())[-10:]


def number_key(number):
    """spam_protection.numbers.variants as one key: a US number by its ten
    digits, anything else by its digits, a value with no digits as it is."""
    text = str(number or '').strip()
    digits = ''.join(c for c in text if c.isdigit())
    if not digits:
        return text
    if len(digits) == 10 or (len(digits) == 11 and digits.startswith('1')):
        return digits[-10:]
    return digits


def caller_key(value):
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    return digits[-10:] if len(digits) >= 7 else ''


def normalize_caller(raw):
    """routing.asterisk_handler.normalize_caller."""
    raw = (raw or '').strip()
    digits = ''.join(c for c in raw if c.isdigit())
    if len(raw) <= 20 and len(digits) <= 15:
        return raw
    if digits.startswith('1') and len(digits) >= 11:
        return '+' + digits[:11]
    if digits:
        return '+' + digits[:15]
    return raw[:20]


# ── the plan (written by fallback_sync.py) ───────────────────────────────────

PLAN_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE numbers (number TEXT PRIMARY KEY, d10 TEXT, data TEXT);
CREATE INDEX numbers_d10 ON numbers (d10);
CREATE TABLE campaigns (id TEXT PRIMARY KEY, data TEXT);
CREATE TABLE buyers (id TEXT PRIMARY KEY, data TEXT);
CREATE TABLE tfn_buyers (d10 TEXT, buyer_id TEXT);
CREATE INDEX tfn_buyers_d10 ON tfn_buyers (d10);
CREATE TABLE blocks (key TEXT, organization_id TEXT, campaign_id TEXT, buyer_id TEXT,
                     publisher_id TEXT, reason TEXT, expires_at REAL);
CREATE INDEX blocks_key ON blocks (key, organization_id);
CREATE TABLE allows (key TEXT, organization_id TEXT, campaign_id TEXT);
CREATE INDEX allows_key ON allows (key, organization_id);
"""


def write_plan(plan, path=None):
    """Store the plan B sent, replacing the old one in one step."""
    path = path or PLAN_DB
    tmp = f'{path}.new-{os.getpid()}'
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    try:
        db.executescript(PLAN_SCHEMA)
        db.executemany('INSERT INTO meta VALUES (?, ?)', [
            ('version', str(plan.get('version'))),
            ('generated_at', str(plan.get('generated_at') or 0)),
            ('fetched_at', str(time.time())),
            ('area_states', json.dumps(plan.get('area_states') or {})),
        ])
        db.executemany('INSERT OR REPLACE INTO numbers VALUES (?, ?, ?)', [
            (n, d10(n), json.dumps(v)) for n, v in (plan.get('numbers') or {}).items()])
        db.executemany('INSERT INTO campaigns VALUES (?, ?)', [
            (k, json.dumps(v)) for k, v in (plan.get('campaigns') or {}).items()])
        db.executemany('INSERT INTO buyers VALUES (?, ?)', [
            (k, json.dumps(v)) for k, v in (plan.get('buyers') or {}).items()])
        db.executemany('INSERT INTO tfn_buyers VALUES (?, ?)', [
            (d10(line.get('tfn')), k) for k, v in (plan.get('buyers') or {}).items()
            for line in v.get('lines') or [] if d10(line.get('tfn'))])
        db.executemany('INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?, ?)', [
            (number_key(b.get('number')), b.get('organization_id'), b.get('campaign_id'),
             b.get('buyer_id'), b.get('publisher_id'), b.get('reason') or '', b.get('expires_at'))
            for b in plan.get('blocks') or []])
        db.executemany('INSERT INTO allows VALUES (?, ?, ?)', [
            (number_key(a.get('number')), a.get('organization_id'), a.get('campaign_id'))
            for a in plan.get('allows') or []])
        db.commit()
    finally:
        db.close()
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


class Plan:
    def __init__(self, path=None):
        path = path or PLAN_DB
        self.db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)
        meta = dict(self.db.execute('SELECT key, value FROM meta'))
        self.generated_at = float(meta.get('generated_at') or 0)
        self.area_states = json.loads(meta.get('area_states') or '{}')
        self._campaigns = {}
        self._buyers = {}

    @classmethod
    def open(cls, path=None):
        try:
            return cls(path)
        except sqlite3.Error:
            return None

    def number(self, called):
        row = self.db.execute('SELECT data FROM numbers WHERE number = ?', (called,)).fetchone()
        return json.loads(row[0]) if row else None

    def campaign(self, cid):
        if cid not in self._campaigns:
            row = self.db.execute('SELECT data FROM campaigns WHERE id = ?', (cid,)).fetchone()
            self._campaigns[cid] = json.loads(row[0]) if row else None
        return self._campaigns[cid]

    def buyer(self, bid):
        if not bid:
            return None
        if bid not in self._buyers:
            row = self.db.execute('SELECT data FROM buyers WHERE id = ?', (bid,)).fetchone()
            self._buyers[bid] = json.loads(row[0]) if row else None
        return self._buyers[bid]

    def buyers_of_tfn(self, number):
        return [r[0] for r in self.db.execute('SELECT DISTINCT buyer_id FROM tfn_buyers WHERE d10 = ?',
                                              (d10(number),))]

    def blocks(self, caller, org, now):
        return [dict(zip(('campaign_id', 'buyer_id', 'publisher_id', 'reason'), r[:4]))
                for r in self.db.execute(
                    'SELECT campaign_id, buyer_id, publisher_id, reason, expires_at FROM blocks '
                    'WHERE key = ? AND organization_id = ?', (number_key(caller), org))
                if r[4] is None or r[4] > now]

    def allows(self, caller, org):
        return [r[0] for r in self.db.execute(
            'SELECT campaign_id FROM allows WHERE key = ? AND organization_id = ?', (number_key(caller), org))]


# ── the journal: every call A sent somewhere, and what A decided alone ───────

JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY, uniqueid TEXT, created_at REAL, caller TEXT, caller_key TEXT,
    called TEXT, campaign_id TEXT, organization_id TEXT, publisher_id TEXT,
    buyer_id TEXT, dest TEXT, dest_d10 TEXT, rule_id TEXT, rule_destination_id TEXT,
    outcome TEXT, error TEXT, auto_block INTEGER DEFAULT 0, refused_destination TEXT,
    refused_buyer_id TEXT, source TEXT, trace TEXT, synced INTEGER DEFAULT 0, synced_at REAL);
CREATE INDEX IF NOT EXISTS calls_outcome ON calls (outcome, created_at);
CREATE INDEX IF NOT EXISTS calls_caller ON calls (caller, created_at);
CREATE INDEX IF NOT EXISTS calls_campaign ON calls (campaign_id, created_at);
CREATE INDEX IF NOT EXISTS calls_dest ON calls (dest_d10, created_at);
CREATE INDEX IF NOT EXISTS calls_unsynced ON calls (synced, source);
CREATE INDEX IF NOT EXISTS calls_uniqueid ON calls (uniqueid);
CREATE INDEX IF NOT EXISTS calls_caller_key ON calls (caller_key);
CREATE TABLE IF NOT EXISTS rr (key TEXT PRIMARY KEY, idx INTEGER);
"""


class Journal:
    def __init__(self, path=None, timeout=2.0):
        self.db = sqlite3.connect(path or JOURNAL_DB, timeout=timeout)
        self.db.execute(f'PRAGMA busy_timeout = {int(timeout * 1000)}')
        self.db.executescript(JOURNAL_SCHEMA)

    def add(self, **row):
        cols = ', '.join(row)
        self.db.execute(f'INSERT OR IGNORE INTO calls ({cols}) VALUES ({", ".join("?" * len(row))})',
                        tuple(row.values()))
        self.db.commit()

    def _rows(self, sql, args):
        cur = self.db.execute(sql, args)
        names = [c[0] for c in cur.description]
        return [dict(zip(names, r)) for r in cur]

    def dials(self, since, where='', args=()):
        sql = 'SELECT * FROM calls WHERE outcome = ? AND created_at >= ?' + (f' AND {where}' if where else '')
        return self._rows(sql, ('dial', since) + tuple(args))

    def live(self, since, ids, callers):
        """Calls sent since `since` whose channel Asterisk still has (by
        unique id, or by caller after a transfer), looked up by index."""
        found = {}
        for column, values in (('uniqueid', sorted(ids)), ('caller_key', sorted(c for c in callers if c))):
            for i in range(0, len(values), 500):
                chunk = values[i:i + 500]
                sql = (f'SELECT * FROM calls WHERE outcome = ? AND created_at >= ? AND {column} IN '
                       f'({",".join("?" * len(chunk))})')
                for row in self._rows(sql, ('dial', since) + tuple(chunk)):
                    found[row['id']] = row
        return list(found.values())

    def cap_rows(self, since, generated_at, where='', args=()):
        """Calls that may not be in B's counts yet: sent since `since`, and
        every call A routed alone that B did not have when the plan was made."""
        sql = ('SELECT * FROM calls WHERE outcome = ? AND (created_at >= ? OR '
               "(source = 'a' AND (synced = 0 OR synced_at > ?)))" + (f' AND {where}' if where else ''))
        return self._rows(sql, ('dial', since, generated_at) + tuple(args))

    def rr_next(self, key):
        row = self.db.execute('SELECT idx FROM rr WHERE key = ?', (key,)).fetchone()
        return row[0] if row else 0

    def rr_set(self, key, idx):
        self.db.execute('INSERT OR REPLACE INTO rr VALUES (?, ?)', (key, idx))
        self.db.commit()


def live_channels(timeout=2):
    """(unique ids, caller keys) of the channels Asterisk has now, or None."""
    try:
        out = subprocess.run(['asterisk', '-rx', 'core show channels concise'],
                             capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    ids, callers = set(), set()
    for line in out.stdout.splitlines():
        parts = line.split('!')
        if len(parts) > 1:
            ids.add(parts[-1].strip())
            if len(parts) > 7 and caller_key(parts[7]):
                callers.add(caller_key(parts[7]))
    return ids, callers


# ── deciding ─────────────────────────────────────────────────────────────────

class Router:
    def __init__(self, plan, journal, now=None, live=None, rng=None):
        self.plan = plan
        self.journal = journal
        self.now = now if now is not None else time.time()
        self._live = live
        self._live_loaded = live is not None
        self.rng = rng or random
        self.trace = []
        self._live_rows = None

    # live calls
    def live_rows(self):
        if self._live_rows is None:
            if not self._live_loaded:
                self._live = live_channels()
                self._live_loaded = True
            if self._live is None:
                # Asterisk could not be asked: every call of the last four
                # hours counts as live, as B counts a row it has no hang-up for.
                rows = self.journal.dials(self.now - LIVE_WINDOW)
            else:
                ids, callers = self._live
                rows = self.journal.live(self.now - LIVE_WINDOW, ids, callers)
            self._live_rows = rows
        return self._live_rows

    def buyer_rows_match(self, row, buyer_id, buyer):
        if row['buyer_id']:
            return row['buyer_id'] == buyer_id
        tfns = {d10(line['tfn']) for line in (buyer or {}).get('lines') or []}
        return row['dest_d10'] in tfns

    # caps
    def _new_to_b(self, row):
        """Not in B's counts at the plan: sent after it, or routed by A and
        not yet on B when the plan was made."""
        gen = self.plan.generated_at
        if row['created_at'] > gen:
            return True
        return row['source'] == 'a' and (not row['synced'] or (row['synced_at'] or 0) > gen)

    def _used(self, cap, rows):
        """How far into a cap: B's count at the plan, plus what B did not
        count yet."""
        if cap.get('rolling'):
            recent = [r for r in rows if r['created_at'] >= self.now - 3600]
            if cap.get('next') and self.now >= cap['next']:
                return len(recent)
            return cap['used'] + sum(1 for r in recent if self._new_to_b(r))
        if cap.get('next') and self.now >= cap['next']:
            return sum(1 for r in rows if r['created_at'] >= cap['next'])
        start = cap.get('start')
        return cap['used'] + sum(1 for r in rows
                                 if self._new_to_b(r) and (start is None or r['created_at'] >= start))

    def cap_hit(self, caps, rows, order):
        """`rows` may be a function: the journal is only read when there is
        a cap to check."""
        for period in order:
            cap = (caps or {}).get(period)
            if not cap or cap.get('limit', 0) <= 0:
                continue
            if callable(rows):
                rows = rows()
            n = self._used(cap, rows)
            if n >= cap['limit']:
                return period, n, cap['limit']
        return None

    def since_plan(self):
        return min(self.plan.generated_at, self.now - 3600) - 1

    def cap_rows(self, where='', args=()):
        return self.journal.cap_rows(self.since_plan(), self.plan.generated_at, where, args)

    def campaign_cap_reason(self, cid, camp):
        hit = self.cap_hit(camp.get('caps'), lambda: self.cap_rows('campaign_id = ?', (cid,)),
                           ('daily', 'monthly', 'global'))
        if hit:
            return {'daily': 'Campaign daily limit reached', 'monthly': 'Campaign monthly limit reached',
                    'global': 'Campaign total limit reached'}[hit[0]]
        cc = camp.get('max_concurrency') or 0
        if cc > 0 and sum(1 for r in self.live_rows() if r['campaign_id'] == cid) >= cc:
            return 'Campaign cap reached'
        return None

    def buyer_line(self, buyer, number):
        """RoutingEngine.buyer_destination: a live row before a paused one,
        newest first."""
        target = d10(number)
        if not target:
            return None
        lines = [l for l in (buyer or {}).get('lines') or [] if d10(l['tfn']) == target]
        if not lines:
            return None
        live = [l for l in lines if l['enabled']]
        return (live or lines)[-1]

    def line_rows(self, buyer_id, line):
        target = d10(line['tfn'])
        return [r for r in self.cap_rows('dest_d10 = ?', (target,))
                if r['buyer_id'] in (None, '', buyer_id)]

    def line_cap_hit(self, buyer_id, line):
        return self.cap_hit(line.get('caps'), lambda: self.line_rows(buyer_id, line),
                            ('hourly', 'daily', 'monthly', 'global'))

    def line_live(self, buyer_id, buyer, line):
        target = d10(line['tfn'])
        return sum(1 for r in self.live_rows()
                   if r['dest_d10'] == target and self.buyer_rows_match(r, buyer_id, buyer))

    def buyer_caps_ok(self, buyer_id, buyer):
        if not buyer.get('caps'):
            return True
        rows = [r for r in self.cap_rows() if self.buyer_rows_match(r, buyer_id, buyer)]
        return self.cap_hit(buyer['caps'], rows, ('daily', 'monthly')) is None

    def buyer_cc_ok(self, buyer_id, buyer, number):
        line = self.buyer_line(buyer, number)
        if line and line.get('concurrency_cap', 0) > 0:
            if self.line_live(buyer_id, buyer, line) >= line['concurrency_cap']:
                return False
        if (buyer.get('max_concurrency') or 0) <= 0:
            return True
        live = sum(1 for r in self.live_rows() if self.buyer_rows_match(r, buyer_id, buyer))
        return live < buyer['max_concurrency']

    # destinations
    def refusal(self, dest, call):
        dup_only = call.get('dup_only_destination')
        n = d10(dest['destination'])
        if dup_only and n != dup_only:
            return 'duplicate routes to its original destination'
        if n in call.get('dup_exclude_destinations', ()):
            return 'already took this caller (duplicates: different)'
        bid = dest.get('buyer_id')
        if bid and bid in call.get('dup_exclude_buyers', ()):
            return 'buyer already took this caller (duplicates: different)'
        if bid and bid in call.get('blocked_buyers', ()):
            return 'this buyer has blocked the caller'
        buyer = self.plan.buyer(bid)
        if not buyer:
            return None
        reason = fault = None
        line = self.buyer_line(buyer, dest['destination'])
        if buyer['status'] != 'active':
            reason, fault = f"buyer {buyer['name']} is {buyer['status']}", 'Destination missed (buyer paused)'
        elif line is None and (dest.get('type') or 'phone') == 'phone' and len(d10(dest['destination']) or '') >= 10:
            # CH-502: a line whose number is not on the buyer's Destinations
            # page takes no calls (RoutingEngine.line_not_listed).
            reason = f"destination {dest['destination']} is not on the Destinations page"
            fault = 'Destination missed (not on Destinations page)'
        elif line is not None and not line['enabled']:
            reason, fault = f"destination {dest['destination']} is switched off", 'Destination missed (TFN off)'
        elif not self.buyer_caps_ok(bid, buyer):
            reason, fault = f"buyer {buyer['name']} has reached its cap", 'Destination missed (cap full)'
        elif not self.buyer_cc_ok(bid, buyer, dest['destination']):
            reason, fault = f"buyer {buyer['name']} is at concurrency limit", 'Destination missed (CC full)'
        elif line is not None:
            hit = self.line_cap_hit(bid, line)
            if hit:
                reason = f"destination {dest['destination']} reached its {hit[0]} cap {hit[1]}/{hit[2]}"
                fault = 'Destination missed (cap full)'
        if fault and 'refused_destination' not in call:
            call['refused_destination'] = {'tfn': dest['destination'], 'buyer_id': bid, 'label': fault}
        return reason

    def dup_filter(self, dests, call):
        dup_only = call.get('dup_only_destination')
        excl_d = call.get('dup_exclude_destinations', ())
        excl_b = call.get('dup_exclude_buyers', ())
        blocked_b = call.get('blocked_buyers', ())
        if not dup_only and not excl_d and not excl_b and not blocked_b:
            return dests
        return [d for d in dests
                if not (dup_only and d10(d['destination']) != dup_only)
                and d10(d['destination']) not in excl_d
                and not (d.get('buyer_id') and d['buyer_id'] in excl_b)
                and not (d.get('buyer_id') and d['buyer_id'] in blocked_b)]

    def by_weight(self, eligible):
        if len(eligible) == 1:
            return eligible[0]
        weights = [max(0, d.get('weight') or 0) for d in eligible]
        if sum(weights) <= 0:
            return eligible[0]
        return self.rng.choices(eligible, weights=weights, k=1)[0]

    def valid_destination(self, rule, call):
        tiers = []
        for d in rule['destinations']:
            if tiers and tiers[-1][0] == d['priority']:
                tiers[-1][1].append(d)
            else:
                tiers.append((d['priority'], [d]))
        for _p, tier in tiers:
            eligible = []
            for d in tier:
                reason = self.refusal(d, call)
                if reason is None:
                    eligible.append(d)
                else:
                    self.trace.append({'step': 'destination', 'passed': False,
                                       'detail': f"{d['destination']}: {reason}"})
            if eligible:
                return self.by_weight(eligible)
        return None

    def time_match(self, rule):
        cond = rule.get('conditions')
        if not cond:
            return False
        try:
            t = datetime.fromtimestamp(self.now, tz=timezone.utc)
            return (t.weekday() in cond.get('days', [])
                    and cond.get('start_time', '00:00') <= t.strftime('%H:%M') <= cond.get('end_time', '23:59'))
        except Exception:
            return False

    def evaluate(self, rule, call):
        kind = rule['type']
        cond = rule.get('conditions') or {}
        if kind in ('time_based', 'schedule_based'):
            matched = self.time_match(rule)
        elif kind == 'geo_based':
            matched = bool(rule.get('conditions')) and (
                (cond.get('states') and call['caller_state'] in cond.get('states', []))
                or (cond.get('area_codes') and call['caller_area_code'] in cond.get('area_codes', [])))
        elif kind == 'round_robin':
            dests = self.dup_filter(sorted(rule['destinations'], key=lambda d: d['priority']), call)
            if not dests:
                return None
            key = f"round_robin_{rule['id']}"
            idx = self.journal.rr_next(key)
            self.journal.rr_set(key, (idx + 1) % len(dests))
            return dests[idx % len(dests)]
        elif kind == 'weighted':
            dests = self.dup_filter(list(rule['destinations']), call)
            if not dests:
                return None
            total = sum(d.get('weight') or 0 for d in dests)
            pick = self.rng.uniform(0, total)
            acc = 0
            for d in dests:
                acc += d.get('weight') or 0
                if pick <= acc:
                    return d
            return dests[-1]
        elif kind in ('priority', 'overflow'):
            return self.valid_destination(rule, call)
        elif kind == 'caller_specific':
            matched = bool(rule.get('conditions')) and call['caller'] in cond.get('numbers', [])
        else:
            # tag based (Asterisk calls carry no tags), RTB, ping post
            matched = False
        return self.valid_destination(rule, call) if matched else None

    # the call
    def blocked_before_row(self, caller, org, publisher_id):
        """RoutingEngine.is_blocked_caller: dropped with no call row."""
        if not caller:
            return False
        entries = self.plan.blocks(caller, org, self.now)
        if publisher_id and any(b['publisher_id'] == publisher_id for b in entries):
            return True
        team = [b for b in entries if not b['campaign_id'] and not b['buyer_id'] and not b['publisher_id']
                and b['reason'] not in AUTO_BLOCK_REASONS]
        if not team:
            return False
        return None not in self.plan.allows(caller, org)

    def route(self, cid, camp, num, caller, call):
        org = camp['organization_id']
        allowed = self.plan.allows(caller, org)
        if cid not in allowed and None not in allowed:
            if camp.get('anonymous_block') and caller in ANONYMOUS_VALUES:
                return {'error': 'Anonymous caller blocked'}
            org_blocks = [b for b in self.plan.blocks(caller, org, self.now)
                          if not b['buyer_id'] and not b['publisher_id']
                          and (not b['campaign_id'] or b['campaign_id'] == cid)]
            if org_blocks:
                org_blocks.sort(key=lambda b: (b['campaign_id'] is None,))
                return {'error': BLOCKED_CALLER_LABEL,
                        'auto_block': org_blocks[0]['reason'] in AUTO_BLOCK_REASONS}
        self.trace.append({'step': 'blacklist', 'passed': True, 'detail': ''})

        if camp.get('duplicate_call_block') and (camp.get('duplicate_call_block_hours') or 0) > 0:
            since = self.now - camp['duplicate_call_block_hours'] * 3600
            if self.journal.dials(since, 'campaign_id = ? AND caller = ?', (cid, caller)):
                return {'error': 'Duplicate call blocked'}

        reason = self.campaign_cap_reason(cid, camp)
        if reason:
            return {'error': reason}
        if not num.get('balance_ok', True):
            return {'error': INSUFFICIENT_FUNDS_LABEL}
        if camp.get('routing_type') == 'rtb':
            return {'error': 'RTB no winner'}

        mode = camp.get('duplicate_handling') or 'normal'
        prior = []
        if mode in ('original', 'different') and caller:
            since = self.now - ORIGINAL_MEMORY
            if mode == 'original':
                rows = self.journal.dials(since, 'campaign_id = ? AND caller = ?', (cid, caller))
            else:
                rows = self.journal.dials(since, 'organization_id = ? AND caller = ?', (org, caller))
            prior = sorted((r for r in rows if r['dest']), key=lambda r: -r['created_at'])
        if prior:
            if mode == 'original':
                call['dup_only_destination'] = d10(prior[0]['dest'])
            elif camp.get('duplicate_direction') == 'buyer':
                call['dup_exclude_buyers'] = {r['buyer_id'] for r in prior if r['buyer_id']}
                call['dup_exclude_destinations'] = {d10(r['dest']) for r in prior}
            else:
                call['dup_exclude_destinations'] = {d10(r['dest']) for r in prior}

        blocked = {b['buyer_id'] for b in self.plan.blocks(caller, org, self.now) if b['buyer_id']}
        if blocked:
            call['blocked_buyers'] = blocked

        def run():
            for rule in camp.get('rules') or []:
                d = self.evaluate(rule, call)
                if d:
                    return rule, d
            return None, None

        rule, dest = run()
        if not dest and prior:
            if mode == 'different':
                return {'error': 'Duplicate call'}
            for k in ('dup_only_destination', 'dup_exclude_destinations', 'dup_exclude_buyers'):
                call.pop(k, None)
            rule, dest = run()
        if dest:
            return {'rule': rule, 'dest': dest}
        refused = call.get('refused_destination')
        if refused:
            return {'error': refused['label'], 'refused': refused}
        return {'error': 'No matching rule found'}

    def own_tfn(self, buyer, number):
        target = d10(number)
        for line in (buyer or {}).get('lines') or []:
            if target and d10(line['tfn']) == target:
                return line['tfn']
        return None

    def live_tfn(self, buyer_id, buyer, exclude=(), only=None):
        """RoutingEngine.pick_live_destination."""
        rows = [l for l in buyer.get('lines') or [] if l['enabled']]
        if only:
            pinned = [l for l in rows if d10(l['tfn']) == only]
            return pinned[0] if pinned else None
        if exclude:
            rows = [l for l in rows if d10(l['tfn']) not in exclude]
        rows = [l for l in rows if not self.line_cap_hit(buyer_id, l)]
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        key = f'buyer_live_rr_{buyer_id}'
        start = self.journal.rr_next(key)
        n = len(rows)
        for step in range(n):
            line = rows[(start + step) % n]
            cap = line.get('concurrency_cap') or 0
            if cap > 0 and self.line_live(buyer_id, buyer, line) >= cap:
                continue
            self.journal.rr_set(key, (start + step + 1) % n)
            return line
        self.journal.rr_set(key, (start + 1) % n)
        return rows[start % n]


def plan_age(plan, now=None):
    return int((now or time.time()) - plan.generated_at) if plan else None


def decide(called, caller_raw, uniqueid='', now=None, plan=None, journal=None, live=None, rng=None):
    """What B's route endpoint would answer, decided on A. Same reply shape:
    {"action": "dial", "buyer_number", "call_log_id", "max_duration",
    "recording_enabled"} or {"action": "hangup", "reason"}."""
    now = now if now is not None else time.time()
    plan = plan or Plan.open()
    if plan is None:
        return {'action': 'hangup', 'reason': 'no_fallback_plan'}
    if now - plan.generated_at > MAX_PLAN_AGE:
        return {'action': 'hangup', 'reason': 'fallback_plan_too_old'}
    caller = normalize_caller(caller_raw)
    if not caller or not called:
        return {'action': 'hangup', 'reason': 'Missing fields'}
    num = plan.number(called)
    if num is None:
        return {'action': 'hangup', 'reason': 'number_not_found'}
    cid = num['campaign_id']
    camp = plan.campaign(cid)
    if not camp or camp.get('status') != 'active':
        return {'action': 'hangup', 'reason': 'campaign_inactive'}
    journal = journal or Journal()
    router = Router(plan, journal, now=now, live=live, rng=rng)
    org = camp['organization_id']
    if router.blocked_before_row(caller, org, num.get('publisher_id')):
        return {'action': 'hangup', 'reason': BLOCKED_CALLER_LABEL}

    digits = ''.join(c for c in caller if c.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    area = digits[:3] if len(digits) == 10 else ''
    call = {'caller': caller, 'caller_area_code': area,
            'caller_state': plan.area_states.get(area, 'Unknown') if area else ''}
    result = router.route(cid, camp, num, caller, call)

    call_id = str(uuid.uuid4())
    base = dict(id=call_id, uniqueid=uniqueid or '', created_at=now, caller=caller,
                caller_key=caller_key(caller), called=called, campaign_id=cid, organization_id=org,
                publisher_id=num.get('publisher_id'), source='a', synced=0)
    trace = {'steps': router.trace, 'plan_age_seconds': plan_age(plan, now)}
    if 'error' in result:
        refused = result.get('refused') or {}
        journal.add(outcome='refused', error=result['error'], auto_block=int(bool(result.get('auto_block'))),
                    refused_destination=refused.get('tfn'), refused_buyer_id=refused.get('buyer_id'),
                    trace=json.dumps(dict(trace, result=result['error'])), **base)
        return {'action': 'hangup', 'reason': result['error']}

    rule, dest = result['rule'], result['dest']
    number = dest['destination']
    buyer_id = dest.get('buyer_id')
    buyer = plan.buyer(buyer_id)
    if buyer:
        own = router.own_tfn(buyer, number)
        if own:
            number = own
        else:
            live = router.live_tfn(buyer_id, buyer, exclude=call.get('dup_exclude_destinations') or (),
                                   only=call.get('dup_only_destination'))
            if live:
                number = live['tfn']
    # The hang-up report is held from now on; then the call is written down.
    mark_pending(call_id, caller)
    try:
        journal.add(outcome='dial', buyer_id=buyer_id, dest=number, dest_d10=d10(number), rule_id=rule['id'],
                    rule_destination_id=dest['id'],
                    trace=json.dumps(dict(trace, selected={'destination': number,
                                                           'buyer': (buyer or {}).get('name'),
                                                           'rule': rule.get('name')})), **base)
    except Exception:
        unmark_pending(call_id)
        raise
    return {'action': 'dial', 'buyer_number': number, 'call_log_id': call_id, 'max_duration': 3600,
            'recording_enabled': bool(camp.get('recording_enabled', True))}


def note_b_call(called, caller_raw, uniqueid, reply, now=None, plan=None, journal=None):
    """A call B routed: kept so A knows it is live and counts it against caps."""
    if not isinstance(reply, dict) or reply.get('action') != 'dial' or not reply.get('call_log_id'):
        return
    plan = plan or Plan.open()
    num = plan.number(called) if plan else None
    dest = str(reply.get('buyer_number') or '')
    camp = plan.campaign(num['campaign_id']) if num else None
    buyers = set(plan.buyers_of_tfn(dest)) if plan else set()
    if not buyers and camp:
        # A rule line naming a buyer with a number that is not on its
        # Destinations page: the buyer is the rule line's.
        buyers = {d['buyer_id'] for r in camp.get('rules') or [] for d in r['destinations']
                  if d.get('buyer_id') and d10(d['destination']) == d10(dest)}
    buyers = sorted(buyers)
    caller = normalize_caller(caller_raw)
    # Never holds up a call B already routed: a busy journal skips the note.
    (journal or Journal(timeout=0.3)).add(
        id=str(reply['call_log_id']), uniqueid=uniqueid or '',
        created_at=now if now is not None else time.time(), caller=caller, caller_key=caller_key(caller),
        called=called, campaign_id=num['campaign_id'] if num else None,
        organization_id=camp['organization_id'] if camp else None,
        publisher_id=num.get('publisher_id') if num else None,
        buyer_id=buyers[0] if len(buyers) == 1 else None, dest=dest, dest_d10=d10(dest),
        outcome='dial', source='b', synced=1)


def mark_pending(call_id, caller=''):
    """call_ended.sh holds this call's hang-up report until B has the call.
    The caller's last ten digits are inside, for a hang-up that arrives
    without the call id (a transfer at the buyer). If the marker cannot be
    written the call is not placed: it could not be billed."""
    os.makedirs(PENDING_DIR, exist_ok=True)
    with open(os.path.join(PENDING_DIR, call_id), 'w') as f:
        f.write(caller_key(caller) + '\n')


def unmark_pending(call_id):
    try:
        os.remove(os.path.join(PENDING_DIR, call_id))
    except OSError:
        pass
