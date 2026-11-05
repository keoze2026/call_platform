# Customer Accounts Isolation Plan

Sir approved the plan on 5 Nov 2026. Every point closes with proof by 20 Nov,
in his priority order: **1, 5, 3, 11**, then 2, 4, 6, 7, 8, 9, 10. His addition:
the automated isolation tests grow with each point as it is built (2, 4, 6-10),
not only on 20 Nov.

The live copy of this plan (with tick boxes) is the Docs page "Customer
Accounts Isolation Plan". This file is the same plan in the repository, with
the status as of **5 Nov 2026, 2:40 PM EST**.

## How every step runs

1. Read-only check on live data first (no change).
2. Patch on a temporary branch with its own tests; the full test suite must pass.
3. Rehearsal for any database change on a copy of live (time taken, counts
   before and after). Since 5 Nov: the rehearsal runs the migration through
   Django, not only psql (see CH-581 fix).
4. Push to GitHub from the PC. Claude never pushes.
5. Deploy on Server B after calling hours only (Sir's rule).
6. Proof (command output) to Sir, times in EST.

**Never `git pull` on Server B outside a deploy.** Code with a new database
column, pulled before its migration, breaks every process that restarts
(5 Nov, 1:31-1:39 PM EST: the portal errored until 0022/0023 ran).

## Timeline and status

| Order | Point | Dates (EST) | Change IDs | Status |
|---|---|---|---|---|
| 1 | 1. Tenant isolation | 5-7 Nov | CH-578, CH-580 | In progress |
| 2 | 5. TFNs kept separate | 8-9 Nov | CH-581 (+ fix) | Live; proof to Sir pending |
| 3 | 3. Rates and internal data hidden | 10 Nov | CH-582 | Draft (local, CI not run) |
| 4 | 11. Isolation tests (then every deploy) | 11-12 Nov | next free ID | Not started |
| 5 | 2. Fresh portal + 4. Header, search, stats | 13 Nov | next free ID | Not started |
| 6 | 6. Calls and routing | 14-15 Nov | next free ID | Not started |
| 7 | 7. Notifications and bot | 16 Nov | next free ID | Not started |
| 8 | 8. Recordings and files | 17-18 Nov | next free ID | Not started |
| 9 | 9. IDs and branding + 10. Access and audit | 19 Nov | next free IDs | Not started |
| 10 | Final isolation run + demo to Sir | 20 Nov | - | Not started |

Change IDs are shared with the rest of the team's work: take the next free
number on both repositories (up to CH-608 used on 5 Nov).

## Point 1 - Tenant isolation

Every row belongs to exactly one account, and the database itself refuses to
show a row to any other account.

**1a. Inventory (done, 5 Nov).** Three accounts: Avortyx (slug `interns`, our
main account) holds everything (52 users, 17 campaigns, 82 buyers, 174 TFNs,
31,903 calls); texora and Keoze have 1 user each and nothing else; 1 user has
no account (hans@gmail.com). *Sir to decide:* which extra accounts stay, and
hans@gmail.com.

**1b. CH-578 (live, 5 Nov 9:08 AM EST).** The account column on the 36 child
tables, filled from the parent and required; filled on every save. Proof to
Sir: in the one Point 1 message.

**1c. CH-580, database row-level security (with the other developer).**
- Restricted database login `avx_portal` (no superuser, no bypass), used by
  `web` only (`PORTAL_DATABASE_URL`, `AVX_SERVICE=web`).
- Policy `avx_account` on every account table; carrier rate table for the
  main account only. `python manage.py rls --check` must print 0.
- Patch: the latest `backend-CH-580.patch`, rebuilt to apply after CH-581 and
  its fix (full suite with all three: 1,360 tests OK).
- Left: rehearsal B on Server B (`/root/rehB.sh`: whole suite on PostgreSQL
  with and without the rules; fix anything failing only with the rules), push,
  deploy after hours (backup, `scripts/portal_db_role.sh`, migrate,
  `rls --check`, recreate `web` only), rollback test (remove
  `PORTAL_DATABASE_URL`, recreate `web`).

**1d. Proof:** `rls --prove`; `pg_stat_activity` shows `avx_portal`; then one
message to Sir for Point 1.

## Point 5 - TFNs kept separate (CH-581)

- Pools: `main` (ours, locked), `inventory` (a customer's), `quarantine`
  (released by a customer, `TFN_QUARANTINE_DAYS`, default 30).
- Database trigger `avx_tfn_pool_guard` refuses: our TFN in another account,
  a main TFN leaving the main pool, a TFN changing account outside quarantine.
- **CH-581 fix:** migration 0023 first failed in the rehearsal ("IndexError:
  tuple index out of range": `%` in the trigger SQL); messages now use `||`.
- **Live since 5 Nov ~1:39 PM EST** (0022 and 0023 applied; trigger present;
  0 errors since).
- Left: `docker compose run --rm -T --no-deps web python manage.py tfn_pools
  --prove` on live as the proof (174 main; both moves refused), then proof to
  Sir. *Sir to decide:* quarantine days.

## Point 3 - Rates and internal data hidden

Draft on a local branch (`ch582`): `is_main_account`, `MainAccountAuth`;
Owner, White Label, KYC (Trust Engine) and AI Audit refused to customer
accounts; profile flag `is_main_account`. Left: full CI, the portal part
(menus and route guard), patch, rehearsal, deploy, proof.

## Point 11 - Isolation tests

Two test customer accounts (Test A, Test B); an API sweep of every endpoint
and a page sweep as Test A, Test B and the main account; any other account's
ID, name, number or price in a response fails. Runs before every deploy.
*Sir to confirm:* the test accounts live on the real system, marked as test,
kept out of reports and billing.

## Points 2, 4, 6-10 (13-19 Nov)

- **2 + 4:** a new account starts empty with default settings; header,
  search, dashboards and Live Monitor show only its own data.
- **6:** calls route only to the customer's destinations; failover never
  crosses accounts; per-customer concurrency limit.
- **7:** alerts per account; customers connect their own bot.
- **8:** storage path per account; signed, expiring recording links.
- **9 + 10:** random IDs; no Avortyx names or TFNs in customer pages,
  exports or emails; customer owner and staff roles; Sir can view any account,
  logged; audit log per account.

Each adds its own cases to the isolation sweep in the same patch.

## Related, outside the plan (5 Nov)

- CH-594 (frontend): publishers no longer see the buyer, destination name or
  number; buyers no longer see the publisher.
- CH-605 (frontend + backend): buyers no longer see the called number,
  campaign or publisher, on screen or in the Excel; publishers' Reports file
  drops buyer and destination.
- CH-607 (frontend + backend): buyers no longer see Repeated, on screen or in
  the Excel. Open for Sir: the buyer Telegram report still offers Repeated,
  Called Number, Campaign and Publisher as optional columns.
- Backend CH-605 and CH-607 need `git pull` + `docker compose restart web`
  on Server B after calling hours.

## Risks and rollback

- Row-level security (1c) is the riskiest step. A missed case shows empty
  data, never another account's. Rollback: one `.env` line.
- Every database change is rehearsed on a copy of live before the real run.
- Calls are never touched mid-day; routing changes (point 6) deploy only
  after calling hours, with a test call before and after.
