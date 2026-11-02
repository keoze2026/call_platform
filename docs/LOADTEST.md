# 600-call load test on Server A (CH-465)

Sir's conditions:
- 600 calls at once for at least 30 minutes;
- Server B switched off part of the way;
- buyer concurrency limits still applied while B is off;
- every call made while B was off appears on B afterwards, billed.

**Nothing here touches the live system.** SIPp on Server A plays both sides.
The test callers (127.0.0.2) dial the test number on A's Asterisk, and the
test "buyers" (127.0.0.3) answer. No call leaves Server A, no carrier is
used, and no real buyer is rung. The test campaign, its calls and their
charges exist only in Server B's copy of the database. Celery is off on B,
so no Telegram messages are sent.

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
| `setup_b.py` | B | Creates the test campaign, number, buyers and lines. Refuses to run anywhere but Server B. |
| `report_b.py` | B | Counts the test calls on B: by status and by buyer, how many A routed alone, billing, and the most calls each buyer had at once. |
| `pjsip_loadtest.conf`, `extensions_loadtest.conf` | A | The test callers' and test buyers' SIP settings, and the test dial plan. The dial plan follows the same steps as live calls, but the buyer leg goes to SIPp on A. |
| `caller.xml`, `make_pcap.py` | A | The SIPp test caller and its audio. |
| `watch.sh` | A | Prints one line every 10 seconds: calls up, calls per test buyer, held reports, load, memory. |
| `install_sipp.sh` | A | Installs SIPp 3.7.7 as `/usr/local/bin/sipp` (see above). |

The step-by-step commands, and which PC runs each one, are in the install
message for CH-465.

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

The cleanup steps are part of the CH-465 install message:
- remove the test settings from Asterisk;
- delete the test recordings;
- clear A's fallback journal;
- reload B's copy of the database;
- empty B's Redis queue.
