# Avortyx Stats API

Read-only call statistics for integrations (bots, dashboards, scripts).
Every figure comes from the same code the portal's reports run on, so these
numbers always match the screen.

Base URL: `https://avortyx.io/api/stats`

## Authentication

Bearer API key on every request:

    Authorization: Bearer <your-api-key>

Keys are created in the portal: **Settings → API keys → Create**. The key is
shown once at creation. A key acts as the user who created it — same
organization, same role, same data visibility.

Common query parameters (all optional):

| param           | example               | meaning                                  |
|-----------------|-----------------------|------------------------------------------|
| `date_from`     | `2026-10-01`          | first day of the window (inclusive)      |
| `date_to`       | `2026-10-10`          | last day of the window (inclusive)       |
| `timezone_name` | `America/New_York`    | the zone days are cut in; default UTC    |

---

## 1. GET /campaigns — campaigns with all call statistics

    curl -H "Authorization: Bearer KEY" \
      "https://avortyx.io/api/stats/campaigns?date_from=2026-10-10&timezone_name=America/New_York"

One row per campaign:

| field                 | meaning                                                      |
|-----------------------|--------------------------------------------------------------|
| `campaign_id` / `campaign_name` | which campaign                                     |
| `total_calls`         | incoming — every call that arrived, including live ones       |
| `connected_calls`     | reached a destination (completed or in progress)              |
| `not_connected_calls` | did not (missed, busy, refused)                               |
| `live_calls`          | in flight right now                                           |
| `qualified_calls`     | connected and not a duplicate                                 |
| `converted_calls`     | answered and long enough to count (campaign's min duration)   |
| `conversion_rate`     | converted / incoming, percent                                 |
| `duplicate_calls` / `dupe` | every duplicate that arrived, answered **or dropped**    |
| `avg_duration`        | **AHT**, seconds — talk time from buyer pickup, averaged over connected calls |
| `total_duration_sec`  | **TCL**, seconds — total talk time of connected calls         |
| `total_revenue` / `total_payout` / `total_profit` | money for the window; `null` = not visible to this key's role |
| `billable_minutes` / `total_cost` | platform usage billing                           |

## 2. GET /buyers — buyers with caller statistics

Same query parameters, same field meanings, one row per buyer
(`buyer_id`, `buyer_name`, plus the fields above).

## 3. GET /missed — missed calls by campaign and by buyer

    curl -H "Authorization: Bearer KEY" "https://avortyx.io/api/stats/missed?date_from=2026-10-10"

    {
      "by_campaign": [ {"id": "...", "name": "23 JUNE", "missed": 3, "refused": 142, "incoming": 488} ],
      "by_buyer":    [ {"id": "...", "name": "ADC11",  "missed": 3, "refused": 0,   "incoming": 62} ]
    }

`missed` = arrived and nobody connected (no answer or busy — includes
duplicates dropped under the Different rule). `refused` = blocked before
routing (cap, do-not-call, blacklist) — listed separately so a blocked caller
is never read as a missed opportunity.

## 4. GET /tfn/{number} — one TFN with cc and cap

    curl -H "Authorization: Bearer KEY" "https://avortyx.io/api/stats/tfn/18779641530"

Matches on the last ten digits (`+1` optional). Returns the destination row:
buyer, enabled, **`live_calls` (cc)**, **`concurrency_cap` (cap)**, daily /
monthly / total call counters and revenue for the window.
`404` = no destination on that number.

## 5. GET /concurrency — calls in flight right now

    curl -H "Authorization: Bearer KEY" "https://avortyx.io/api/stats/concurrency"

    {
      "total_cc": 7,
      "by_campaign": [ {"id": "...", "name": "23 JUNE", "cc": 7} ],
      "by_buyer":    [ {"id": "...", "name": "ADC11", "cc": 7} ],
      "taken_at": "2026-10-10T15:20:11Z"
    }

A snapshot at the moment of the request — the same figures the Live Monitor
shows.

---

## 6. GET /destinations — every destination with caps and calls

One row per destination visible to the key — the same fields as
`/tfn/{number}`: `id`, `tfn`, `name`, `buyer_id`, `buyer_name`, `enabled`,
`concurrency_cap`, `live_calls`, `daily_cap`, `daily_calls`, `monthly_cap`,
`monthly_calls`, `global_cap`, `global_calls` — plus `incoming_calls`, the
calls that reached the TFN in the date window (`date_from`/`date_to`,
defaulting to today in `timezone_name`). Cap counters stay on their natural
windows (today, this month, all time) whatever window you ask for;
`incoming_calls` is the window figure.

Filters: `buyer_id=<uuid>`, `enabled=true|false`, `has_calls=true` (only
destinations that received calls in the window). A buyer-created key sees
only that buyer's destinations.

    curl -H "Authorization: Bearer $KEY" \
      "https://avortyx.io/api/stats/destinations?has_calls=true&date_from=2026-10-01"

## 7. GET /buyers — extra fields and include_idle

Every `/buyers` row now also carries `status` (`active` / `paused` /
`archived`), `concurrency_cap` (summed over the buyer's enabled
destinations) and `live_calls` (summed the same way — the figure routing
caps against). Pass `include_idle=true` to also get buyers with no calls in
the window, as rows with zero counters.

    curl -H "Authorization: Bearer $KEY" \
      "https://avortyx.io/api/stats/buyers?include_idle=true"

## Ground rules

- **Read-only.** These endpoints change nothing.
- `null` money fields mean the key's role is not shown that figure — never
  treat them as zero.
- Metric definitions match the portal exactly: AHT is talk time from buyer
  pickup over connected calls; Dupe counts every duplicate that arrived.
- Keep the key secret; anyone holding it sees what its creator sees. Revoke
  in Settings → API keys.
