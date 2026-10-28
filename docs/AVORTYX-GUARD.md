# Avortyx Guard

Automatic checks that stop a change reaching the portal until it is proven
not to break anything else, and that watch the live numbers every hour, so
Sir no longer has to check them by hand (CH-354, CH-355; Sir, 29 Oct).

Five layers. Layers 1 to 3 run **before** a change is merged; layers 4 and 5
run **after** it is live. Any difference becomes a fix plus a new test.

| Layer | What | Where it runs | Where it reports |
|---|---|---|---|
| 1. Tests and build | backend tests, migrations, secret scan; portal type check, secret scan | GitHub Actions on every pull request | the pull request (red / green); merge blocked when red |
| 2. Review bot | an impact report against the system map | GitHub Actions on every pull request | a comment on the pull request |
| 3. Portal = server | every API path the portal calls exists; the restart tracker | GitHub Actions; the server | the pull request; a banner for admins |
| 4. Numbers checker | charges, calls, payouts, balances, TFNs, tabs | the server, every hour | Telegram |
| 5. Robot check | logs in and opens the main pages | GitHub Actions, after each deploy and every 6 h | Telegram, with a screenshot |

---

## 1. Every function

### Layer 1: tests on every change

**Backend** (`.github/workflows/tests.yml`, job "backend tests"):

- `makemigrations --check`: fails when a model changed without its migration.
- The whole test suite, on an in-memory database (`config/test_settings.py`;
  no `.env`, Redis or Celery needed): 778 tests today.
- `docs/api-paths.txt` is current (test `ApiPathsTests`): fails when the API
  changed and `python manage.py api_paths --write` was not run.
- Secret scan: fails when a Telegram bot token, AI API key or AWS key is
  in the code.

**Portal** (`.github/workflows/checks.yml`, job "portal checks"):

- No new TypeScript errors: the old ones are counted in
  `.github/type-errors-allowed` (85). A change may lower it, never raise it.
- API contract (layer 3, below).
- Secret scan, as above.

### Layer 2: the review bot

`.github/workflows/review-bot.yml` in both repos. On every pull request
(not drafts) the bot reads `docs/REVIEW-BOT.md`, `docs/SYSTEM-MAP.md` (what every
section reads and writes, the call and money flows, what must stay true) and
the change, searches the code for every other place that uses what changed,
and posts one comment:

```
## Avortyx Guard review
Verdict: Safe to merge / Merge after fixes / Do not merge
Sections touched / Other sections affected / Money and numbers /
Tests / Portal (or Backend) / Fix before merge
```

It only reports what it checked in the code. It does not change code. It
comments with the repo's own GitHub token, so no extra app is installed.

### Layer 3: the portal and the server agree

- **API contract.** The backend writes every path it answers to
  `docs/api-paths.txt` (`python manage.py api_paths`). The portal's
  `scripts/check-api-paths.mjs` finds every `/api/...` the portal calls and
  fails when one is not on the server (renamed, removed, or not built). In
  CI it first fetches the latest list from the backend repo when the
  `BACKEND_REPO_TOKEN` secret is set; otherwise it uses its copy in
  `contract/api-paths.txt`. Known gaps are listed in
  `contract/api-paths-unknown.txt` (two today, see "Found while building").
- **Restart tracker.** `GET /api/guard/status` (team only) says which code
  the server runs (`running`: commit + last CH), which is on disk after
  `git pull` (`on_disk`), and the CH ids waiting for the restart
  (`pending`). The portal shows admins a slim banner while anything is
  pending, or while the server runs older code than the portal needs
  (`lib/server-version.ts`, `PORTAL_NEEDS_SERVER`). It disappears by itself
  after the restart. The daily Telegram summary lists the pending CH ids too.

### Layer 4: the numbers checker

`analytics/guard.py`. Read only: it never changes a row. Calls younger than
15 minutes are left out (their charge and Reports copy are still being
written). One check failing to run never stops the others.

| Check | Must hold | Example of what it catches |
|---|---|---|
| Cost | each ended call's balance charge = call log Cost = Reports Cost; no call charged twice | CH-352: Reports showed $0 for charged calls |
| Calls in Reports | every ended call has its Reports row | a call missing from Reports |
| Payout | Reports payout = the payout paid on the call | CH-250: campaign payout shown instead of the TFN's |
| Balance | each account's balance = its last ledger entry's "balance after" | a balance changed without a ledger entry, or two writes racing |
| Stuck live calls | no call ringing / in progress for over 2 hours | calls that never ended in our records |
| TFNs | every active TFN on a campaign has a carrier | the KMQ numbers added without their carrier |
| Tabs agree (daily) | each Campaign / Publisher / Buyer row's calls and Cost = its calls added up one by one | a tab adding up differently from Dialed or Date |

**When it runs** (`tasks.guard_check`, Celery beat, every hour):

- Today's checks. A new or changed problem is sent to Telegram once; the same
  problem is not repeated every hour; "back to normal" is sent when it clears.
- After 6 AM Eastern, once a day: yesterday in full (tabs too) and a summary,
  sent even when everything passed, with what is waiting for the restart.

**Messages look like this:**

```
Avortyx Guard · Oct 29 ET
🔴 Cost
• 3 calls: Reports shows $0.00, charged $2.70. e.g. 1730..., 1730...
✅ Calls in Reports, Payout, Balance, Stuck live calls, TFNs
```

### Layer 5: the robot check

`scripts/smoke.cjs` in the portal, run by `.github/workflows/smoke.yml` after
every successful production deploy, every 6 hours, and by hand (Actions >
Robot check > Run workflow). It logs in with the robot account and opens
Dashboard, Call Logs, Reports, Campaigns, Campaign Groups, TFN Check,
Numbers, Buyers, Publishers, Destinations and Billing. A page fails when it
shows the error screen, sends the robot back to login, gets a 5xx from the
server, throws in the browser, or takes over 30 s. On a failure: a Telegram
message with a screenshot of the first failing page, and a red run. It only
opens pages; it never clicks Save, Delete or Apply.

---

## 2. Set-up (once)

### On the server (`/opt/call_platform`)

1. Pull the code (no restart): `git pull`.
2. Optional, in `.env` (add with `nano .env`; never paste tokens in chat):
   - `GUARD_CHAT_ID=<team chat id>`: alerts go there. Without it they go to
     the same chat as the server status alerts.
   - `GUARD_SUMMARY_CHAT_ID=<Sir's chat id>`: also receives the daily summary
     only (not every alert).
3. Try it at once, read only: `docker compose exec -T web python manage.py guard_check`
4. At the quiet-time restart (0 live calls, its own step), the hourly check
   and `/api/guard/status` start: `docker compose up -d web celery_worker celery_beat`.

### On GitHub, both repos (`keoze2026/call_platform`, `keoze2026/Avortyx`)

1. **Secrets** (repo > Settings > Secrets and variables > Actions > New secret):

   | Secret | Repo | Value |
   |---|---|---|
   | `REVIEW_BOT_API_KEY` | both | the review bot's AI key (the API key you are given for it) |
   | `PORTAL_URL` | Avortyx | the live portal address, `https://...` |
   | `GUARD_EMAIL`, `GUARD_PASSWORD` | Avortyx | the robot account (below) |
   | `GUARD_REPORTS_PIN` | Avortyx | optional: the robot's Reports PIN |
   | `TELEGRAM_BOT_TOKEN`, `GUARD_CHAT_ID` | Avortyx | the alert bot's token and the team chat id |
   | `BACKEND_REPO_TOKEN` | Avortyx | optional: a fine-grained token with read access to call_platform contents |

2. **Robot account:** in the portal, Workspace > invite a team member, e.g.
   `robot@<your domain>`, with a view-only role, 2FA off. Set its password and
   put it in the secrets above. It only opens pages.
3. **Branch protection**, after the first green run (Actions tab):
   Settings > Branches > Add rule:
   - call_platform, branch `master`: tick "Require a pull request before
     merging" and "Require status checks to pass", check **backend tests**.
   - Avortyx, branch `main`: the same, check **portal checks**.

---

## 3. Daily use

**Making a change** (instead of pushing to master / main):

```powershell
git checkout -b ch-356
git am "$HOME\Downloads\ch356-backend.patch"
git push -u origin ch-356
```

Open the link git prints (or GitHub > Pull requests > New). Within minutes the
pull request shows the checks (green or red) and the review bot's comment.
Merge when green and the review says safe; fix first when it does not. Vercel
deploys the portal on merge; the robot check runs right after.

**Telegram:**

| Message | Meaning | What to do |
|---|---|---|
| 🔴 Cost / Calls / Payout ... | the numbers disagree; the message names the calls | send it to the developer; it is fixed with a test and, if past data is wrong, a dry-run-first command like `sync_call_costs` |
| ✅ back to normal | the problem cleared | nothing |
| Daily check, all passed | yesterday is right | nothing |
| ⏳ Waiting for the server restart: CH-... | code pulled but not running | do the quiet-time restart |
| Robot check failed + screenshot | a page is broken on the live portal | send it to the developer |

**By hand on the server (read only):**

```bash
docker compose exec -T web python manage.py guard_check                       # today so far
docker compose exec -T web python manage.py guard_check --day 2026-10-28 --tabs
docker compose exec -T web python manage.py guard_check --send                # also to Telegram
docker compose exec -T web python manage.py api_paths                         # every API path
```

**Sir:** reads the daily summary; looks only at what is flagged.

---

## 4. Adding a check

A new kind of bug gets a new check in `analytics/guard.py` (a function that
returns a `Finding`, added to `run()`), a test in `analytics/test_guard.py`
that fails on the bad data, a row in `docs/SYSTEM-MAP.md` section 5, and a
line in this file.

## 5. Not covered yet

- A test server (staging) where restarts are tried first: when one of the new
  servers arrives.
- Rules nobody wrote down: a new rule from Sir goes into `BUSINESS-RULES.md`
  the same day, or the guard cannot protect it.

## 6. Found while building

- The portal calls `/api/buyers/{id}/reporting-config` (Buyer settings >
  reporting columns), which the server does not have: saving which columns a
  buyer sees does nothing.
- The portal names `/api/billing/wallets/` for the Capitalist wallet list,
  which was never built (the card already says so).

## Files

| Backend | Portal |
|---|---|
| `analytics/guard.py`, `guard_build.py`, `guard_api.py`, `api_paths.py` | `components/app-shell/server-update-banner.tsx`, `lib/server-version.ts` |
| `analytics/management/commands/guard_check.py`, `api_paths.py` | `scripts/check-api-paths.mjs`, `scripts/smoke.cjs` |
| `tasks.guard_check`, beat `guard-check` (hourly) | `contract/api-paths.txt`, `contract/api-paths-unknown.txt` |
| `config/test_settings.py`, `docs/api-paths.txt` | `.github/type-errors-allowed` |
| `.github/workflows/tests.yml`, `review-bot.yml` | `.github/workflows/checks.yml`, `review-bot.yml`, `smoke.yml` |
| `docs/REVIEW-BOT.md`, `docs/SYSTEM-MAP.md`, `docs/AVORTYX-GUARD.md` | `docs/REVIEW-BOT.md`, `docs/SYSTEM-MAP.md`, `docs/AVORTYX-GUARD.md` (copies) |
| Tests: `analytics/test_guard.py` | `lib/i18n/extra/server-update.ts` |
