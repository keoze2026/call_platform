# Avortyx system map

What each part of the platform does, what it reads and writes, and what must
stay true. The review bot reads this on every pull request (docs/REVIEW-BOT.md),
so a change is judged against the whole app, not only the lines it touches.

Kept in the backend repo (`docs/SYSTEM-MAP.md`); the portal repo has a copy in
the same place. **Update both in the same change** when a flow, a table or a
section changes. Sir's rules are in `BUSINESS-RULES.md`; every past change is
in `CHANGES.md` (quote CH ids).

## 1. The two halves

| | Backend `keoze2026/call_platform` (master) | Portal `keoze2026/Avortyx` (main) |
|---|---|---|
| Stack | Django + django-ninja, Celery worker + beat, Postgres, Redis, Asterisk | Next.js (app router), TypeScript, Zustand stores |
| Runs on | One server, `/opt/call_platform`, Docker Compose (`web` = daphne, `celery_worker`, `celery_beat`, `postgres`, `redis`) | Vercel |
| Goes live | `git pull` puts code on disk, but **daphne and Celery run the old code until restarted**. Restarts happen only at quiet time (0 live calls). Management commands (`docker compose exec web python manage.py ...`) run new code at once. | On every push to main (Vercel). A preview per pull request. |
| Talks through | `/api/...` (ninja, `config/api.py`; list in `docs/api-paths.txt`), WebSocket for live calls, Telegram bots | `lib/api/http.ts`: camelCase in the portal = snake_case on the wire, converted both ways, deeply |

Because the portal goes live at once and the server only after a restart, a
portal change that needs a new endpoint or field **must work, or say so
clearly, against the old server** until the restart (CH-353 shows a warning;
`/api/guard/status` says what is running).

## 2. Portal sections

| Section (portal route) | Portal code | Backend API | Backend code | Main tables |
|---|---|---|---|---|
| Dashboard `/dashboard` | `analytics.service`, calls/buyers/destinations stores | `/api/analytics/dashboard`, `/calls` | `analytics/services.py` `get_dashboard`, `get_call_log` | `call_records` |
| Call Logs `/calls` | `analytics.service` (`allCalls`, 2000 a page) | `/api/analytics/calls` | `get_call_log`, `_live_window` (CH-345) | `call_records` + live `CallLog` rows |
| Reports `/reports` (Call Summary tabs, Call Log, Hourly) | `app/(app)/reports/page.tsx`, `components/reports/*`, `lib/reports-cache.ts` | `/api/analytics/calls`, `/campaigns`, `/publishers`, `/buyers`, `/carriers`, exports | `get_campaign_performance`, `get_publisher_performance`, `get_buyer_performance`, `get_carrier_performance`, `_cost`, `_call_cost`; `report_cache.py` (finished days in Redis 6 h) | `call_records` |
| Campaigns `/campaigns`, `/campaigns/[id]` | campaigns store, `campaign-routing.ts` | `/api/campaigns/`, `/api/routing/` | `campaigns/`, `routing/` | `campaigns`, `routing_rules`, `rule_destinations` |
| Campaign Groups `/campaigns/groups` | `campaign-groups.service`, `components/campaigns/groups/*` | `/api/campaign-groups/` | `campaigns/groups.py`, `groups_api.py` | `campaign_groups`, `rule_destinations` |
| Buyers, Destinations | buyers / destinations stores | `/api/buyers/`, `/api/destinations/` | `buyers/` | `buyers`, `destinations` |
| Publishers | publishers store | `/api/publishers/` | `publishers/` | `publishers` |
| Numbers (TFNs), pools | numbers store | `/api/numbers/`, `/api/carriers/` | `phone_numbers/` | `phone_numbers`, Carrier |
| TFN Check `/tfn-check`, public `/check/[token]` | `tfn-check.service`, `components/tfn-check/*` | `/api/numbers/check/`, `/api/public/tfn-check/` | `phone_numbers/check_api.py`, `tfn_check.py`, `tfn_test.py` | TfnCheckEvent, TfnTest (`tfntest-` call sids) |
| Routing `/routing` | `campaign-routing.ts` | `/api/routing/` | `routing/` | `routing_rules` |
| Billing `/billing` | `billing.service` | `/api/billing/` | `billing/services.py` | `billing_accounts`, `transactions` |
| Owner `/owner`, Carrier Costs | owner pages | `/api/owner/` | `accounts/owner_api.py`, `phone_numbers/owner_costs.py` | all orgs (owner only) |
| Automated Reports | `components/automated-reports/*` | `/api/analytics/reports/`, `/api/team-report/` | `analytics/scheduled_reports*`, `team_report.py` | `team_report_chats` |
| Blocked numbers, VoIP Shield, TCPA Shield | their stores | `/api/spam/` | `spam_protection/` | `spam_shields` |
| Settings, Workspace, roles | auth / workspace | `/api/accounts/` | `accounts/` | `users`, `custom_roles` |
| Live, Queue, IVR | `queue.service`, `ivr.service` | WebSocket, `/api/queue/`, `/api/ivr/` | `live/`, `call_queue/`, `ivr/` | `call_queue`, `ivr_flows` |

Access: publisher and buyer logins see only their own calls (`scope_queryset`
in `analytics/services.py::_base_qs`) and never the cost or another party's
money (`mask_partner_money`); a buyer never sees TTC. Campaign Groups and the
guard are team only.

## 3. A call, end to end

1. Carrier → Asterisk → AGI `route_call.py` → `POST /api/twilio/asterisk/route/` (`routing/asterisk_handler.py`, engine in `routing/engine.py`): campaign by TFN, caller checks (blocked caller = "CallerId Blocked", CH-349), balance guardrail, destination by priority / weight. A `CallLog` row is created (status ringing).
2. Asterisk dials; on hangup `macro-call_end` → `/usr/local/bin/call_ended.sh` → `POST /api/twilio/asterisk/call-ended/` with duration, answered, recording.
3. `settle_call_money` (asterisk_handler): revenue and publisher payout on the `CallLog`, then the balance charge: `BillingService.call_cost` = ceil(minutes) × rate × (1 + markup) + recording fee + VoIP Shield fee, `charge_call` (one CHARGE per call sid, idempotent). The amount is saved on `CallLog.platform_cost` **and** the Reports copy (CH-352).
4. Every `CallLog` save in a final status queues `mirror_call_record` (`routing/signals.py`), which writes the Reports copy `CallRecord` (`call_records`) with the same id. `.update()` fires no signal: anything written with `.update()` on `CallLog` must also be written to `CallRecord`.
5. A refused call: `tasks.charge_rejected_call` takes the rejected-call fee (+ VoIP Shield) and writes both tables.
6. A try that is retried by the sender is folded into the next try (`retry_hidden`, CH-215) and kept out of Reports.

## 4. Money rules (see BUSINESS-RULES.md)

- Cost shown anywhere = what was charged (`platform_cost`); a call never
  charged (before 18 Sep 2026, or the charge failed) shows the estimate; a
  TFN test call (`tfntest-` sid) costs 0.
- A summary row's Cost = charged + estimate of its uncharged answered calls =
  the same calls added up one by one (CH-352). Every tab must agree.
- Payout in Reports = the payout paid on the call (`CallLog.publisher_payout`),
  else the campaign's (CH-250).
- Every balance change writes a `Transaction` with balance before / after.
- Days are Eastern time unless the user picked another zone.

## 5. What must always be true (checked by Avortyx Guard every hour)

`analytics/guard.py`; a difference goes to Telegram.

| Check | Must hold |
|---|---|
| cost | balance charge = `CallLog.platform_cost` = `CallRecord.platform_cost`, per call; one charge per call |
| calls | every ended, not-hidden call has its `CallRecord` |
| payout | `CallRecord.payout` = `CallLog.publisher_payout` when one was paid |
| balance | account balance = its last transaction's balance after |
| stuck | no call ringing / in progress for over 2 hours |
| tfns | every active TFN on a campaign has a carrier |
| tabs | each Campaign / Publisher / Buyer row's calls and Cost = its calls added up |

A new kind of bug gets a new check here, and a test that fails on the old code.

## 6. Change rules

- One change = one CH id, an entry in `CHANGES.md`, tests that fail without it.
- Backend: `python manage.py test --settings=config.test_settings` must pass;
  model changes need their migration; an API change needs
  `python manage.py api_paths --write` (the portal's contract check reads it).
- Portal: no new TypeScript errors (`.github/type-errors-allowed`); every
  `/api/...` it calls must be in `contract/api-paths.txt`
  (`node scripts/check-api-paths.mjs`); every text in all four languages
  (en, ru, ja, zh).
- Never a secret in the code: tokens live in `.env` (server) or Vercel.
- Data-changing commands are dry runs by default, with `--apply`.
- Nothing that restarts the server outside quiet time.
