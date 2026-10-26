# Cancel hold (CH-296) - lab plan before anything goes live

Goal: when the caller hangs up while the buyer rings, hold our CANCEL for
1000 ms so a buyer who picks up in that moment is recorded Answered, 1 s.

## Parts
1. Backend (done, tested): POST /api/twilio/asterisk/held-answer/ and the
   hang-up handling (routing/held_answer.py). Needs one web restart.
2. SIP relay: Kamailio, docs/kamailio/kamailio.cfg (draft).
3. Asterisk: endpoint carrier-out + dialplan switch (drafts in this folder).

## Lab (on the server, no live traffic touched)
1. `apt-get install -y kamailio kamailio-utils-modules sip-tester`
   (http_client is in kamailio-utils-modules; sipp is sip-tester).
2. Start Kamailio with the draft config on 127.0.0.1:5070 and a test port,
   pointed at a local SIPp "buyer" instead of XOLO.
3. SIPp caller -> Kamailio -> SIPp buyer, three scenarios:
   a. buyer answers, caller talks, BYE              -> passes through unchanged
   b. caller CANCELs, buyer never answers           -> CANCEL reaches the buyer
                                                       about 1000 ms later, 487
   c. caller CANCELs, buyer answers 400 ms later    -> 200 OK reaches the caller,
                                                       ACK + BYE, CANCEL dropped,
                                                       held-answer report sent
   Also: the caller re-sends its CANCEL every 500 ms (SIP timer) - the
   retransmissions must be absorbed, not forwarded twice.
4. Only then: carrier-out + dialplan on the live server, switched on for ONE
   test caller (database put avortyx cancel_hold_caller <our test phone>),
   at a quiet time. Check: XOLO accepts calls from port 5080, recordings,
   MAX_DURATION, no early answer, hang-up reports.
5. Then all calls: database put avortyx cancel_hold 1. Watch one day with
   docs/asterisk/size_cancel_hold.py; raise HOLD_MS to 1500 if needed.

Rollback at any moment: asterisk -rx "database del avortyx cancel_hold"
(no reload, next call goes the old way).
