# Avortyx handover: where everything stands

Written 10 Nov 2026, 1:45 AM Nairobi (9 Nov, 6:45 PM EST).

---

## 1. Servers (changed tonight: Server B moved)

| Server | IP | State |
|---|---|---|
| **Server A** `avx-calls` (calls) | `13.140.236.148`, private `10.20.0.1` | Live, unchanged except 3 small changes tonight (below) |
| **NEW Server B** `avx-web2` (portal, API, database, AI) | **`147.93.7.36`**, private `10.20.0.2` | **LIVE since 10 Nov 1:23 AM Nairobi.** Contabo VDS XXL, 2-year plan, Carlstadt US-east |
| Old Server B `avx-web` | `13.140.236.153` | **Stopped**: app, cron, private link off. Database and nginx still there. Keep until 17 Nov, then cancel. **Never start it**: alerts and AI checks would run twice |
| Old server `vmi3333575` | `156.67.25.167` | Old backup, untouched (user: leave it) |

**Copy patches to the NEW server from now on:**
```
scp "$env:USERPROFILE\Downloads\<file>" root@147.93.7.36:/tmp/
```
Each PC: run `ssh-keygen -R 147.93.7.36` once. The new server took old B's SSH identity, so the first login warns about a changed key; answer `yes`. The Dynasoft PC is already done.

### What was done tonight (the move)
1. New server: Docker, firewall (22, 80, 443; 51820 from Server A only; all on `wg0`), nginx and certificates, AI checker (`/opt/avx-voice`), keys, gatekeeper `/usr/local/bin/avx-from-a`.
2. Old B stopped. Its cron list is saved in `/root/crontab-B-final.txt`.
3. Final database copy: **40,670 calls on both** (32 seconds).
4. New server took over the private link `10.20.0.2` and old B's SSH identity, started all 6 services, and installed the 9 cron lines.
5. **Server A, 3 changes:**
   - firewall: `ufw allow from 147.93.7.36 port 51820/udp`
   - WireGuard Endpoint now `147.93.7.36:51820` (live and in `/etc/wireguard/wg0.conf`)
   - nginx `rec.v0l1.com`: `allow 147.93.7.36` and the new IPv6 added (backup `/root/rec.v0l1.com.bak-move`)
   - Asterisk and Kamailio were **not** restarted.
6. **Cloudflare:** `avortyx.io`, `b-api.avortyx.io` and `www.avortyx.io` now point to `147.93.7.36` (still Proxied). `app` and `b-app` (Vercel) are unchanged.

### Proven working on the new server
| Check | Result |
|---|---|
| `avortyx.io` / `b-api.avortyx.io` | 200 / 200 |
| Server A to new B routing | `403` (correct: B answered), ping 0.6 ms |
| A's fallback | plan 19 s old from new B, 0 waiting, 0 held |
| Test call | in Call Logs (Missed: no buyer after hours, expected) |
| `/status` bot | reports "Portal server avx-web2 147.93.7.36", all 9 services running |
| Database backup | OK, 100.5 MB to B2 |
| Lookups | RealValidito 419,113 phone / 501,729 DNC left; IPQS success, 37,695 credits |
| AI checker (`voice_check`, `ai_final`) | running, each run under 60 s |

**No IP whitelisting is needed anywhere else.** Carriers whitelist Server A
(unchanged); lookups are proven above; there is no ping/post traffic; the AI
services use keys.

**Failover is the same as before:** if B fails, Server A routes calls alone and
syncs them back later. If A fails, calls stop until the AWS standby exists.

### To do on the move
- [ ] **Tomorrow morning:** the first answered calls get AI labels (AI Audit page; "Pending AI check" goes down).
- [ ] Optional failover drill, after hours only:
  1. on new B: `docker compose stop web_calls`
  2. make a test call
  3. on Server A: `fallback_sync.py --status` should count +1 and show waiting 1
  4. on new B: `docker compose start web_calls`
  5. `--status` should show waiting 0
- [ ] Docs patch with the new IP: `deploy/split_nginx.sh` (B_V4/B_V6), `scripts/b2_nginx.sh`, `docs/HANDOVER.md`, `docs/CUSTOMER-ACCOUNTS-PLAN.md`, `docs/SERVER-MOVE-IMPLEMENTATION.md`. Not urgent: the live config is already right.
- [ ] Platform Excel: Server B row, DNS and Commands sheets, now `147.93.7.36`.
- [ ] 17 Nov: cancel old B (`13.140.236.153`) in Contabo.
- If anything breaks badly: `SWITCH-NIGHT-Server-B.md` section 7 switches back to old B in about 2 minutes.

---

## 2. Code

| Repo | State |
|---|---|
| Backend `call_platform` master | `84f03055`: CH-650 pushed and running on the new B. **CH-648's routing part (the report copy follows the call) is now live too**: `web_calls` started fresh tonight with this code |
| Frontend `Avortyx` main | CH-650 (Sir's No Voice report) live. **CH-651** (bar to the right of the title) was sent as a patch. Check it was pushed: `git log --oneline -3` on main should show CH-651 |

**Next free change ID: CH-652.**

**How we work (do not skip):**
- Claude never pushes. You apply the patch on the PC with `git am` and push.
- **Backend:** run the full test suite on Server B in a throwaway clone first. Use the pattern in `docs/HANDOVER.md`, now on `147.93.7.36`. Wait for `OK` before pushing, then deploy with `git pull && docker compose restart web`.
- **Frontend:** push to main and Vercel deploys.
- Every change gets a CHANGES.md entry.
- Sir's rules:
  - No deploys during calling hours.
  - `web_calls`, postgres, redis or a server restart only after hours, with a test call before and after.
  - Never turn anything on or off without Sir.
  - Every fix needs proof.

---

## 3. Customer portal (client isolation)

Sir's hard rule: **"No client can ever see anyone else's report or TFN."**

| Point | State |
|---|---|
| 1. Row-level security in PostgreSQL (CH-580) | **With the other developer**, not ours |
| 2 to 7 | Done (see `docs/CUSTOMER-ACCOUNTS-PLAN.md`) |
| 8. Recording links protected | **Done 9 Nov**: nginx on Server A checks every link with B over the private link (`10.20.0.2:8001/api/recordings/check`); recording folders on |
| 9. | Done |
| 10. Owner sees client activity | **Done**: proof rows `owner_viewed` in the interns and texora accounts (9 Nov, 08:05 AM EST) |
| 11. Isolation screens and Robot check | **Done**: GitHub Actions workflow "Robot check" (`.github/workflows/smoke.yml`) logs in as `robot@avortyx.io` (Viewer, read-only) and opens 11 pages. **Run #218 green.** On failure, the screenshot is kept 7 days (artifact `robot-check-screenshot`) |
| CH-648 (9 Nov, the A49/BOP leak) | **Done and live**: a buyer or publisher sees a report row only if the call itself is theirs too. 1,458 tests OK |
| CH-649 | Export includes live calls (0 sec), so file rows = screen total |

**Next on the customer portal:**
- [ ] 20 Nov demo for Sir.
- [ ] Walk Sir through the client portal plan on the texora account.
- [ ] CH-580 with the other developer.
- [ ] `forwarding1` login: a password reset command was given; not confirmed run.

---

## 4. AI audit

- **Live:** Sir's No Voice report (CH-650): headline, 4 cards, share, status, Robocall column (the columns now add up), see calls, evidence, repeat callers with Block.
- **Waiting for Sir:** the v3 design (7-day trend, money paid on No Voice, hourly heatmap, auto-actions all off until approved). The screenshot was sent.
- **Accuracy:** only **4 calls** are marked in the portal (all correct). The 50 to 70% in the group is the auditors' estimate. The auditors were asked to mark 75 calls (15 No Voice and 10 Valid each). Then re-run the accuracy SQL on Server B and give Sir the real figure.

---

## 5. Other open items
- [ ] **Rotate all keys and passwords.** The credentials CSV, root password and a Google password were pasted in chat on 9 Nov. The credentials Excel goes to Sir encrypted.
- [ ] AWS standby (covers a Server A failure): Sir sends the new email Monday. Approved $496.31/month (c7i.4xlarge Ohio, 1-yr plan).
- [ ] Bandwidth: book the call (suggested Tue 5:30 PM Nairobi).
- [ ] Capitalist $10k pending since 30 Oct.
- [ ] Google: removal requests for the old "Avortyx Prototype" pages.
- [ ] Not started: the 6 Nov Excel issues / data accuracy standard; calls stuck "in progress" (end-of-call not received).
