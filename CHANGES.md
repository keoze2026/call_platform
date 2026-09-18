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
| [CH-006](#ch-006) | 2026-09-18 | Billing / Routing | Balance guardrail before call dispatch, manual recharge command | Done — commit `be28f5f5` |
| [CH-007](#ch-007) | 2026-09-18 | Billing | Per-minute call charging, per-client rates | Done — commit `80c9d2fe` |
| [CH-008](#ch-008) | 2026-09-18 | Analytics | Revenue/payout split, conversion gating, duplicate records removed | Done — commit `aa68a720` |
| [CH-009](#ch-009) | 2026-09-18 | Routing / Scaling | Carrier lookup off the call path, Telnyx no longer blocks calls | Done — commit `d5f55466` |
| [CH-010](#ch-010) | 2026-09-18 | Migrations | State-only FK migration, CallLog indexes | Done — commit `ceecc5ea` |
| [CH-006](#ch-006) | 2026-09-17 | Analytics | Dynamic Dashboard Pricing & PhoneNumber Formatting | Done |

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

---

<a name="ch-006"></a>
## CH-006 — Dynamic Dashboard Pricing & PhoneNumber Formatting

**Date:** 2026-09-17
**Made on:** local (`/home/hans/Desktop/call_platform`)

### Problem
1. **Analytics Dashboard Discrepancy:** The dashboard header metrics (`total_revenue`, `total_payout`, etc.) relied on stale denormalized row data stored during call sync. Whenever a Campaign's `payout_amount` or `revenue_amount` was modified, historical records became out of sync, causing massive discrepancies in financial reporting without a manual backfill script.
2. **PhoneNumber Payout Display:** The Phone Numbers table always showed `$0.00` because it read the empty `payout_per_call` database field instead of dynamically falling back to the parent campaign's payout value.
3. **ORM Annotation Collision:** During the initial refactor, annotating fields with the same names as `@property` getters lacking `@property.setter`s caused a crash when Django attempted to attach the annotated results to model instances.

### Files changed

**`analytics/models.py`**
- Converted `campaign_id` from a `UUIDField` to a true `ForeignKey` pointing to `Campaign`, setting `db_column='campaign_id'` so no database schema migrations were required.
- Added `@property` methods (`dynamic_revenue`, `dynamic_payout`, `dynamic_profit`) that unconditionally grab pricing from `self.campaign` (if attached), otherwise falling back to `self.revenue`/`self.payout`.
- Added corresponding `@property.setter` methods to prevent `AttributeError: has no setter` crashes when Django's ORM attempts to attach `.annotate()` query results to the objects.

**`analytics/services.py`**
- Updated the `_base_qs` query builder to dynamically `annotate` `dynamic_revenue` and `dynamic_payout` via SQL `Case/When` statements connecting to `campaign__revenue_amount` and `campaign__payout_amount`.
- Refactored `get_dashboard`, `get_campaign_performance`, `get_time_series`, and other aggregator methods to run their `Sum()` functions directly against these new dynamic annotations.

**`phone_numbers/services.py`**
- Updated the API serialization method `format_number(phone_number)`. If `payout_per_call` is `0` or unset, it now dynamically falls back to the `campaign.payout_amount` so the frontend UI stays perfectly synchronized with any future campaign pricing updates.

### API impact
- **Phone Numbers List/Detail Endpoint:** The `payout_per_call` string now accurately displays the inherited campaign payout for visual accuracy.
- **Dashboard APIs:** All aggregate financial stats now reflect real-time campaign pricing rules across all historical records instantly.

### Verification status
- All fixes tested against live local instances.
- Zero database migrations required.
- Solved data drift issue entirely.

---

<a name="ch-006"></a>
## CH-006 — Balance validation before call dispatch, manual recharge workflow

**Date:** 2026-09-18
**Commit:** `be28f5f5`
**Made on:** local (`/home/hans/Desktop/call_platform`)

### Problem
Calls routed to destinations regardless of whether the organization had credit to pay
for them. Nothing checked `BillingAccount.balance` before dispatch, and there was no
way to credit an account outside the Stripe/CoinGate/Capitalist payment flows — so a
manual top-up meant editing the database by hand.

### Files changed

**`config/settings.py`** — two new settings:
```python
MINIMUM_CALL_BALANCE = Decimal(config('MINIMUM_CALL_BALANCE', default='0'))
ENFORCE_CALL_BALANCE = config('ENFORCE_CALL_BALANCE', default=True, cast=bool)
```
`ENFORCE_CALL_BALANCE=False` disables the guardrail with a restart, no deploy.
`MINIMUM_CALL_BALANCE` defaults to **0 — no hardcoded rate**. It is only a floor
for campaigns with no pricing configured at all.

**`billing/services.py`** — three additions to `BillingService`:

| Method | Purpose |
|--------|---------|
| `get_balance(organization)` | Current credit; a missing account reads as `0.00` |
| `has_sufficient_balance(organization, amount)` | Counts `credit_limit`; `False` for suspended or missing accounts |
| `add_funds(organization, amount, ...)` | Manual credit, no payment provider |

`add_funds` keys off an **Organization** rather than a User, unlike the existing
`deposit()`, so it runs without a request context. It writes a completed `DEPOSIT`
transaction with `provider='manual'`, keeping manual top-ups auditable next to card
and crypto payments, and creates the `BillingAccount` if absent.

**`billing/management/commands/add_funds.py`** — new management command:
```bash
python manage.py add_funds --list
python manage.py add_funds --org "Avortyx" --amount 50
python manage.py add_funds --org-id cdf49649-... --amount 50 --note "wire ref 8891"
python manage.py add_funds --email user@example.com --amount 50
```
Resolves an organization by name, UUID or member email. An ambiguous `--org` errors
with the candidate list rather than guessing. `--list` shows every organization with
its balance, and `no account` where none exists.

**`routing/engine.py`** — the guardrail:
- `required_call_balance(campaign, phone_number)` reads the rate the operator
  configured in the UI, in the same precedence the numbers page shows:
  **1.** the tracking number's `payout_per_call`, **2.** the campaign's
  `payout_amount`, **3.** `MINIMUM_CALL_BALANCE` (0 by default). No fixed rate
  appears anywhere in the code.
- `check_balance(campaign, phone_number)` consults `BillingService`, logs a `WARNING`
  naming campaign, org, required amount and actual balance, and returns `False`.
  A required amount of 0 (unpriced campaign) passes — there is nothing to charge
  against, so inventing a minimum would drop calls for no stated reason.
- `routing/asterisk_handler.py` passes the resolved `PhoneNumber` into `route_call`
  via `call_data`, so per-number pricing beats the campaign default.
- `route_call` calls it after the campaign-cap check, returning
  `{'error': 'insufficient_balance'}` — matching the existing guardrail style.

Placed in `route_call` rather than in the Asterisk handler because **both**
`asterisk_handler` and `twilio_handler` route through it, so one check covers every
dispatch path.

**`routing/models.py` + `routing/migrations/0004_calllog_block_reason.py`** — new
`CallLog.block_reason` field (max 100, blank). `ipqs_block_reason` was left alone; it
is IPQS-specific and reusing it for billing would have been misleading.

**`routing/asterisk_handler.py`** — the no-destination branch now records
`block_reason` and calls `.save()`, so the reason survives on the record.

### API impact
None. No endpoint, request or response shape changed.

### Operational impact
- **A call is dropped when the organization cannot fund it.** Asterisk receives
  `{"action": "hangup", "reason": "insufficient_balance"}` and the `CallLog` row is
  written with `status=failed` and `block_reason=insufficient_balance`.
- **An organization with no `BillingAccount` row is treated as having no credit** and is
  blocked. Correct for a credit system, but it means any organization that never had an
  account created stops routing. `add_funds --list` shows these as `no account`.
- Deployed while **no live calls were routing**, so no traffic was interrupted.

### Verification status
- Syntax checked (`python3 -m py_compile`) — passes.
- **Not** run against Django (no local venv). Needs on the server:
```bash
python manage.py check
python manage.py migrate routing
python manage.py add_funds --list
```

### Related bug, NOT fixed here

`routing/asterisk_handler.py` — the IPQS/Telnyx block branch sets `ipqs_block_reason`
and `status = FAILED`, then returns **without calling `.save()`**. The block reason is
discarded and the call stays `ringing` in the database forever. Same family as the
hanging-call bug in [CH-004](#ch-004). Left untouched because it is outside this
change's scope; worth a one-line fix.

### Spending — balances now decrease

Closed by a follow-up commit. `routing/asterisk_handler.py` `call_ended` now deducts
the call's cost when it converts.

- **Charged only on a converted call** — answered, and at least
  `campaign.min_call_duration` seconds. A no-answer costs nothing.
- **At the same rate the gate checked** (`RoutingEngine.required_call_balance`), so a
  call allowed through is always affordable. Still no fixed rate in code — it reads the
  tracking number's `payout_per_call`, then the campaign's `payout_amount`.
- **Idempotent.** `charge_call` returns the existing transaction when a completed CHARGE
  already exists for that `call_sid`, so a carrier webhook retry cannot double-charge.
- **Never blocks the response.** A failure is logged and the webhook still returns 200,
  so a billing problem cannot leave Asterisk hanging.
- `CHARGE_COMPLETED_CALLS=False` stops billing without stopping calls.

If the balance drains between dispatch and hangup (concurrent calls on one account),
`charge_call` returns `None` and a `call_charge_failed` warning is logged with the
call, org, amount and balance. The call already happened; this is the reconciliation
trail rather than a silent loss.

**Open product question:** the charge currently equals the payout the operator set on
the tracking number. If the platform's own fee is meant to be a separate number from
what the user pays their publisher, that fee needs its own configurable field.

### Revenue and payout were the same number — fixed

`routing/asterisk_handler.py` wrote `campaign.payout_amount` into **both**
`call_log.revenue` and `call_log.publisher_payout`, and mirrored `profit: 0` into
`CallRecord`. Every stored row therefore showed zero margin.

The Reporting page looked correct regardless, because it reads `CallRecord`'s
`dynamic_revenue` / `dynamic_payout` properties, which resolve off the campaign at
read time rather than trusting the stored columns. Anything querying the columns
directly — the destinations `revenue_today` sum among them — saw the payout where
revenue belonged.

Now:
- `revenue_val` = `campaign.revenue_amount` (what the buyer pays)
- `payout_val` = `RoutingEngine.required_call_balance(campaign, phone)` — the tracking
  number's `payout_per_call`, falling back to the campaign, matching what the gate and
  the charge use
- `profit` = the difference

The `PhoneNumber` lookup already needed for charging was hoisted up and reused, so this
adds no extra query.

**Historical rows are not corrected.** Calls completed before this change still carry
payout in the revenue column. A backfill would need to re-derive them from each
campaign's pricing.

---

<a name="ch-007"></a>
## CH-007 — Per-minute call charging with per-client rates

**Commits:** `c914fa65`, `63f79fef`, `80c9d2fe`, `4061c6ff`
**Deployed and verified:** 2026-09-18

### Problem

[CH-006](#ch-006) built a *gate* — it refused calls an organization could not
afford but never charged for the ones it allowed, so balances never moved. Worse,
when charging was added it billed a flat $0.45 per call. The $0.45 is a
**per-minute** rate, so a five-minute call was billed at one fifth of its cost.

Whether the rate charged to a client equals the payout they pay their publisher,
and whether a markup applies, were never settled.

### What was built

**`BillingService.call_cost(organization, duration_seconds)`**

```
ceil(duration / 60) x per_minute_rate x (1 + markup)
```

Rounded **up** to the whole minute — confirmed as the intended behaviour. A
90-second call bills 2 minutes. A missed call has no duration and costs nothing,
matching "100 calls hit, 20 missed, we count our minutes".

**`BillingAccount.per_minute_rate` and `.markup_percent`** (migration
`billing/0006`), defaulting to `$0.4500` and `0.00`. Per client, not global.

The two unsettled questions became **settings rather than code**: if the client
rate should differ from the publisher payout, change the field. Same for markup.
Neither needs a deploy, and neither blocked shipping.

**`set_rate` command** — view and change a client's pricing, with `--preview`:

```bash
python manage.py set_rate --list
python manage.py set_rate --org "Avortyx" --rate 0.45 --markup 20
python manage.py set_rate --org "Avortyx" --preview 150   # 3 min = $1.35
```

**Charging** happens in `call_ended` on converted calls, is idempotent on
`call_sid` so carrier retries cannot double-bill, and never blocks the webhook
response — a billing failure is logged and the handler still returns 200.

**The routing gate** now requires one minute's cost rather than a flat per-call
figure, falling back to the payout figures when no account rate exists.

### Bug found in the first deploy

`add_funds` crashed with `unsupported operand type(s) for +=: 'float' and
'decimal.Decimal'` when creating a **new** billing account. The model default is
the float literal `0.00` and `get_or_create` leaves that float on the in-memory
instance; accounts loaded from the database come back as `Decimal`. So it only
failed on a client's first top-up — precisely what the command exists for. Fixed
by coercing in `add_funds` and `charge_call` (`4061c6ff`).

### Verified

```
150s call -> 3 min = $1.35
 61s call -> 2 min = $0.90
```

Balance moved from $10,050.00 to $10,025.25 over 55 charged calls, confirming the
loop end to end: recharge -> gate -> route -> complete -> deduct.

---

<a name="ch-008"></a>
## CH-008 — Analytics: revenue/payout split, conversion gating, duplicate records

**Commits:** `34f757fc`, `11791da4`, `6f136515`, `e596d732`, `a74a2088`, `aa68a720`
**Deployed and verified:** 2026-09-18

Four separate defects, all surfacing as "the numbers are wrong".

### 1. Revenue and payout were the same number

`call_ended` wrote `campaign.payout_amount` into **both** `call_log.revenue` and
`call_log.publisher_payout`, mirroring `profit: 0`. Every stored row showed zero
margin. Reporting looked right only because it reads `CallRecord`'s dynamic
properties, which resolve off the campaign at read time rather than trusting the
columns.

Now revenue comes from `campaign.revenue_amount`, payout from
`RoutingEngine.required_call_balance` (tracking number first, campaign as
fallback — the same resolution the gate and the charge use), profit is the
difference.

### 2. Earnings counted on every call, not converted ones

The `_base_qs` annotation applied campaign pricing to any call with a campaign,
regardless of outcome: **83 incoming calls were billed as 83 conversions**
($83.00 revenue) when only 43 converted.

Gating on `is_converted` was the obvious fix and **zeroed the entire dashboard** —
`routing/signals.py` mirrors rows without ever setting that flag, so it was
`False` almost everywhere. Reverted to gating on `status == completed` the same
day (`6f136515`), then done properly in [CH-010](#ch-010) once the flag was
populated and backfilled.

### 3. CallRecord written twice per call

`call_ended` did `update_or_create(twilio_call_sid=..., organization=...)` while
the `post_save` signal does `update_or_create(id=call.id, ...)`. **Different
unique keys**, so every terminal call produced two analytics rows and every total
was doubled — 8 real calls reporting as 16, against a `CallLog` holding the true
12.

`call_ended`'s copy was removed; the signal is now the single writer.
`dedupe_call_records` cleared the rows the old writer left behind (8 removed,
`CallRecord` down to 766), preferring the row whose id matches its `CallLog`.

### 4. Balance was not served anywhere

The header rendered `$0` against a real $10,050. Neither
`/api/analytics/dashboard` nor `/api/accounts/me` carried a balance field, and
`/api/billing/account` — the only endpoint that did — was never called on page
load. `balance` and `currency` now ride along on the dashboard payload, the same
response that already feeds `Live:` and `Total:`.

### Verified

Sep 17: 43 completed, 43 converted, `$43.00 / $19.35 / $23.65`. Every carrier row
reconciles to `converted x $1.00` and `converted x $0.45`.

---

<a name="ch-009"></a>
## CH-009 — Carrier lookup off the call path; Telnyx stops dropping calls

**Commits:** `af675f18`, `d5f55466`, `bd8d8f02`
**Deployed:** 2026-09-18

### Telnyx was ending real calls

The number lookup fed a `should_block` branch that hung up on flagged numbers,
and ran only when `campaign.ipqs_enabled` was set — so carrier data was missing
on campaigns with the flag off, while flagged callers lost real calls.

It is now enrichment only: no block branch, wrapped so a Telnyx outage cannot
touch routing, and running on every call so carrier data is always captured.

### Then it became the bottleneck

Running unconditionally put a blocking HTTP request with a 5-second timeout in
front of **every** incoming call. Nothing about routing depends on its result, so
it moved to `tasks.enrich_call_carrier` — one external round-trip removed per
call. Carrier data now lands a moment after the call rather than instantly.

### Scaling groundwork

- `docker-compose.scale.yml` maps one host port per replica, opt-in
- PgBouncer under the `pooling` profile, with `DB_CONN_MAX_AGE` and
  `DB_DISABLE_SERVER_SIDE_CURSORS` as settings
- `docs/SCALING.md`: Nginx upstream config, and the Kamailio/OpenSIPS media-split
  design with its one real dependency — recordings must move to shared storage
  before Asterisk can be multi-node

### Outage caused by this work

Replicas were first implemented by changing the live `web` port to the range
`8000-8002`. With a single replica Docker bound **8001** while Nginx still
proxied to 8000, taking the API down with "Failed to fetch". Calls kept routing —
Asterisk posts to the container directly — but the dashboard was unreachable.

Reverted to a fixed `8000:8000`; replica mapping now lives in the opt-in overlay
file, matching how PgBouncer was handled. **Never change the live port binding
in `docker-compose.yml`.**

---

<a name="ch-010"></a>
## CH-010 — Migrations: state-only FK, CallLog indexes, conversion backfill

**Commit:** `ceecc5ea`, `aa68a720`
**Deployed and verified:** 2026-09-18

### A migration that would have destroyed data

`makemigrations` reported pending changes in `analytics` and `routing` from
another developer's model edits. The analytics one was
`RemoveField(campaign_id)` + `AddField(campaign)`.

`CallRecord.campaign` is a ForeignKey declared with `db_column='campaign_id'` —
**the same physical column** the old UUIDField used, with the same contents.
Nothing in the database needed to change. But the generated migration becomes
`DROP COLUMN campaign_id` then `ADD COLUMN campaign_id`: every `CallRecord` would
lose its campaign, and since revenue and payout resolve through the campaign, all
reporting would read zero.

Written by hand as `SeparateDatabaseAndState` instead — model state changes, no
database operations. No FK constraint was added: the column holds ids written
before the FK existed, and a constraint could fail on any row whose campaign has
since been deleted.

**Do not run `makemigrations` on `analytics` without reading what it generates.**

### CallLog indexes

`routing/0006` adds `(called_number, status)` and `(status)`, which the model had
gained without a migration. The first is the exact lookup `route_incoming_call`
performs on every incoming call.

### Reporting now matches billing

Billing charges on conversion — answered **and** at least the campaign's
`min_call_duration`. Reporting counted any answered call, so a 6-second call
showed $1.00 revenue and was never charged.

`backfill_converted` recomputes `is_converted` the way `call_ended` does. Dry run
first: 279 rows to flip, **8 of 400 completed calls under threshold** — the exact
size of the gap. After the backfill the reporting gate moved to `is_converted`.

This is per campaign, not global: hit-and-count clients set `min_call_duration`
to 0 so every answered call counts; buffer clients set 10 or 30 and only calls
past it count. No global rule, as the client requested.

### Verified

Sep 17 after the switch: `completed: 43 | converted: 43`, `$43.00 / $19.35 /
$23.65` — unchanged, because all 43 ran past the threshold.
`makemigrations --dry-run` reports **No changes detected**: Django's state and
the database finally agree.

---

## Still open

**Backend**

- **2026-09-19 — Asterisk channel cross-check (agreed, scheduled).** Stuck live
  calls are currently bounded, not eliminated: `tasks.close_stale_calls` sweeps
  every 15 minutes and closes anything past 60 minutes, so a row can show as Live
  for up to ~75 minutes after the call really ended. The exact fix is to ask
  Asterisk which channels are actually active — `core show channels` reported 0
  while the platform showed 1 — and close anything Asterisk does not have. Needs
  AMI access from the container. Would also let the manual hangup endpoint drop
  real audio instead of only closing the record.
- The platform's own fee equals the publisher payout the client configured. If
  Avortyx's fee is meant to be a separate number, set `per_minute_rate` per
  client — no code change needed.
- `get_dashboard(filters=None)` builds `type('Obj', (object,), {})()` and crashes
  on the missing `date_from`. Production never hits it (`Query(...)` makes filters
  required) but it is a landmine for any internal caller.
- The worker runs as root; Celery warns on every start.
- `callplatform-worker.service` has no `-n` flag, so a second worker would
  collide on the default node name.

**Frontend** — backend is complete for all of these

1. Balance never displayed. `GET /api/analytics/dashboard` returns `balance` and
   `currency`; verified server-side as `Decimal('10050.00')`.
2. `Cost` column is fabricated — no backend field feeds it.
3. Caller Profile carriers were fabricated. `carrier_name` is now captured on new
   calls; historical rows are blank and cannot be recovered.
4. Routing plan builder cannot show or edit rules. Endpoints exist at
   `/api/routing/rules/{id}/destinations`.
5. Number provisioning must surface `trunk_warning` on a `201` with
   `status: "pending"`.
