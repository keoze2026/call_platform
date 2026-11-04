# Deploy log

Sir, 4 Nov: "Keep a deploy log: what changed, when and who deployed it, so we
can trace which change caused a bug."

One line per deploy, newest at the bottom. Times are EST. Before a deploy goes
live it passes the checklist below; write the result in the last column.

## Pre-deploy checklist (Sir, 4 Nov)
1. All destinations off -> every test call "Not forwarded".
2. One destination on -> only that one gets calls.
3. Buyer filter on the Destinations page works.
4. Header Balance / Total correct.
5. Reports totals match the Call Log.

Items 1-2 are also automated in CI (`routing/test_not_forwarded.py`).

| When (EST) | Change | Server(s) | What changed | Deployed by | Checklist / proof |
|---|---|---|---|---|---|
| 2026-11-04 05:35 | CH-519 | B (web, web_calls, celery_worker, celery_beat) | Refused calls show "Not forwarded"; 15-min skip off (alert only); 77 test calls corrected | ZDFRS | CI 1,098 OK; 77 -> 0 on a line (query); live re-test pending; items 3-5 not run |

Earlier deploys (CH-514 failover, CH-516, CH-517) are described in CHANGES.md;
their exact times were not logged.
