# Hand-over - where everything stands

**Read this first.** Status as of **9 Nov 2026, 5:10 AM EST** (1:10 PM
Nairobi). One page for all open work. Details of the customer accounts work
(points 8, 10, 11 with their commands) stay in
[CUSTOMER-ACCOUNTS-PLAN.md](CUSTOMER-ACCOUNTS-PLAN.md), section "Where
everything stands".

Change IDs are shared by both repositories. Last used: **CH-644** (this
file). Next free: **CH-645**.

---

## 1. How the work is done

- **Claude never pushes.** Build on a branch, `git format-patch -1 --stdout`,
  send the file. The user pushes from a PC.
- **The scp line comes first, every time** (from the PC that downloaded it;
  use the newest file if the browser saved a "(1)" copy):

      scp "$env:USERPROFILE\Downloads\<file>.patch" root@13.140.236.153:/tmp/

- **Full test suite on Server B before any backend push** (throwaway clone,
  live untouched). Wait for the last line: `OK`.

      rm -rf /tmp/ciNNN && git clone -q /opt/call_platform /tmp/ciNNN && cd /tmp/ciNNN
      git config user.name "ci" && git config user.email "ci@localhost"
      git fetch -q "$(git -C /opt/call_platform remote get-url origin)" master && git checkout -q FETCH_HEAD
      git am /tmp/<file>.patch && git log --oneline -1
      cd /opt/call_platform && docker compose run --rm --no-deps -T -v /tmp/ciNNN:/ci -w /ci web python manage.py test --settings=config.test_settings 2>&1 | tail -5

  A new API address must be listed in `docs/api-paths.txt`
  (`manage.py api_paths --write`) or `analytics.test_guard` fails.
- **Push** (the PC's own folders - see section 2):

      cd <backend folder>; git pull; git am "$env:USERPROFILE\Downloads\<patch>"; git log --oneline -1; git push

- **Deploy on Server B after calling hours only**, test call before and after
  anything that touches calls: `cd /opt/call_platform && git pull`, migrations
  if any, restart only what the change needs (`web`; `celery_worker` for
  alerts/reports; `web_calls` only after hours). A changed `.env` value read
  at container start needs `docker compose up -d <service>`; settings read by
  `manage.py` commands (new process each run) take effect on the next run.
- **Database changes**: rehearsal on a copy of live first
  (`rehearse-632.sh` pattern).
- **Frontend** working-tree files and its CHANGES.md are CRLF; keep each
  file's own line endings (check `file <path>` before and after an edit - a
  whole-file line-ending change makes every line show as changed). Backend
  files are LF.
- **Sir's rules**: never turn anything on or off on our own; nothing that
  enters the system may disappear; every fix reported with proof; no deploys
  during calling hours; "don't generate a patch if you don't understand";
  never blame TFNs (or anything) until it is proven.
- **Messages to Sir**: short, true, in a code block, times in EST, no
  questions he has already answered, no patch numbers unless asked.
- **No secrets in chat, ever** (keys, tokens, passwords, the recording link
  secret). Commands that check keys print names only:
  `grep -oE "^(NAME1|NAME2)=." .env | cut -d= -f1`.

## 2. Machines and folders

| Where | What |
|---|---|
| Server A `avx-calls` 13.140.236.148 (private 10.20.0.1) | Asterisk, Kamailio, nginx for rec.v0l1.com. No docker. Code at CH-577 (`0d9cf4f6`), behind master - safe to pull (CUSTOMER-ACCOUNTS-PLAN A2). Contabo VDS XL: 16 threads, 62 GB RAM, 464 GB disk |
| Server B `avx-web` 13.140.236.153 (private 10.20.0.2) | `/opt/call_platform`, docker compose: web, web_calls, celery_worker, celery_beat, postgres, redis, pgbouncer. Host crons: `voice_check.py --loop 55`, `ai_final.py --loop 55`, `host_watch.py`. Contabo VDS XXL: 24 threads, 94 GB RAM, 697 GB disk |
| Server A -> B | ssh only through `/usr/local/bin/avx-from-a` (mark_late_answers, mark_late_answers_check, recordings_to_mp3) |
| PC "Hidden" | `C:\Users\Hidden\koeze\call_platform` (master), `C:\Users\Hidden\koeze\Avortyx` (main) |
| PC "Dynasoft" | `C:\Users\Dynasoft\Online_Job\call_platform` (master), `C:\Users\Dynasoft\Online_Job\Avortyx` (main) |

Code backup (one zip, both repos, committed files only - no `.env`,
`node_modules`, `public/videos`), Hidden PC (swap the folders on Dynasoft):

    $d="$env:TEMP\avx_code"; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory $d | Out-Null
    cd C:\Users\Hidden\koeze\call_platform; git checkout master; git pull; git archive --format=zip -o "$d\b.zip" HEAD
    cd C:\Users\Hidden\koeze\Avortyx; git checkout main; git pull; git archive --format=zip -o "$d\f.zip" HEAD -- . ":(exclude)public/videos"
    Expand-Archive "$d\b.zip" "$d\call_platform"; Expand-Archive "$d\f.zip" "$d\Avortyx"
    Compress-Archive -Path "$d\call_platform","$d\Avortyx" -DestinationPath "C:\Users\Hidden\Downloads\avortyx_code.zip" -Force; Remove-Item $d -Recurse -Force

## 3. Changes since 7 Nov and their state

| ID | Repo | What | State |
|---|---|---|---|
| CH-638 | backend | Customer accounts hand-over in CUSTOMER-ACCOUNTS-PLAN.md | Pushed |
| CH-639 | frontend | TFNs expired pop-up in Sir's design (dark card, gold border, "Add replacements", "View all") | Pushed, live |
| CH-640 | frontend | The pop-up stays closed after X (remembered per login and batch in the browser; a new batch shows again) | Pushed. Live proof (X, refresh, stays closed; bell keeps the numbers) not yet sent |
| CH-641 | backend | TFNs expired notice: what is retired Fri 12 PM - Mon morning stays in the bell until Mon 12 PM EST; weekdays unchanged (Sir, 7 Nov) | Pushed (`1ed0229`) before the Server B test result came back. **Confirm the test output is OK and that it was deployed** (`docker compose restart web`) |
| CH-642 | frontend | Reports, Publisher tab: campaign names next to publishers come from the report's own calls, not today's Phone Numbers list | Pushed, live, proven (6 Nov report shows H-04, H-09, H-05, L-13) |
| CH-643 | backend + frontend | AI Audit: Correct / Wrong also on Valid and "Caller spoke, no issue" calls; accuracy % counts them; new "missed" count (Valid marked No Voice); the LLM's examples include them, mistakes first | Patches sent 9 Nov. **Not yet tested / pushed / deployed** - see 4.1 |
| CH-644 | backend | This file | - |

## 4. Open work, in order

### 4.1 AI audit accuracy (Sir: "I need 100% accuracy")

What we found (data, 6-8 Nov, Server B):

- The AI job is live: `ai_final.py` runs every minute; Claude
  (`claude-haiku-5-5`) with AssemblyAI transcripts gave the final label on
  1,501 calls in 3 days (11 errors). Keys `ANTHROPIC_API_KEY`,
  `ASSEMBLYAI_API_KEY` are set.
- 3,841 calls checked in 3 days: valid 2,675, agent_only 449, dead_caller
  252, caller_ok 230, robocall 184, ivr 23, agent_beep 19, mixed 9.
- Only **4** calls were marked Correct / Wrong in the portal in 30 days. The
  audit team's 50-70% ratings (Audit - Group A, 8 Nov) are not recorded, so
  the AI had nothing to learn from.
- Their complaints are misses (No Voice called Valid) - which could not be
  marked before CH-643.
- Claude read only **14%** of Valid calls (380 of 2,640): a Valid call with
  15 s or more of caller speech skips it (`AI_FINAL_SKIP_CALLER_SECONDS`,
  default 15). The Silero speech counter **undercounts** the caller (0-6 s
  on real multi-minute conversations); Claude reading the words corrects it.
  Echo was suspected and ruled out.
- Possible label mismatch (not proven): Sir's own note puts "agent picks up
  and says thanks for calling" under Dead Caller; the AI calls that Agent
  Only. Only the team's real examples can settle it.

Steps left:

1. CH-643: scp, tests on Server B (`/tmp/ci643`), push both repos, deploy
   (`git pull && docker compose restart web`), screenshot the AI Audit page
   (Valid calls with buttons) for Sir.
2. Claude on every call - only with Sir's OK (cost about 7x more AI calls):

       cd /opt/call_platform && grep -q "^AI_FINAL_SKIP_CALLER_SECONDS=" .env && sed -i 's/^AI_FINAL_SKIP_CALLER_SECONDS=.*/AI_FINAL_SKIP_CALLER_SECONDS=0/' .env || echo "AI_FINAL_SKIP_CALLER_SECONDS=0" >> .env
       docker compose exec -T web python manage.py shell -c "from django.conf import settings; print('skip seconds:', settings.AI_FINAL_SKIP_CALLER_SECONDS)"

   Expect `0.0`. Check the AssemblyAI and Anthropic usage pages after a day.
   Optional, Sir's call: `AI_LLM_MODEL` = a stronger Claude model.
3. Audit team marks every call they check in the AI Audit page and sends 10
   missed calls (caller number + time + right label). Check one call
   (read-only):

       N=<caller number>
       cd /opt/call_platform && docker compose exec -T -e N=$N web python manage.py shell <<'EOF'
       import os; from datetime import timedelta; from zoneinfo import ZoneInfo
       from django.utils import timezone; from routing.models import CallLog
       ET=ZoneInfo('America/New_York'); n=os.environ['N'][-10:]
       for c in CallLog.objects.filter(caller_number__endswith=n, created_at__gte=timezone.now()-timedelta(days=7)).order_by('-created_at')[:5]:
           print(c.created_at.astimezone(ET).strftime('%d %b %I:%M %p EST'), c.campaign and c.campaign.name, c.publisher and c.publisher.name, c.duration, 's')
           print(' AI:', c.ai_label, '| rule:', c.ai_rule_label, '| conf:', c.ai_confidence, '| by:', c.ai_final_by or 'rule only')
           print(' caller s:', c.voice_caller_seconds, '| agent s:', c.voice_agent_seconds, '| first caller:', c.voice_caller_first, '| beep:', c.voice_beep_seconds, '| sides:', c.voice_channels)
           print(' summary:', c.ai_summary); print(' transcript:', (c.ai_transcript or '')[:400]); print(' audit:', c.ai_review_label or '-', c.ai_review_note)
       EOF

4. With the verdicts in: compare accuracy and "missed" on the AI Audit page;
   tune only from real examples.

### 4.2 Clients portal project (Sir, 9 Nov: "start working on clients portal")

Plan sent to Sir 9 Nov. Goal: a client signs up, pays, sets up and runs
calls on their own, never seeing our data or internal sections. Five layers:

1. Isolation - done (customer accounts points 1-10); point 8 final step and
   the demo left.
2. Guided client journey: sign up -> verify email -> KYC -> top up -> first
   TFN -> first campaign -> first call; each step unlocks the next; nothing
   goes live without KYC and balance. (Register, KYC, Getting Started,
   Billing pages exist.)
3. Client-only menu: Dashboard, Live, Reports, Calls, Campaigns, Numbers,
   Buyers, Publishers, Routing, Billing, Settings. Never Owner, Carrier
   Costs, Company Profit, Trust Engine, AI Audit (already refused
   server-side, CH-619).
4. Money: wallet at the top, per-minute and per-TFN usage charges, invoices,
   low-balance email/Telegram, pause at zero only after live calls end.
   (Stripe and Capitalist keys set; owner credit/debit/suspend and fees form
   exist.)
5. Sir's control room: every client's status, balance, usage, revenue;
   suspend/activate; view any account (logged, point 10); alerts for new
   signups, KYC waiting, low balance.

Timeline: 9-10 Nov walk the full journey on the test client account
(texora) and list every gap with screenshots; 10-13 Nov layers 2-3;
13-17 Nov layer 4; 17-19 Nov layer 5 + point 8; **20 Nov demo**: a new
client from sign-up to a live test call, plus the isolation proofs. Same
rules as customer accounts: check live first, tests, rehearsal, after-hours
deploy, proof; the isolation sweep grows with every new page.

### 4.3 Standby server (AWS)

- Sir approved **$496.31/month** (7 Nov): EC2 **c7i.4xlarge**, US East (Ohio),
  1-year Compute Savings Plan, no upfront ($376.66) + EBS gp3 1,000 GB, 3,000
  IOPS, 125 MBps ($80) + 400 GB data out for an 8-hour failover at 600 calls
  ($36) + 1 public IPv4 ($3.65). 12 months $5,955.72. Estimate:
  https://calculator.aws/#/estimate?id=ffe9d935169f4a75a4ad975de6c1a84c08d4ca70
- The standby is **one machine for everything**: calls (Asterisk, Kamailio),
  portal, database, Redis, workers. Today that work runs on 40 threads
  (A 16 + B 24); c7i.4xlarge has 16 vCPU / 32 GB, c7i.8xlarge 32 / 64 GB.
  RAM used today: A 1.6 GB + B 7.9 GB. Real peak so far: 74 calls at once.
- Sir's rule: run the **600-call test before buying the plan**; if CPU stays
  above 60%, re-quote c7i.8xlarge (same Compute plan still applies, about
  double the server line). Same test on Server A for 1 hour. Report CPU,
  RAM, packet loss, jitter, dropped calls. Existing tooling:
  [LOADTEST.md](LOADTEST.md) (600-call test, CH-465 / CH-469).
- Before the plan is bought AWS bills on-demand ($0.714/h server; $640.87 a
  month if left a full month). Cheapest test: set up on a small size
  (c7i.large, about $2/day), switch to c7i.4xlarge only for the test day
  (stop, change type, start), total under about $30. Sent to Sir 8 Nov.
- Accounts: each server on its own email/account (Sir). Sir shares the new
  email on Monday 9 Nov. Root user with MFA; region us-east-2.
- The platform's call limit is 400 (`/status` "0 / 400"); for 500-600 calls
  it must be raised on the standby (and on Server A if it takes that load).
- Contabo today: Server B XXL EUR 254.14/month, Server A XL EUR 175.04/month
  (Server A moved to a 12-month term). Server B 12-month quote: USD 274.04/month.

### 4.4 Status bot

- Works automatically (checked 7 Nov): each server's `host_watch.py` sends an
  alert within 1 minute of a change (critical ones repeat every 15 min until
  resolved); portal checks (`status_watch`, celery) every 15 min (red repeats
  hourly, yellow every 12 h). Alerts go to the Notifications topic; SSH
  logins and portal changes to Non Priority. The full report is sent only on
  `/status` - asked Sir whether it should also go on a timer (no answer yet).
- Server A: 0 alerts waiting, bot token and chat set. Server B's own watcher
  state not yet checked (same command, on Server B):

      python3 -c "import json,time;s=json.load(open('/var/lib/avortyx/host_watch_state.json'));print('NOT SENT (waiting):',len(s.get('pending') or []));print('still open:',list((s.get('ongoing') or {}).keys()));[print(time.strftime('%d %b %H:%M',time.localtime(e[0])),e[1],'RED' if e[2] else '') for e in s.get('events',[])[-10:]]"

- Not set up (grey on `/status`): outside reachability monitor, Contabo API,
  backup server / failover (the standby), carrier balance (`/balance <amount>`),
  subscriptions file (`run/subscriptions.json`).
- To confirm with the user: the SSH login to Server A from a new IP on
  7 Nov 10:05 AM EST.

### 4.5 Customer accounts leftovers

See CUSTOMER-ACCOUNTS-PLAN.md: A1-A4 (Point 8: watch unsigned plays, pull
Server A, copy the link secret by hand, nginx enforcement after hours,
account folders on), B (Point 10 live proof: owner opens texora's fees form),
C (Point 11: green "Isolation screens" run, "Robot check" red-step log,
20 Nov demo), D (CH-580 row-level security with the other developer; Server
A bulk-download alert waits for a grep).

### 4.6 6 Nov call report problems (from the Excel, not fixed)

364 converted connected calls with $0 revenue (HOZ 144, Q08 119, BHS 60, Q50
33, Q28 8); summary sheets disagree with the call log (revenue 1,372 vs
1,284; payout 1,730.25 vs 1,636.35 - the 93 "No Destination" calls carry
money in the summary); 75 "Missed With Conversion" 0 s calls paid; 377
repeated calls paid; TTC shown on unanswered calls; 3 calls of exactly
7,200 s; odd buyer/destination name pairs (A49 -> N2/W3, R48 -> R-xxxx,
Q16 -> CGS_AGS); 3 numbers under two buyers. Payout rules for Missed With
Conversion and repeated calls are Sir's decision.

### 4.7 Data accuracy standard (Sir, 9 sections)

Plan and timeline drafted (9-27 Nov), priority order of sections 1, 2, 4, 3;
code needed for most sections. Not started.

## 5. Facts worth knowing

- **TFN expiry**: the nightly rule (1:30 AM EST) retires TFNs over the daily
  answered-calls limit. A campaign whose every TFN was retired shows
  **Incomplete**, at the bottom, with no publisher (7 Nov: H-04/DS-01,
  H-05/DS-02, H-09/YY-02). Adding replacement TFNs with the publisher brings
  it back. Proof query (read-only) lists `released` numbers with
  `retired_reason` and the last call's campaign/publisher.
- **Balances to watch**: daily - XOLO and Freedom (KMQ) carriers (calls stop);
  weekly - IPQualityScore, Telnyx (caller line-type lookups), RealValidito,
  AssemblyAI, Backblaze B2; monthly - Twilio (key set, SMS not in use),
  Contabo, domains (avortyx.io, v0l1.com), AWS. Stripe and Capitalist are
  customer payments; CoinGate is not set.
- **Recording disk**: split WAVs (caller/agent) are about 1.92 MB per
  call-minute and are deleted after 12 h; 600 calls for 8 h is about 550 GB.
- **AWS prices used** (Ohio): c7i.4xlarge $0.714/h on-demand, $376.66/month
  on a 1-year Compute Savings Plan (no upfront), $344.79 on an EC2 Instance
  Savings Plan; gp3 $0.08/GB-month; data out $0.09/GB; public IPv4
  $0.005/h.
