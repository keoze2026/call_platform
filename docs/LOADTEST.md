# 600-call load test on Server A (CH-465, CH-469)

Sir's conditions:
- 600 calls at once for at least 30 minutes;
- Server B switched off part of the way;
- buyer concurrency limits still applied while B is off;
- every call made while B was off appears on B afterwards, billed;
- while the calls run: 1,000 TFNs added, a campaign paused and played, and a
  30-day report run.

**Nothing here touches live data or live calls.** SIPp on Server A plays both
sides. The test callers (127.0.0.2) dial the test number on A's Asterisk, and
the test "buyers" (127.0.0.3) answer. No call leaves Server A, no carrier is
used, and no real buyer is rung.

## Live and test, side by side (CH-469)

Since the move, Server B holds the LIVE database, so the test runs on its own
copy. Only the test number's calls use it.

| | Live | Test |
|---|---|---|
| Routing on B | `web_calls`, 10.20.0.2:8001 | `avx-loadtest-routing`, 10.20.0.2:8011 |
| Database | `call_platform` | `avortyx_loadtest`, copied from live at setup |
| Redis | database 0 | database 9 (no worker: no background jobs run) |
| Telegram, email, Revo, lookups | on | off (`make_env_b.py`) |
| A's routing script | `route_call.py` | `route_call_loadtest.py` |
| A's hang-up report | `call_ended.sh`, `/var/spool/call_ended` | `call_ended_loadtest.sh`, `/var/spool/call_ended_loadtest` |
| A's fallback state | `/var/lib/avortyx/fallback` | `/var/lib/avortyx/fallback-loadtest` |
| A's sync | `avx-fallback-sync` | `avx-loadtest-sync`, `avx-loadtest-chansync` (temporary) |
| Recordings | `/var/spool/asterisk/recordings` | `/var/spool/asterisk/recordings-loadtest` |

Every script that writes to a database (`setup_b.py`, `during_b.py`,
`report_b.py`) refuses to run on any database but `avortyx_loadtest`.

## The run

All commands start with `cd /opt/call_platform`.

| # | Where | Command |
|---|---|---|
| 1 | Server B | `bash scripts/loadtest/b_loadtest.sh setup` |
| 2 | Server A | `bash scripts/loadtest/a_loadtest.sh setup` |
| 3 | Server A | `bash scripts/loadtest/a_loadtest.sh buyers` |
| 4 | Server A | `bash scripts/loadtest/a_loadtest.sh callers 50 200`: the short check |
| 5 | Server B | `bash scripts/loadtest/b_loadtest.sh report`: 200 calls, 0 open, both buyers OK |
| 6 | Server A | `bash scripts/loadtest/a_loadtest.sh callers 600 12500`: about 32 minutes |
| 7 | Server A, second window | `bash scripts/loadtest/a_loadtest.sh watch` |
| 8 | Server B, minute 5 | `bash scripts/loadtest/b_loadtest.sh during tfns`, then `during pause`, then `during report` |
| 9 | Server A, minute 8 | `bash scripts/loadtest/a_loadtest.sh snapshot` (CPU, memory, jitter and loss) |
| 10 | Server B, minute 10 | `bash scripts/loadtest/b_loadtest.sh down`: Server A routes alone |
| 11 | Server B, minute 20 | `bash scripts/loadtest/b_loadtest.sh up` |
| 12 | Server B, after the end | `bash scripts/loadtest/b_loadtest.sh report` |
| 13 | Server A | `bash scripts/loadtest/a_loadtest.sh cleanup` |
| 14 | Server B | `bash scripts/loadtest/b_loadtest.sh cleanup` |

## The test setup

| | |
|---|---|
| Test number | `+19995550100` (area code 999 does not exist) |
| Campaign | `LOADTEST`, in our own workspace |
| Buyer LT-A | line `+19995550001`, at most **400** calls at once |
| Buyer LT-B | line `+19995550002`, at most **250** calls at once |
| Calls | 600 at once, 90 s each with audio, 16,000 in total (about 40 min) |
| Server B off | from minute 10 to minute 20 |

400 + 250 = 650, so all 600 test calls connect. The two limits show that
concurrency is enforced: by B, and by A while B is off. Every call is
recorded, as live calls are. The recordings are deleted afterwards.

SIPp itself runs on Server A as well, so A carries more load in this test
than with real traffic.

## The test program: SIPp 3.7.7, not Ubuntu's 3.7.2 (CH-468)

Ubuntu's SIPp, 3.7.2 (package `sip-tester`), has a bug when it plays audio
on many calls. On every call it reads the audio file again and never frees
the previous copy. It also frees a record that the calls already playing
still use. Memory grows by about 1 MB per call, and the test callers freeze
or crash (`Segmentation fault`, `buffer overflow detected`). SIPp 3.7.3
fixed this ("Fix crash when using PCAP play with more than one call") and
restored the `-mp` option.

Same test caller, 600 calls at once, 3,000 calls of 60 s:

| | Memory | Calls failed |
|---|---|---|
| SIPp 3.7.2 | 0.8 GB, growing to 2.0 GB | 0 (this run) |
| SIPp 3.7.7 | 24 MB, flat | 0 |

At 16,000 calls, 3.7.2 would need about 11 GB.

Install 3.7.7 once on Server A (about 1 minute, lowest CPU priority; it does
not touch Asterisk or Kamailio):

    sudo bash scripts/loadtest/install_sipp.sh

Then run the test with `/usr/local/bin/sipp`, for both the callers and the
buyers. Ubuntu's `/usr/bin/sipp` is left as it is.

## Files (`scripts/loadtest/`)

| File | Runs on | What it is |
|---|---|---|
| `b_loadtest.sh` | B | Every step on Server B: the test copy, its routing, down/up, Sir's extras, the report, cleanup. |
| `make_env_b.py` | B | The test routing's settings: test database, Redis database 9, outside services off. |
| `setup_b.py` | B | Creates the test campaign, number, buyers and lines. Test database only. |
| `during_b.py` | B | Sir's extras during the load: 1,000 TFNs, pause and play, 30-day report. Test database only. |
| `report_b.py` | B | Test database only. Counts the test calls on B: by status and by buyer, how many A routed alone, billing, and the most calls each buyer had at once. |
| `pjsip_loadtest.conf`, `extensions_loadtest.conf` | A | The test callers' and test buyers' SIP settings, and the test dial plan. The dial plan follows the same steps as live calls, but the buyer leg goes to SIPp on A. |
| `caller.xml`, `make_pcap.py` | A | The SIPp test caller and its audio. |
| `watch.sh` | A | Prints one line every 10 seconds: calls up, calls per test buyer, held reports, load, memory. |
| `install_sipp.sh` | A | Installs SIPp 3.7.7 as `/usr/local/bin/sipp` (see above). |
| `a_loadtest.sh` | A | Every step on Server A: setup, test buyers, test callers, watch, snapshot, cleanup. |
| `route_call_loadtest.py`, `call_ended_loadtest.sh` | A | The live routing script and hang-up report, pointed at the test copy. |

The steps are in "The run" above.

## Passing

- `watch.sh` shows `calls=600` from about minute 2 until the end.
- LT-A never goes above 400 and LT-B never above 250, including minutes 10 to 20.
- SIPp: failed calls close to 0.
- `report_b.py`, once the test has finished and B is back:
  - every test call is on B;
  - about 4,000 were "routed by Server A while B was off";
  - "still open" is 0;
  - the completed calls are charged;
  - both buyers say "OK".

## Afterwards

`a_loadtest.sh cleanup` (Server A) removes:
- the test dial plan, the test SIP settings and the two test scripts;
- the test sync services;
- the test fallback state, the test spool and the test recordings.

The logs stay in `/root/loadtest`.

`b_loadtest.sh cleanup` (Server B) removes:
- the test routing;
- the test database;
- Redis database 9.

It then prints the live call count, to show live data was not changed.
