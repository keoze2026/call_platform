# TFN test call: one-time Asterisk setup (CH-175)

The platform places the test call through the Asterisk manager (AMI). Without
this setup the test still works, but only checks the platform side
(`mode: platform_check`). With it the test is a real call (`mode: real_call`).

Run on the VPS as root.

1. Find the address the containers use to reach the host:

   docker network inspect call_platform_default -f '{{(index .IPAM.Config 0).Gateway}}'

   Call it GW (for example 172.18.0.1). If the network name differs, list them
   with `docker network ls`.

2. Add this to `/etc/asterisk/manager.conf` (replace GW and the secret):

   [general]
   enabled = yes
   port = 5038
   bindaddr = GW

   [avortyx]
   secret = A_LONG_RANDOM_SECRET
   deny = 0.0.0.0/0.0.0.0
   permit = 172.16.0.0/255.240.0.0
   read = call
   write = originate

   Bound to the Docker bridge only, so it is not reachable from the internet.

3. `asterisk -rx "manager reload"` then `asterisk -rx "manager show users"`.

4. Add to `/opt/call_platform/.env`:

   ASTERISK_AMI_HOST=GW
   ASTERISK_AMI_PORT=5038
   ASTERISK_AMI_USER=avortyx
   ASTERISK_AMI_SECRET=A_LONG_RANDOM_SECRET
   TFN_TEST_CALLER_ID=+1XXXXXXXXXX
   TFN_TEST_TRUNK=carrier

   `TFN_TEST_CALLER_ID` is the caller ID on the test call. Use a number the
   carrier lets us present (one we own). The inbound leg is recognised by this
   number, so no real customer should call from it. An earlier manual
   originate with no caller ID was refused with 403 (From: Anonymous).

5. Recreate the containers so they read the new env:

   cd /opt/call_platform && docker compose up -d --force-recreate web celery_worker celery_beat

How it works: the worker originates `PJSIP/<number>@carrier` from our Asterisk.
The carrier sends it back to us as an inbound call to that number. The AGI
route request arrives with the test caller ID, the platform checks the number,
campaign and routing, records the result and answers `hangup`. No CallLog row,
no fee, no buyer is rung.

Carrier limits seen on 20 Oct: the SBC answers 500 for a call from our trunk
to our own TFN, and the Kamailio in front of it answers 403 for a caller ID it
does not know. When the carrier refuses the test call, the test falls back to
the platform check and the reason says "Real call not placed (the carrier
refused the test call ...)". The carrier has to allow it for real calls to work.
