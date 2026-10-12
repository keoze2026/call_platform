# Before you ship — the mistake list, as a checklist

Every item here is a mistake we actually shipped, with its CHANGES entry.
A new endpoint, page or field does not go out until each line is answered
for it. This file exists because recording a mistake narratively did not
stop it recurring: CH-087/CH-089 documented "org-scoped but not role-scoped"
and the Stats API shipped the identical hole the same week.

## Any new endpoint
- [ ] Auth declared on the router — missing auth was the Reports PIN 500
- [ ] Role scoping, not just organization: buyer sees theirs, publisher
      theirs, partner key = its creator (CH-087, CH-089, the Stats TFN slip)
- [ ] Money masked for partner roles - payout/profit/cost/balance are null,
      never zero (CH-088/089)
- [ ] Every response field named on the schema or passed as dict knowingly —
      Ninja silently drops undeclared keys (caller geo, CH-067)
- [ ] Destructive actions: confirmed, logged, idempotent on retry (CH-085)

## Any new field or figure
- [ ] It reaches all five layers or it vanishes: column → mirror → mirror
      defaults → formatter → schema (caller geo; recording_url CH-077)
- [ ] Never invented client-side - no hash(), no placeholder figures
      (Live CH-013, TTC CH-091)
- [ ] Verified at its SOURCE, not through a derived copy - answered_at was
      hangup-minus-duration and "verified" itself (CH-080/091)
- [ ] Days cut in the user's timezone (CH-060s); money as Decimal

## Any new behaviour
- [ ] The rule lives in data, not code, when users will change it (carriers
      CH-083; duplicate switches that saved nothing, CH-092)
- [ ] Clocks start on USE, not on creation (TFN expiry, CH-094)
- [ ] Remove a guard only after replacing what it protected - the
      one-destination limit guarded a .first() in routing (CH-098)
- [ ] Queries grouped, never per row (CH-074/086); frontend pulled, tsc'd

## Before saying "done"
- [ ] Tested on the server, output shown - both numbers, never a proxy
- [ ] After touching any api.py: one real HTTP request answered, not a
      service-layer call - shell tests passed while every endpoint 500'd,
      because they never import the API module (CH-107)
- [ ] A script that proves things is itself proven: the phase 2 smoke died
      on sys.path before testing anything, and the restart it gated never
      ran (CH-106 addendum)
- [ ] Scripts run as `python scripts/x.py` pin the project root on
      sys.path - sys.path[0] is scripts/, not the root (CH-106 addendum)
- [ ] Server commands ship as committed files, never pasted heredocs -
      `docker compose exec` has no stdin without -T and pasted blocks
      mangle (CH-107)
- [ ] After a mechanical batch edit, audit route-by-route with a DIFFERENT
      pattern than the injector used; "N edits made" says nothing about
      what was missed (CH-106)
- [ ] CHANGES.md entry written in the same push as the code - and the
      mistake goes in the entry, not just the fix (CH-107 exists because
      a full-API outage went unrecorded)
