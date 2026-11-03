#!/bin/bash
# CH-469 load test only (Server A): the hang-up report for the test calls.
#
# Installed as /usr/local/bin/call_ended_loadtest.sh by
# scripts/loadtest/a_loadtest.sh setup, removed by its cleanup. Only the test
# dial plan ([loadtest-call-end]) and the test sync call it.
#
# Runs the live call_ended.sh unchanged, reporting to the TEST routing on
# Server B, with the test's own spool, held-report markers and log. These are
# set here whatever the environment says about the live ones.
export CALL_ENDED_URL="${LOADTEST_B_URL:-http://10.20.0.2:8011}/api/twilio/asterisk/call-ended/"
export CALL_ENDED_SPOOL="${LOADTEST_SPOOL:-/var/spool/call_ended_loadtest}"
export CALL_ENDED_PENDING="${LOADTEST_STATE:-/var/lib/avortyx/fallback-loadtest}/pending"
export CALL_ENDED_LOG="${LOADTEST_LOG:-/tmp/call_end_loadtest.log}"
exec "${LOADTEST_LIVE_ENDED:-/usr/local/bin/call_ended.sh}" "$@"
