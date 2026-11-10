# Avortyx handover: where everything stands (for ZDFRS)

Updated 10 Nov 2026, 12:45 PM Nairobi (4:45 AM EST). Replaces the 1:45 AM version.

---

## 1. Servers

| Server | Address | State |
|---|---|---|
| **Server A** `avx-calls` (calls) | `13.140.236.148`, private `10.20.0.1` | Live. Changed on 10 Nov for the move only: firewall allows the private link from `147.93.7.36`; WireGuard endpoint `147.93.7.36:51820`; nginx `rec.v0l1.com` allows the new IP and IPv6 (backup `/root/rec.v0l1.com.bak-move`). Asterisk/Kamailio not restarted |
| **Server B** `avx-web2` (portal, API, database, AI) | **`147.93.7.36`**, private `10.20.0.2` | **Live since 10 Nov 1:23 AM Nairobi.** Contabo VDS XXL, 2-year plan, 24 cores, 94 GB, 697 GB |
| Old Server B `avx-web` | `13.140.236.153` | **Stopped** (app, cron, private link off). Keep until 17 Nov, then cancel. Never start it |
| Old server | `156.67.25.167` | Old backup, untouched |

**From the PCs** (both have the shortcuts and keep-alive):
- `ssh b` = new Server B, `ssh a` = Server A, `ssh oldb` = old B
- Copy files with **`avx-web:/tmp/`** (not `b:` - Windows reads `b:` as a drive):
  `scp "$env:USERPROFILE\Downloads\<file>" avx-web:/tmp/`

Cloudflare: `avortyx.io`, `b-api.avortyx.io`, `www.avortyx.io` -> `147.93.7.36` (Proxied). `app` / `b-app` = Vercel, unchanged.

---

## 2. Code (everything is pushed and deployed)

| Repo | Head | Last changes |
|---|---|---|
| Backend `call_platform` master | `e1c08a38` | CH-652 handover, CH-653 isolation re-check scripts, CH-654 support chat pictures, CH-580 database row-level security, CH-655 per-customer call limit, CH-656 AI comment translation |
| Frontend `Avortyx` main | `0ad13c6` | CH-654 support chat pictures, CH-655 call limit field (Owner), CH-656 AI Audit fully translated |

**Next free change ID: CH-657.** Migrations applied on 10 Nov: `support/0003`, `accounts/0017`.

**How we work:** Claude never pushes; patches are applied with `git am` on the PC and pushed. Backend: full test suite on Server B in a throw-away clone first (`OK` before push), CHANGES.md entry always. No deploys in calling hours; `web_calls`, postgres, redis or a server restart only after hours with a test call before and after. Give every new patch version a new file name (`-v2`, `-v3`): Windows renames repeat downloads and the old file gets copied by mistake.

---

## 3. Database row-level security (CH-580): live, how to watch it

- The portal (`web`) connects as **`avx_portal`** (restricted; sees only the logged-in account's rows). Routing (`web_calls`), Celery and `manage.py` keep the main login - calls are not affected.
- Proof on 10 Nov: `rls --check` 0 tables missing; `rls --prove` 79 tables, 0 problems; Robot check #227 green after the switch.
- Backup taken before: `/root/before-ch580-20261110-0214.dump` (101 MB).
- **Watch for errors** (first hours of traffic):
  ```
  cd /opt/call_platform && docker compose logs --since 2h web 2>&1 | grep -iE "row-level|permission denied|InFailedSql|Traceback" | tail -5
  ```
- **Way back** (portal back to the main login in seconds, nothing lost):
  ```
  cd /opt/call_platform && sed -i 's/^PORTAL_DATABASE_URL=/#PORTAL_DATABASE_URL=/' .env && docker compose up -d web
  ```
  Back on: `sed -i 's/^#PORTAL_DATABASE_URL=/PORTAL_DATABASE_URL=/' .env && docker compose up -d web`

---

## 4. Customer accounts (client isolation): all 11 points done

- Status document for Sir (summary, proof per point, how to check it, final run, test customer login): **Customer Accounts: Separation Status** - https://claude.ai/artifact/VUWmtVoeTN5Xe8wBWQiJVD
- Word plan updated to 10 Nov: `Customer_Accounts_Isolation_Plan_10Nov.docx`.
- Point 6: per-customer call limit (Owner -> account -> Edit Rates & Fees -> "Live calls at once (limit)", 0 = no limit). **Not yet in Server A's backup routing** (applies whenever Server B routes).
- Point 7: customers use our bot, see only their own data.
- hans@gmail.com (old test login, no account) **deactivated**, nothing deleted.
- TFN quarantine: **30 days** (confirmed on live).
- Test customer (demo account, holds nothing of ours): login in the status document; change its password or suspend it after demos.
- **Re-run the full proof** (read-only, any time, after any deploy), on Server B:
  ```
  cd /opt/call_platform && git pull -q
  docker compose exec -T web python manage.py shell < scripts/isolation_recheck.py 2>&1 | grep -E "^OK|^FAIL|RESULT"
  docker compose exec -T web python manage.py shell < scripts/point9_check.py 2>&1 | grep "POINT 9 TOTAL"
  docker compose exec -T web python manage.py same_account --prove 2>&1 | tail -1
  docker compose exec -T web python manage.py recording_links --prove 2>&1 | tail -1
  docker compose exec -T web python manage.py rls --check; docker compose exec -T web python manage.py rls --prove 2>&1 | tail -1
  ```
  Expected: `RESULT: ALL OK`, `0 items of ours`, `0 problems`, `0 problems`, `0` and `problems: 0`. Then GitHub (Avortyx repo -> Actions): Robot check and Isolation screens green.

---

## 5. Other things done on 10 Nov

- **Support chat pictures** (CH-654): paste or attach in the "Need help?" chat, up to 2 MB (PNG/JPG/GIF/WEBP); goes to the Telegram support group as a photo; the team answers with a photo captioned `<8 characters of the chat> <text>`.
- **AI Audit fully translated** (CH-656): times, units, AI/EST/IVR/Telegram/Excel/TFN words, and the AI's own comments (translated once by the audit LLM, kept 90 days, English on hover). Excel column headings stay English.
- **AI accuracy**: only 4 calls marked in the portal (all correct); the audit group's 50-70% is an estimate. Each auditor asked to mark 25 calls (15 No Voice + 10 Valid) on the AI Audit page. Accuracy SQL is in the chat history (per day, AI vs team, per auditor, missed No Voice).
- **Alerts after the move**: "KMQ went silent" (quiet evening plus the routing test), "Backup Routing Active" / "Routing Disruption" (the routing test), "Delivery rate 88%" (24h spam rejections, unrelated).

---

## 6. Open - do these

- [ ] **This morning:** first answered calls get AI labels on the AI Audit page ("Pending AI check" goes down).
- [ ] **This morning:** the RLS error watch above shows nothing.
- [ ] **Failover drill - redo after hours** (the first try did not record a call routed by Server A): new B `docker compose stop web_calls` -> test call -> Server A `python3 /opt/call_platform/scripts/fallback/fallback_sync.py --status` must go from **8 to 9** and "waiting for B: 1" -> new B `docker compose start web_calls` -> waiting 0. If it stays 8, start `web_calls` at once and investigate.
- [ ] **Test support chat pictures end to end** (paste a screenshot in the chat, check it in Telegram).
- [ ] **AI accuracy:** when the auditors have marked their calls, run the accuracy SQL and send the real figure.
- [ ] **17 Nov:** cancel old Server B (`13.140.236.153`) in Contabo.
- [ ] Docs patch with the new IP: `deploy/split_nginx.sh` (B_V4/B_V6), `scripts/b2_nginx.sh`, `docs/HANDOVER.md`, `docs/CUSTOMER-ACCOUNTS-PLAN.md`, `docs/SERVER-MOVE-IMPLEMENTATION.md`; platform Excel (Server B row, DNS, Commands).
- [ ] Call limit in Server A's backup routing (optional).
- [ ] **Rotate all keys and passwords** (pasted in chat on 9 Nov); credentials Excel to Sir encrypted.
- [ ] AI Audit v3 design: waiting for Sir's approval.
- [ ] AWS standby (covers a Server A failure): Sir's email; approved $496.31/month.
- [ ] Bandwidth call booking; Capitalist $10k pending since 30 Oct; Google removals; 6 Nov Excel issues; calls stuck "in progress"; `forwarding1` password reset not confirmed.
