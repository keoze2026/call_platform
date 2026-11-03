#!/usr/bin/env python3
"""CH-469 load test only (Server A): the routing script for the test number.

Installed as /usr/share/asterisk/agi-bin/route_call_loadtest.py by
scripts/loadtest/a_loadtest.sh setup, removed by its cleanup. Only the test
dial plan ([loadtest-in]) calls it; live calls keep using route_call.py.

It runs the live route_call.py unchanged, with two settings replaced:
  - Server B's routing  -> the TEST routing, http://10.20.0.2:8011
                           (live routing is 8001)
  - the fallback state  -> /var/lib/avortyx/fallback-loadtest
                           (live is /var/lib/avortyx/fallback)
Both are set here whatever the environment says about the live ones, so a
test call can never reach live routing or the live fallback journal.
"""
import os
import runpy

TEST_B = os.environ.get('LOADTEST_B_URL', 'http://10.20.0.2:8011')
TEST_STATE = os.environ.get('LOADTEST_STATE', '/var/lib/avortyx/fallback-loadtest')
LIVE_AGI = os.environ.get('LOADTEST_LIVE_AGI', '/usr/share/asterisk/agi-bin/route_call.py')

os.environ['AVX_ROUTE_BASE'] = TEST_B
os.environ['AVX_FALLBACK_DIR'] = TEST_STATE
runpy.run_path(LIVE_AGI, run_name='__main__')
