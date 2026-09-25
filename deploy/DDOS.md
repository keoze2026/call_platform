# Protecting the platform from floods

Ordered by how much each step actually buys you. The first two are worth more
than everything below them combined.

**What the application rate limits added in CH-027 do not do.** They run after a
request reaches Django, so they stop scraping and credential stuffing. A
volumetric flood saturates the network card and Nginx workers long before Django
is reached. Application limits are the last layer, not the first.

**The likelier attack on a call platform is not a web flood.** It is SIP: toll
fraud and registration brute force. Flooding a dashboard costs the attacker money
and earns nothing. Stealing minutes earns them money directly. Step 4 matters
more here than it would for an ordinary web app.

---

## 1. Close the ports that should never have been open

`docker compose ps` showed Postgres, Redis and Daphne published on `0.0.0.0` —
the public internet, not this machine. Redis had no password. The Postgres
password was the literal string `changeme_in_production`, committed to the
repository.

Fixed in `docker-compose.yml`: every host binding is now `127.0.0.1`. Containers
reach each other over the compose network by service name and never needed a
host port.

    docker compose down && docker compose up -d
    ss -tlnp | grep -E ':(5432|6379|8000|6432)'     # expect 127.0.0.1 only

Binding Daphne to loopback also closes a bypass: port 8000 was reachable
directly, so anyone could skip Nginx and every limit configured there.

**The password still needs changing.** `POSTGRES_PASSWORD` only applies when the
data directory is first created, so the running database keeps the old one:

    # pick a new password, put it in .env as POSTGRES_PASSWORD and DATABASE_URL
    docker compose exec postgres psql -U call_platform -d call_platform \
      -c "ALTER USER call_platform WITH PASSWORD 'THE_NEW_PASSWORD';"
    docker compose restart web celery_worker celery_beat

Set `REDIS_PASSWORD` in `.env` at the same time and add it to `REDIS_URL`.

## 2. A firewall, so a missed binding is not an open door

    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw enable
    ufw status verbose

Do not open 5432, 6379, 8000 or 6432. Reach a database over SSH instead:

    ssh -L 5432:127.0.0.1:5432 root@<server>

SIP ports belong here too, but only for the carrier — see step 4.

## 3. Nginx limits

Apply `deploy/nginx-rate-limits.conf`, then:

    nginx -t && systemctl reload nginx

**The Asterisk callbacks are exempted on purpose.** `/api/twilio/asterisk/`
decides where live calls go and closes finished ones. Rate limiting it drops
calls. It is protected by a shared secret instead.

## 4. fail2ban on SIP

    apt install fail2ban
    # /etc/fail2ban/jail.local
    [asterisk]
    enabled  = true
    port     = 5060,5061
    filter   = asterisk
    logpath  = /var/log/asterisk/messages
    maxretry = 5
    bantime  = 86400

Better still, only accept SIP from the carrier:

    ufw allow from <CARRIER_IP> to any port 5060 proto udp

A SIP port open to the whole internet is scanned continuously.

## 5. Cloudflare in front of the HTTP hosts

The only step that absorbs a genuinely volumetric attack, because it soaks it up
before it reaches this machine. Free tier is enough.

Proxy these:

- `avortyx.io`
- `rec.v0l1.com`

**Do not proxy anything Asterisk uses for SIP.** Cloudflare's free tier does not
carry SIP or UDP, and routing signalling through it kills inbound calls. Only
HTTP hostnames go behind the orange cloud.

After enabling it, Nginx sees Cloudflare's IP on every request, so restore the
real client address — otherwise every rate limit counts the whole internet as one
visitor and either blocks everybody or nobody:

    # in http{}
    real_ip_header CF-Connecting-IP;
    # plus set_real_ip_from lines for Cloudflare's published ranges

And lock the origin down so nobody can bypass Cloudflare by hitting the IP
directly:

    ufw delete allow 80/tcp
    ufw delete allow 443/tcp
    # then allow 80/443 only from Cloudflare's published ranges

## 6. Already done

Application rate limiting: `60/m` per IP before authentication, `600/m` per user
after, counted in Redis so it holds across workers and survives a restart.
`API_THROTTLE_ANON` and `API_THROTTLE_USER` change it without a deploy.

---

## Checking it worked

    ss -tlnp | grep -E ':(5432|6379|8000)'    # 127.0.0.1 only
    ufw status verbose                        # 22, 80, 443 only
    curl -s -o /dev/null -w '%{http_code}\n' https://avortyx.io/api/docs   # 200

Confirm calls still route after every step. Nothing here should touch the call
path, and step 3 is the one that could if the Asterisk exemption is dropped.
