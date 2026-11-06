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
| 1 | 1. Tenant isolation | 5-7 Nov | CH-578, CH-580 | Checked on live 6 Nov: a customer login saw 0 of our data, opened 0 of our records (tenant_check). CH-580 (database rules) with the other developer |
| 2 | 5. TFNs kept separate | 8-9 Nov | CH-581 (+ fix) | Done: proven live 6 Nov (174 main; both moves refused) |
| 3 | 3. Rates and internal data hidden | 10 Nov | CH-619 | Done: live 6 Nov, proven (customer login refused on all 6 sections) |
| 4 | 11. Isolation tests (then every deploy) | 11-12 Nov | CH-623 | Built 6 Nov: test database only, runs with the suite before every deploy |
| 5 | 2. Fresh portal + 4. Header, search, stats | 13 Nov | next free ID | Not started |
| 6 | 6. Calls and routing | 14-15 Nov | CH-631, CH-632, next free ID | CH-631 live 6 Nov (routing ignores a link into another account). CH-632 built: the database refuses such links (rehearsal, then live after calling hours). Left: per-customer concurrency limit |
| 7 | 7. Notifications and bot | 16 Nov | CH-633, next free ID | CH-633 built: our bot commands, AI Telegram messages and the morning report stay ours. Left: customers' own bot |
| 8 | 8. Recordings and files | 17-18 Nov | CH-634, next free ID | CH-634 built: every recording link signed for its holder, valid while the holder has an account (Sir, 7 Nov). Then: Server A nginx checks links; customer accounts' own storage folder |
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
hans@gmail.com. **6 Nov:** hans@gmail.com (an agent login with no account, no
data) sees nothing and is left as it is - nothing switched off on our own.

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

CH-619 (backend + frontend, 6 Nov): `is_main_account`, `MainAccountAuth`;
the whole Owner router (Owner, Carrier Costs, Company Profit, statements),
White Label admin, KYC (Trust Engine) and AI Audit refused to customer
accounts, any role; profile flag `is_main_account`; the portal hides those
menus and sends their pages back to the Dashboard. No database change. Left:
CI on Server B, push, deploy (restart web), proof.

## Point 11 - Isolation tests

Two test customer accounts (Test A, Test B); an API sweep of every endpoint
and a page sweep as Test A, Test B and the main account; any other account's
ID, name, number or price in a response fails. Runs before every deploy.
**Decided 6 Nov (CH-623):** the test accounts exist only in the test
database (`accounts/test_isolation.py`), created and thrown away on every run
of the suite - never on the live system, so calls, reports and billing are
never touched. On live, after each deploy, the read-only `tenant_check` (a
real customer login, nothing saved).

## Points 2, 4, 6-10 (13-19 Nov)

- **2 + 4:** a new account starts empty with default settings; header,
  search, dashboards and Live Monitor show only its own data.
- **6:** calls route only to the customer's destinations; failover never
  crosses accounts; per-customer concurrency limit.
  CH-631 (code) and CH-632 (database triggers, `same_account --prove`) cover
  the first two; the concurrency limit is next.
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
