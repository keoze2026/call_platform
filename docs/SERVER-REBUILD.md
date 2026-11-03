# Rebuilding a server from its settings backup (CH-470)

Every day each server packs its settings and uploads them to Backblaze B2
(`scripts/config_backup_b2.py`, 04:40 server time). The archive is called
`backups/config/<server>-<UTC time>.tar.gz`. With it, a lost server is set up
again with the same settings, rather than from memory.

| Server | Name in B2 | Holds |
|---|---|---|
| A, the call server | `avx-calls-…` | Asterisk, Kamailio, recordings site, routing scripts, fallback code, WireGuard, firewall, crontabs, Asterisk's keys (cancel hold) |
| B, the portal server | `avx-web-…` | nginx and certificates, `.env`, `docker-compose.override.yml`, WireGuard, firewall, crontabs |

Each archive has a `rebuild-info.txt`:
- versions, all installed packages, firewall rules and IPs;
- enabled services, crontabs and Asterisk's keys.

**B's database is not in it**: it has its own nightly backup
(`db_backup_b2.py`). Recordings older than a few minutes are already in B2.

The archive holds private keys and `.env`. It sits in the same private
bucket as the database backups. Never copy it anywhere else, and delete
working copies after a rebuild.

## Check that backups run

On either server:

    cd /opt/call_platform
    python3 scripts/config_backup_b2.py --status
    python3 scripts/config_backup_b2.py --list | tail -3

The watchdog sends "Settings Backup Overdue" when no good backup is newer
than 36 hours.

## Rebuild Server A (calls stop until this is done)

Carriers send calls only to `13.140.236.148`, so the rebuilt server must keep
that IP. Use Contabo's **Reinstall** on the same server (the IP stays): Ubuntu
24.04. Ordering a new server gives a new IP, and every carrier would have to
change theirs again.

**1. Fetch the newest A backup. Inside Server B:**

    cd /opt/call_platform
    python3 scripts/config_backup_b2.py --list | grep avx-calls | tail -1
    cd /root && python3 /opt/call_platform/scripts/config_backup_b2.py --fetch <name from the line above>

**2. Copy it to A. On your PC, in PowerShell:**

    scp avx-web:/root/<name> .
    scp <name> avx-calls:/root/

After a reinstall, A's SSH key is new. Remove the old line from
`$HOME\.ssh\known_hosts` if `scp` refuses.

**3. Install the same software. Inside Server A.** The versions are in
`rebuild-info.txt` (read it with `tar -xzOf /root/<name> rebuild-info.txt | less`):

    apt-get update
    apt-get install -y asterisk kamailio kamailio-utils-modules ffmpeg nginx python3 wireguard ufw fail2ban git curl
    systemctl stop asterisk kamailio nginx

**4. Put the settings back.** This restores everything in the list at the top
of `config_backup_b2.py`, including root's SSH keys and the deploy key:

    tar -xzpf /root/<name> -C /
    mkdir -p /var/spool/asterisk/recordings && chown asterisk:asterisk /var/spool/asterisk/recordings && chmod 755 /var/spool/asterisk/recordings
    chown asterisk:asterisk /var/lib/asterisk/astdb.sqlite3

**5. The code** (`.env` is already back in `/opt/call_platform`):

    cd /opt/call_platform
    git init -q && git remote add origin git@github.com:keoze2026/call_platform.git
    git fetch -q origin master && git checkout -f master
    bash scripts/fallback/install_a.sh

**6. Start everything:**

    systemctl daemon-reload
    systemctl enable --now wg-quick@wg0 asterisk kamailio nginx fail2ban avx-fallback-sync
    ufw --force enable
    ping -c 3 10.20.0.2
    asterisk -rx "pjsip show endpoints" | tail -5
    systemctl is-active asterisk kamailio nginx avx-fallback-sync

**7. Check:** one test call to a TFN connects and shows in the portal. Then
`crontab -l` should show the call and recording jobs.

Recordings made in the last few minutes before the loss may be missing.
Everything older plays from B2 through `rec.v0l1.com`.

## Rebuild Server B

The same steps 1 to 4, using the newest `avx-web-…` backup, which can be
fetched from Server A. The software for B is Docker, nginx, WireGuard, ufw,
Fail2Ban and git. Then:
- restore the code as in step 5 (without `install_a.sh`);
- restore the newest database backup: `docs/SERVER-MOVE-IMPLEMENTATION.md`,
  section 5.2, into `call_platform`;
- start the containers: `docker compose up -d`.

While B is down, A keeps routing calls on its own (CH-464), so calls do not
stop.
