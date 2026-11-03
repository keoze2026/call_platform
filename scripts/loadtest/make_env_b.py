#!/usr/bin/env python3
"""CH-469 load test, Server B: the settings for the TEST copy of call routing.

    python3 scripts/loadtest/make_env_b.py /opt/call_platform/.env /opt/call_platform/.env.loadtest

Reads the live .env and writes a copy that can only reach test data:
  - database   the copy avortyx_loadtest, never the live call_platform
  - Redis      database 9: its own route locks, its own task queue (no
               worker reads it, so no background job runs), its own live-page
               messages. The live portal and Celery use database 0.
  - outside    every service that would send, charge or look something up is
               switched off: Telegram, email, Revo, Twilio, Telnyx, number
               lookups, payments, Sentry, Asterisk control.
Everything else (secret key, shared secret with Server A, allowed hosts)
stays as it is, so routing behaves exactly as live.

Every overridden setting is written out explicitly, even when empty: the app
reads .env as well as the environment, so a setting left out would be read
from the live .env.
"""
import os
import sys
from urllib.parse import urlsplit, urlunsplit

TEST_DB = 'avortyx_loadtest'
TEST_REDIS_DB = '9'

OFF = {
    # messages
    'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': '', 'TELEGRAM_SUPPORT_CHAT_ID': '',
    'TELEGRAM_BOSS_CHAT_ID': '', 'TELEGRAM_SUPPORT_THREAD_ID': '', 'TELEGRAM_NOTIFY_THREAD_ID': '',
    'STATUS_CHAT_ID': '', 'REPORTS_BOT_TOKEN': '',
    'EMAIL_HOST': '', 'EMAIL_HOST_USER': '', 'EMAIL_HOST_PASSWORD': '',
    # outside services and lookups that cost money
    'REVO_API_KEY': '', 'TWILIO_ACCOUNT_SID': '', 'TWILIO_AUTH_TOKEN': '', 'TWILIO_TRUNK_SID': '',
    'TELNYX_API_KEY': '', 'IPQS_API_KEY': '', 'IPQS_ENABLED': 'False',
    'REALVALIDITO_API_KEY': '', 'REALVALIDITO_API_SECRET': '', 'DNC_CHECK_ENABLED': 'False',
    'ASSEMBLYAI_API_KEY': '', 'CAPITALIST_API_KEY': '', 'CAPITALIST_API_SECRET': '',
    'CAPITALIST_SECRET': '', 'COINGATE_API_KEY': '', 'SENTRY_DSN': '',
    # no control of the phone system from the test copy
    'ASTERISK_AMI_HOST': '127.0.0.1', 'ASTERISK_AMI_SECRET': '',
    'TFN_AUTO_TEST_ENABLED': 'False', 'CARRIER_WATCH_ENABLED': 'False',
}


def parse(text):
    """KEY=value lines as compose reads them: comments and blanks skipped,
    one pair of surrounding quotes removed."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()   # inline comment
        out[key] = value
    return out


def test_database(url):
    parts = urlsplit(url)
    if not parts.scheme.startswith('postgres'):
        raise SystemExit(f'Refused: DATABASE_URL is not PostgreSQL ({parts.scheme}).')
    if parts.path.strip('/') == TEST_DB:
        raise SystemExit(f'Refused: the live database is called {TEST_DB}.')
    return urlunsplit(parts._replace(path='/' + TEST_DB))


def test_redis(url):
    parts = urlsplit(url or 'redis://redis:6379/0')
    if parts.path.strip('/') == TEST_REDIS_DB:
        raise SystemExit(f'Refused: live Redis already uses database {TEST_REDIS_DB}.')
    return urlunsplit(parts._replace(path='/' + TEST_REDIS_DB))


def build(live):
    if 'DATABASE_URL' not in live:
        raise SystemExit('Refused: no DATABASE_URL in the live .env.')
    env = dict(live)
    env['DATABASE_URL'] = test_database(live['DATABASE_URL'])
    env['REDIS_URL'] = test_redis(live.get('REDIS_URL'))
    env.update(OFF)
    env['SERVER_LABEL'] = 'LOADTEST'
    return env


def main(src, dst):
    with open(src) as f:
        env = build(parse(f.read()))
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        for key, value in env.items():
            if '\n' in value:
                raise SystemExit(f'Refused: {key} has a line break.')
            f.write(f'{key}={value}\n')
    print(f'{dst}: test database {TEST_DB}, Redis database {TEST_REDIS_DB}, '
          f'{len(OFF)} outside services switched off')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
