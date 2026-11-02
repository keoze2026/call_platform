# Avortyx server move: implementation guide for Server A and Server B

Full guide (all phases, the switch and rollback): https://claude.ai/artifact/NfYNPzSHAYpG92C8qwMLrB

You build and test the two new servers. Live calls stay on the current server
(**OLD**, `vmi3333575`) until the switch night, so nothing you do here can
touch a live call, as long as you follow the four rules below.

## The two servers

| | Server A | Server B |
| --- | --- | --- |
| Plan | Contabo VDS XL (16 cores, 64 GB, 480 GB) | Contabo VDS XXL (24 cores, 96 GB, 720 GB) |
| Public IP | `13.140.236.148` | `13.140.236.153` |
| Private link IP | `10.20.0.1` | `10.20.0.2` |
| SSH shortcut | `ssh avx-calls` | `ssh avx-web` |
| Runs | Asterisk, Kamailio (cancel hold), routing scripts (AGI, `call_ended.sh`), recordings (MP3, upload to Backblaze, `rec.v0l1.com`) | nginx + portal API (`web`, `web_calls`), Postgres, Redis, Celery (only from the switch night) |

Both are in Contabo US-east (Carlstadt, New Jersey). For each call, Server A
asks Server B which buyer gets it, over the private WireGuard link.

## Who does what

| Step | You (implementer) | Dev 1 (has OLD, carriers, Sir) |
| --- | --- | --- |
| 0. Access | Send Dev 1 your SSH **public** key (`id_ed25519.pub`) | Puts it on A and B |
| 1. Secure A and B | Firewall, Fail2Ban, updates, key-only login | Sends you OLD's `ufw status` output |
| 2. Private link | WireGuard between A and B | — |
| 3. Server B | Docker, code, database restore, portal API, nginx | Copies `.env`, last night's backup and nginx/certs from OLD to B |
| 4. Server A | Asterisk, Kamailio, scripts, recordings, firewall | Sends you OLD's versions/registrations/crontab output; copies configs from OLD to A |
| 5. Carriers, Cloudflare | — | Asks carriers to add A's IP; prepares Cloudflare |
| 6. Testing | Runs every test, writes down the results | Shows results to Sir |
| 7. Switch night | Runs the A and B steps | Runs OLD, Cloudflare and carrier steps; gets Sir's OK |

You never log in to OLD. Everything from OLD reaches you through Dev 1.

## Four rules

1. **Never touch OLD.** No login, no commands, no changes.
2. **Never start Celery on B before the switch night.** The worker and beat
   send buyer Telegram reports, alerts and Revo checks; two copies running
   would send everything twice. On B never run a bare `docker compose up -d`;
   always name the services (`docker compose up -d web web_calls`).
3. **No carrier registrations on A before the switch night.** A second
   registration could pull live calls to A or knock OLD's off. If a config
   you receive contains `type = registration`, stop and ask.
4. **No secrets in chat.** Never paste `.env` contents, passwords or private
   keys anywhere. Public keys (`.pub`) are fine.

Commands below assume Ubuntu or Debian on both servers. If `head -2
/etc/os-release` shows something else, stop and ask.

---

## Step 0: Your access (your PC)

Send Dev 1 the output of:

```powershell
Get-Content $HOME\.ssh\id_ed25519.pub
```

No key yet? Run `ssh-keygen -t ed25519` first (Enter at every prompt).
When Dev 1 confirms, add the shortcuts:

```powershell
@"

Host avx-calls
  HostName 13.140.236.148
  User root
  IdentityFile ~/.ssh/id_ed25519
  ServerAliveInterval 60

Host avx-web
  HostName 13.140.236.153
  User root
  IdentityFile ~/.ssh/id_ed25519
  ServerAliveInterval 60
"@ | Add-Content -Encoding ascii $HOME\.ssh\config
```

Check both (expect 16 cores / ~64 GB on A, 24 cores / ~96 GB on B):

```powershell
ssh avx-calls "hostname; head -2 /etc/os-release; nproc; free -g | head -2; df -h / | tail -1"
ssh avx-web "hostname; head -2 /etc/os-release; nproc; free -g | head -2; df -h / | tail -1"
```

## Step 1: Secure both servers (A, then B)

Use `avx-calls` as the name on A and `avx-web` on B.

```bash
hostnamectl set-hostname avx-calls
apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
apt-get install -y ufw fail2ban unattended-upgrades git curl htop
printf '[sshd]\nenabled = true\nmaxretry = 5\nbantime = 3600\n' > /etc/fail2ban/jail.local
systemctl enable --now fail2ban
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw --force enable
ufw status numbered
```

Key-only login: first confirm in a **second** window that `ssh avx-calls` (or
`ssh avx-web`) logs in without a password. Then:

```bash
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/10-key-only.conf
sshd -t && systemctl reload ssh
```

If you ever lock yourself out, Dev 1 can open the Contabo panel's VNC console.

**Report:** both servers updated, `ufw` shows only port 22, Fail2Ban `sshd` jail on.

## Step 2: Private link (A and B)

On each server, make a key pair; the last line prints the **public** key:

```bash
apt-get install -y wireguard
umask 077 && wg genkey > /etc/wireguard/private.key
wg pubkey < /etc/wireguard/private.key > /etc/wireguard/public.key
cat /etc/wireguard/public.key
```

On **A** (paste B's public key):

```bash
PEER_PUB='PASTE_B_PUBLIC_KEY'
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.20.0.1/24
ListenPort = 51820
PrivateKey = $(cat /etc/wireguard/private.key)

[Peer]
PublicKey = $PEER_PUB
AllowedIPs = 10.20.0.2/32
Endpoint = 13.140.236.153:51820
PersistentKeepalive = 25
EOF
chmod 600 /etc/wireguard/wg0.conf
ufw allow from 13.140.236.153 to any port 51820 proto udp
systemctl enable --now wg-quick@wg0
```

On **B** (paste A's public key):

```bash
PEER_PUB='PASTE_A_PUBLIC_KEY'
cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.20.0.2/24
ListenPort = 51820
PrivateKey = $(cat /etc/wireguard/private.key)

[Peer]
PublicKey = $PEER_PUB
AllowedIPs = 10.20.0.1/32
Endpoint = 13.140.236.148:51820
PersistentKeepalive = 25
EOF
chmod 600 /etc/wireguard/wg0.conf
ufw allow from 13.140.236.148 to any port 51820 proto udp
systemctl enable --now wg-quick@wg0
```

**Report:** `ping -c 3 10.20.0.2` from A and `ping -c 3 10.20.0.1` from B both answer (about 1 ms).

## Step 3: Server B

**3.1 Docker**

```bash
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version
```

**3.2 Code.** Make a read-only deploy key and send Dev 1 the printed public
key; Dev 1 adds it on GitHub (`keoze2026/call_platform` → Settings → Deploy
keys, write access off). Then clone:

```bash
ssh-keygen -t ed25519 -N '' -f /root/.ssh/github_deploy -C avx-web
cat /root/.ssh/github_deploy.pub
printf 'Host github.com\n  IdentityFile /root/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> /root/.ssh/config
git clone git@github.com:keoze2026/call_platform.git /opt/call_platform
cd /opt/call_platform && git log --oneline -1
```

The commit must match the one Dev 1 reads on OLD.

**3.3 Wait for Dev 1** to copy onto B: `/opt/call_platform/.env`, last
night's backup `/root/db-YYYYMMDD-HHMM.sql.gz`, and OLD's nginx and
certificates into `/root/from-old/`. Then point the TFN-test link at A and
check the settings (no secrets are printed):

```bash
cd /opt/call_platform && chmod 600 .env
sed -i 's/^ASTERISK_AMI_HOST=.*/ASTERISK_AMI_HOST=10.20.0.1/' .env
grep -oE '^(ASTERISK_AMI_HOST|ALLOWED_HOSTS)=.*' .env
grep -oE '^(DATABASE_URL|REDIS_URL)=[a-z]+://' .env
```

**3.4 Let A reach call routing over the private link.** This extra file is
not in git and adds one port on `10.20.0.2` only:

```bash
cat > /opt/call_platform/docker-compose.override.yml <<'EOF'
services:
  web_calls:
    ports:
      - "10.20.0.2:8001:8000"
EOF
```

**3.5 Restore the database copy** (use the real file name):

```bash
cd /opt/call_platform
docker compose up -d postgres redis && sleep 15
gunzip -c /root/db-YYYYMMDD-HHMM.sql.gz | docker compose exec -T postgres psql -U call_platform -d call_platform -v ON_ERROR_STOP=1 -q
```

**3.6 Start the portal API only (no Celery):**

```bash
cd /opt/call_platform
docker compose up -d --build web web_calls
docker compose exec web python manage.py showmigrations | grep '\[ \]' || echo 'no pending migrations'
docker compose exec web python manage.py collectstatic --noinput
curl -s -o /dev/null -w 'routing on 8001: %{http_code} (403 = OK)\n' -X POST http://127.0.0.1:8001/api/twilio/asterisk/route/
docker compose ps
```

**3.7 nginx.** Dev 1 tells you the `avortyx.io` site file name (`SITE_FILE`).
Only that site goes on B; `rec.v0l1.com` goes on A.

```bash
apt-get install -y nginx
cp -a /root/from-old/etc/letsencrypt /etc/
cp /root/from-old/etc/nginx/sites-available/SITE_FILE /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/SITE_FILE /etc/nginx/sites-enabled/
cp -n /root/from-old/etc/nginx/conf.d/* /etc/nginx/conf.d/ 2>/dev/null
nginx -t && systemctl reload nginx
ufw allow 80/tcp && ufw allow 443/tcp
```

**3.8 Check from your PC** (`--resolve` sends only this request to B):

```powershell
curl.exe -s -o NUL -w "B answers: %{http_code}\n" --resolve avortyx.io:443:13.140.236.153 https://avortyx.io/api/docs
```

**Report:** B answers 200, routing 403, `docker compose ps` shows postgres,
redis, web, web_calls up and **no** Celery.

## Step 4: Server A

**4.1 Wait for Dev 1's output from OLD:** Asterisk and Kamailio versions,
`pjsip show registrations`, `database show avortyx`, `crontab -l`, OLD's
`ufw status` (carrier IPs, media port range). If Asterisk on OLD was built
from source (no `asterisk` package), stop and ask before continuing.

**4.2 Install** (Kamailio masked first so it can't take port 5060 early):

```bash
systemctl mask kamailio
apt-get update && apt-get install -y asterisk kamailio kamailio-utils-modules sip-tester ffmpeg nginx python3
systemctl stop asterisk && systemctl disable asterisk
asterisk -V; kamailio -v | head -1
```

Versions must match OLD's. If they don't, stop and send both.

**4.3 Wait for Dev 1** to copy OLD's `/etc/asterisk`, `/etc/kamailio`,
`/var/lib/asterisk/agi-bin`, `/usr/local/bin/call_ended.sh`, `/etc/nginx`,
`/etc/letsencrypt` into `/root/from-old/`, and the Backblaze settings into
`/root/b2.env`.

**4.4 Put them in place, with A's IP:**

```bash
cp -a /root/from-old/etc/asterisk/. /etc/asterisk/
cp -a /root/from-old/etc/kamailio/. /etc/kamailio/
cp -a /root/from-old/var/lib/asterisk/agi-bin/. /var/lib/asterisk/agi-bin/
cp -a /root/from-old/usr/local/bin/call_ended.sh /usr/local/bin/
grep -rln '156.67.25.167' /etc/asterisk /etc/kamailio | xargs -r sed -i 's/156\.67\.25\.167/13.140.236.148/g'
grep -rnE '127\.0\.0\.1:800[01]|localhost:800[01]' /var/lib/asterisk/agi-bin /usr/local/bin/call_ended.sh /etc/kamailio
grep -rnE '^\s*type\s*=\s*registration' /etc/asterisk/pjsip*.conf
```

- Every `127.0.0.1:8000` / `:8001` the first `grep` prints becomes
  `10.20.0.2:8001` (Server B over the private link).
- If the last `grep` prints any registration: **stop**, comment nothing out
  yourself, and send it to Dev 1 (rule 3).

**4.5 Recordings.** Make A's own read-only deploy key exactly as in 3.2 (name
it `avx-calls`, Dev 1 adds it on GitHub), then:

```bash
git clone git@github.com:keoze2026/call_platform.git /opt/call_platform
mv /root/b2.env /opt/call_platform/.env && chmod 600 /opt/call_platform/.env
mkdir -p /var/spool/asterisk/recordings /var/lib/avortyx /var/backups/avortyx
cp -a /root/from-old/etc/letsencrypt /etc/
cp /root/from-old/etc/nginx/sites-available/*rec.v0l1.com* /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/*rec.v0l1.com* /etc/nginx/sites-enabled/
cp -n /root/from-old/etc/nginx/avortyx-b2.conf /etc/nginx/ 2>/dev/null
nginx -t && systemctl reload nginx
python3 /opt/call_platform/scripts/recordings_to_b2.py --status
```

Old recordings are not copied: `rec.v0l1.com` plays any file A doesn't hold
from Backblaze.

**4.6 Firewall.** One SIP line per carrier IP from OLD's `ufw status`; use
OLD's media range if it differs from `10000:20000`:

```bash
ufw allow from CARRIER_IP to any port 5060 proto udp
ufw allow 10000:20000/udp
ufw allow in on wg0 from 10.20.0.2 to any port 5038 proto tcp
ufw allow 80/tcp && ufw allow 443/tcp
ufw status numbered
```

**4.7 Start Asterisk and Kamailio** (no registrations, no carrier sends here yet):

```bash
systemctl unmask kamailio
systemctl enable --now asterisk kamailio
asterisk -rx 'core show version'
asterisk -rx 'pjsip show endpoints' | tail -5
asterisk -rx 'pjsip show registrations'
```

Then add the `avortyx` keys from OLD's `database show avortyx`, one line each,
for example:

```bash
asterisk -rx 'database put avortyx cancel_hold 1'
```

**4.8 Cron jobs: write, don't install.** Put the call and recording lines
from OLD's crontab (channel sync, `call_ended.sh --replay`,
`recordings_to_mp3.sh`, `recordings_to_b2.py`, the daily B2 link renewal) in
`/root/crontab-phase7.txt`. They are installed on the switch night.

**Report:** same versions as OLD, `pjsip show registrations` shows none,
routing scripts point at `10.20.0.2:8001`, private link answers.

## Step 5: Testing (no live traffic, no real buyer)

| Test | Where | Command / how | Pass when |
| --- | --- | --- | --- |
| Backend test suite | B | `cd /opt/call_platform && docker compose exec -T web python manage.py test --settings=config.test_settings --noinput 2>&1 \| grep -E '^(Ran\|OK\|FAILED)'` | `OK` |
| Routing reachable from A | A | `curl -s -o /dev/null -w '%{http_code}\n' -X POST http://10.20.0.2:8001/api/twilio/asterisk/route/` | `403` |
| Old recording plays | PC | `curl.exe -s -o NUL -w "%{http_code}\n" --resolve rec.v0l1.com:443:13.140.236.148 https://rec.v0l1.com/KNOWN_FILE.mp3` | `200` |
| Load test: 600 calls at once for at least 30 minutes, SIPp to SIPp inside A only (Sir) | A | SIPp files provided after Step 4's outputs are seen | No failed call, clear audio, CPU under 60% |
| Server B switched off during the load test (Sir) | A and B | At minute 10, `docker compose stop web web_calls` on B; start them again at minute 20 | Calls keep connecting from A's copy; buyer caps and concurrency still hold; every call made while B was off appears on B afterwards, with its charge and payout |
| Backup restore (Sir) | B | Restore the newest nightly backup into a scratch database (5.2) | Restores with no error; call and transaction counts match the live database at backup time |
| Recording clean-up on A (Sir) | A | 5.3 | Only files older than 7 days and confirmed identical in Backblaze are removed |
| TFN test call (inside our phone system, no buyer rung) | B to A | Command provided at this step | Ends cleanly, steps shown |
| One spare TFN pointed at A by its carrier, called from a mobile, routed to a team member | Mobile to A | Dev 1 arranges the TFN | Rings, connects, recording appears, call row on B |
| Portal speed on B | PC | Dashboard and reports month view through `--resolve` | Under 2 seconds |

Write every result down and send them to Dev 1; Sir sees them before the
switch night is booked.

### 5.1 Routing when Server B is down (Sir's requirement)

Server A must keep routing calls if Server B stops answering:

- Every minute A keeps a local copy from B: each active TFN's destinations
  in routing order, buyer caps with how much of each is used, concurrency
  limits, and blocked callers.
- Each call still asks B first, with a 2-second limit. If B doesn't answer,
  A routes from its copy. A counts its own calls against the caps and
  concurrency limits, so they still hold while B is down (A is the only one
  taking calls then).
- Every call A routes alone is logged on A and sent to B when B is back:
  B creates the call rows and charges and pays them exactly as normal calls,
  so billing stays accurate.

The backend part (the copy for A, accepting the calls A routed alone) and
the routing script change come as a backend patch. It is tested by
switching B off during the load test (table above) before any live call
moves.

### 5.2 Backup restore test (B)

Restores the newest nightly backup into a separate scratch database, never
the real one, then compares counts and removes the scratch database. Use
the real file name from `ls -t /var/backups/avortyx/ | head -1`.

```bash
cd /opt/call_platform
docker compose exec -T postgres psql -U call_platform -d postgres -c 'CREATE DATABASE restore_test OWNER call_platform;'
gunzip -c /var/backups/avortyx/db-YYYYMMDD-HHMM.sql.gz | docker compose exec -T postgres psql -U call_platform -d restore_test -v ON_ERROR_STOP=1 -q
for db in restore_test call_platform; do docker compose exec -T postgres psql -U call_platform -d $db -tAc "select '$db', (select count(*) from call_logs), (select count(*) from transactions);"; done
docker compose exec -T postgres psql -U call_platform -d postgres -c 'DROP DATABASE restore_test;'
```

The restored counts can be slightly lower than the live ones: calls after
the backup time are not in it.

### 5.3 Recording clean-up on A (Sir: the disk must not fill up)

Recordings stay on A's disk for 7 days, then are removed, but only when all
of these hold: Backblaze has the file with the same size and checksum, and
a test file that exists only in Backblaze plays through `rec.v0l1.com`.
Otherwise nothing is removed that run. Older recordings then play from
Backblaze through the same link.

On a new Server A nothing is older than 7 days, so the first removals
happen a week after the switch. The first run is capped at 20 files, then
checked:

```bash
python3 /opt/call_platform/scripts/recordings_to_b2.py --nginx
DELETE_PER_RUN=20 python3 /opt/call_platform/scripts/recordings_to_b2.py --delete-local-days 7
python3 /opt/call_platform/scripts/recordings_to_b2.py --status
```

`--status` shows how many files were removed from disk. Play one of those
recordings in the portal to confirm it now comes from Backblaze. Then the
daily clean-up line goes into `/root/crontab-phase7.txt`:

```
15 4 * * * /usr/bin/python3 /opt/call_platform/scripts/recordings_to_b2.py --delete-local-days 7 >> /var/log/recordings_cleanup.log 2>&1
```

At about 400 GB of recordings a month, 7 days is roughly 90 GB on A's
480 GB disk.

## Step 6: Switch night (your part)

Dev 1 leads the night, checks 0 live calls on OLD and has Sir's OK. You do
only these, when Dev 1 says:

1. **B:** load the final database copy in place of the test copy:

   ```bash
   cd /opt/call_platform
   docker compose stop web web_calls
   docker compose exec -T postgres psql -U call_platform -d postgres -c 'DROP DATABASE call_platform;' -c 'CREATE DATABASE call_platform OWNER call_platform;'
   gunzip -c /root/final.sql.gz | docker compose exec -T postgres psql -U call_platform -d call_platform -v ON_ERROR_STOP=1 -q
   docker compose up -d web web_calls
   ```

2. **B:** after Cloudflare points `avortyx.io` at B and Dev 1 confirms OLD is
   handed over: `docker compose up -d celery_worker celery_beat`, then add
   B's host jobs to its crontab (watchdog, daily status, `host_status.py`,
   the 4:30 AM ET database backup).
3. **A:** turn on the call and recording jobs: `crontab /root/crontab-phase7.txt`.
4. **A and B:** watch for the first hour: `asterisk -rx "core show channels count"`
   on A, `docker compose logs --tail 50 web_calls` on B, Telegram alerts.

Before the night, Dev 1 also gets a small backend patch that teaches the
watchdog and status bot about two servers (phone checks on A, portal checks
on B).
