# Customer Accounts Isolation Plan

Sir approved the plan on 5 Nov 2026. Every point closes with proof by 20 Nov,
in his priority order: **1, 5, 3, 11**, then 2, 4, 6, 7, 8, 9, 10. His addition:
the automated isolation tests grow with each point as it is built (2, 4, 6-10),
not only on 20 Nov.

The live copy of this plan (with tick boxes) is the Docs page "Customer
Accounts Isolation Plan". This file is the same plan in the repository, with
the status as of **6 Nov 2026, 7:30 PM EST**.

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

Status as of **6 Nov 2026, 7:30 PM EST** (7 Nov 3:30 AM Nairobi). The full
hand-over - what is left, in what order, with the commands - is the section
**"Where everything stands"** at the end of this file.

| # | Point | Change IDs | Status |
|---|---|---|---|
| 1 | Tenant isolation | CH-578, CH-580 | Done and proven on live (tenant_check: a customer login saw 0 of our data, opened 0 of our records). **Not live: CH-580, row-level security in the database - with the other developer.** 7 activity-log rows of hans@gmail.com (no account) left as they are |
| 2 | Fresh portal | - | Proven on live 6 Nov (fresh_account_check): texora and keoze hold only their login, setup token, default rules and billing rows; 0 alerts with our data. No change needed |
| 3 | Rates and internal data hidden | CH-619 | Done, proven on live (customer login refused on all 6 owner sections; owner opens them) |
| 4 | Header, search and stats scoped | CH-630 | Proven on live (tenant_check covers every page incl. header figures and search); Live Monitor connection scrubbed (CH-630) |
| 5 | TFNs kept separate | CH-581 (+ fix) | Done, proven on live 6 Nov |
| 6 | Calls and routing | CH-631, CH-632 | Done, proven on live 6 Nov (`same_account --prove`: 16 as expected, 0 problems). Concurrency: Sir, 6 Nov - no per-customer limit ("as much as cx wants"; think of 1000 CC overall past 20 customers) |
| 7 | Notifications and bot | CH-633 | Done, live 7 Nov: bot /status and /balance answer our admins only; AI Telegram messages and the morning report are ours only. Brand stays Avortyx (Sir), so customers use our bot with their own data; their own bot only if Sir asks |
| 8 | Recordings | CH-634, CH-635, CH-646 | **Done, live 9 Nov.** Signed links (7 Nov); Server A enforces them (unsigned 403, signed 200, forged 403); account folders ON (both test files 200 through the B2 fallback). See A below |
| 9 | Random IDs, nothing internal | CH-636 | Done, live 7 Nov. Checked on live (`scripts/point9_check.py`): pages, downloads, emails - nothing of ours. Brand stays Avortyx (Sir, 7 Nov) |
| 10 | Roles and audit | CH-637 | Live 7 Nov. Roles already per account. Owner views/changes logged in our log and the account's own (shown as "Avortyx"). **Left: the live proof row** (owner opens texora's fees form) |
| 11 | Isolation tests + demo | CH-623, CH-624, CH-625, CH-627-629 | API read/write sweeps run with every test run (Server B CI and GitHub). **Left: a green "Isolation screens" run on GitHub; the 20 Nov demo** |

Change IDs are shared with the rest of the team's work: take the next free
number on both repositories. Last used: **CH-646** (9 Nov).
Next free: **CH-647**.

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

## Where everything stands (hand-over, 6 Nov 2026, 7:30 PM EST)

**All open work (AI audit, clients portal, standby server, status bot,
report problems) is in [HANDOVER.md](HANDOVER.md) - read that first.** This
section keeps the customer accounts steps.

Read this first if you pick the work up. Points 1-7 and 9 are done and
proven on live. Points 8, 10 and 11 have the steps below left, in this
order. Every step names where it runs. Nothing here is urgent during calling
hours: all of it is after calling hours unless it says "any time".

### A. Point 8 - recordings (DONE 9 Nov 2026)

Done on 9 Nov, in this order (times EST):

- A1: unsigned plays since signing were the copier's own canary check and
  portal tabs opened before 6 Nov 6:27 PM (all 127 recordings were of calls
  before signing). Nothing hands out unsigned links.
- A2: Server A pulled to CH-645; RECORDING_LINK_SECRET copied B -> A from the
  PC through ssh (the value never shown). PowerShell added a CR to the line:
  removed with `sed -i '/^RECORDING_LINK_SECRET=/s/\r$//'`; checked by
  matching sha256 of the line on both servers.
- A3 (~7:19 AM): steps 1-4 of deploy/recording_links_nginx.conf. Backup
  /root/rec.v0l1.com.bak-20261109-131906. Proof: unsigned 403, signed 200,
  forged 403.
- A4 (~7:37 AM): avx-from-a got `recording_folders` (backup in /root);
  Server A `.env` RECORDING_FOLDERS=1 and the copier cron line given
  MANAGE= (backups /root/env-A.bak-20261109-133739,
  /root/crontab-A.bak-20261109-133739); `recordings_to_b2.py --nginx`.
  Proof: both canaries (top and folder) 200.
- CH-646 (~7:47 AM): the check moved to the private link (Cloudflare refused
  the copier's Python check). Copier's 7:50 AM run: both canaries 200.
- The old server 156.67.25.167 (kept as a backup, user 9 Nov) still runs its
  own copier with old code: its unsigned canary check now gets 403. Harmless;
  left as it is.

The original steps, kept for reference:

What is live: every recording link the platform hands out is signed for the
login (or buyer) it was given to (`?a=<holder>&s=<signature>`,
`routing/recording_links.py`), and `/api/recordings/check` answers 204/403.
RECORDING_LINK_SECRET is set in Server B's `.env` (made on the server on
7 Nov; never printed, never in chat). Server A's nginx does NOT check links
yet - every link still plays, signed or not.

**A1. Watch (any time, from 7 Nov evening EST).** Server A. Signing went on
6 Nov ~6:27 PM EST. Count plays since then; old unsigned links (in Excel
files and Telegram messages sent before) show up as "unsigned":

    grep -h 'GET /recordings/[A-Za-z0-9_.-]*\.\(mp3\|wav\)' /var/log/nginx/access.log \
      | awk '{ if ($7 ~ /[?&]s=/) s++; else u++ } END { print "signed:", s+0, " unsigned:", u+0 }'
    grep -h 'GET /recordings/[A-Za-z0-9_.-]*\.\(mp3\|wav\)' /var/log/nginx/access.log \
      | grep -v '[?&]s=' | awk -F'"' '{print $4 " | " $6}' | sort | uniq -c | sort -rn | head

The second line shows where unsigned plays come from (referer | browser).
Decide with the user whether those old links may stop (Sir's rule: a link
works while its holder has an account; links handed out before 6 Nov carry no
holder).

**A2. Server A code (any time).** Server A's `/opt/call_platform` is a clean
git checkout at CH-577 (`0d9cf4f6`), 29 commits behind. Reviewed 7 Nov: the
missing commits change on Server A only scripts - `ai_final/`, `voice_check/`
(neither runs on Server A), `host_watch.py` (Telegram auto-delete kind only),
`recordings_to_b2.py` (CH-634/635: signed canary check, folders - both off
until their settings exist) and new files. Safe to pull:

    cd /opt/call_platform && git pull && git log --oneline -1

Then put the same `RECORDING_LINK_SECRET=...` line in Server A's
`/opt/call_platform/.env`: the user copies it from Server B's `.env` to
Server A's in their own terminals - never through chat. (Server A's ssh key
to Server B can only run the gatekeeper, so it cannot fetch it.) Check:
`grep -c '^RECORDING_LINK_SECRET=' /opt/call_platform/.env` prints 1 on both.

**A3. nginx checks every link (after calling hours).** Server A, exactly as
`deploy/recording_links_nginx.conf` steps 1-4 (cache zone, the
`/_recording_check` location, `auth_request` + `auth_request_set` lines in
the recordings location, `nginx -t && systemctl reload nginx`). Test call
before and after. Proof:

    curl -s -o /dev/null -w '%{http_code}\n' https://rec.v0l1.com/recordings/b2-canary.mp3          # 401/403 (unsigned)
    # then play a recording in Reports: it plays (signed link)
    python3 /opt/call_platform/scripts/recordings_to_b2.py --status                                    # unchanged

Undo: remove the two auth_request lines, reload nginx.

**A4. Account folders on (after A3 works).** `deploy/recording_links_nginx.conf`
step 5: (a) Server B: add the `recording_folders)` case to
`/usr/local/bin/avx-from-a` before `*) no ;;`; (b) Server A:
`RECORDING_FOLDERS=1` in `.env`, and add the same `MANAGE="ssh -i
/root/.ssh/to_b -o BatchMode=yes -o ConnectTimeout=5 root@10.20.0.2"` the
mp3 cron line has to the `recordings_to_b2.py --delete-local-days 7` cron
line; (c) Server A: `python3 scripts/recordings_to_b2.py --nginx` (writes the
folder-aware rewrite, uploads the folder test file, nginx -t, reload). Check:
`python3 scripts/recordings_to_b2.py --status` and the next run's log line in
`/var/log/recordings_to_b2.log`. Customers have no calls yet, so nothing moves.

Known, harmless: recording `8d2d131e-0857-4401-87aa-140d65ded95e` is 0 s long
and is kept as WAV on every mp3 run ("length mismatch 0s vs 1s").

### B. Point 10 - the live proof

Someone with the Owner section (a superuser of our main account - Sir) opens
**texora's fees form** on the Owner page (opening the list alone calls no
per-account address). Then, Server B:

    docker compose exec -T web python manage.py shell -c "
    from accounts.models import ActivityLog
    from zoneinfo import ZoneInfo
    for r in ActivityLog.objects.filter(action__in=['owner_viewed','owner_changed']).select_related('organization').order_by('-created_at')[:4]:
        print(r.created_at.astimezone(ZoneInfo('America/New_York')).strftime('%I:%M %p EST'), '| log of:', r.organization.slug if r.organization else '-', '|', r.action, '|', r.metadata.get('owner_action'), '| account:', r.metadata.get('target_name'))
    "

Expected: two rows, `log of: interns` and `log of: texora`, both
`owner_viewed | opened the account`. Open question for the user: also log the
Owner LIST view (our log only)?

### C. Point 11 - tests and demo

- GitHub "Isolation screens" (frontend repo, CH-625): needs one green run
  after CH-628/629. The run's log or screenshot is the proof.
- GitHub "Robot check" (frontend): red on every run; needs the log of its red
  step before anything is changed.
- 20 Nov demo to Sir: the live proofs already gathered (tenant_check,
  fresh_account_check, `same_account --prove`, `recording_links --prove`,
  `scripts/point9_check.py`, the Point 10 rows) plus the GitHub runs.

### D. Not ours to finish

- CH-580 (Point 1, row-level security in PostgreSQL): with the other
  developer.
- Server A bulk recording-download alert (`scripts/host_watch.py`, recordings
  per IP per hour): Server B (13.140.236.153) fetches recordings for the AI
  checks; waiting for the user's grep output before the watcher is changed.

### How the work is done (keep to it)

- **Never push.** Patches: build on a branch, `git format-patch -1`, send the
  file. The user copies it to Server B from the PC (always give this first):
  `scp "$env:USERPROFILE\Downloads\<file>.patch" root@13.140.236.153:/tmp/`
- **Full tests on Server B before any push** (a throwaway clone, live
  untouched):

      rm -rf /tmp/ciNNN && git clone -q /opt/call_platform /tmp/ciNNN && cd /tmp/ciNNN
      git config user.name "ci" && git config user.email "ci@localhost"
      git fetch -q "$(git -C /opt/call_platform remote get-url origin)" master && git checkout -q FETCH_HEAD
      git am /tmp/<file>.patch && git log --oneline -1
      cd /opt/call_platform && docker compose run --rm --no-deps -T -v /tmp/ciNNN:/ci -w /ci web python manage.py test --settings=config.test_settings 2>&1 | tail -5

  Last result: **1,448 tests OK** (skipped 21) at CH-637. A new API address
  must be added to `docs/api-paths.txt` (`manage.py api_paths --write`) or
  `analytics.test_guard` fails.
- **The user pushes** from the PC (`git pull; git am ...; git push` in
  `C:\Users\Hidden\koeze\call_platform`), then deploys on Server B:
  `git pull`, migrations if any, restart only what the change needs
  (`web`; `celery_worker` for alerts/reports; `web_calls` only after calling
  hours with a test call before and after). A changed `.env` needs
  `docker compose up -d <service>`, not `restart`.
- A database change gets a rehearsal on a copy of live first (CH-632's
  `rehearse-632.sh` is the pattern: copy, migrate, prove, undo, re-apply,
  drop the copy).
- Sir's rules: never turn anything on or off on our own; nothing that enters
  the system may disappear; every fix reported with proof; no deploys during
  calling hours. Messages to Sir: short, true, times in EST, no patch numbers
  unless asked.
- No secrets in chat, ever (tokens, passwords, keys, the link secret).
- Frontend repo files and its CHANGES.md are CRLF; backend files are LF.

### Servers and where things are

- Server A `avx-calls` (13.140.236.148): Asterisk, nginx for
  `rec.v0l1.com` (recordings: local disk, then Backblaze B2 bucket
  `avortyx-recordings`), cron: `recordings_to_mp3.sh` (*/5),
  `recordings_to_b2.py --delete-local-days 7` (*/10), `--nginx` (03:17 daily
  token renewal). No docker. Talks to Server B over ssh as
  `root@10.20.0.2` - only the commands `/usr/local/bin/avx-from-a` allows
  (mark_late_answers, recordings_to_mp3; recording_folders after A4).
- Server B `avx-web` (13.140.236.153), `/opt/call_platform`: docker compose
  `web` (portal API, 127.0.0.1:8000), `web_calls`, `celery_worker`,
  `celery_beat`, `postgres`, `redis`. Main account: slug `interns`
  (MAIN_ORGANIZATION_SLUG). Customer accounts on live: texora, keoze.
- Read-only live checks: `scripts/exposure_check.py`,
  `scripts/point9_check.py` (run with `manage.py shell <`),
  `manage.py same_account`, `manage.py recording_links`,
  `manage.py recording_folders`.
