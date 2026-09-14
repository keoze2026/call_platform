# Change Log — Call Platform Backend

Running record of every change made during the handover-prep work.
Newest at the bottom. Each change has an ID — quote the ID when discussing one.

## Index

| ID | Date | Area | Summary | Status |
|----|------|------|---------|--------|
| [CH-001](#ch-001) | 2026-08-31 | Phone Numbers | Attach purchased numbers to Twilio SIP trunk | Done — commit `b6e602ce` |
| [CH-002](#ch-002) | 2026-08-31 | Routing | Destination edit/delete/list API endpoints | Done — commit `76b76357` |
| [CH-003](#ch-003) | 2026-08-31 | Phone Numbers | Trunk-attach failure no longer aborts a paid-for purchase | Done — commit `76b76357` |
| [CH-004](#ch-004) | 2026-09-13 | Multi-tenant / Analytics | Org alignment, analytics backfill, webhook + recording fixes | Done — commit `d470a7d4` |
| [CH-005](#ch-005) | 2026-09-14 | Celery / Scaling | Task registration fix, async webhooks, decoupled analytics mirroring | Done — commit `e8e5a888` |

## Open items (not done yet)

| ID | Area | Summary | Priority |
|----|------|---------|----------|
| [OPEN-4](#open-4) | Repo | `phone_numbers/services.py.bak_trunk` committed by mistake | Low |

---

<a name="ch-001"></a>
## CH-001 — Attach purchased numbers to Twilio SIP trunk

**Date:** 2026-08-31
**Commit:** `b6e602ce`
**Made on:** server (`/opt/call_platform`), pushed to GitHub, pulled locally

### Problem
Buying a number through `POST /api/numbers/purchase` bought it from Twilio but never
attached it to the Elastic SIP Trunk. Calls to the new number never reached Asterisk,
so a freshly provisioned tracking number silently did not work.

### Files changed

**`config/settings.py`** (line 191) — new setting:
```python
TWILIO_TRUNK_SID = config('TWILIO_TRUNK_SID', default='')
```

**`phone_numbers/services.py`** (lines 54-64, inside `PhoneNumberService.purchase_number`) —
after `client.incoming_phone_numbers.create(...)`, attach the number to the trunk:
```python
if settings.TWILIO_TRUNK_SID:
    try:
        client.trunking.v1.trunks(settings.TWILIO_TRUNK_SID).phone_numbers.create(
            phone_number_sid=purchased.sid
        )
    except Exception as trunk_error:
        raise ValueError(
            f"Number purchased ({purchased.phone_number}) but failed to attach to SIP trunk: {trunk_error}"
        )
```

### API impact
None — no request or response shape changed.

### Notes / caveats
- `default=''` means that if `TWILIO_TRUNK_SID` is not set in the server's `.env`, the
  attach is **silently skipped** and purchases appear to succeed while the number still
  does not route. Confirm the real value is present on the server.
- The `raise ValueError` path is a problem — see [OPEN-1](#open-1).

---

<a name="ch-002"></a>
## CH-002 — Routing destination edit/delete/list API endpoints

**Date:** 2026-08-31
**Commit:** `76b76357`
**Made on:** local (`/home/hans/Desktop/call_platform`)

### Problem
`RuleDestination` (which buyer / which phone number a routing rule sends calls to) had
**create-only** API coverage. There was no endpoint to list, edit, or delete a destination,
so the routing plan builder in the frontend could not change a buyer's routing number.
The only way to change it was direct database access — not viable for handover.

Also, `GET /api/routing/rules` returned no destination information at all, which is why the
plan builder rendered "0 nodes / 0 buyers" even when a real destination existed.

Note: `/api/destinations/` is a **different** model (buyer destinations, in
`buyers/destinations_api.py`) and was never the routing-rule editor.

### Files changed

**`routing/schemas.py`** — new schema after `CreateDestinationSchema`:
```python
class UpdateDestinationSchema(Schema):
    destination_type: Optional[str] = None
    destination: Optional[str] = None
    phone_number: Optional[str] = None
    buyer_id: Optional[str] = None
    priority: Optional[int] = None
    weight: Optional[int] = None
```
All fields optional — PATCH semantics, only what you send gets changed.

**`routing/services.py`** — five new methods on `RoutingService`, added after `add_destination`:

| Method | Purpose |
|--------|---------|
| `list_destinations(rule_id, user)` | All destinations for a rule |
| `get_destination(rule_id, destination_id, user)` | One destination, scoped to the rule |
| `update_destination(rule_id, destination_id, data, user)` | Partial update |
| `delete_destination(rule_id, destination_id, user)` | Delete |
| `format_destination(destination)` | Shared response formatter |

`update_destination` details:
- validates `destination_type` against `RuleDestination.DestinationType.choices`
- accepts `destination` **or** `phone_number` as aliases, matching `add_destination`
- looks up `buyer_id` scoped to the caller's organization; raises `"Buyer not found"` otherwise
- `_detach_buyer` flag (set by the API layer) clears the buyer

**`routing/services.py`** — `list_rules` now annotates counts:
```python
.annotate(
    destination_count=Count('destinations', distinct=True),
    condition_count=Count('conditions', distinct=True),
)
```
Annotated rather than counted per-row, so this adds no N+1 queries.

**`routing/api.py`** — three new endpoints, plus two smaller edits:
- imported `UpdateDestinationSchema`
- `add_destination` now returns `RoutingService.format_destination(...)` instead of an
  inline dict — **identical output**, just deduplicated
- `list_rules` response now includes `destination_count` and `condition_count`

### API impact

**New endpoints** (all require JWT auth, all scoped to the caller's organization):

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/api/routing/rules/{rule_id}/destinations` | `200` list of destinations, `404` if rule not found |
| `PATCH` | `/api/routing/rules/{rule_id}/destinations/{destination_id}` | `200` updated destination, `400` invalid input, `404` not found |
| `DELETE` | `/api/routing/rules/{rule_id}/destinations/{destination_id}` | `200` `{message, success}`, `404` not found |

**Destination object shape** (same everywhere — create, list, patch):
```json
{
  "id": "uuid",
  "destination_type": "phone | sip | buyer",
  "destination": "+254796503329",
  "priority": 1,
  "weight": 100,
  "buyer_id": "uuid or null",
  "buyer_name": "string or null"
}
```

**Changed response:** `GET /api/routing/rules` list items gained two fields:
`destination_count` (int) and `condition_count` (int). Additive only — nothing removed
or renamed, so existing frontend code keeps working.

**To change a buyer's routing number** (the original handover complaint):
```
PATCH /api/routing/rules/{rule_id}/destinations/{destination_id}
{ "destination": "+254796503329", "buyer_id": "7239441a-..." }
```
Send `"buyer_id": null` explicitly to detach the buyer and leave a bare phone destination.

### Verification status
- Syntax checked (`python3 -m py_compile`) — passes.
- `python manage.py check` on the server (2026-08-31) — **0 issues**.
- No migration needed — no model changes.

---

<a name="ch-003"></a>
## CH-003 — Trunk-attach failure no longer aborts a paid-for purchase

**Date:** 2026-08-31
**Commit:** `76b76357`
**Made on:** local (`/home/hans/Desktop/call_platform`)
**Resolves:** [OPEN-1](#open-1) — implements **option A**

### Problem
In [CH-001](#ch-001), a failed SIP-trunk attach raised `ValueError`. That bubbled up to the
outer `except Exception` in `purchase_number` and returned HTTP 400 — but Twilio had already
sold and billed the number. The organization was charged for a number with **no database
record**, invisible and unmanageable from the UI.

Same problem when `TWILIO_TRUNK_SID` was unset: the old code skipped the attach silently, so
the purchase reported success while the number could never receive a call.

### Files changed

**`phone_numbers/services.py`** — `purchase_number`, trunk block rewritten:
- never raises; records the reason in a local `trunk_warning` instead
- an **unset** `TWILIO_TRUNK_SID` is now an explicit warning, not a silent skip
- the `PhoneNumber` row is always created

**`phone_numbers/services.py`** — status now reflects reality:
```python
status=PhoneNumber.Status.PENDING if trunk_warning else PhoneNumber.Status.ACTIVE
```
`PENDING` was used because the `Status` choices are only `active` / `released` / `pending` —
there is no `inactive`. This matters: `routing/asterisk_handler.py` looks up incoming numbers
with `status='active'`, so a `pending` number correctly will not accept calls.

**`phone_numbers/services.py`** — `format_number` now emits `trunk_warning`
(via `getattr(..., None)`, so every other caller is unaffected and returns `null`).

**`phone_numbers/schemas.py`** — `PhoneNumberOutSchema` gained `trunk_warning: Optional[str] = None`.

**`phone_numbers/api.py`** — `purchase_number` carries the warning across the
`get_number` refetch, which would otherwise drop the in-memory attribute.

### API impact

`POST /api/numbers/purchase` **no longer returns 400 when the trunk attach fails.** It returns
`201` with:
```json
{ "status": "pending",
  "trunk_warning": "Number purchased but failed to attach to SIP trunk: <reason>. It will not receive calls until the attach succeeds." }
```
A fully successful purchase returns `"status": "active"` and `"trunk_warning": null`.

`trunk_warning` is `null` on every other endpoint that returns a phone number.

**Frontend note:** the provisioning flow should show `trunk_warning` when present — a `pending`
number looks bought but cannot take calls. Silently treating 201 as success hides that.

### Verification status
- Syntax checked (`python3 -m py_compile`) — passes.
- `python manage.py check` on the server (2026-08-31) — **0 issues**.
- No migration needed — `PENDING` is an existing choice, no model change.

### Still to do
No retry endpoint exists for attaching a `pending` number to the trunk. Today the fix is to
release and re-purchase, or attach it by hand in the Twilio console. Worth adding if this
turns out to happen more than rarely.

---

<a name="open-1"></a>
## OPEN-1 — Purchase hard-fails after Twilio has already charged — RESOLVED by [CH-003](#ch-003)

**Priority:** High — **resolved 2026-08-31, option A**
**Location:** `phone_numbers/services.py` lines 60-63

When the trunk attach fails, the code raises `ValueError`. That propagates to the outer
`except Exception` at the end of `purchase_number` and returns HTTP 400 — **after** Twilio
has already sold and billed the number. Result: the organization is charged for a number
with no database record and no way to see or manage it from the UI.

Options discussed:
- **A (recommended)** — save the `PhoneNumber` row with `status='inactive'`, return 201 with
  a warning field. Number is visible, attach can be retried.
- **B** — save it, return 201, log the error. Simple, but a non-routing number looks healthy.
- **C** — keep current behavior, but release the number back to Twilio first so nothing is
  paid for.

Option **A** was implemented — see [CH-003](#ch-003).

<a name="open-2"></a>
## OPEN-2 — `TWILIO_TRUNK_SID` missing from `.env.example` — CLOSED, NO CHANGE MADE

**Closed 2026-08-31, not actioned.** The live server is already configured. `.env.example`
was left untouched.
**Location:** `.env.example`, `# Twilio` section

The setting exists in `config/settings.py` but the example env file does not mention it, so a
fresh deploy will silently skip the trunk attach. Add `TWILIO_TRUNK_SID=your-trunk-sid`.

<a name="open-3"></a>
## OPEN-3 — `CallRecord` timestamps stamped at sync time, not call time — CLOSED, NOT AN ISSUE

**Closed 2026-08-31.** The hourly chart was already fixed and confirmed working before this
review. Kept here only as a note on `auto_now_add` behaviour if calls are ever bulk-backfilled.

**Priority:** none
**Location:** `routing/signals.py`, `analytics/models.py` line 67

`routing/signals.py` mirrors `CallLog` into `analytics.CallRecord` (which the dashboard and
reports read from). But `CallRecord.created_at` is `auto_now_add`, and the signal never sets
`started_at` — so every mirrored row is stamped when the sync ran, not when the call happened.

Live traffic is close enough that this is invisible. Any **backfill of historical calls**
would pile every record into the backfill hour — which is exactly what a broken
"calls by hour" chart looks like. Worth ruling out before blaming the frontend chart.

Fix: set `started_at` from `call.answered_at` / `call.created_at`, and force `created_at` with
a follow-up `.filter(id=...).update(created_at=...)` (an `auto_now_add` field ignores values
passed to `update_or_create` defaults).

<a name="open-4"></a>
## OPEN-4 — Backup file committed by mistake

**Priority:** Low
**Location:** `phone_numbers/services.py.bak_trunk`

A 259-line pre-change copy of `services.py` got included in commit `b6e602ce`. The change it
was backing up is committed, so git already holds that version.

```bash
git rm phone_numbers/services.py.bak_trunk
git commit -m "Remove backup file"
```
<a name="ch-004"></a>
## CH-004 — Multi-Tenant Account Alignment, Analytics Backfill, & Webhook Fixes

**Date:** 2026-09-13
**Commit:** `d470a7d4`
**Made on:** server (`/opt/call_platform`), pushed to GitHub, pulled locally

### Problem
1. **Tenant Isolation Discrepancy:** The primary developer/user account (`haansjuma`) was accidentally mapped to an empty tenant organization (`jumahte`), causing all dashboard summary queries and API metrics to return zero records despite the database containing hundreds of valid calls.
2. **Hanging Calls & Malformed Webhooks:** Live Asterisk calls were permanently getting stuck in the `ringing` state because the end-of-call webhook payload was constructed using raw inline string concatenation in the dialplan, causing JSON syntax errors under variable-quoting constraints. Furthermore, duration (`CDR billsec`) was being queried before the channel had actually terminated, always returning `0`.
3. **Analytics Sync Gap & Timezone Bug:** Historical call records completed prior to analytics signal updates were missing from the analytics mirroring table (`CallRecord`), causing reporting gaps. Additionally, a naive-versus-timezone-aware datetime bug in the reporting API's date-range filter silently excluded all calls from hourly breakdowns.
4. **Recording Playback Failures:** Call recording endpoints returned `403 Forbidden` due to overly restrictive directory permissions on the Asterisk spool, and audio URLs pointed to the raw server IP instead of the SSL-secured domain (`avortyx.io`).

### Files Changed

**`routing/asterisk_handler.py`** — rewritten end-of-call handling:
- Moved webhook execution from immediate `Dial()` inline calls to Asterisk's dedicated hangup (`h`) extension to guarantee duration is captured post-termination.
- Shifted payload creation from risky string concatenation to a robust standalone shell script (`call_ended.sh`) ensuring valid JSON output.

**`routing/services.py` & `routing/api.py`** — multi-tenant and routing adjustments:
- Realigned user account organization mapping to the populated `Avortyx` tenant (`cdf49649-c655-431c-a9fc-cecf24da81a4`).
- Validated backend query filters for `connected`, `no_answer`, and `failed` status states against Django request factories, confirming the paginated `items` response structure yields correct data counts.

**`analytics/services.py` & `analytics/schemas.py`** — reporting fixes:
- Patched timezone handling on analytics filtering logic to prevent silent date-range omissions.
- Executed one-time backfill routines to mirror historical call logs into the analytics reporting table.

### API & Operational Impact
- **Dashboard Recovery:** Dashboard metric queries and reports now correctly populate with data when viewing under the correct organization tenant (`Avortyx`).
- **Webhook Reliability:** Asterisk call completion webhooks now successfully update call logs to `completed`/`no_answer`/`failed` with precise durations and valid JSON payloads.
- **Recordings Access:** Audio streams serve correctly over HTTPS via `avortyx.io` domains with proper file permissions.

### Verification Status
- Validated via direct Django test runner scripts on the server (`/opt/call_platform/venv/bin/python`).
- Verified query metrics return 50 connected items, 50 no-answer items, and 1 failed item successfully under test harness environments.

---

<a name="ch-005"></a>
## CH-005 — Celery task registration, async webhooks, decoupled analytics mirroring

**Date:** 2026-09-14
**Commit:** `e8e5a888`
**Made on:** local (`/home/hans/Desktop/call_platform`)
**Roadmap:** Scaling Step 1 — Asynchronous Worker Scaling

### Problem

Audit of the Celery setup found three issues, in dependency order:

**F1 — tasks were almost certainly never registered with the worker.**
`config/celery.py` called `app.autodiscover_tasks(['tasks'])`. That form looks for a module
named `tasks.tasks`; `tasks.py` is a flat top-level module, not a package. The bare
`autodiscover_tasks()` scans `INSTALLED_APPS` for `<app>/tasks.py` — no app has one, and
`tasks` is not an installed app. Nothing imported `tasks.py` at worker startup, so all six
beat entries and `transcribe_call_recording` would fail as unregistered tasks.

**F3 — webhook delivery blocked the callback thread.**
`routing/twilio_handler.py` called `WebhookService.dispatch` → `deliver` → `_send`, a blocking
`httpx.post` with `timeout=webhook.timeout_seconds` (default 10), looped over every matching
webhook. Three webhooks pointed at a dead endpoint held the handler for 30 seconds.

**F4 — analytics mirroring ran synchronously inside the request.**
The `post_save` receiver in `routing/signals.py` did an `update_or_create` on `CallRecord`
during every terminal `CallLog` save, inside the web request and its transaction.

**F5 — broker settings were defined twice**, the first pair with no default (crashing boot if
the env var was absent) and immediately overwritten by the second.

F1 was a hard prerequisite: converting F3/F4 to `.delay()` while tasks were unregistered would
have silently stopped webhooks firing and sent the dashboard back to zero.

### Files changed

**`config/celery.py`** — `Celery('call_platform', include=['tasks'])` replaces the
unresolvable `autodiscover_tasks(['tasks'])`. `include=` imports the module directly at worker
startup, which is what actually registers the `@app.task` entries. The `INSTALLED_APPS` scan is
kept for future app-level task modules.

**`config/settings.py`** — removed the dead pair:
```python
CELERY_BROKER_URL =  config('CELERY_BROKER_URL')        # no default -> raised if unset
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND')
```
The `REDIS_URL`-based assignments two lines below were already the effective values.

**`webhooks/services.py`** — new `WebhookService.enqueue(webhook, event, payload)` creates the
`WebhookDelivery` row and hands the HTTP call to `tasks.send_webhook` via `.delay()`.
`dispatch()` now calls `enqueue()` instead of `deliver()`.

`deliver()` was left synchronous **on purpose** — `POST /api/webhooks/{id}/test`
(`webhooks/api.py`) returns `response_code` and `response_body` to the caller, so making it
async would break that endpoint's contract.

**`routing/signals.py`** — the `update_or_create` body moved into a standalone
`mirror_call_log(call_log_id)` function that a worker can call. The receiver now only enqueues,
via `transaction.on_commit` — without that the worker can race the web process and read a
`CallLog` row that has not committed yet. `mirror_call_log` re-checks terminal status at
execution time, since the row can change between enqueue and run.

**`tasks.py`** — new `tasks.mirror_call_record` task calling `mirror_call_log`.

### Fallback behaviour

Both refactors wrap `.delay()` in `try/except` and run the work inline if it raises. A broker
outage degrades to the previous synchronous behaviour rather than dropping events.

### API impact
None. No request or response shape changed, no new endpoints, no migration.

### Operational impact
- Webhook events are no longer sent from the request thread. The Asterisk/status callback
  handler returns without waiting on remote endpoints.
- `CallRecord` mirroring happens in a worker after commit. The analytics table is now
  **eventually** consistent with `CallLog` rather than immediately — expect sub-second lag
  normally, longer if the worker queue backs up.
- A worker **must** be running for webhooks and analytics to work under normal operation.
  This was not true before this change.

### Systemd consolidation (server-side, no repo files)

`celery.service` retired in favour of `callplatform-worker.service` and
`callplatform-beat.service`. Commands as run:
```bash
sudo systemctl stop celery.service
sudo systemctl disable celery.service
sudo mv /etc/systemd/system/celery.service /root/celery.service.disabled
sudo systemctl daemon-reload
sudo systemctl restart callplatform-worker
```
`systemctl mask` was skipped — it failed with *"File ... already exists"* because the
unit file was still in place, and moving the file away made masking unnecessary. The
unit is preserved at `/root/celery.service.disabled` if it is ever needed.

**Still open:** `callplatform-worker.service` has no `-n` flag, so it uses the default
node name `celery@vmi3333575`. Harmless with one worker, but the moment a second is
added the collision returns. Add `-n callplatform@%h` to `ExecStart` before scaling out.

Also noted: the worker runs as **root** (`uid=0`), which Celery warns against on every
start. Not addressed here.

### Other applications on this host

`ps` shows a second, unrelated Celery fleet as uid `10001` from
`/usr/local/bin/python3.12`, also invoked `-A config`, on queues `pacer`, `events`,
`dispatch`, `telemetry`, `maintenance`, and one consuming the default `celery` queue
alongside `payments`, `fiscal`, `claims`, `interop`.

A shared broker would let that fleet consume this platform's messages and discard them
as unregistered. It does **not** share one: the worker log reports `mingle: all alone`
on `redis://127.0.0.1:6379/0`, so no other node is on that broker. No action needed,
recorded because the process list looks alarming at a glance.

### Verification status — deployed and verified 2026-09-14

- `python manage.py check` on the server — **0 issues**.
- `celery -A config inspect registered` lists **`tasks.mirror_call_record`** and
  **`tasks.send_webhook`**. One node (`celery@vmi3333575`), `mingle: all alone`.
- Broker confirmed `redis://127.0.0.1:6379/0`, worker running from
  `/opt/call_platform/venv`.
- **Not yet exercised end to end.** No call had come through since deploy (newest
  `CallLog` was 2026-09-11), so the `.delay()` path has not run against live traffic.
  The first real call confirms it: if `CallRecord` does not gain a row after a call
  reaches a terminal status, the mirroring task is not firing.

### Correction to the F1 diagnosis

The audit claimed tasks were never registered with the worker. That was wrong —
`celery -A config inspect registered` on the *pre-change* code already listed
`tasks.send_webhook`, so something was importing `tasks.py` at worker startup. The
`include=['tasks']` change is correct and makes registration explicit rather than
incidental, but it was not repairing a live breakage.

### What actually went wrong on deploy

The first post-deploy `inspect registered` did not list `tasks.mirror_call_record`,
which looked like a failed rollout. The real cause was the duplicate service:

- `celery.service` was still running, started **2026-09-11**, on code three days old.
- Neither it nor `callplatform-worker.service` passes `-n`, so **both claimed the node
  name `celery@vmi3333575`**. `inspect` got its reply from the stale process.

Retiring `celery.service` and restarting the worker resolved it. This is the
duplicate-node symptom the consolidation step was meant to fix.

### Still open from the Step 1 audit
- **F2** — `analytics/tasks.py` does not exist. `analytics/scheduled_reports_api.py` imports
  `send_scheduled_report` from it inside `try/except Exception: pass`, so "Run now" on a
  scheduled report silently does nothing and returns `{"ok": true}`.
- **F6** — no task queues or routes; everything shares one default queue, so a slow
  transcription sits in front of webhook retries.
- **F7** — beat uses interval floats, not crontab, so daily jobs drift on every restart.
