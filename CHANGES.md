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
| [CH-011](#ch-011) | 2026-09-18 | Billing | TFN provisioning fee, monthly portal fee | Done — commit `6bcd6689` |
| [CH-012](#ch-012) | 2026-09-18 | Routing | Stale-call cleanup, Asterisk channel sync, manual hangup | Done — commit `3ba24887` |
| [CH-013](#ch-013) | 2026-09-19 | Analytics | Duplicate detection, qualified fix, summary columns | Done — commit `0a651d74` |
| [CH-014](#ch-014) | 2026-09-19 | Analytics | Carrier normalisation and breakdown endpoint | Done — commit `2018a1a7` |
| [CH-015](#ch-015) | 2026-09-18 | Accounts | Telegram account linking | Done — commit `6f78e1e8` |
| [CH-016](#ch-016) | 2026-09-20 | Notifications | Per-user pop-up alert preferences | Done — commit `ba858308` |
| [CH-017](#ch-017) | 2026-09-21 | Accounts | Invitation and reset email actually sent | Done — commit `6c11aff5` |
| [CH-018](#ch-018) | 2026-09-21 | Config | Domains and sender addresses made configurable | Done — commit `3d907f16` |
| [CH-019](#ch-019) | 2026-09-21 | **Security** | **Open registration closed, access requests locked to staff** | Done — commit `ea3a7fdd` |
| [CH-020](#ch-020) | 2026-09-21 | **Security** | Rate limiting made effective, secret-key guard, invite validation | Done — commit `ee74f76a` |
| [CH-021](#ch-021) | 2026-09-21 | Analytics | Per-call detail view and routing decision trace | Done — commit `50162d79` |
| [CH-022](#ch-022) | 2026-09-22 | Notifications | Alert detection, rule validation | Done — commit `7c67d112` |
| [CH-023](#ch-023) | 2026-09-22 | Analytics | Qualified, Dupe and Paid redefined; export columns | Done — commit `583f8ea4` |
| [CH-024](#ch-024) | 2026-09-25 | **Security** | **Roles enforced: capability guards + row scoping** | Done — commit `517bf6c6` |
| [CH-025](#ch-025) | 2026-09-25 | **Security** | Superuser never scoped; roles endpoint derived from the real table | Done — commit `cedf45da` |
| [CH-026](#ch-026) | 2026-09-25 | Reliability | 14 swallowed failures logged; notification rules self-seed; scratch files removed | Done — commit `c8c457aa` |
| [CH-027](#ch-027) | 2026-09-25 | **Security** | API-wide rate limiting; RTB bid scoping; error responses no longer leak internals | Done — commit `1f8e7b91` |
| [CH-028](#ch-028) | 2026-09-25 | Billing / Analytics | Cost calculated in the backend for the first time | Done — commit `7a2668c9` |
| [CH-006](#ch-006) | 2026-09-17 | Analytics | Dynamic Dashboard Pricing & PhoneNumber Formatting | Done |

## Open items (not done yet)

Kept current. Anything not on this list is either done and has a CH entry, or
was investigated and closed as not a fault — also with a CH entry. Last
reviewed **2026-10-01**.

**Closed on 1 October:** one live destination per buyer (CH-052, fixed from the data side with no change under `routing/`), partner permissions and reporting visibility (CH-051,
20 checks pass, export verified 403 off / 200 on), duplicate TFNs, duplicate and
junk buyers, empty `Buyer.phone_number`, blank `renews_at` (all CH-050).

### Blocked on the boss — I cannot close these

| ID | Area | Summary | Needed |
|----|------|---------|--------|
| OPEN-10 | Billing | Crypto payment not completing | The boss reported it 30 Sep. First thing on 1 Oct |
| OPEN-11 | Spam / DNC | DNC scrubbing and the TCPA shield have no data behind them | 25,000 lookup credits, $50. Deferred to 1 Oct because payment is broken |
| OPEN-12 | Billing | Capitalist payments never confirm automatically | Callback URL set to `https://avortyx.io/api/billing/capitalist-webhook` |
| OPEN-13 | Billing | CoinGate is on the payment screen with no key | The API key, or a decision to remove it from the UI |

### A decision, not a bug

| ID | Area | Summary | Detail |
|----|------|---------|--------|
| OPEN-14 | Campaigns | The campaign cap turned away **133 calls** — 79 on 28 Sep, 54 on 23 Sep | Configured behaviour. Either the cap is below what the publisher sends or the traffic is above what the buyer agreed to take. Nobody chose to refuse it; a setting did. Needs the boss, and it is worth naming which campaign |

### Mine, in the order I would take them

| ID | Area | Summary | Priority |
|----|------|---------|----------|
| ~~OPEN-15~~ | Buyers | Duplicate TFNs across buyers — `+18553752923` is on Q08, R48 and CRM, and seven more numbers are on two buyers each. Calls are attributed by `destination_number`, so one call to a shared TFN counts for every buyer holding it | Resolved 2026-10-01 — CH-050 |
| ~~OPEN-16~~ | Routing | `routing/asterisk_handler.py` resolves the live destination with `order_by('-created_at').first()` — a buyer with two enabled destinations had every call sent to one, the other silently dead | Resolved 2026-10-01 — CH-052, from the data side. A buyer can now only have one live destination, so that lookup is correct by construction. No file under `routing/` changed |
| OPEN-29 | Server | Ubuntu reports *"System restart required"*, 65 updates pending including one security update | **Needs a maintenance window.** A reboot drops every call in progress and takes Asterisk, nginx and the containers down for a few minutes. Not to be done ad hoc |
| ~~OPEN-17~~ | Buyers | `Buyer.phone_number` is empty on all 42 buyers, and `routing/engine.py` uses it as the destination for an RTB call. All five campaigns are `priority`, so it never runs today | Resolved 2026-10-01 — CH-050 |
| ~~OPEN-18~~ | Buyers | Junk in the buyer table: `xczxczxcxz`, a second `RNY` with no destinations, `Q16` with no destinations | Resolved 2026-10-01 — CH-050 |
| ~~OPEN-19~~ | Phone Numbers | `renews_at` is written only from what the request sends and **nothing reads it**, so the Renews column can only ever be blank | Resolved 2026-10-01 — CH-050 |
| ~~OPEN-4~~ | Repo | ~~`phone_numbers/services.py.bak_trunk` committed by mistake~~ | Resolved — file removed |

### Frontend — his developer, not me

| ID | Summary |
|----|---------|
| OPEN-20 | Caller Identity double-counts, 48 against a real 24, from an "Unknown" row |
| OPEN-21 | `net` and `grossProfit` columns the backend does not send |
| OPEN-22 | Raw translation keys showing, e.g. `toolsUI.reports.summary.columns.net` |
| OPEN-23 | Still not fully moved to `/api/analytics/snapshot` |
| OPEN-24 | Token-refresh race causing intermittent 401s |
| OPEN-25 | Buyer payout column reads `Buyer.payout_amount`, which is genuinely zero. The payout lives on the campaign |
| OPEN-26 | Publisher/buyer settings page: read the toggle list from `GET /api/accounts/partner-permissions` instead of hardcoding it, and save with `PATCH /api/accounts/{buyer\|publisher}/{id}/settings`. The backend now exists (CH-051) |
| OPEN-27 | Render controls from `capabilities` and `visible_report_columns` on `/api/accounts/me`, not from the role name — that is why controls appear and then return 403 |
| OPEN-28 | The publisher settings page renders a publisher id that is not in the database. `cdf49649-c655-431c-a9fc-cecf24da81a4` is not a publisher, buyer or user; the real one is `b64f7363-949a-482c-8b4a-5add8f3df355`. Every call that page makes with it will 404 |

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

<a name="ch-020"></a>
## CH-020 — Rate limiting made effective, secret-key guard, invite validation

**Commits:** `8c39e6d9`, `ee74f76a` · Deployed 2026-09-21

**Rate limiting existed but barely applied.** No `CACHES` was configured, so Django
fell back to per-process local memory. `django_ratelimit` counts attempts in the
cache, so every worker kept its own count and every restart cleared it. Now backed
by the Redis already running for Celery.

**The default `SECRET_KEY` is committed to this repository**, and `SIMPLE_JWT` signs
tokens with it — anyone holding the code could mint a valid token for any account
if the environment variable went missing. Startup now fails with `DEBUG=False`
rather than running on it. Production was confirmed already using its own key.

**Three auth endpoints had no limit.** `verify-mfa` had none, and a six-digit code is
trivially brute-forced. Password reset request (mailbox flooding) and confirm
(token guessing) had none either.

**Workspace invite accepted any text as an email.** `x Koreaavortyx@mailnesia.com` —
an address with a space in it — was stored and shown as an active member while
the invitation went nowhere. Now validated, trimmed and lowercased.

`scripts/security_check.py` reports the settings actually in force. 10/10 passing
on production; one account holds platform staff.

---

<a name="ch-021"></a>
## CH-021 — Per-call detail view and routing decision trace

**Commits:** `f7317982`, `d6c3bf9c`, `50162d79` · Deployed 2026-09-21

`GET /api/analytics/calls/{id}/detail` returns everything known about one call:
caller profile, routing, financials, recording and a timeline. Built from
`CallLog` rather than the analytics mirror, since the mirror carries only terminal
calls and drops routing detail.

**The first live response exposed four gaps, all fixed:**

- `fraud_score` showed 0 on every call. Telnyx does not supply it — its wrapper sets
  0 meaning "not provided" — so a clean-looking score was shown for a value nobody
  measured. Now null.
- `area_code`, `region` and `country` were always null. Only the Twilio path set
  them, and all live traffic arrives through Asterisk.
- `rule_id` and `rule_name` were always null. `route_call` returns the rule it
  chose and the handler discarded it.
- The timeline rendered out of order: `answered_at` is derived as hangup minus
  duration and can land before `created_at`.

**Routing decision trace.** `RoutingEngine` decided where a call went and discarded
its reasoning. `RouteTrace` now records every guardrail outcome, every destination
considered with why it was rejected, and the one chosen, stored on
`CallLog.routing_trace`. It is passive — it records and never influences a
decision. The summary separates `evaluated` from `not_reached`, since routing stops
at the first success and destinations after the winner were never examined.

Fields the lookup provider does not supply — city, zip, timezone, fraud score —
are null by design and need a different provider.

Also fixed a doubled `@staticmethod` left by an earlier edit, which ran on Python
3.12 and would break on anything older.

The billing account now returns `per_minute_rate`, `markup_percent`,
`tfn_purchase_fee`, `monthly_portal_fee` and `portal_fee_next_due`. The $49.99
monthly fee was verified charging correctly on production.

---

<a name="ch-022"></a>
## CH-022 — Alert detection and notification rule validation

**Commits:** `c6b3d8ca`, `7c67d112` · Deployed 2026-09-22

The alert types could be switched on in settings but nothing ever looked for
them. `notifications/detectors.py` now watches for campaign, buyer and destination
caps, buyers missing calls, and handle time dropping, every five minutes.

Cap alerts fire at **80%** as well as at the limit, so there is warning before calls
start being refused. Each alert is silent for 24 hours after firing. The
behavioural ones need evidence first: a buyer needs 3 calls in the window before a
miss rate counts, and handle time is compared against the campaign's own previous
seven days. Verified on production — ADC11 at 155/154 fired `buyer.cap_reached`.

**Notification rules accepted values that do not exist.** The two rules on
production used event `webhook.failing` and channel `in_app`; neither is defined.
Dispatch had no branch for an unknown channel, so the rules looked configured and
delivered nothing. Create and update now reject undefined values, and dispatch
logs a warning instead of skipping silently.

**Delivery needs rules.** Detection works, but no rule exists for any alert type, so
alerts currently reach nobody. Rules are created in the Notifications screen.

---

<a name="ch-023"></a>
## CH-023 — Qualified, Dupe and Paid redefined; export columns

**Commits:** `3b6e6ae2`, `d82857da`, `583f8ea4` · Deployed 2026-09-22

The reference platform showed Connected 104, Qualified 81, Paid 78, Converted 100,
Dupe 23 — four different figures — where this platform showed one figure in four
columns. They were all the same calculation.

**Definitions, as given and as implemented:**

| Column | Means |
|---|---|
| Connected | every answered call, repeats included |
| Dupe | answered, from a caller who rang before |
| Qualified | answered, from a new caller — Connected minus Dupe |
| Converted | answered and past the campaign minimum duration |
| Paid | every converted call, repeats included |

The reference figures confirm Qualified: 104 connected less 81 qualified is exactly
the 23 dupes shown.

**Paid was confirmed, not guessed.** An inferred rule — paying only a new caller's
first converted call — fitted the reference 78 exactly. Asked directly before
changing money logic: both calls are payable. So Paid equals Converted, and billing
was already correct. The reference 78 must come from a filter that platform applies
and this one does not.

**Repeats look back across days.** The first version counted a repeat only within
the day viewed, giving 6 against a reference 23. Now uses the `is_duplicate` flag
stored at arrival, which looks back across the campaign's
`duplicate_call_block_hours`. That window is per campaign and adjustable without a
code change.

For the 21st: **103 connected, 89 qualified, 14 dupe.** The remaining gap is data,
not definition — counting every call ever made, this platform holds at most 18
repeat callers for that day, so the reference platform has calls this one does not.

The per-call `is_qualified` flag carries the same definition, recomputed on existing
records by data migration `analytics/0008`, so the Qualified drill-down lists
exactly the calls the column counts.

**Export.** The backend export gained Qualified, Duplicate, Carrier and the call id.
The export the operators actually download is built by the frontend and does not
use it — see Still open.

**The Tag column is invented.** `CallLog.tags` is never written by anything, so the
Qualified, Repeat, VIP and High intent values in the frontend export are fabricated
client-side. Counting Qualified from that column cannot match anything.

---

## Still open

**Backend**

- ~~**2026-09-19 — Asterisk channel cross-check.**~~ **Resolved in CH-012** —
  `scripts/asterisk_channel_sync.sh` and the `active_channels` endpoint ship, so
  a stuck live call closes in about a minute rather than 75. Original note below.

- **(historical)** Stuck live
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
- ~~`get_dashboard(filters=None)` crashes on the missing `date_from`.~~
  **Resolved in CH-028** — `_base_qs` and `_live_qs` read every filter field
  through a helper that tolerates `filters` being None. Hit while verifying cost
  from the shell, which is exactly the internal caller it was a landmine for.
- ~~The worker runs as root; Celery warns on every start.~~
- ~~`callplatform-worker.service` has no `-n` flag.~~
  Both **obsolete** — the systemd units were replaced by Docker Compose in
  CH-005. Kept struck through so they are not re-raised.

**Frontend** — backend is complete for all of these

1. Balance never displayed. `GET /api/analytics/dashboard` returns `balance` and
   `currency`; verified server-side as `Decimal('10050.00')`.
2. ~~`Cost` column is fabricated — no backend field feeds it.~~ **Resolved in
   CH-028** — the API returns `total_cost` and `billable_minutes`. The frontend
   must delete its own calculation.
3. Caller Profile carriers were fabricated. `carrier_name` is now captured on new
   calls; historical rows are blank and cannot be recovered.
4. Routing plan builder cannot show or edit rules. Endpoints exist at
   `/api/routing/rules/{id}/destinations`.
5. Number provisioning must surface `trunk_warning` on a `201` with
   `status: "pending"`.

---

<a name="ch-011"></a>
## CH-011 — TFN provisioning fee and monthly portal fee

**Commit:** `6bcd6689` · Deployed 2026-09-18

$20 per tracking number and $49.99 a month, both per-client fields on
`BillingAccount` beside the per-minute rate. A `Plan` model with `monthly_cost`
already existed but nothing imports it, so the $499 plan shown on the Billing
page drives no charge.

The TFN fee is checked for affordability **before** Twilio is called — checking
after would mean Twilio had already billed for a number the client cannot pay
for. A failure once the number exists is logged rather than raised: losing the
number over a billing error is worse than an unbilled provision the transaction
log can reconcile.

The portal fee bills on each account's own 30-day cycle rather than a calendar
date, so a mid-month signup is not billed twice and the customer base does not
land on one day. `portal_fee_charged_at` is stamped only on success, so an
account that cannot cover it is retried tomorrow rather than losing the month.

`set_rate` manages all of it, with `--preview` to check what a call of a given
length would cost.

---

<a name="ch-012"></a>
## CH-012 — Stale calls, Asterisk reconciliation, manual hangup

**Commits:** `1f3291b9`, `3ba24887`, `2d887efd` · Deployed 2026-09-18

A call sat in `in_progress` for 126 minutes while Asterisk reported **zero
active channels** — the end-of-call webhook had never fired. Stuck rows inflate
live counts and never reach the analytics mirror, since only terminal statuses
are mirrored.

`close_stale_calls` sweeps every 15 minutes and closes anything past
`STALE_CALL_MINUTES` as `no_answer` with zero duration, so it is never charged
and never counts as revenue. That bounds the problem at roughly 75 minutes but
cannot eliminate it, because age is all it knows.

`POST /api/twilio/asterisk/active-channels/` makes Asterisk the authority. A
host cron posts its channel list every minute and rows Asterisk does not have
are closed. Three guards, since closing a real call is far worse than leaving a
stale row: rows under `ASTERISK_SYNC_GRACE_SECONDS` are never touched, a zero
count closes everything past that grace, and when Asterisk does report channels
the ids are trusted **only** if at least one matches a live row — otherwise the
id format differs from what the dialplan sends and nothing is closed. The host
script exits without posting when the Asterisk CLI is unreachable, so an outage
cannot be read as "no calls are up".

Orphaned rows now clear in under 60 seconds.

`POST /api/routing/calls/{id}/hangup` closes a call showing as live. It does
**not** drop audio — Asterisk owns the channel and there is no AMI connection —
and the response says so.

**Root cause still open:** the dialplan does not reliably reach `call_ended`.
This treats the symptom.

---

<a name="ch-013"></a>
## CH-013 — Duplicate detection, qualified, and the summary columns

**Commits:** `0f911731`, `2f2736f5`, `7a29c3a7`, `ef40b06b`, `0a651d74` · 2026-09-19

**DUPE always read 0.** `is_duplicate` was never written by anything —
`analytics/services.py` hardcoded it to `False` and no other path set it.
`RoutingEngine.is_duplicate` existed but only to decide whether to *block* a
call; the result was discarded. `CallLog.is_duplicate` now records it on arrival,
detection running regardless of `duplicate_call_block` — that flag governs
blocking, not reporting.

**Qualified read 81 against 87 connected.** `is_qualified` is written by two
one-off scripts and the Twilio path, but not by the signal that mirrors live
Asterisk calls, so it stayed `False` on everything written since. The signal now
sets both flags from one helper so they cannot drift.

**Five columns were invented client-side.** The campaigns endpoint never
returned Connected, Not Connected, Paid, Live or duplicate counts, so no
server-side fix could move them. All three breakdowns now return them, connected
and not-connected as complements summing to `total_calls`.

`AnalyticsFilterSchema` gained the boolean filters, so
`/api/analytics/calls?is_qualified=true` works — it was silently ignored,
returning the full list, which is why a drill-down disagreed with its header.

---

<a name="ch-014"></a>
## CH-014 — Carrier normalisation

**Commit:** `2018a1a7` · Deployed 2026-09-19

Telnyx returns the operating entity, not a brand: one network arrived as
`Verizon Wireless:6006 - SVR/2`, `CELLCO PARTNERSHIP DBA VERIZON WIRELESS - OH`
and a dozen more — 33 distinct strings across 150 calls. The Caller Profile tab
was showing six invented carrier names instead, including Sprint and US Cellular,
which do not appear in the data at all.

`routing/carriers.py` maps the raw string to a family, matching MVNOs before
their host network so Metro is not swallowed by T-Mobile nor Cricket by AT&T,
and covering acquired entities still present in LRN data — Cingular and the Bell
operating companies as AT&T, Omnipoint, Powertel, Aerial and SunCom as T-Mobile,
Cellco Partnership as Verizon, Eliska as Cricket. An unrecognised carrier keeps
its own name rather than collapsing into Unknown.

Real spread: Verizon 59, AT&T 33, T-Mobile 28, Metro 11, Boost 2, Cricket 1,
Onvoy 1. `GET /api/analytics/carriers` serves the breakdown.

---

<a name="ch-015"></a>
## CH-015 — Telegram account linking

**Commit:** `6f78e1e8` · Deployed 2026-09-18

Four profile endpoints. `POST /api/accounts/me/telegram/link` issues a
single-use code and returns `{url, code, expires_at}`; opening the link sends the
bot `/start <code>`, which is the only point at which the chat id becomes
knowable. Codes use `secrets.token_urlsafe(16)` — inside Telegram's start-payload
character set and well under its 64-character limit — expire after 15 minutes,
and requesting a new link retires any outstanding one.

The webhook grew a `/start` branch beside the existing support-reply handling
and still answers 200 on every path, since a non-200 makes Telegram retry the
same update indefinitely.

---

<a name="ch-016"></a>
## CH-016 — Pop-up alert preferences

**Commit:** `ba858308` · 2026-09-20

Per-user control over which alerts surface as pop-ups, deliberately separate from
`NotificationRule`: a rule decides whether an event is dispatched and to whom, this
decides only whether it interrupts the person looking at the dashboard. One
person wanting cap alerts on top should not change what anyone else receives.

`GET /api/notifications/events` serves the catalogue rather than the frontend
hardcoding it, so a new alert type appears in settings automatically. Adds
`destination.cap_reached`, `buyer.missed` and `aht.low`.

**Open:** those three have preference storage and dispatch plumbing but no
detection logic firing them.

---

<a name="ch-017"></a>
## CH-017 — Invitation and password reset email

**Commit:** `6c11aff5` · Deployed 2026-09-21

**Neither flow ever sent anything.** The invite endpoint created the user,
generated a temporary password, returned it in the API response and stopped — so
an invited member was never contacted, which is why a freshly invited address
showed an empty inbox while the member read as Active. The reset flow was worse:
a `TODO: send email` beside a `print()` pointing at `app.callplatform.com`, a
domain that is not ours, so every reset link ever produced was dead.

`accounts/emails.py` centralises both, returning `(sent, error)` rather than
raising so a caller can report delivery instead of failing the request over SMTP.
The invitation carries a set-password link backed by the existing
`PasswordResetToken` rather than the password itself.

`test_email` prints the resolved SMTP configuration and sends a message, so
delivery can be proven independently of the invite flow.

**Sender:** the SMTP account authenticates as a specific mailbox, and the host
rejects sending as another domain — `553 Sender address rejected: not owned by
user`. Changing the visible sender needs that mailbox to exist on the mail host,
not a code change.

---

<a name="ch-018"></a>
## CH-018 — Domains and sender addresses made configurable

**Commits:** `0bbc82eb`, `3d907f16` · Deployed 2026-09-21

The domain was hardcoded in **thirteen places across eight files**, and
inconsistently — referral redirects and buyer invites pointed at one domain,
avatars and recordings at another. The sender address was hardcoded in nine more
places across four files, with notifications falling back to a placeholder.

`FRONTEND_URL`, `PUBLIC_SITE_URL` and `MEDIA_BASE_URL` now cover the domains,
kept separate so the client portal can move to a disposable domain without
touching the marketing site. `PLATFORM_FROM_EMAIL` and `PLATFORM_SUPPORT_EMAIL`
cover the sender. `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` read from env
— a portal on a new domain needs its origin in both or the browser blocks every
request before it reaches a view, which fails looking exactly like a backend
outage.

`contact_api` imported `settings` inside a function below three module-level uses
of it, which would have raised `NameError` on the first contact-form submission.

---

<a name="ch-019"></a>
## CH-019 — Security audit: registration, access requests, tenant scoping

**Commits:** `1e3c3ea0`, `e40fcf00`, `ea3a7fdd`, `3139f17f` · Deployed 2026-09-21

A full pass over 256 endpoints found one chain open to anyone on the internet.

**`POST /api/accounts/register` was unauthenticated** and created an
Organization plus a user with `role=ADMIN`. **No endpoint in the codebase checks
a role** — five roles exist and the only mention of permissions anywhere is the
Django admin config. So a stranger could self-register and immediately reach
endpoints acting across every organization:

- `GET /api/accounts/access-requests/` — every prospect's name, company, email,
  phone and use case, platform-wide. Not organization-scoped.
- `POST .../approve/` — create arbitrary organizations and users.
- `DELETE /api/buyers/{id}/campaigns/{id}` — delete another organization's
  assignment, no organization filter.

The access-request flow exists precisely to gate signup; open registration
bypassed it entirely.

**Closed.** Registration is off unless `OPEN_REGISTRATION=True`. The
platform-wide endpoints now require `StaffAuth` — Django superuser or staff.
Organization admin is deliberately not enough, since the first user of any
organization is an admin of it. Submitting a request and setting a password stay
public, as they must. `detach_campaign` is scoped to the caller's organization.

Two accounts held superuser on production — a test account and one with no
organization at all. Both revoked. `grant_staff` manages this and warns when
nobody holds it, in which case access requests cannot be approved by anyone.

**Also found:** destinations could be saved with no buyer — five were, all
active and none reachable, since routing resolves the live destination by buyer.
Worse, `DestinationUpdateSchema` had no `buyer_id`, so it could not be corrected
through the API by any client. Create now requires it; update accepts it and
resolves the Buyer scoped to the caller's organization rather than assigning the
raw id, which would have stored another organization's id without complaint.

`GET /api/accounts/roles` built its list and never returned it, answering `None`
and failing response validation with a 500 — the Roles screen had never worked.

`scripts/smoke_test.py` exercises 62 read-only endpoints in one command and
reports 500s, bad auth and slow queries. **62/62 passing.**

---

## Still open

**Backend**

- ~~**2026-09-25 — texora runs out.**~~ **Resolved, and it was never a manual job.**
  `tasks.charge_portal_fees` runs daily, charges each account on its own 30-day
  cycle and retries tomorrow if the balance is short. No action needed for texora
  or anyone else. Listing it as a task was the mistake. See CH-026.
- ~~**2026-09-25 — two scratch files in the repository root.**~~ **Resolved in
  CH-026** — eight were removed, including three that rewrote source files in
  place against hardcoded `/opt/call_platform` paths.
- **Campaign pricing.** Revenue is $1.00 per converted call while the platform
  charges $0.45 per minute, so a seven-minute call earns $1.00 and costs $3.15.
  Raised and confirmed as intended, recorded here so it is not re-raised.
- ~~**Notification rules.**~~ **Resolved in CH-026** — rules are seeded per
  workspace on every alert sweep and at signup, and undeliverable rules are
  switched off automatically. No hand-building in the UI.
- ~~**Role enforcement.**~~ **Resolved in CH-024 and CH-025** — 87 capability
  guards, 12 row-scoping points, verified against production data.
- **2026-09-22 — delete `routing/twilio_handler.py` (agreed, scheduled).** 496
  lines of Twilio call handling that nothing uses: Asterisk handles inbound,
  `call_ended` handles hangups, and recordings come from Asterisk to
  avortyx.io/recordings — the Twilio `RecordingUrl` line has never run for any
  call. It still serves live public endpoints at `/api/twilio/incoming-call/`
  and `/api/twilio/call-status/`, and holds the old charging and CallRecord
  paths that caused the double-billing and duplicate-record bugs. Removing it
  also clears 8 of the 24 swallowed exceptions.
  **Blocked on one answer:** click_to_call and click_to_call_connect have no
  Asterisk equivalent. Confirm with the frontend dev whether any dial button
  exists in the UI. If not, delete the file; if so, keep those two and remove
  the rest.
  Twilio itself stays — number provisioning and SMS both use it.

- 24 swallowed exceptions (`except: pass`), 8 of them in
  `routing/twilio_handler.py` where a failure disappears without trace. This is
  the category that hid the duplicate-record and payout bugs.
- The Asterisk dialplan does not reliably reach `call_ended`. CH-012 treats the
  symptom.
- The `Plan` model and 7 other model fields are dead code.
- Buyers have empty `phone_number`. Harmless on the current routing path, but the
  RTB path returns `auction.winner.phone_number` as the destination.
- Web replicas and PgBouncer are configured and intentionally inactive.

**Frontend**

- **The Call Log export is built client-side** and invents a Tag column. Should use
  `GET /api/analytics/calls/export`, which has real Qualified, Duplicate and Carrier.
- Call detail panel and routing trace to build against
  `GET /api/analytics/calls/{id}/detail`.
- Billing page should show the per-client rates now on `/api/billing/account`.
- Connected includes live calls while Qualified and Dupe do not, so Qualified +
  Dupe = Connected only once live calls finish.
- `Cost` column is fabricated — no backend field feeds it.
- TCL renders as `mm:ss` rather than `hh:mm:ss`. The value is correct.
- Routing plan builder cannot show or edit rules; endpoints exist.
- `trunk_warning` not surfaced on number purchase.
- Hangup button not wired to `POST /api/routing/calls/{id}/hangup`.

---

## CH-024 — Roles actually enforced (capability + row scoping)

**Problem**
The five roles (admin, manager, agent, buyer, publisher) were labels on a column
and nothing else. Every logged-in account could reach every endpoint: an agent
could delete a campaign, open billing, change another user's role. A buyer login
could read the whole organization's calls, including other buyers' numbers,
payouts and recordings.

**Two separate problems, fixed separately**

| | question | fix |
|---|---|---|
| capability | what a role may **do** | a guard on the endpoint |
| scope | what a role may **see** | a filter on the rows |

A capability guard alone is not enough for buyers and publishers: their login
sits *inside* the organization, so their data is beside everyone else's. Rows
have to be filtered, not just endpoints blocked.

**New file: `accounts/permissions.py`** — the only place roles are defined.

- `Capability` — VIEW, EDIT, CREATE, DELETE, BILLING, MEMBERS, SETTINGS
- `ROLE_CAPABILITIES` — role → set of capabilities
- `require(user, capability)` — raises 403 naming the capability *and* the role,
  so a blocked request explains itself
- `scope_queryset(user, qs, buyer_field, publisher_field)` — narrows rows

Who can do what:

| role | view | edit | create | delete | billing | members | settings |
|---|---|---|---|---|---|---|---|
| admin / reseller | Y | Y | Y | Y | Y | Y | Y |
| manager | Y | Y | Y | Y | - | - | - |
| agent | Y | Y | - | - | - | - | - |
| buyer / publisher | own rows only | - | - | - | - | - | - |

A role missing from the table gets VIEW only, so adding a role to the model is
harmless until its capabilities are granted deliberately.

**New fields: `User.buyer`, `User.publisher`** (migration `accounts/0009`)
`role='buyer'` told us the login was a buyer but not *which* buyer, so there was
nothing to filter on. Both are `SET_NULL`, `related_name='logins'`. A buyer login
with no link sees **nothing** rather than falling through to everything — the
safe direction if someone forgets to set it.

**87 capability guards** across accounts, campaigns, buyers, publishers,
phone_numbers, routing, dni, ivr, webhooks, destinations, spam_protection,
notifications. POST→CREATE, DELETE→DELETE, PATCH/PUT→EDIT, plus MEMBERS on
member management and SETTINGS on workspace settings. Billing is gated at the
router (`BillingAuth`), not per endpoint, so a new billing endpoint is covered
the day it is written.

**12 row-scoping points** — every path that can return call data:
`routing/services.py` (list + get by id), `routing/api.py` (live calls, hangup),
`analytics/services.py` (`_base_qs`, `_live_qs` — this covers the dashboard,
reports and the CSV export), `analytics/api.py` (recording, detail, live),
`routing/consumers.py` (the websocket live feed), and the buyer/publisher
listings so a buyer cannot enumerate the competition.

Fetch-by-id was scoped too, not just the lists. Blocking the list while leaving
`GET /calls/{id}` open would have meant a buyer could still read any call by
guessing or reusing an id.

**Routing untouched.** No change to `routing/engine.py`, the dialplan, the
Asterisk handlers or the webhooks. Scoping applies to reading, never to the
call path — an inbound call has no logged-in user.

**Public endpoints unchanged.** The DNI snippet endpoint, `assign_number`, the
IVR gather webhook and the conversion postback carry `auth=None` and got no
guard — a guard there would have broken live traffic. Verified by a scan that
walks each `auth=None` decorator and its body.

**Still open**
- The frontend should hide what a role cannot do. The backend now returns 403
  with a readable reason, so the UI can show the message rather than guess.
- Existing buyer/publisher logins need their `buyer`/`publisher` link set once,
  in the admin. Until linked they see no calls — deliberately.

---

## CH-025 — Two holes CH-024 left, found by looking at the live data

Enforcing roles exposed the fact that the roles on the accounts were wrong.
They had been wrong for a long time; nothing noticed because nothing read them.

**1. Every client login was sitting on `buyer`**

Five accounts — each the owner of their own workspace — carried `role='buyer'`:

| account | workspace |
|---|---|
| haansjuma@gmail.com | Avortyx |
| keoze2026@gmail.com | hans juma |
| texora613@gmail.com | texora (paying client) |
| devstarfive0812@gmail.com | Keoze |
| testuser@test.com | Test Co |

The moment capabilities went live these accounts could see nothing and create
nothing. Set to `admin`, which is what an account that owns a workspace is.

Not a registration bug — `accounts/services.py` sets `admin` on signup and the
model default is `agent`. These were changed by hand back when role was
decorative. New signups are unaffected.

The lesson: enforcement should have been preceded by a look at what the column
actually contained. Building the gate and checking the keys afterwards is the
wrong order.

**2. A superuser could be scoped**

`scope_queryset` read `role` alone, so a superuser carrying a stray role would
have had their dashboard filtered to nothing. Superusers are never scoped now —
support has to see the whole workspace.

**3. `/workspace/roles` disagreed with the guards**

The endpoint was written out by hand and had drifted: it offered a `viewer` role
the model does not have, omitted `reseller`, and listed capability names
(`call.view`, `billing.manage`) that were never the ones checked. The frontend
builds its permission screens from this, so it was showing people access they
did not have.

It now derives from `ROLE_CAPABILITIES` — the same table the guards read, so the
two cannot disagree again. Added `scoped_to_own_records` so the UI knows which
roles see only their own rows.

**Verified against production data**

| check | result |
|---|---|
| admin sees the whole workspace | 1484 of 1484 |
| buyer with no link | 0 |
| buyer linked to ADC11 | 1397, exactly ADC11's calls |
| buyer linked to Test Buyer | 29, exactly theirs |
| buyer capabilities | `['view']` |

The first pass tested against a buyer with no calls, where correct filtering and
a blanket block look identical. Re-run against the two busiest buyers, which
distinguishes them.

---

## CH-026 — Stop the system needing a person to finish its work

Three separate things, one theme: the system detected a condition, or took
money, or purchased a number, and then depended on somebody noticing.

### Portal fees were never a manual job

Listed "fund texora before the 18th" as a task. Wrong — `tasks.charge_portal_fees`
already runs daily, charges each account on its own 30-day cycle, and retries
tomorrow if the balance is short. No action needed, for texora or anyone.

### 14 silently swallowed failures

`except Exception: pass` in places where the failure mattered. Two were money:

| where | what was lost |
|---|---|
| `billing/api.py` Stripe webhook | customer pays, exception, **no credit applied**, no record |
| `routing/api.py` call charge | call completes, exception, **never billed** |

Two were routing correctness: the destination concurrency cap stopped applying
with no sign, and the live-destination lookup fell back to a stale number, so
calls went to the previous destination and looked normal.

The rest: approval emails (`fail_silently=False` and then swallowed, so the
approval looked successful while the person never got their password link),
routing-rule sync after a destination change, scheduled reports reported as sent
but never queued, support tickets saved with nobody pinged.

Phone numbers took a different fix per situation, because the cost of failing
differs:

- `purchase_number` — Twilio has already charged. Raising would lose the number,
  so it is kept and the response carries a warning that the campaign is
  unassigned.
- `import_existing_number` — number is kept, mismatch logged.
- `update_number` — nothing irreversible has happened, so it now **refuses**
  with "Campaign not found" instead of reporting success on an assignment it
  silently discarded. This is why a number could look assigned in the UI while
  routing saw no campaign at all.

Left alone: two in `call_queue` that are genuinely expected (most calls are
answered without being queued) and now say so in a comment, and eight inside
the legacy Twilio handler, which is on its way out.

### Alerts were detected and then dropped

`NotificationService.dispatch` only sends if a rule exists for the event, and
rules could only be built by hand in the UI. No workspace had any, so every
detected alert died silently.

New `notifications/defaults.py`:

- creates the six default rules for a workspace, addressed to its admins
- runs on **every alert sweep**, so existing workspaces repair themselves with
  nobody on the server, and at signup so a new workspace is covered from its
  first call
- fills in recipients only when the list is empty, so removing yourself from an
  alert stays removed
- switches off rules pointing at an undeliverable event or channel (the
  `webhook.failing` / `in_app` junk) rather than deleting them
- never modifies a rule that already exists

`dispatch` now logs when an event fires with no rule, so this cannot go quiet
again. The event set is `DEFAULT_NOTIFICATION_EVENTS` in settings, so it changes
without a deploy.

### Legacy Twilio routing made visible instead of guessed at

Numbers are attached to a SIP trunk and reach Asterisk over SIP, so the Twilio
voice webhooks are never called. The risk was never that they run — it is that
they are a **second routing implementation** with their own duplicate detection
and billing. If a number fell off the trunk, calls would route through untested
code and bill differently from every other call.

Rather than delete on an assumption, each entry point now logs
`LEGACY TWILIO PATH USED` with the caller, callee and SID.
`LEGACY_TWILIO_ROUTING` can switch it off once the logs are clean. Defaults on,
so nothing changes today.

### Scratch files removed

`fix_all.py`, `fix_services.py`, `patch_signal.py`, `sync_script.py`,
`test_dashboard.py`, `test_export.py`, `test_ids.py`,
`scripts/fix_historical_duplicates.py`.

The first three rewrote source files in place against hardcoded
`/opt/call_platform` paths — running one by accident would have overwritten
`analytics/services.py`. Kept `locustfile.py`, which is the load-test harness.

**Still open**
- Delete `routing/twilio_handler.py` once the legacy-path logs show nothing
  reaching it. `click_to_call` is the only piece whose use is unconfirmed.

---

## CH-027 — Security audit: rate limits, endpoint auth, posted data

Answering three questions with a scan rather than from memory.

### Rate limiting — was partial, now global

Only six endpoints were limited: login, MFA, both password-reset steps, contact
and access request. Everything else — including the endpoints that read the
whole call log, purchase numbers or move money — could be called as fast as a
client could manage.

Now applied at the API root: `60/m` per IP before authentication, `600/m` per
user after. Counted in Redis, so the limit is shared across workers and survives
a restart. Both are settings (`API_THROTTLE_ANON`, `API_THROTTLE_USER`), so they
change without a deploy.

Support chat was the worst gap: unauthenticated, and every message is forwarded
to Telegram. An open relay into the team's chat. Now 5 chats and 30 messages a
minute per IP.

### Endpoint auth — 28 routers, 25 public endpoints, all deliberate

Every router declares auth. 25 endpoints carry `auth=None`, and each was checked
for its own protection:

| group | how it is protected |
|---|---|
| login, register, refresh, password reset | public by necessity, rate limited |
| Stripe / CoinGate / Capitalist webhooks | signature verified before anything is credited |
| Asterisk route / call-ended / active-channels | shared secret, HMAC compared, fails closed |
| conversion postback | secret token in the URL |
| DNI assign + snippet | pool id is the public key, by design |
| IVR webhooks | flow id only |
| white-label config | public branding, by design |
| set-password | one-time token |
| support chat | now rate limited |

Two with a weaker story: the IVR webhooks are guarded only by knowing a flow id,
and the RTB bid endpoint by knowing an auction id.

### A real hole in RTB bidding

`POST /api/rtb/bid` took only an auction id and then ran:

    RTBBid.objects.filter(auction=auction, status=PENDING).update(bid_amount=...)

The auction id is sent to **every buyer invited to that auction**, so it
identifies the auction, not the bidder. Any invited buyer could set every other
buyer's bid, and the last caller decided the price for all of them.

The bid now carries `buyer_id` and updates only that buyer's own pending row.

Worth knowing: the live auction path in `rtb/engine.py` collects bids from the
ping responses directly, so this callback is a second, asynchronous way in. It
is reachable, which is what matters.

### Posted data

Every endpoint takes a Ninja schema, so types are validated before a handler
runs. Money and call paths go further — signatures on payment webhooks, shared
secret on Asterisk, token on conversions.

### Error responses were leaking internals

The global exception handler returned `str(exc)` to the caller, handing out SQL
fragments, file paths and library internals to anyone who could make a request
fail. The detail now goes to the log and the caller gets a generic message.
Full text still returned when `DEBUG` is on.

**Still open**
- IVR webhooks are guarded only by a flow id. Fine while Asterisk handles
  routing, worth a shared secret if they are ever used in anger.
- No per-endpoint limits on the expensive reads (CSV export of the full call
  log). The global limit covers abuse, not cost.

---

## CH-028 — Cost calculated in the backend for the first time

**What was wrong**

Nothing in the backend ever returned a cost. `CallLog.twilio_cost` exists on the
model but no code writes it, so it is `0.0000` on every row, and no summary
response carried a cost field at all.

The frontend filled the gap by multiplying total talk time by the rate. That is
wrong twice:

1. **No per-call rounding.** Billing charges `ceil(duration ÷ 60)` — a 90-second
   call bills as 2 minutes. Summing raw seconds first discards every part-minute.
2. **No markup.** The account's `markup_percent` was ignored entirely.

Measured on the 23 JUNE campaign:

| | minutes | cost |
|---|---|---|
| frontend method (raw talk time) | 4,762.18 | $2,142.98 |
| correct (each call to a whole minute) | 5,126 | $2,306.70 |

Understated by $163.72, or 7.6%. The 364-minute gap is the part-minutes we bill
and the dashboard did not count.

**The fix**

`AnalyticsService._billing_rate()` reads the client's own rate and markup once
per request; `_cost_from_minutes()` converts billable minutes to money on the
same rule as `BillingService.call_cost`. Billable minutes are computed in SQL
with `Ceil(duration_seconds / 60.0)`, per row, so the rounding matches the
invoice rather than being applied to a total.

Returned from the dashboard totals, all four summary breakdowns and the CSV
export, with `billable_minutes` beside it so any figure can be checked by hand.

**Verified on production**

    billable_minutes = 5162
    total_cost       = 2322.90      (5162 x $0.45, markup 0%)

**Definition decided, not escalated — later confirmed**

Profit stays `Revenue − Payout`, with Cost as its own column beside it. Cost is
the per-minute charge; it is not subtracted from Profit.

Confirmed afterwards: *"Cost has nothing to do with revenue... Cost should be
debited. According to min cost we will charging from user, which is $0.45."*
Cost is the per-minute rate debited from the client's balance, independent of
revenue and payout, and settable per account — a screenshot showed it at $0.40,
which is `per_minute_rate` and changes with `manage.py set_rate`, no deploy.

**Displayed cost vs the ledger — checked, and left as calculated**

Since Cost is what gets debited, the column has to agree with the balance.
Verified against the transaction ledger:

| | |
|---|---|
| chargeable calls before 18 Sep | 332 — never charged |
| chargeable calls since 18 Sep | 383 |
| ledger charges | 383 |

Charging shipped 18 September in CH-007, and every chargeable call since has been
debited. The 332 are exactly the calls from 26 August to 17 September, before the
feature existed. All fifteen most recent calls are charged.

Kept as a calculation rather than read from the ledger: from the day billing
shipped the two agree exactly, the formula is the one the invoice uses, and
sourcing from the ledger would show $0 for every call before 18 September, which
reads as broken rather than honest.

Not backfilled. Charging clients now for calls made before billing existed is a
commercial decision, not a technical one.

**Not changed: payout**

Checked while here, and it is correct. `earned = Q(is_converted=True)` gates
before anything else in `dynamic_revenue` / `dynamic_payout`, so an unconverted
call contributes zero everywhere — dashboard, summaries and export all read the
dynamic annotations.

The stored `CallRecord.payout` column is *not* gated, so 772 unanswered calls
carry a stored $0.45. **Nothing reads that column**, so it reaches no total and
no invoice. Recorded here because it looks alarming in a raw query and should
not be re-raised as a bug.

**Still open**
- `billable_seconds` on CallRecord is populated on 635 of 1447 rows and its sum
  (273,972) differs from `duration_seconds` (287,669). Cost uses
  `duration_seconds`, which is what `BillingService.call_cost` is called with, so
  cost matches the invoice. Worth deciding whether `billable_seconds` should be
  the billing basis, or dropped like `twilio_cost`.

---

## CH-029 — Ports closed, and a flood-protection plan

Asked how to protect the system from a DDoS. Looking at the exposure first turned
up something worse than a flood.

### The database and Redis were on the public internet

`docker compose ps` showed:

    postgres  0.0.0.0:5432->5432/tcp
    redis     0.0.0.0:6379->6379/tcp
    web       0.0.0.0:8000->8000/tcp

`0.0.0.0` is every interface, not just this machine. Redis had no password. The
Postgres password was the literal string `changeme_in_production`, committed to
the repository — so the credential for an internet-reachable database was
published in git.

Every host binding is now `127.0.0.1`. Containers reach each other over the
compose network by service name and never needed a host port. Local access is
unaffected; a laptop reaches the database over an SSH tunnel.

Daphne on `0.0.0.0:8000` was a bypass as well: anyone could hit the application
directly and skip Nginx, and with it every limit, header and TLS check set there.
The API rate limits added in CH-027 still applied, but nothing in front of them
did.

`POSTGRES_PASSWORD` and `REDIS_PASSWORD` now come from `.env`. The running
database keeps its old password until `ALTER USER` is run — the environment
variable only applies when the data directory is first created. Command is in
`deploy/DDOS.md`.

### Flood protection, in order of what it buys

`deploy/DDOS.md` has the steps; `deploy/nginx-rate-limits.conf` is the config.

| step | stops | where |
|---|---|---|
| close the ports | direct database attack | done above |
| firewall | a missed binding becoming an open door | `ufw` |
| Nginx limits | slow-loris, request floods | this repo |
| fail2ban on SIP | toll fraud, registration brute force | server |
| Cloudflare | genuinely volumetric floods | DNS |
| app rate limits | scraping, credential stuffing | CH-027 |

Two things worth stating plainly.

**The application limits do not stop a DDoS.** They run after the request reaches
Django. A volumetric flood saturates the network and Nginx workers long before
that. They are the last layer, not the first.

**For a call platform the likelier attack is SIP, not the web.** Flooding a
dashboard costs an attacker money and earns nothing. Stealing minutes earns them
money directly, which is why fail2ban and restricting SIP to the carrier's
address matter more here than for an ordinary web app.

### The Asterisk callbacks are exempt from rate limiting, deliberately

`/api/twilio/asterisk/route/` decides where a live call goes and
`call-ended` closes and bills it. Rate limiting either drops calls. They are
protected by a shared secret, HMAC compared, failing closed. The Nginx config
says so at the top so the exemption is not removed by someone tidying up.

### Deployed and verified 2026-09-25

    postgres  127.0.0.1:5432
    redis     127.0.0.1:6379
    web       127.0.0.1:8000

Portal 200, support chat 201, web log clean, and
`POST /api/twilio/asterisk/active-channels/ 200` — Asterisk reaching the API
through the new firewall, which is better proof the call path survived than a
test call would have been.

### SIP was open to the internet, and the fix nearly broke calls

`ufw` had `5060/udp ALLOW Anywhere` at rule 5, above a `DENY Anywhere` at 14. UFW
matches top-down, so the blanket allow won and the deny never applied. Someone
had restricted SIP to the carrier correctly and it was being shadowed.

Deleting the blanket rule would have dropped calls. The carrier allow-list held
**one** IP; Asterisk's `identify` section matches **two**:

    match=45.79.4.41,100.53.112.140
    contact=sip:100.53.112.140:5060

The missing address is the one the carrier's contact actually points at. It was
added above the deny before anything was removed.

Verified from the kernel's own evaluation order rather than by placing a call:

    15  ACCEPT  100.53.112.140  tcp 5060
    16  ACCEPT  100.53.112.140  udp 5060
    17  ACCEPT  45.79.4.41      tcp 5060
    18  ACCEPT  45.79.4.41      udp 5060
    19  DROP    everything else tcp 5060
    20  DROP    everything else udp 5060

`10000:20000/udp` (RTP) deliberately left open — media arrives from varying
addresses and restricting it is the usual cause of one-way audio.

### Incident: support chat 500, caused by the CH-027 rate limit

The guard added to `start_chat` and `send_message` reads `settings`, but the
module-level import was never added. The patch checked whether
`from django.conf import settings` appeared in the file — it does, inside
`send_telegram_support` — so the check passed, the import was skipped, and both
endpoints raised `NameError` on every request.

Every visitor using the widget got a 500. Found only because the new generic
error handler surfaced in the widget; the old handler would have shown the
visitor a raw `NameError`.

Same pattern as the `contact_api.py` bug: a name imported inside one function and
used at another scope. A scan of the rest of the codebase comes back clean.

**Lesson for the patch scripts**: "is this import present anywhere in the file"
is the wrong question. Function-local imports satisfy it without putting the name
in module scope.

### Incident: two failed password rotations, both the same mistake

The rotation was attempted twice and took the site down both times. The database
password changed and the application kept sending the old one.

**One cause, not two.** `docker compose restart` **does not re-read `env_file`**.
A container's environment is baked in when it is created, so restart reuses the
old values. `.env` was correct both times and the container never saw it: the
database had the new password while the application kept sending the old one.

`up -d` is required — the same thing that made the port bindings need
`down && up -d` earlier the same evening, and which I had explained to the user
an hour before failing to apply it here.

At the time I attributed the first failure to a different cause: that the guard
had checked whether the old password appeared anywhere in `.env` rather than
inside `DATABASE_URL`. The rotation script disproved that — it read
`changeme_in_production` straight out of `DATABASE_URL` (22 characters, one
occurrence), so the first attempt's rewrite had been correct all along. The
explanation was a guess that fit the symptom, and it was wrong. Corrected here
rather than left standing.

Recovery each time was the backup plus `ALTER USER` back to the old password.

`deploy/rotate_db_password.sh` now does it properly: reads the current password
out of `DATABASE_URL` rather than assuming it, replaces it everywhere it appears,
uses `up -d`, verifies with a real query, and rolls itself back if that query
fails.

**Done 2026-09-25.** Rotated successfully with the script: read the old password
out of `DATABASE_URL`, rewrote it, recreated the containers with `up -d`, and
verified with a real query. Backup kept on the server as
`.env.backup-2026-09-25-212615`.
- Two dead duplicate ACCEPT rules sit after the SIP DROP and never match.
  Harmless, worth tidying.
- The frontend polls hard: dashboard, campaigns and a 100KB destinations list
  every ~15s. Within the 600/m limit, but worth reducing.
- Cloudflare not enabled. When it is, Nginx needs `real_ip_header
  CF-Connecting-IP` or every rate limit will count the whole internet as one
  visitor, and the origin needs locking to Cloudflare's ranges so the proxy
  cannot be bypassed.

---

## CH-030 — Open for 2026-09-26

Recorded at the end of 25 September. Nothing here blocks calls; routing, billing
and reporting all work.

### 1. CONFIRMED: the rate is $0.045, not $0.45 — clients were overcharged 10x

Settled late on the 25th. Told the rate of $0.45 gives $6,132.59 for 13,628
minutes, the boss replied *"it wont cost $6K"* and corrected himself to
*"only 600ish"*. $0.045/min gives $613.26. That is the rate.

**Both done, 2026-09-26.**

    rate set          Avortyx: $0.045/min (a 90s call bills as 2 min = $0.09)
    call charges      395 between 18 and 26 September
    charged at $0.45  $1,157.85
    correct at $0.045 $115.78
    credited back     $1,042.07   ref rate-correction-2026-09-26
    new balance       $9,934.22

Because the rate is a straight multiplier the correction is exactly one tenth, so
no per-call recalculation was needed.

**Fees were nearly refunded by mistake.** The first query bucketed everything with
`transaction_type='charge'`, which includes the $49.99 monthly portal fee and the
$20 number fees — `charge_fee` writes the same type as `charge_call`. Those are
fixed amounts and have nothing to do with the per-minute rate, so dividing them by
ten would have credited money that was charged correctly. Call charges carry a
`call_sid` and fees do not; filtering on that separates them. Worth remembering:
`transaction_type` alone does not distinguish a call from a fee.

### 1b. Background to the rate question (kept for reference)

`per_minute_rate` is **$0.45**. Two of the boss's messages on the evening of the
25th compute with **$0.045**, and an earlier screenshot of his showed `0.40`.

His platform's own figures give an implied rate, derived from four campaigns:

| campaign | minutes | calls | cost | $/min |
|---|---|---|---|---|
| C-02 | 11,333.20 | 1,047 | 416.359 | 0.03674 |
| C-03 | 1,401.67 | 123 | 51.310 | 0.03661 |
| C-05 | 394.27 | 32 | 14.315 | 0.03631 |
| C-11 | 498.85 | 77 | 18.900 | 0.03789 |
| total | 13,627.98 | 1,279 | 500.884 | 0.03675 |

A two-variable fit gives **$0.03502/min + $0.01854/call**, matching every row to
within 0.6% — carrier billing, per minute plus a per-call setup fee, not a flat
per-minute rate.

For that same traffic:

    $0.45  /min  ->  $6,132.59     <- what our system would charge
    $0.045 /min  ->  $613.26
    their figure ->  $500.88

So our rate is roughly **12x** theirs. Either $0.45 is correct and we are a
platform charging well above carrier cost, or it should be $0.045 and every call
billed since 18 September has been charged ten times over.

**This is the first thing to settle**, because it decides whether the 383 charges
already taken need correcting. The rate itself is a setting:

    docker compose exec web python manage.py set_rate --org "Avortyx" --rate 0.045

No deploy, and no code change either way.

Also worth putting to him: his platform's cost has a per-call component. Ours is
purely per-minute. If the two must agree exactly, a per-call fee field is needed.

### 2. Frontend — the backend is complete for all of these

- **Cost column** still calculated in the browser. Shows $232.62 where the API
  returns $251.55, because it sums raw talk time instead of rounding each call up.
- **`grossProfit` and `net` columns** are frontend inventions; neither exists in
  the API. `net` subtracts Cost from Profit and displays -$209.20. Profit is
  Revenue - Payout, with Cost as its own column.
- Both columns render raw translation keys
  (`toolsUI.reports.summary.columns.net`) instead of labels.
- The `$499/month Growth` plan card on the Billing page is invented. Real charges
  are $49.99/month, $0.45/min, $20/number, all returned by
  `GET /api/billing/account`.
- The six Expenses categories (Rent Numbers, VoIP Shield, ...) do not exist.
  `/api/billing/expenses` buckets by transaction type.
- "Jordan Kim · Solutions engineer · Replying now" and the online-teammates count
  in the support widget are fabricated. There is no agent identity or presence in
  the backend, and a visitor is being told a named person is answering when the
  message goes to a Telegram group.
- **Destinations panel shows 0 calls and $0 revenue** while the dashboard above
  it shows 1,513 calls for the same range. `/api/destinations/` is called as
  `?page=1&page_size=500` with no dates, and with no dates the endpoint returns
  **today only**. The column headers are labelled with the selected range, so the
  panel claims 28 Aug – 26 Sep while showing today. Confirmed: 0 calls to
  `+18779641530` today, 1,445 across the range. `start_date` and `end_date`
  (YYYY-MM-DD) are already supported and just need passing.

  Not a formatting problem. The destination's `tfn` and the call's
  `destination_number` both store `+18779641530` and match exactly — stripping
  the `+` would break the match, not fix it.
- Dashboard polls every ~15s including a 100KB destinations list that does not
  change. Limit is 600 req/min per user.
- Intermittent 401s on `/api/analytics/dashboard`, consistent with a token
  refresh race.

### 3. Duplicate window — decision needed

Ours counts a repeat caller within `duplicate_call_block_hours`, currently 24, per
campaign. On 23 JUNE that is 4 duplicates against their 30. Both calculations are
internally consistent; the rule differs. Matching theirs would drop our Qualified
from 73 to about 48, which is a large visible change, so it is a decision rather
than a fix.

### 4. Before any DDoS testing

- ~~`deploy/nginx-rate-limits.conf` not applied.~~ **Applied 2026-09-26.** Zones
  in `/etc/nginx/conf.d/ratelimit-zones.conf`, location blocks in the
  `callplatform` site. 30r/s burst 60 for the API, 10r/m for login and password
  reset, 20 concurrent connections per address.

  The site had no `/api/` location at all — everything fell through `location /`.
  The Asterisk callbacks use `^~` so no regex location can take precedence over
  them.

  Verified under 40 concurrent connections:

      /api/campaigns/                 401s turning to 429s   - limiting works
      /api/twilio/asterisk/route/     200 requests, all 403, zero 429

  The second is the one that mattered: rate limiting the endpoint that routes
  live calls would drop them. Sequential `curl` does not reach 30r/s, so the
  first attempt showed no 429 and proved nothing — the parallel run is what
  settles it.
- Cloudflare not enabled on `avortyx.io` and `rec.v0l1.com`.
- Contabo must be told in writing before any volumetric test, or they will
  blackhole the IP and take SIP down with it.
- Application-layer testing is safe now. Volumetric is not, until the two above
  are done.

### 5. Smaller items

- **Delete `routing/twilio_handler.py` on or after 2026-10-02.** Checked on the
  26th and the log is clean, but that proves less than it looks: the
  instrumentation only went in on the 25th and the containers were recreated
  that night, so the evidence window is about a day, not the week the
  `--since 168h` suggests. The architecture says it is unreachable — numbers sit
  on the SIP trunk and reach Asterisk directly, never touching these webhooks —
  but one day of logs is not enough to remove a routing path while calls are
  live, and the file costs nothing while it waits. Removes 8 swallowed
  exceptions with it.

      docker compose logs --since 168h web celery_worker | grep "LEGACY TWILIO PATH USED"

- Two dead duplicate ACCEPT rules sit after the SIP DROP in ufw.
- ~~Delete `.env.backup-2026-09-25-212615`.~~ Done 2026-09-26.
- IVR webhooks are guarded only by knowing a flow id.

---

## CH-031 — Crypto payment has never worked

Asked to confirm crypto payment was working. It is not, and never has been. No
customer has ever successfully paid by any method: the only money that has
reached the platform is four manual credits.

### CoinGate is not configured

`COINGATE_API_KEY` is empty and `COINGATE_ENVIRONMENT` is `sandbox`. It cannot
take a payment. Zero orders have ever been created.

### Capitalist builds a checkout, but the confirmation can never land

31 payments, $16,392.03, every one at `pending`. **No callback has ever been
received** — nothing in the Nginx access log has ever hit
`/api/billing/capitalist-webhook`.

Not money owed, though. Every pending payment is from June — the 9th, 10th and
26th — and the amounts read as someone testing the page: $0.03, $10, $12, then
$1000 four times within minutes, $10000, $500, $250. Abandoned test checkouts.
Nothing since June.

Three faults, all of which had to be fixed before a payment could complete:

**The webhook looked the payment up by the wrong field.**

    Transaction.objects.get(id=order_id, provider='capitalist')

`order_id` is the order number we send Capitalist (`260928-001`); `id` is a
UUID. The lookup could never match. Worse, the order number was never stored at
all — `capitalist_payment_id` held the row's own UUID — so there was nothing to
match against even with the right field. Both fixed: the order number is now
stored and the lookup uses it.

**Every refusal returned `{"received": true}`.** A bad signature, an unknown
order and a successful credit were indistinguishable. Each path now logs what it
refused and why, because a payment taken and not credited has to be findable.

**The field names disagree.** Outgoing signs `merchantid` / `number`; the
verifier expects `merchant_id` / `order_number`; the webhook read `order_id`. The
webhook now accepts `order_number`, `order_id` or `number`, and `payment_state`
or `status`.

### Two credential pairs, only one read

`CAPITALIST_MERCHANT_ID` / `CAPITALIST_SECRET` are what the code reads.
`CAPITALIST_API_KEY` / `CAPITALIST_API_SECRET` also exist and are populated in
the environment, and **no code reads them**. If those are the working
credentials, every checkout ever built was signed with the wrong secret and
Capitalist would have rejected it — which would explain a callback never
arriving. Marked in settings rather than deleted, so the values are not lost.

### New: `manage.py confirm_crypto_payment`

Crediting someone who has genuinely paid used to mean a shell session.

    python manage.py confirm_crypto_payment --list
    python manage.py confirm_crypto_payment --order 260928-001
    python manage.py confirm_crypto_payment --order 260928-001 --fail

It credits on your say-so and does not verify with the provider, so check their
side first.

**Still open — needed before crypto can actually take money**
- Confirm which Capitalist credential pair is the live one.
- Set the callback URL to `https://avortyx.io/api/billing/capitalist-webhook` in
  the Capitalist merchant dashboard. Nothing in the checkout parameters tells
  them where to post, so it has to be configured on their side.
- Get Capitalist's callback field names from their documentation. The webhook now
  accepts three spellings, but the signature check still expects a fixed set.
- CoinGate: either supply a live API key and set `COINGATE_ENVIRONMENT=live`, or
  remove the option from the UI. It currently offers a payment method that cannot
  work.

---

## CH-032 — The dashboard was showing four different moments at once

Reported as a data mismatch: the header read **Total 192** while the chart read
**190**, and one panel showed **$44** where another showed **$45**.

Nothing was wrong with the data. Measured at a single instant everything
reconciles:

    terminal calls   199
    live calls         5
    total            204
    connected         47   converted 47   revenue $47
    no answer        147

And the screenshot reconciles too, for the moment it was taken: 141 no answer +
49 connected = 190 exactly.

The cause is that the page assembles itself from **four separate requests** —
dashboard, campaigns, destinations and the revenue series — each landing a second
or two apart. With calls arriving continuously, each request sees a different
number. The header polled two calls later than the chart; the destinations panel
loaded one conversion later than the revenue panel. Between the screenshot and
the check, terminal calls moved 190 → 199 and revenue $44 → $47 on their own.

Every figure was right for the instant it was taken. They were simply not the
same instant.

### `GET /api/analytics/snapshot`

Returns `dashboard`, `campaigns`, `time_series` and `destinations` from one
evaluation, with `taken_at` so the page can show what moment it is displaying.
The destinations section is wrapped so a failure there costs that panel rather
than the whole dashboard.

The individual endpoints stay for anything that needs one section alone.

**For the frontend**: replace the four polls with one call to `/snapshot`. That
removes the mismatch and cuts the dashboard's request volume by four, which also
helps with the rate limit.

**Worth stating plainly**: this will keep being reported as a bug for as long as
the page makes four requests, because during live traffic it will keep happening.
It is not fixable from the backend alone — the endpoint exists, the frontend has
to use it.

---

## CH-033 — Every visitor shared one rate-limit bucket

The dashboard started failing with `429 Too Many Requests` on every request, and
the browser reported most of them as CORS errors.

Both were the same fault. Nginx terminates TLS and proxies to the container, so
Django's `REMOTE_ADDR` was the Docker gateway — **one address for the entire
internet**. The rate limiting added in CH-027 keys on that address, so every
visitor shared a single 60-per-minute anonymous bucket. A few browser tabs
exhausted it and everybody was locked out.

The CORS errors were the 429s in disguise: a request rejected before the
response headers are written has no `Access-Control-Allow-Origin`, so the browser
blames CORS. The origins were configured correctly the whole time —
`https://www.avortyx.com` was already in the list.

### `config/real_ip.py`

Middleware, registered first so everything below it sees the corrected address.
It replaces `REMOTE_ADDR` with the address the proxy reports, and **only when the
connection itself arrives from a proxy we run** — otherwise anyone could set
`X-Real-IP` and appear to be someone else, evading their own limit or exhausting
another visitor's. Trusted networks are a setting.

Behind Cloudflare this is still the genuine visitor: Nginx resolves
`CF-Connecting-IP` before setting `X-Real-IP`, so the two layers compose.

**Verified after deploy**

    150 parallel to /api/accounts/me       401s turning to 429s   - limit works, per IP
    100 parallel to /api/twilio/asterisk/  100 x 403, zero 429    - call routing exempt
    429s in normal dashboard use            0

**Worth remembering**: the Daphne access log prints the connection it received,
which is always Nginx. It is not what the rate limiter reads, so it cannot be
used to check this — the first attempt to verify used it and proved nothing.

---

## CH-034 — Cost is now what was charged, not a recalculation

The boss reported the cost figure wrong for a fifth time. He was right, and two
separate faults were behind it.

### The invoice rounds every call; the column rounded once

`BillingService.call_cost` quantizes each call to the cent, so a one-minute call
at $0.045 is charged $0.05. The column multiplied the minute total once and
rounded at the end, losing the part-cents.

For 28 September:

    563 billable minutes x $0.045   = $25.34   shown
    52 charges in the ledger        = $25.50   actually taken
                                      $0.16    apart

### The column used today's rate on yesterday's calls

Worse, and not previously spotted. Every call before 26 September was charged at
$0.45/min. Once the rate was corrected to $0.045, the column recalculated those
same calls at the new rate — so every historical date displayed a figure that was
never charged to anyone.

### The fix: store what was charged

`platform_cost` on `CallLog` and `CallRecord`, written the moment the charge
succeeds, mirrored to analytics, and backfilled from the transaction ledger by
`call_sid`. Every display path — dashboard totals, all four summary breakdowns
and the CSV export — reads it.

Recalculation survives only as a fallback for calls that predate charging going
live on 18 September, so those show an estimate rather than zero.

The column cannot drift from the invoice again, because it is no longer deriving
the number. It is reporting it.

### How this was missed for a week

On 25 September I checked that every chargeable call had a charge — 383 calls,
383 charges, no gaps — and then wrote that the displayed cost matched the ledger.
Those are different claims. Counting rows proves nothing about summing amounts,
and the check to prove it was one more line of the same query.

The same shape caused three other failures the same week: the support chat
outage (the import existed in the file, not at module scope), both failed
password rotations (`.env` contained the password, but not inside `DATABASE_URL`;
and `restart` does not reload `env_file`), and a refund that nearly returned the
monthly portal fee (grouped by `transaction_type` rather than checking each row
was a call).

Each time the check was adjacent to the claim. Recorded because the claim was
repeated to the client five times on the strength of it.

---

## CH-035 — A quarter of calls never recorded their ending

`POST /api/twilio/asterisk/call-ended/` was returning 400 on **79 of 278 calls in
a single day** — 28% of traffic.

The dialplan posts:

    "duration":${CDR(billsec)}

unquoted. When a call is never answered `billsec` is empty, so the body becomes
`"duration":,` — not valid JSON. The handler refused it, and those calls never
reported their outcome at all. They only appeared as no-answer because the stale
-call sweep closed them later.

**Two fixes.**

The handler now repairs an empty value rather than refusing it, and logs when it
does. A refusal is only returned if the body is unparseable even then, and it now
records what it could not read instead of a bare 400. Duration that is empty,
missing or non-numeric is treated as zero, which is what an unanswered call is.

Repairing rather than refusing is deliberate: the call has already happened, and
losing its outcome is worse than accepting a slightly malformed body from our own
switch behind a shared secret.

**The dialplan should also be corrected** so the body is valid in the first place.
In `/etc/asterisk/extensions.conf`, quote the value:

    "duration":"${CDR(billsec)}"

The handler accepts both, so this can be done at any time.

**How it went unnoticed**: the endpoint returned 400 with no logging, so a
quarter of calls failing looked identical to nothing happening. Found only by
grepping for the path while investigating something else.

---

## CH-036 — Caller profile and do-not-call, via RealValidito

Two features existed in the interface with nothing behind them.

**The caller profile** showed city, zip and timezone. Telnyx supplies a carrier
name and a line type and nothing else, so those fields were empty on every call,
and `fraud_score` was hardcoded to `0` — meaning any rule depending on it never
fired.

**The TCPA Shield** had no data source at all.

### What the lookups give us

`POST /phonelookup/validate` returns `city`, `state`, `zip`, `timezone`,
`number_type` (Mobile / Landline / Toll-Free / VoIP), `network_name` and
`network_type`. Used in the existing enrichment task, which already runs off the
call path. Telnyx stays as the fallback when RealValidito is unconfigured or out
of credits.

`POST /dnclookup/validate` returns four lists: `federal_dnc`, `state_dnc` (six
states), `tcpa_litigator` and `cleaned_number`. The litigator list is the
valuable one — those are the numbers that generate lawsuits.

### Where the DNC check runs

Immediately after the blacklist, before anything is spent on routing. Both
answer the same question: may this caller be contacted at all.

Two switches, deliberately separate:

    DNC_CHECK_ENABLED   look the number up and record the result
    DNC_BLOCK_LISTED    refuse the call when it is listed

Running with the first on and the second off records the exposure without turning
traffic away. That is the order to switch them on — find out how many calls it
would refuse before refusing any.

Every call stores `is_dnc` and `dnc_reason` whether or not it was blocked, so the
exposure is visible either way.

### Nothing here can stop a call

Every failure — no credentials, no credits, a timeout, a malformed response —
returns a result that lets the call through, and `checked` is False so a caller
can tell "this number is clean" from "we could not find out". Losing a call is
worse than missing a check, and the check can be repeated.

### Credits

Both endpoints accept up to 1,000 numbers, but a call cannot wait for a batch, so
lookups happen one at a time and every result is cached. A caller who rings ten
times costs one credit, not ten. Profiles are held 30 days; DNC results 7, since
a number can join the register at any time and a stale "clean" is the expensive
direction to be wrong.

    python manage.py lookup_credits
    python manage.py lookup_credits --number 4705551234

That prints the remaining balance on both services and tests the credentials. A
wrong key and an empty balance both look like "the check did nothing" from
outside, and this tells them apart.

**Still open**
- Credentials into `.env` on the server: `REALVALIDITO_API_KEY`,
  `REALVALIDITO_API_SECRET`.
- Switch on with `DNC_CHECK_ENABLED=True` and `DNC_BLOCK_LISTED=False` first,
  look at how many calls come back listed, then decide about blocking.
- The frontend can now show city, zip and timezone on the caller profile, and the
  TCPA Shield has real data to display.

---

## CH-037 — Routing returned 500 on every call for two hours

Every call to `/api/twilio/asterisk/route/` returned **500**. Asterisk got no
routing decision, the dialplan fell through to `nobuyer`, played the goodbye
prompt and hung up. Twenty-plus calls, each answered and dropped after one
second.

    NameError: name 'call_data' is not defined
    routing/asterisk_handler.py line 90

Introduced by me a few hours earlier, recording DNC status on the call:

    is_dnc=bool((call_data.get('dnc') or {}).get('listed')),

inserted into `CallLog.objects.create(...)`. `call_data` does not exist at that
point — the call log is created **before** routing runs, and the DNC result is
produced *inside* the engine afterwards. The lines were in the wrong place
entirely, not just referencing the wrong name.

**Fixed** by naming the dict passed into `route_call` and reading the result back
after routing returns, which is the only point where it exists.

### Why it shipped

`py_compile` passed, because a `NameError` is a runtime failure, not a syntax
one. I checked that the code compiled and treated that as checking it worked.

An AST pass over the function for names that are used but never assigned would
have caught it in a second, and that is now what I run on any edit inside the
call path.

### Why it took two hours to find

Worse than the bug. I checked the web log with `--since 30m` at a point *before*
the failing calls happened, saw nothing, and concluded no traffic was arriving.
Everything after that — the firewall scan, the SIP capture, the message telling
the client the carrier had stopped sending — was built on an empty result from a
window that ended before the traffic started.

The calls were in the log the whole time, returning 500, with the traceback
attached. Widening the window by fifteen minutes would have shown it immediately.

**The rule**: when a log query comes back empty, widen the window before drawing
any conclusion from it. An empty result from the wrong window looks identical to
an empty result from the right one.

---

## CH-038 — The Live Monitor's counters were counting the wrong thing

Reported as "details missing": the monitor showed **4 calls in flight** with
Started, Completed, Missed and Revenue all at **0**.

Nothing was wrong with the calls. The backend only ever returned the list of live
calls — there was no endpoint supplying those four figures — so the page was
tallying events it had seen since it connected. A call that began before the page
was opened was never "started" as far as that tally knew, which is why four calls
could be in flight with nothing counted.

### `GET /api/analytics/live/summary`

    {
      "as_of":      "2026-09-29T21:57:00Z",
      "in_flight":  4,
      "started":    278,
      "completed":  52,
      "missed":     226,
      "revenue":    "52.00",
      "longest_active": {
        "caller_number": "16623642999",
        "campaign_name": "23 JUNE",
        "started_at":    "...",
        "seconds":       483
      }
    }

Today's totals rather than a running tally, so the panel reads the same whenever
it is opened and agrees with the dashboard. `missed` is everything that reached
nobody — unanswered, refused or failed — which is the complement of completed
once live calls are excluded.

`longest_active` replaces whatever the page was deriving for its featured call.

**For the frontend**: poll `/api/analytics/live/summary` for the four counters and
the featured call, and keep the websocket for the radar. The counters then survive
a page refresh, which is the actual complaint.

---

## CH-039 — Buyer and publisher invitations never worked

Reported by the client: "Trouble in sending publisher and buyer access invite."
The dialog sat on *Inviting…* and never finished.

**There was no publisher invite endpoint.** The interface offered "Invite a
publisher" with nothing behind it.

**The buyer one existed and was broken three ways:**

- it emailed `buyer.created_by.email` — the admin who created the record, not
  the buyer, so the invitation went to whoever was clicking the button
- it generated a token, put it in the link, and **never stored it**, so the link
  it sent could never be validated by anything
- it ignored the email address the form collects

### Built on the flow that already works

Staff accounts already use `SetupToken` plus `/api/accounts/set-password/`, which
validates a token, sets the password and returns a login. The partner invites now
use the same thing rather than inventing a parallel mechanism.

`accounts/partner_invites.py` creates or reuses the partner's account, links it to
the buyer or publisher record through `User.buyer` / `User.publisher`, invalidates
any earlier unused link, issues a token valid for 48 hours, and emails the address
given on the form.

The link matters as much as the email: without it the login has the role but no
way to know *which* buyer it is, and the row-level scoping shows it nothing. With
it, the partner signs in and sees only their own calls — which is what the roles
work was for.

### Two details worth keeping

**An admin being invited keeps their role.** Narrowing an existing admin to the
partner view would lock them out of their own workspace, so the role is only set
on a new account or one that is already a partner.

**A failed email does not lose the invitation.** `send_mail` runs with
`fail_silently=False`, and the response carries `email_sent` plus the
`setup_link`, so the link can be shared directly if mail is down. The previous
version used `fail_silently=True`, which is how an invitation that was never
delivered looked exactly like one that was.

`Buyer` stores the address as `contact_email`; `Publisher` uses `email`. Both are
used as a fallback when the form omits one.

---

## CH-040 — End-to-end scan

Full pass over the codebase after a week in which two outages were caused by
errors that compiled cleanly and failed at runtime.

### Two real bugs found and fixed

**`routing/api.py` — `RoutingEngine` used but never imported.** The manual hangup
endpoint would have raised `NameError` and returned 500 the first time anyone
used it. Exactly the fault that took routing down on 29 September.

**`billing/api.py` — `coingate_deposit` referenced `currency`, which was not a
parameter.** It has never fired because CoinGate has no API key, but it would
have failed on the first real call. Now takes `currency` with a default, matching
`capitalist_deposit`.

Both were found by an AST pass for names used but never defined in scope — the
check that `py_compile` cannot do, since a `NameError` is a runtime failure.

### Clean

- Every file compiles.
- No secrets hardcoded; `DEBUG` defaults to `False`.
- 25 public endpoints, each individually protected (signature, shared secret,
  one-time token or public by design).
- Four routers without router-level auth, all deliberate: access requests,
  contact, support chat, Telegram webhook.
- Guardrail order in the call path is correct: blacklist, DNC, duplicate,
  campaign cap, balance.

### Known and accepted

**11 swallowed exceptions remain.** Eight are in `routing/twilio_handler.py`,
which is dead code awaiting removal; two in `call_queue` are genuinely expected
and documented; one in `config/real_ip.py` is a deliberate fall-through on a
malformed address.

**Dead columns that nothing writes**: `twilio_cost` and `billable_seconds`. Both
are exposed in the admin and could be mistaken for real data. `twilio_cost` in
particular reads as a cost figure and is always zero.

### Worth acting on

**The DNC check makes a blocking HTTP call inside the routing path.** Results are
cached and it fails open, but the first call from any new number waits up to four
seconds for an external service before being routed. This is the same shape as
the Telnyx lookup that was deliberately moved off the call path in CH-009. It
should be moved to a pre-warmed cache or an asynchronous check.

---

## CH-041 — Do-not-call removed from the call path entirely

Calls stopped routing for a second time. The cause was the DNC check added in
CH-036, which made a blocking HTTP call to an external service **inside**
`route_call`. When that service was slow the request never completed, so the AGI
got no answer, every call hung up, and nothing appeared in the access log — a
request that never finishes is never written to it.

That is the same fault CH-009 fixed, where the carrier lookup was moved off the
call path for exactly this reason. Putting it behind a setting was not enough: a
setting gets switched on, and I switched it on myself the same day I wrote a scan
warning that it was dangerous.

**The check now runs in the enrichment task**, after the call is already routed,
alongside the caller profile lookup. It records `is_dnc` and `dnc_reason` on the
call and cannot delay or block one, because it is no longer between the caller
and the destination.

`routing/engine.py` and `routing/asterisk_handler.py` contain no reference to any
external service. Verified, and the rule is now recorded: nothing that calls
another company's server belongs in those two files — not behind a flag.

**What this costs**: a listed caller is no longer refused before the call
connects; it is recorded afterwards. Blocking would need the register held
locally, checked in memory. Worth doing, and not worth another outage.

---

## CH-042 — A watchdog, so the client is not the alert

Twice in two days the platform broke and the client noticed first: routing
returned 500 for two hours, and nginx sat down for eight. Both were plainly
visible on the server the whole time. Nobody was looking.

`deploy/watchdog.sh` runs from cron every two minutes and checks:

| | why |
|---|---|
| routing endpoint answers 403 | anything else means calls cannot be routed |
| portal returns 200 | what the client sees |
| nginx running | it stopped on 30 Sep and stayed stopped |
| asterisk running | no calls arrive at all if it is down |
| all containers running | |
| calls arriving in busy hours | everything can answer while nothing comes in |
| disk under 85% | 45GB of unrotated logs nearly filled it once |

Alerts go to the support Telegram group.

**Deliberately outside Django and outside the call path.** It only reads. If the
watchdog itself breaks, nothing breaks with it — which is also why it sends a
daily heartbeat: silence should mean "nothing is wrong", not "the watchdog died".

**Alerts once per fault, and again when it clears.** A two-hour outage sending
sixty identical messages trains everyone to ignore the next real one. The
recovery message carries how long it was down.

**The traffic check is the subtle one.** Both outages looked identical from
outside: every service answering, no calls arriving. It only runs during busy
hours, so a quiet night is not an alert.

    */2 * * * * /opt/call_platform/deploy/watchdog.sh >> /var/log/avortyx-watchdog.log 2>&1

---

## CH-043 — Four changes that shipped without a log entry

Recorded late. These went out during the outage on 30 September and the entries
were skipped while firefighting, which is exactly when the log matters most.

### Routing put back to the last working version (`d78e710a`)

`routing/engine.py` and `routing/asterisk_handler.py` returned to `7e799e95` —
the state on 29 September when calls were flowing, before the do-not-call work.

Four changes to the call path were made chasing one mistake: the DNC check went
in, a `NameError` was fixed, the flag was switched off, then the code was
hand-removed — and that last edit introduced a fresh 500. Reverting once at the
start would have ended it in a minute rather than hours.

The RealValidito caller lookup moved to the Celery enrichment task, which runs
after a call is connected. Telnyx remains the fallback.

### Workspace activity showed who, never what (`0c67ca5c`)

`target_type`, `target_id` and `target_name` were hardcoded empty on every row,
so the log named the person and the action but never the record. The writers put
that in `metadata` and nothing read it.

Now returns them, plus the readable action label and the metadata itself.

**Still open**: the log only contains logins, and nothing since 25 September.
Campaigns, buyers, publishers, destinations and settings changes are never
written at all, so the page has little to show even with the reader fixed.

### Caller profile filled (`afa86d5e`)

`city`, `zip_code` and `timezone` were returned as hardcoded `None` in the call
detail view, so the panel was blank on every call. Nothing wrote them either —
Telnyx returns only a carrier name.

Three changes, none of them routing logic: `routing/models.py` declares five
columns migration 0012 had already created, the enrichment task writes city, zip
and timezone from the lookup it already performs, and the reporting layer returns
what is stored.

### Watchdog number parsing (`37b8af1b`)

The Django shell prints a banner before the value; stripping the whole output to
digits glued its numbers onto the answer and reported a twenty hour gap as 75,234
minutes.

### Caller Identity double-counts — frontend

Reports showed 48 calls and $46 where the real figure is 24 and $23. The API
returns three rows — AT&T 10, Verizon 8, T-Mobile 6, summing to 24, matching the
dashboard. The page adds an "Unknown" row containing all 24 and sums it with the
others. Backend verified correct; the fix is on the frontend.

---

## CH-044 — An audit that actually calls the endpoints

The scan in CH-040 checked that the code compiles, that names resolve, that no
secrets are committed and that every endpoint is authenticated. It reported
"everything working". It could not have told you that `target_name` was hardcoded
empty on every activity row, that the caller profile returned `None` on every
call, or that Caller Identity double-counted its totals.

Those three reached the client one at a time, over hours, each reported as a new
fault. They were all the same fault: a field that can never be filled.

`scripts/endpoint_audit.py` calls all 34 read endpoints as a real logged-in user
and reports, per endpoint: whether it answered, how long it took, and **which
fields are empty on every single row**. A field empty across every row is either
dead or hardcoded — that is the signature of every one of those bugs.

    docker compose exec web python manage.py shell < scripts/endpoint_audit.py

Read-only: GET requests only, writes nothing, safe during live traffic.

It will list some fields that are simply zero today. The ones that matter are
those that can never be anything else — which is the judgement the list makes
possible rather than makes for you.

---

## CH-045 — `organization.members` does not exist, and never did

Found by the endpoint audit, which reported every notification rule as inactive.
There were only two rules, both disabled junk, and none of the six defaults.

    AttributeError: 'Organization' object has no attribute 'members'

The related name on `User.organization` is **`users`**, not `members`. Three
places used the wrong one, and all three failed silently:

**`notifications/defaults.py`** — `ensure_default_rules` threw on every run since
25 September. No default rule was ever created, so **no alert has ever been
sent**: not low balance, not a cap being reached, not a buyer missing calls. The
detection worked and the delivery worked; there was nothing in between.

**`tasks.py`** — `process_auto_recharge` threw after taking payment from Stripe,
so an account could be charged and never credited.

**`billing/api.py`** — the Stripe webhook, same failure. This is precisely the
case the logging added in CH-026 was written for: *"customer pays, exception, no
credit applied, no record"*. That logging would have caught it; this is why it
was happening.

All three now use `users`.

### Why it stayed hidden

Every one sat inside a `try` that logged or swallowed. The code ran, something
was written to a log nobody read, and the feature simply never worked. Nothing
reported an error to a person.

The endpoint audit found it in one pass because it asks a different question:
not "does this run" but "does this produce anything". A rule list that is always
empty, a field always blank — that is the signature, and it is the same signature
as the Cost column, the caller profile and the workspace activity target.

---

## CH-046 — Invoices counted fees as calls

The endpoint audit showed every invoice at zero. Three exist — June, July and
August — and those are **correct**: charging only went live on 18 September, so
there was nothing to bill.

The real fault is in what the September invoice would have produced tomorrow.

    total_calls = Count('id', filter=Q(transaction_type='charge'))

Calls and fees share `transaction_type='charge'`. `charge_fee` writes the same
type as `charge_call`, so the $49.99 monthly portal fee and every $20 number
purchase were being counted as calls on the invoice.

Only a call carries a `call_sid`. The invoice now separates them:

    total_calls    charges with a call_sid
    call_charges   what the usage cost
    fee_charges    portal and number fees
    total_revenue  both, which is what the client was billed

Same distinction that nearly caused the portal fee to be refunded during the rate
correction. `transaction_type` alone does not tell a call from a fee, and this is
the second place that has mattered.

---

## CH-047 — Destination cap counters all showed today's number

**Date:** 2026-09-30
**Files:** `buyers/destinations_api.py`, `scripts/diagnose_destinations_buyers.py`

### Problem

`format_destination` built one query, `rec_q`, that already carried the day (or
requested date range) filter, and then added the hour, month and all-time
filters on top of it:

    rec_q &= Q(created_at__gte=today_start)     # the day filter, inside rec_q
    ...
    hourly_count  = CallLog.objects.filter(rec_q, created_at__gte=hour_ago)
    monthly_count = CallLog.objects.filter(rec_q, created_at__gte=month_start)
    global_count  = CallLog.objects.filter(rec_q)

`month_start` is earlier than `today_start`, so adding it narrows nothing —
`monthly_count` was today's calls. `global_count` had no extra filter at all, so
it was also today's calls. Four labels in the interface — hourly, daily, monthly
and all-time usage against each cap — were the same number.

The monthly and global caps are therefore unreadable in the UI: a destination
with a 5,000/month cap showed 24 used on a day it had 24 calls, whatever it had
actually done that month.

### Fix

`dest_q` (organization plus the destination's TFN, no dates) is now separate from
`range_q` (the requested range, defaulting to today). Each counter composes the
one filter it needs.

### Not fixed here, and why

This function is display only — it is called by `/api/destinations/` and by the
analytics snapshot, never by the routing engine, which enforces caps with its own
queries. Nothing in this change can affect call routing.

Two things it does **not** explain, and a read-only diagnostic
(`scripts/diagnose_destinations_buyers.py`) is there to settle them before
anything else is changed:

  - the audit saw `callsToday` zero on a day with 24 calls, which the counter
    bug above does not cause. The match is `destination_number == d.tfn`, an
    exact string compare, so a formatting difference between what routing writes
    and what the destination holds would produce exactly this.
  - every buyer came back with an empty `phone_number` and a zero
    `payout_amount`. Both fields *are* in the API response, so this is the data,
    not the serializer — either they were never filled in, or the payout really
    lives on the campaign.

Also noted, not changed: `routing/asterisk_handler.py` resolves the live
destination with `Destination.objects.filter(buyer=..., enabled=True)
.order_by('-created_at').first()` — the newest destination for that buyer, not
the one the routing engine picked. A buyer with two enabled destinations has all
its calls attributed to one of them. That is inside the call path and stays
untouched.

---

## CH-048 — Activity log recorded logins and nothing else

**Date:** 2026-09-30
**Files:** `accounts/current_request.py` (new), `accounts/activity_signals.py` (new),
`accounts/apps.py`, `accounts/models.py`, `accounts/migrations/0010_activitylog_record_actions.py`,
`config/settings.py`

### Problem

`/api/accounts/workspace/activity` has never shown anything but logins.

The reader was repaired earlier (commit `0c67ca5c`) so that an entry names the
record it refers to. That was necessary and not sufficient: there was nothing to
name. Every `ActivityLog.objects.create(...)` in the codebase is in
`accounts/services.py`, and they cover only account events — login, logout,
password change, MFA, API keys, profile. Creating a buyer, editing a campaign,
deleting a destination, buying a number: none of it wrote a line.

The action enum made it worse than an omission. It had no value that *could*
describe a record change, so there was no way to write one without changing the
model first.

An audit page that shows logins is not an audit page. Nobody can answer who
disabled a destination or changed a payout, which for a platform that moves
money between buyers and publishers is the question it exists to answer.

### Fix

Written against the models, not the endpoints — 40-odd create, update and delete
endpoints would each need a line, and the 41st would not get one.

**`accounts/current_request.py`** — middleware holding the request in a
`contextvars.ContextVar` so a model signal can tell who is acting. A context
variable rather than a thread local because this runs under daphne: a sync view
is handed a thread from a pool, and a thread local can be read by whichever
request borrows that thread next.

The API authenticates with a bearer token, so Django Ninja puts the user on
`request.auth` while `request.user` stays anonymous; admin and the session views
are the other way round. Both are checked.

**`accounts/activity_signals.py`** — `post_save` and `post_delete` handlers for
the eight models a person edits: Buyer, Destination, Publisher, Campaign,
RoutingRule, PhoneNumber, NotificationRule, User.

Each receiver is bound to its own `sender`. A receiver registered without one is
called for **every** save the platform makes, which here means every CallLog
write on the call path — thousands a day, each entering a handler to look up a
dictionary and leave. Bound per model, a call never enters this code.

Three new actions: `record_created`, `record_updated`, `record_deleted`. The
record itself goes in `metadata` as `target_type`, `target_id`, `target_name`,
which is exactly what the reader already surfaces.

### Deliberately quiet in three places

  - **no actor, no entry.** A Celery task, a management command or the rule
    seeding has no person behind it. An entry with no actor is worse than none
    on a page whose only question is who did this.
  - **`last_login` saves are skipped.** Every login saves the user to stamp it,
    and that already has its own `login` entry. Logging the save as well would
    double every login in the feed.
  - **a failed write never takes the save with it.** The handler swallows and
    logs its own failures. An audit line is worth having; it is not worth losing
    the change it was describing.

### Deploy

Needs `migrate` and a restart of web *and* the workers — the middleware and the
`AppConfig.ready()` hook are both load-time. The migration is a `choices` change
and emits no SQL on PostgreSQL; it exists so the migration state matches the
model.

---

## CH-049 — Closing out the audit list: what was real and what was not

**Date:** 2026-09-30
**Files:** `scripts/verify_activity_log.py` (new), `scripts/diagnose_destinations_buyers.py` (new),
`scripts/diagnose_phone_numbers.py` (new)

The endpoint audit produced seven items. Four were real and are fixed. Three
were the audit's own blind spot, and saying so is the point of this entry — the
alternative is changing working code to make a report look better.

### The audit's blind spot

It flags a field that is empty on **every row**. That is the right signal for a
dead or hardcoded field, and it is how the Cost column, the caller profile and
the empty `target_name` were caught. But it cannot tell the difference between:

  - a field nothing can ever fill, and
  - a field nobody has filled in yet

and with a small number of rows the second is far more likely than the first.

### Destinations showing zero calls today — not a bug

101 of 102 destinations have `enabled=False`. The audit asked for `page_size=5`,
destinations are ordered newest first, so it got five disabled destinations that
have never taken a call. Zero was the correct answer.

The one enabled destination, `+18779641530`, reported **24 calls today** against
the 24 the workspace actually had, with revenue on 23 of them. The match is an
exact string compare on `destination_number`, and it works: routing writes the
TFN in the same format the destination holds it.

A real bug was found in the same function while checking this — see CH-047.

### Buyers with no payout or phone number — not a bug in the API

`phone_number` and `payout_amount` are both in the response. All 42 buyers have
an empty `phone_number`, and 38 of 42 have a zero `payout_amount`. The payout
lives on the campaign instead (C-11, C-02, C-03, C-05 at $1.00, 23 JUNE at
$0.45), so a buyer column showing $0.00 is accurate and is the wrong field for
the interface to show.

`Buyer.phone_number` is used in one functional place: `routing/engine.py` returns
`auction.winner.phone_number` as the destination for an RTB call. Every buyer's
is empty, so an RTB call would route to an empty string. **All five campaigns are
`routing_type='priority'`**, so that path never runs today. It is a landmine for
whenever someone switches a campaign to RTB, not a live fault, and the fix is to
fill the field — not to touch the routing engine.

### Phone numbers with 17 empty fields — not a bug

There is **one** phone number in the system. "Empty on every row" was computed
across a single row. Of the 17, `cap_enabled`, `daily_cap`, `monthly_cap`,
`concurrency_*`, `vendor_enabled`, `traffic_source_*` and `sms_enabled` are
settings that are switched off, and read empty because that is what off looks
like. `state` is empty because the number is toll-free, and a toll-free number
has no state.

It is attached to both a campaign and a publisher, which is what decides whether
a number earns anything.

Two mislabels, neither affecting behaviour: `vendor` says `Twilio` while
`twilio_sid` is empty — the number arrives over the SIP carrier, not through
Twilio's API, which is consistent with how calls actually reach the platform —
and `number_type` says `local` on a toll-free number.

`renews_at` is worth noting for later: it is written only from what the request
sends, and **nothing in the codebase reads it**. The Renews column can only ever
be blank. Not fixed, because with one number it costs more than it returns.

### Verifying rather than asserting

`scripts/verify_activity_log.py` exists because the log query straight after the
CH-048 deploy showed only September entries — nothing had been edited since the
restart, so it proved nothing in either direction. Twice before, something was
reported working on the strength of it compiling.

It makes a change as a real authenticated actor, reads the entry back, and checks
the actor, record name, type and IP on it; confirms a change with nobody behind
it writes nothing; and confirms no activity-log receiver is bound to `CallLog`,
which is the one thing that would put that code on the call path. It runs inside
a transaction that is rolled back, and the last two checks confirm nothing
survives.

All checks pass.

### Still open, and not mine to close

  - **79 calls in 7 days have no `destination_number`** — 14% of 545. They never
    reached a buyer. Being looked at next.
  - **duplicate TFNs across buyers.** `+18553752923` belongs to Q08, R48 and CRM;
    seven more numbers are on two buyers each. Calls are attributed by
    `destination_number`, so one call to a shared TFN counts for every buyer
    holding it. Dormant while they are disabled, wrong the moment one is enabled.
  - **junk in the buyer table:** `xczxczxcxz`, a second `RNY` with no
    destinations, and `Q16` with no destinations.
  - **`routing/asterisk_handler.py`** resolves the live destination with
    `order_by('-created_at').first()` — the newest destination for that buyer,
    not the one the engine picked. Inside the call path; untouched.

### The 177 calls with no destination — closed

Cross-tabbed rather than inferred. `Campaign cap reached` accounts for 133 of
them, on exactly two days:

    2026-09-28    79
    2026-09-23    54

79 + 54 = 133, and no cap-reached call sits on any other day. The campaign cap
was hit twice in September and 133 calls were turned away. That is the system
doing what it was configured to do — and it is a business number, not a
technical one. Either the cap is below what the publisher is sending, or the
traffic is above what the buyer agreed to take. Nobody decided to refuse that
revenue; a setting did.

The remaining 44 are historical. All 30 of the `completed` ones carry buyer
ADC11, campaign `23 JUNE`, a duration and revenue — 8 September and earlier,
none since. So the attribution and the money are correct and only the
`destination_number` column is blank.

**Not backfilling it, deliberately.** ADC11 has exactly one destination today, so
filling those 30 rows with `+18779641530` would look obviously right. It would
also be an assertion I cannot prove: I do not know that destination existed when
those calls ran, and if it did not, I would be writing a destination the call
never used into the permanent record.

That is precisely what the frontend has been doing with Cost, `grossProfit` and
the `$499 Growth` plan — inventing a plausible value because a column looked
empty. The cost of leaving it blank is that 30 calls from early September are
missing from one destination's all-time count. The cost of guessing is a call
log that says something untrue and cannot be told apart from one that does not.
Blank is the smaller cost.

Nothing in the call path was touched to reach this conclusion; it is all reads.

---

## CH-050 — Buyer and destination data made correct, and kept correct

**Date:** 2026-10-01
**Files:** `buyers/models.py`, `buyers/destination.py`, `buyers/services.py`,
`buyers/destinations_api.py`, `buyers/sync.py` (new), `buyers/apps.py`,
`buyers/migrations/0011_buyer_data_integrity.py`,
`buyers/migrations/0012_unique_buyer_name.py`,
`buyers/migrations/0013_disable_duplicate_live_destinations.py`,
`buyers/migrations/0014_unique_enabled_destination_tfn.py`,
`phone_numbers/services.py`, `phone_numbers/migrations/0006_backfill_renews_at.py`

Four faults, all found on 30 September, all of them data rather than code. A
script would have cleaned each one up once and none of them would have stayed
clean, because the interface that created them is still there. Each is fixed in
three parts: repair what exists, stop it recurring, and give a clear message
when somebody tries.

### Duplicate TFNs across buyers

`+18553752923` belonged to Q08, R48 **and** CRM. Seven more numbers were on two
buyers each.

Calls are attributed by an exact match on `destination_number`, so one call to a
shared number is counted for every destination holding it: each of those buyers
sees the call, each cap counts it, and the totals stop adding up to the traffic
that arrived. It did no harm only because 101 of the 102 destinations are
switched off — and became wrong the moment anyone enabled a second.

  - a partial unique constraint on `(organization, tfn)` **where enabled** —
    the disabled rows are somebody's record of numbers used before and are kept
  - any enabled duplicates found are switched off, newest first, so the oldest
    keeps the traffic and the rest stay visible to be corrected
  - create and update now answer with *"+1855… is already live on Q08. Two live
    destinations cannot share a number — every call to it would be counted for
    both."* instead of an IntegrityError 500

### Two buyers called RNY

Any report naming one of them was ambiguous. Unique on `(organization, name)`,
enforced in the database so it cannot return through the UI, the API, the admin
or a script — the places a check in any one of them would miss. Create and
rename both say so in words first.

### Junk rows

`xczxczxcxz`, a second `RNY` and `Q16`. Removed, but **only** where a buyer has
no calls, no destinations and no campaign assignments — it has never been part
of anything. A row with history is kept however odd its name; a name is not
worth losing a record over. A duplicate that survives on those grounds is
renamed with a visible suffix rather than deleted.

### Every buyer's phone number empty

All 42 had `phone_number=''`. That field is not decoration: `routing/engine.py`
returns `auction.winner.phone_number` as the destination for an RTB call, so an
empty one routes an RTB call to nothing. Every campaign is on `priority`, which
is exactly why it would have stayed hidden until the first time someone switched
one to RTB and the calls quietly went nowhere.

The migration fills what is already there. `buyers/sync.py` is what stops it
drifting again — add a destination, change its number, delete it, and the
buyer's number follows. Three rules, each of which matters:

  - **only when empty or stale.** A number typed in by hand is never
    overwritten; it is replaced only when it still holds the destination's
    previous value, which means this wrote it in the first place.
  - **only when unambiguous.** A buyer with two enabled destinations has no
    single number. Guessing sends calls to the wrong company, so nothing is
    written.
  - **never from the call path.** It runs when a destination is saved — a person
    editing a record — and is bound to `Destination` alone.

**The routing engine is not changed.** The fix is to fill the field it reads.

### Numbers with no renewal date

`renews_at` was written only from what the purchase request sends, and the
interface does not send it, so it was null on every number and **nothing read
it**. The Renews column could only ever be blank and no reminder could be built.

A number renews monthly from the day it was bought, so the date is derivable
rather than guessed: purchases now set it, and the migration fills the existing
ones by stepping forward a month at a time until the date is in the future —
giving the next renewal, not one in the past. The API now also returns
`renews_in_days`, which is what a person actually wants from it.


### Addendum — why this is four migrations and not two

The first attempt put the cleanup and its constraint in one migration each. Both
failed on the first `ALTER TABLE`:

    cannot ALTER TABLE "buyers" because it has pending trigger events

Deleting a buyer cascades to its caps and campaign links, which queues deferred
foreign-key trigger events on the table. PostgreSQL will not alter a table that
has them outstanding, and they only settle at the end of the transaction — which
is also when the constraint was trying to be added.

Django wrapped the whole migration in one transaction, so the failure took the
cleanup down with it: the deletions printed to the console and were then rolled
back. Nothing was applied and no data was lost, which is the behaviour you want,
but the output reads as though work was done.

Split so each migration is its own transaction, and the triggers have settled
before the constraint is added:

    0011  remove junk buyers, rename duplicates, fill phone numbers
    0012  unique (organization, name) on buyers
    0013  switch off destinations live on a shared number
    0014  unique (organization, tfn) on enabled destinations

The data migration and the constraint that protects it are adjacent and
dependent, so they still apply together or not at all — just in four
transactions rather than one.

---

## CH-051 — Partner permissions and report columns: a backend for the toggles

**Date:** 2026-10-01
**Files:** `accounts/partner_permissions.py` (new), `accounts/partner_settings_api.py` (new),
`accounts/permissions.py`, `accounts/services.py`, `analytics/api.py`,
`publishers/models.py`, `buyers/models.py`, `config/api.py`,
`publishers/migrations/0002_partner_permissions.py`,
`buyers/migrations/0015_partner_permissions.py`,
`scripts/verify_partner_permissions.py` (new)

### Problem

The publisher settings page offers five permission toggles and eight reporting
checkboxes. The page says so itself, in a banner nobody should have had to
write:

> **Preview — these settings save to this browser only.** Member invites,
> permissions, reporting visibility, and the cap toggle don't yet round-trip to
> the server.

They lived in `localStorage`. They survived a refresh on that machine, which is
what made them convincing, and meant nothing anywhere else: two admins looking
at the same publisher saw different settings, and no toggle gated anything at
all. A publisher with "Audio Recording" switched off could still fetch
recordings, and one with "Download Reports" off could still download the whole
call log.

Invites and the cap had endpoints already (CH-0xx, and `PATCH /{id}/cap`). The
permissions and the reporting visibility had nothing — not an endpoint, not a
column, not a field on the model. That part was mine, not the frontend's.

### Built on the capability system, not beside it

A toggle now maps to a capability that `require()` already enforces on every
endpoint that calls it. Nothing has to remember to consult a second system.

    Manage Traffic      EDIT
    Number Creation     CREATE
    Audio Recording     RECORDINGS   (new)
    Block Numbers       BLOCK_NUMBERS (new)
    Download Reports    EXPORT       (new)

`capabilities_for()` gains one branch: a login whose role is `buyer` or
`publisher` gets its capabilities from the toggles on its own record rather than
from the role table. That is the thing a role alone cannot express — one
publisher allowed to buy numbers and another not.

Row scoping is untouched and still applies on top. A partner with every toggle
on still only ever sees its own calls.

### The catalogue is served, not hardcoded

`GET /api/accounts/partner-permissions` returns every permission and report
column the backend actually supports, with the labels and descriptions.

The frontend kept its own list. That is precisely how a toggle ends up on screen
with nothing behind it: nobody can tell a setting that saves from one that does
not until somebody checks, and nobody checked for months. A toggle should exist
because the backend has one.

An unknown key sent to the save endpoint is **refused**, not ignored. Silently
dropping it would recreate the same fault from the other direction — the
interface believing it saved something it did not.

### What a login is told about itself

`/api/accounts/me` now returns `capabilities`, `is_scoped`,
`visible_report_columns`, `partner_type`, `partner_id` and `partner_name`.

The interface was deciding what to show from the role name alone, so it rendered
controls that then returned 403. Now it can render what the login actually
holds.

### Endpoints

    GET   /api/accounts/partner-permissions
    GET   /api/accounts/{buyer|publisher}/{id}/settings
    PATCH /api/accounts/{buyer|publisher}/{id}/settings

The read returns `has_been_set`, so the interface can tell "nobody has decided"
from "somebody switched everything off". They look identical otherwise and only
one of them is worth asking about.

Saving requires `MEMBERS`, not `EDIT`: changing what another company's login may
do is a member-management decision, not an edit to a record.

### Buyers get the same fields

A buyer login is the same kind of thing — an outside company with an account
inside this workspace. The settings page treats them alike, so the storage does
too, rather than waiting for the buyer version of this bug to be found
separately.

### Defaults chosen at the safe end

Both columns start empty, meaning "nobody has decided" rather than "nothing is
allowed", so no existing partner login changes behaviour on deploy. The defaults
are read-only plus their own reports. `revenue` is **off** by default: a
publisher is paid a payout and showing it what the call was sold for hands it
the margin.

### Verified rather than asserted

`scripts/verify_partner_permissions.py` creates a throwaway publisher and login,
switches toggles on and off, and checks the capability follows — then calls the
real export endpoint and confirms it returns **403** with the toggle off and
**200** with it on. It also checks an unknown permission is dropped, that
columns come back in catalogue order rather than the order they were sent, and
that staff are unaffected.

It runs in a transaction that is rolled back, and the last two checks confirm
nothing survives.

---

## CH-052 — One live destination per buyer, fixed without touching routing

**Date:** 2026-10-01
**Files:** `buyers/destination.py`, `buyers/destinations_api.py`,
`buyers/migrations/0016_one_enabled_destination_per_buyer.py`,
`buyers/migrations/0017_unique_live_destination_per_buyer.py`

**No file under `routing/` is changed by this. `git status routing/` is empty
and `routing/asterisk_handler.py` line 149 is untouched.**

### Problem

`routing/asterisk_handler.py` resolves which number a buyer's call goes to:

    live_dest = Destination.objects.filter(buyer=buyer, enabled=True) \
        .only('tfn').order_by('-created_at').first()

A buyer with two enabled destinations has every call sent to whichever was
created last. The other is dead: the interface shows it as active, it never
rings, and nothing anywhere says so. Attribution, caps and reporting all point
at one destination while the client believes traffic is split across two.

### Why this is not fixed in the handler

The obvious change is to make the handler use the destination the routing engine
actually selected. That is a change to the file that routes live calls, and
calls have already been lost twice this week to changes near it.

The same fault disappears from the other side. The handler takes `.first()` of
the buyer's enabled destinations — if a buyer can only ever have **one**, that
lookup is correct by construction. There is one row to find, so "the newest" and
"the right one" are the same row.

This is not a new restriction. It is the rule the platform has always followed
without saying so; the interface simply let you create a second one and believe
it worked.

### What was done

  - **0016** leaves each buyer with one enabled destination, **keeping the
    newest**. That is the opposite of the TFN cleanup in 0013, deliberately: the
    newest is the one currently receiving calls. Keeping the oldest would be
    tidier and would silently move live traffic to a different number, which is
    the one thing a migration must not do.
  - **0017** adds a partial unique constraint on `buyer` where `enabled`, so a
    second live destination cannot be created through the UI, the API, the admin
    or a script.
  - the create and update endpoints answer *"ADC11 already routes to
    +18779641530. A buyer can only have one live destination — a second one
    would never receive a call. Switch +18779641530 off first if you want calls
    to go somewhere else."* instead of an IntegrityError 500.

### A side effect worth having

`buyers/sync.py` refuses to write `Buyer.phone_number` when a buyer has more
than one enabled destination, because there is no single number that is
unambiguously theirs. With this constraint there never is more than one, so the
sync now resolves for every buyer that has a live destination rather than
declining on 19 of them.
