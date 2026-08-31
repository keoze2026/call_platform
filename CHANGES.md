# Change Log — Call Platform Backend

Running record of every change made during the handover-prep work.
Newest at the bottom. Each change has an ID — quote the ID when discussing one.

## Index

| ID | Date | Area | Summary | Status |
|----|------|------|---------|--------|
| [CH-001](#ch-001) | 2026-08-31 | Phone Numbers | Attach purchased numbers to Twilio SIP trunk | Done — commit `b6e602ce` |
| [CH-002](#ch-002) | 2026-08-31 | Routing | Destination edit/delete/list API endpoints | Done — commit `76b76357` |
| [CH-003](#ch-003) | 2026-08-31 | Phone Numbers | Trunk-attach failure no longer aborts a paid-for purchase | Done — commit `76b76357` |

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
## OPEN-2 — `TWILIO_TRUNK_SID` missing from `.env.example` — RESOLVED

**Priority:** Low — **resolved 2026-08-31**. Template only; the live server was already
configured correctly.
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
