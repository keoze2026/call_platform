# Server reboot runbook

The login banner says `*** System restart required ***` — installed security
updates need one reboot to apply. A reboot ends every call in progress and
takes the platform down for roughly 2–4 minutes, so it runs in a quiet
window, never casually.

## Before (pick the moment)

```
asterisk -rx "core show channels"
```

Wait until it reads `0 active calls`. Night hours per the traffic reports
are the safe window.

## The reboot

```
cd /opt/call_platform && docker compose down && reboot
```

The SSH session drops — that is the reboot working, not a failure.

## After (reconnect, ~2 minutes later)

```
cd /opt/call_platform && docker compose up -d && sleep 20 && docker compose ps && systemctl status asterisk --no-pager -l | head -5 && curl -s -o /dev/null -w "API: %{http_code}\n" https://avortyx.io/api/accounts/access-requests/ -X POST -H 'Content-Type: application/json' -d '{"name":"x","company":"x","email":"reboot-check@smoke-test.invalid"}'
```

Expect: every container `Up`, asterisk `active (running)`, `API: 201` (or
429, which also proves the API answers). Then one real test call to a live
TFN, and `docker compose exec web python scripts/access_request_audit.py
--cleanup` to remove the check row.

## If something does not come back

- Containers not up: `docker compose up -d` again, then `docker compose logs
  --tail 50 web`.
- Asterisk not running: `systemctl start asterisk`.
- Calls failing but API fine: carrier side — check `asterisk -rx "pjsip show
  registrations"`.
