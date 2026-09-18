# Scaling the Call Platform

Written 2026-09-18. Ordered by what actually limits throughput today, measured
against the code in this repo — not a generic checklist.

## Where the load is

One incoming call currently costs:

| Work | Where |
|---|---|
| `PhoneNumber` lookup (indexed, unique) | `routing/asterisk_handler.py` |
| `CallLog` insert | same |
| `RoutingEngine.route_call` — campaign + prefetched rules, blacklist, duplicate, caps, balance | `routing/engine.py` |
| `Destination` lookup when a buyer is chosen | `asterisk_handler.py` |
| One `CallLog` update | same |

Roughly five database round-trips, all on indexed columns, no external HTTP.
Peak observed load is 256 calls in an hour (~0.07/sec), so nothing here is
close to a limit yet.

## 1. Carrier lookup moved off the call path — DONE

The Telnyx number lookup was a blocking HTTP request with a 5-second timeout
running on **every** incoming call, before routing proceeded. Nothing about
routing depends on its result — it only records carrier and line type for
reporting — so it now runs in a Celery worker via `tasks.enrich_call_carrier`.

Effect: one external round-trip removed from every call. This was the single
largest ceiling; a Telnyx slowdown would previously have throttled call
routing directly.

Carrier data now appears a moment after the call rather than instantly. The
reporting breakdown is unaffected.

## 2. Run more than one web process

`daphne -b 0.0.0.0 -p 8000 config.asgi:application` is a single process on a
single event loop. Extra CPU does not help it.

The app is stateless — JWT auth, Redis channel layer, no local session state —
so replicas need no code change.

```bash
docker compose up -d --scale web=3
```

`docker-compose.yml` publishes `8000-8002:8000`, so each replica takes its own
host port. Scaling back to 1 binds 8000 exactly as before.

Then point Nginx at all three. In the `server` block's sibling scope:

```nginx
upstream callplatform {
    least_conn;
    server 127.0.0.1:8000 max_fails=2 fail_timeout=10s;
    server 127.0.0.1:8001 max_fails=2 fail_timeout=10s;
    server 127.0.0.1:8002 max_fails=2 fail_timeout=10s;
    keepalive 32;
}
```

and in the `location /` block, replace the single `proxy_pass` target:

```nginx
location / {
    proxy_pass http://callplatform;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;      # websockets: /ws/live-calls/
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 300s;
}
```

`proxy_set_header Upgrade` matters — the live-calls websocket breaks without it.

Verify with `nginx -t` before `systemctl reload nginx`.

## 3. PgBouncer — only after step 2

`CONN_MAX_AGE=600` means every web and worker process holds a Postgres
connection open for ten minutes. One web replica plus two Celery workers is
fine. Three replicas plus workers starts to crowd Postgres's default 100
connections.

PgBouncer is defined in `docker-compose.yml` under the `pooling` profile, so it
does not start with the normal stack:

```bash
docker compose --profile pooling up -d pgbouncer
```

Then in `.env`:

```
DATABASE_URL=postgresql://call_platform:changeme_in_production@pgbouncer:6432/call_platform
DB_CONN_MAX_AGE=0
DB_DISABLE_SERVER_SIDE_CURSORS=True
```

Both extra settings are required. Transaction pooling hands a different backend
connection to each transaction, so Django must not hold connections itself
(`DB_CONN_MAX_AGE=0`) and must not open server-side cursors that outlive a
statement.

Restart `web` and `celery_worker` afterwards. Roll back by removing the three
lines and restarting.

**Change the password.** `changeme_in_production` is still the literal value in
`docker-compose.yml`.

## 4. Query trimming — DONE

- The redundant `CallLog` save after the carrier lookup is gone with the lookup
- Remaining saves use `update_fields`, so they write the changed columns only
- The `Destination` lookup uses `.only('tfn')`

Small next to items 1 and 2, but free.

## 5. Asterisk media and SIP — design only, not yet needed

Asterisk currently terminates SIP signalling **and** bridges media on one host.
The ceiling is that host's CPU and NIC, not this application — a Django process
that dropped out entirely would not stop calls already bridged.

Rough capacity on a single modern host:

| Codec | Concurrent bridged calls |
|---|---|
| G.711 pass-through, no transcoding | several thousand |
| G.711 with recording | roughly 500–1500 |
| Transcoding (G.729 ↔ G.711) | low hundreds |

Recording is enabled per campaign, so the middle row is the realistic figure.

**Split signalling from media when you approach it.** A SIP proxy — Kamailio or
OpenSIPS — fronts several Asterisk media nodes:

```
carrier SIP trunk
        ↓
   Kamailio proxy          registrar, load balancing, failover
        ↓
  ┌─────┴─────┬───────────┐
Asterisk   Asterisk   Asterisk      media, recording, AGI
  node 1     node 2     node 3
        ↓
  this platform's /api/twilio/asterisk/route/ endpoint
```

What this application would need:

1. **Nothing in the routing decision.** `route_incoming_call` already keys off
   the dialed number and returns a destination. It does not care which Asterisk
   node asked.
2. **`ASTERISK_SHARED_SECRET` on every node.** Already how auth works; just
   distribute the same secret.
3. **Recording storage must become shared.** Today recordings are read from the
   local Asterisk spool. Multiple nodes means NFS, S3, or an object store — and
   `recording_url` must resolve regardless of which node captured the call. This
   is the one piece of real work.
4. **`call_log_id` already correlates.** It is returned on the route response
   and echoed back to `call_ended`, so a call can start on one node and report
   from another.

Do not build this before Asterisk actually saturates. Measure first:
`core show channels count` under peak load, plus host CPU and network.
