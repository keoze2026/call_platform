# Server A routes while Server B is down (CH-464)

Sir, 2 Nov: Server A keeps routing calls when Server B is down. Buyer caps
and concurrency limits still apply, and every call made meanwhile is synced
to B so billing stays accurate.

## How it works

| Piece | Where | What it does |
|---|---|---|
| `routing/fallback.py` | B | `GET /api/twilio/asterisk/fallback-plan/`: the routing plan (numbers, campaigns, rules, buyers, lines, caps used, blocked and allowed callers, balance). `POST /api/twilio/asterisk/fallback-calls/`: writes the calls A routed alone, with A's call ids. |
| `scripts/fallback/avx_fallback.py` | A | Decides a call from the plan, the same way `routing/engine.py` does. |
| `scripts/fallback/fallback_agi.py` | A | Called by `route_call.py`. Asks B first, and decides on A only when B gives no answer (nothing listens, no answer within 1.5 s, a 5xx). |
| `scripts/fallback/fallback_sync.py` | A | The `avx-fallback-sync` service. Every 5 s it sends A's own calls to B; every minute it fetches the plan. |
| `docs/asterisk/call_ended.sh` | A | Holds the hang-up report of a call A routed alone until B has the call, then delivers it, so it is billed as usual. |

State on A is in `/var/lib/avortyx/fallback/`:
- `plan.sqlite3` is the last plan;
- `journal.sqlite3` holds every call A sent and what A decided alone;
- `pending/` holds markers for the held hang-up reports.

Check it at any time, which changes nothing:

```bash
python3 /opt/call_platform/scripts/fallback/fallback_sync.py --status
```

## Install

Neither part touches the current server. B is not live yet.

**B** (after the patch is on GitHub). Load the code; this restarts B's API
only:

```bash
cd /opt/call_platform && git pull && docker compose restart web web_calls
```

**A**, steps 1 to 4:

1. Load the code:

   ```bash
   cd /opt/call_platform && git pull
   ```

2. Create the state folder, owned by the asterisk user, who runs the AGI:

   ```bash
   install -d -o asterisk -g asterisk -m 750 /var/lib/avortyx/fallback /var/lib/avortyx/fallback/pending
   ```

3. Install the new hang-up script. Server A's copy came from the current
   server with B's address put in, so the address is put in again:

   ```bash
   cp /usr/local/bin/call_ended.sh /root/call_ended.sh.before-ch464
   cp /opt/call_platform/docs/asterisk/call_ended.sh /usr/local/bin/call_ended.sh
   sed -i 's#http://127.0.0.1:8000#http://10.20.0.2:8001#' /usr/local/bin/call_ended.sh
   chmod 755 /usr/local/bin/call_ended.sh
   ```

4. Start the sync service, and check that the plan arrived:

   ```bash
   cp /opt/call_platform/deploy/avx-fallback-sync.service /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now avx-fallback-sync
   sleep 10; python3 /opt/call_platform/scripts/fallback/fallback_sync.py --status
   ```

5. Install the new routing script. It is the old one with two changes: a
   call B cannot take is routed by A instead of hung up, and the secret is
   read from `/etc/asterisk/call_ended.secret` instead of being written in
   the file:

   ```bash
   cp /usr/share/asterisk/agi-bin/route_call.py /root/route_call.py.before-ch464
   install -o asterisk -g asterisk -m 755 /opt/call_platform/scripts/fallback/route_call.py /usr/share/asterisk/agi-bin/route_call.py
   ls -l /etc/asterisk/call_ended.secret
   ```

   To go back: `cp /root/route_call.py.before-ch464 /usr/share/asterisk/agi-bin/route_call.py`.

6. The hang-up reports that could not be delivered are resent every minute
   by the asterisk user's crontab (as on the current server). Added on the
   switch night, with A's other jobs:

   ```
   * * * * * /usr/local/bin/call_ended.sh --replay >> /tmp/call_ended_replay.log 2>&1
   ```

## Testing with B switched off (load test, Sir)

On B: `docker compose stop web web_calls`. Calls on A keep connecting.

On A, `--status` shows calls "waiting for B". On B:
`docker compose start web web_calls`. Within seconds, A's calls appear on
B with their buyer, line and charge.

## While B is away, A is stricter, never looser

- **Duplicates:** the rules only see calls that went through A, and count
  every call sent to a line, not only answered ones.
- **Caps:** B's last counts plus every call B has not counted yet. A day or
  month that rolls over starts again from zero.
- **Balance:** as at the last plan. A plan older than 48 hours is not used;
  calls are then refused, as they are today without B.
- **RTB campaigns:** refused. Number-pool spam rules are not run (as on any
  error today).
