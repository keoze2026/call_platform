# Business Rules — what Sir has set

The rules the platform must follow, in one place, as Sir (TM) gave them.
Each rule names the change that put it in the code (see CHANGES.md in this
repository, and the portal's CHANGES.md for the screen side).

**When Sir gives a new rule, add it here in the same change that builds it.**
A rule that is waiting for his OK goes under "Waiting for Sir", not above.

Last updated: 24 Oct 2026.

---

## 1. Money

### 1.1 What customers (user workspaces) pay — CH-255
| Charge | Amount | When |
|---|---|---|
| Per minute | **$0.045** | Every **answered** call, converted or not. Each call rounded **up to the whole minute** (61 s = 2 min). |
| Call recording | $0.0025 a minute | Per answered minute, when the call is recorded. |
| VoIP Shield | $0.01 a call | Every call the Shield **checked** — answered, missed or refused. |
| Rejected call | $0.015 a call | A call refused before routing (blocked, cap, no rule…). |
| TFN purchase | **$20** a number | When the workspace provisions a number. |
| Portal | **$49.99** a month | Every 30 days from the last charge. |
| Markup | 0% | On the per-minute rate only. |

- Amounts are kept to four decimals ($0.015 is $0.015, not $0.02).
- A call that happened is **always charged**, even if the balance ran out
  during it or it was let through on the credit limit — the balance may go
  below zero.
- **Out of balance:** a user workspace's call is **missed** with the comment
  **"Insufficient Funds"**. No fee is taken for it.
- **Our own portal is never refused for balance.** "Our portal" = any
  workspace an owner (superuser) login belongs to — today **Avortyx** only.
  Never give an owner login to a customer's workspace: its calls would no
  longer be refused for balance.

### 1.2 What carriers charge us — CH-254 (Owner › Carrier Costs)
| Carrier | Per TFN / month | Monthly charge | Per minute |
|---|---|---|---|
| XOLO High Risk (XHR) | $15 | **$350 for High + Low Risk together** (set once, on XHR) | $0.040 |
| XOLO Low Risk / forwarding (XLR) | $5 | — | $0.015 |
| Freedom (KMQ) | $5 | none | $0.035 |
| XOLO termination (routing on to buyers) | — | $350 | $0.004 |

- The month is the **Eastern** calendar month. The page total is every TFN
  carrier **plus** routing.
- Minutes are exact seconds until an invoice shows a carrier rounds
  (Edit rates › Minutes billed: 6 / 30 / 60 s).
- A TFN keeps the price it was added at; a new price applies to numbers
  added after it (CH-189).
- Routing cost opens on **Today** (CH-253).

### 1.3 What buyers pay us and publishers earn — CH-247, CH-248, CH-250
- Each campaign destination bills by **its own rule**, as on Revo:
  **Rate** and **Convert ON** = Incoming Call / Call Connected / Call Length
  (seconds). Adding a destination to a campaign **requires** both (CH-248).
- **Incoming:** every call forwarded converts, answered or not.
  **Connected:** converts when the buyer answers. **Length:** answered and at
  least the seconds set.
- **Payout** is each call's own: the tracking number's payout if it has one
  (e.g. Yahoo Native $5), else the campaign's, as it was when the call came
  in. Changing a payout later never changes past calls (CH-250).

---

## 2. Reports — how calls are shown

### 2.1 Chart candles and Total Calls ring — CH-250, CH-251, CH-252
- **Two colours only: blue and red.**
- **Blue:** Converted, and Not Converted (answered but not converted — listed
  on its own in the legend and tooltip, but drawn blue). A missed call that
  converted (destination paid on Incoming) is blue.
- **Red:** missed **and** not converted — missed, overflow missed, campaign
  out of concurrency, a buyer who missed under a Connected rule, no
  destination, blocked, failed, Insufficient Funds.
- Example, 10 calls, 6 answered / 4 missed: Incoming rule → 10 blue;
  Connected rule → 6 blue, 4 red.

### 2.2 Status and Fail Reason wording
| Situation | Shown as |
|---|---|
| Missed but converted (destination on Incoming) | Status **"Missed With Conversion"** (CH-250; Sir may pick other words) |
| Fail Reason | Only for calls that did **not** convert; converted calls show "—" (CH-250) |
| Destination side could not take the call | **"No Destination"** (CH-151) |
| Caller on Blocked Numbers | Missed, **"Callerid Blocked"** |
| Cap reached | **"Overflow Missed"** |
| Switch never reported the end | **"Not Connected"** (CH-229) |
| Out of balance (user workspace) | Missed, **"Insufficient Funds"** (CH-255) |
| Duplicate on "Different" with nowhere left | Missed, **"Duplicate call"** |

### 2.3 Totals
- **Repeated** counts any earlier call from the caller to the campaign inside
  its duplicate window, like Revo's Dupe (CH-230). Clicking it lists those
  calls and it turns **yellow** (CH-236, CH-237).
- A sender's retries of one call are one row (CH-215, CH-223).
- An answer under a second counts as connected, as Revo counts it (CH-222).

---

## 3. Routing

### 3.1 Priority and weight — CH-249
- A call goes to the **highest priority** first. Inside one priority it is
  shared **by weight** (100/100/100 evenly, 75/25 three to one).
- The next priority gets a call only when no line of the higher one can take
  it. The routed TFN is exactly the one dialled.

### 3.2 Repeated callers — CH-241, CH-242
- **Different:** a repeat caller **never** reaches a TFN that already had him
  — in **any** campaign of the workspace, remembered for a year, whether that
  call was answered or not. No "could go". When every TFN has had him, the
  call is missed "Duplicate call".
- **Original / Normal:** the caller may go back to the same destination
  (Original keeps him on the one that answered him).

### 3.3 Caps
- **0** on concurrency, daily or monthly cap means **no limit** (CH-235).

---

## 4. Access and privacy

- **Shared campaigns:** publishers on one campaign see only **their own**
  TFNs, calls and counters — never another publisher's (CH-238).
- **API keys:** visible and manageable **only by the workspace's main login**
  (its first admin) and the platform owner — not publishers, buyers, other
  admins or managers (CH-243).
- **TFN Test Calls:** run inside our own phone system; **nothing goes to
  XOLO** or any carrier, no buyer is rung, nothing is billed (CH-239).
  Publishers can test **only their own** TFNs, when "TFN Test Calls" is on in
  their Publisher Access (CH-240).
- Do not show customers or partners internal codes or carrier details.

---

## 5. Notifications

- **Watchdog (Telegram):** `🔴 AVORTYX | Title` … `Detected: {time}` and
  `🟢 AVORTYX | Title` … `Resolved: {time}`, Eastern time, professional
  wording, no status codes or paths (CH-244, CH-245).
- **TFN Expired:** "1 TFN Expired / The following number has expired and is
  no longer receiving calls: 18888910358 / Add a replacement TFN to keep
  calls routing without interruption." No "until 12 PM" line; it clears by
  itself at 12 PM Eastern (CH-246).

---

## Waiting for Sir (not built)

- **Repeated callers and publisher payout:** on Revo a repeated caller pays
  the buyer but the publisher gets **$0**. Not built until Sir says yes.
- **"Missed With Conversion"** — Sir may choose two other words.
- **Carrier rounding** (per second / 6 s / per minute) and **part-month TFN
  fees** — from each carrier's invoice.
- **Buyer Telegram reports** (Excel per destination, set time per buyer,
  "No Recording Link") — wording to be chosen by Sir.

## Will not be built
- Changing a call's real status in a buyer's report (e.g. Not Connected shown
  as Connected): reports must show what happened.
