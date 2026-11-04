"""CH-543: AI audit from each side of the call - Sir's four labels, per-side
numbers saved, pushed live, Telegram, main account only, review and accuracy.

The listening (Silero on each side, beeps) runs on the host in
scripts/voice_check; here its results are fed to voice_results as it sends them.

Run:  python manage.py test routing.test_ai_labels
"""
import importlib.util
import io
import json
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from buyers.models import Buyer
from campaigns.models import Campaign
from routing import ai_labels
from routing.models import CallLog
from routing.signals import mirror_call_log


def two(caller, agent, beep=0.0, first=None, no_data=False, cid='x'):
    return {'id': cid, 'channels': 2, 'speech': caller + agent, 'audio': 40, 'caller_speech': caller,
            'agent_speech': agent, 'beep': beep, 'caller_first': first, 'no_data': no_data}


@override_settings(VOICE_MIN_SECONDS=1.0, AI_BEEP_SECONDS=0.3)
class LabelRuleTests(TestCase):
    def label(self, r):
        return ai_labels.decide(r)['label']

    def test_sirs_four_labels(self):
        self.assertEqual(self.label(two(6.0, 9.0, first=3.2)), 'valid')
        self.assertEqual(self.label(two(0.0, 0.0)), 'dead_caller')
        self.assertEqual(self.label(two(0.0, 5.5)), 'agent_only')            # opening, customer silent
        self.assertEqual(self.label(two(0.2, 5.5, beep=1.4)), 'agent_beep')  # opening + beeps, no human

    def test_audio_fault_only_when_no_data(self):
        r = ai_labels.decide(two(0, 0, no_data=True))
        self.assertEqual((r['label'], r['fault']), ('dead_caller', True))
        self.assertIn('Possible audio fault', r['comment'])
        self.assertFalse(ai_labels.decide(two(0, 0))['fault'])               # a quiet line is not a fault

    def test_confidence_lower_near_the_line(self):
        self.assertGreater(ai_labels.decide(two(8, 9))['confidence'], ai_labels.decide(two(1.2, 9))['confidence'])
        self.assertGreater(ai_labels.decide(two(0, 5, beep=2))['confidence'],
                           ai_labels.decide(two(0, 5, beep=0.4))['confidence'])

    def test_one_channel_calls_from_before_the_split(self):
        self.assertEqual(self.label({'id': 'x', 'speech': 0.0, 'audio': 30}), 'dead_caller')
        self.assertEqual(self.label({'id': 'x', 'speech': 7.0, 'audio': 30}), 'mixed')


@override_settings(ALLOWED_HOSTS=['*'], AI_AUDIT_EMAILS='audit@x.io',VOICE_MIN_SECONDS=1.0, AI_BEEP_SECONDS=0.3,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AiAuditFlowTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-10', status='active')
        self.buyer = Buyer.objects.create(organization=self.org, name='Q35')
        self.n = 0

    def call(self, **kw):
        self.n += 1
        c = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyer,
                                   caller_number=f'+1555{self.n:07d}', called_number='+18443754175',
                                   twilio_call_sid=f's{self.n}', status='completed', duration=40,
                                   destination_number='+18444896670',
                                   ended_at=timezone.now() - timedelta(minutes=2), **kw)
        CallLog.objects.filter(pk=c.pk).update(recording_url=f'https://rec.example.com/recordings/{c.id}.mp3')
        mirror_call_log(c.id)
        return CallLog.objects.get(pk=c.pk)

    def results(self, rows):
        out = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO('\n'.join(json.dumps(r) for r in rows) + '\n')):
            call_command('voice_results', stdout=out)
        return out.getvalue()

    def api(self, role, **kw):
        self.n += 1
        # CH-548: our staff here is the audit team login (AI_AUDIT_EMAILS).
        email = 'audit@x.io' if role == 'admin' else f'{role}{self.n}@x.io'
        u = User.objects.create(username=f'{role}{self.n}', email=email, organization=self.org,
                                role=role, **kw)
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')

    def test_pending_split_links_reach_the_listener(self):
        c = self.call()
        out = io.StringIO()
        call_command('voice_pending', stdout=out)
        item = json.loads(out.getvalue().splitlines()[0])
        self.assertEqual(item['split'], {
            'caller': f'https://rec.example.com/recordings/split/{c.id}-caller.wav',
            'agent': f'https://rec.example.com/recordings/split/{c.id}-agent.wav'})

    def test_each_side_is_saved_with_the_label_and_reports_copy(self):
        c = self.call()
        self.results([two(0.0, 6.2, beep=1.3, cid=str(c.id))])
        c.refresh_from_db()
        self.assertEqual((c.voice_channels, c.voice_caller_seconds, c.voice_agent_seconds, c.voice_beep_seconds),
                         (2, 0.0, 6.2, 1.3))
        self.assertEqual((c.ai_label, c.ai_confidence, c.voice_audio_fault), ('agent_beep', 90, False))
        self.assertIsNotNone(c.ai_checked_at)
        self.assertEqual(CallRecord.objects.get(pk=c.pk).ai_label, 'agent_beep')
        self.assertEqual(c.voice_label, 'voice')                           # the agent spoke

    def test_open_pages_are_told_at_once(self):
        c = self.call()
        layer = get_channel_layer()
        ch = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(f'reports_org_{self.org.id}', ch)
        self.results([two(5, 5, cid=str(c.id))])
        self.assertEqual(async_to_sync(layer.receive)(ch)['data']['event'], 'ai.checked')

    @override_settings(VOICE_TELEGRAM_ALERTS=True, REPORTS_BOT_TOKEN='t')
    def test_telegram_names_sirs_labels_with_each_side(self):
        from analytics.models import TeamReportChat
        TeamReportChat.objects.create(organization=self.org, chat_id='-100', title='Team')
        silent, opening, good = self.call(), self.call(), self.call()
        with mock.patch('analytics.team_report.telegram') as tg:
            self.results([two(0, 0, no_data=True, cid=str(silent.id)), two(0, 4.0, cid=str(opening.id)),
                          two(7, 8, cid=str(good.id))])
        text = tg.call_args[0][1]['text']
        self.assertIn('No voice - 2 calls', text)
        self.assertIn('<b>No Voice | Dead Caller (Possible audio fault)</b>', text)
        self.assertIn('<b>No Voice | No Beep or Customer</b>', text)
        self.assertIn('caller 0.0s, agent 4.0s', text)
        self.assertNotIn(str(good.id), text)

    def test_buyers_and_publishers_get_nothing(self):
        c = self.call()
        for client in (self.api('buyer', buyer=self.buyer), self.api('publisher')):
            self.assertEqual(client.get('/api/ai-audit/calls').status_code, 403)
            self.assertEqual(client.get('/api/ai-audit/status').status_code, 403)
            self.assertEqual(client.post(f'/api/ai-audit/calls/{c.id}/review', data='{"correct": true}',
                                         content_type='application/json').status_code, 403)

    def test_page_shows_pending_then_label_with_counts(self):
        waiting, done = self.call(), self.call()
        self.results([two(0, 0, cid=str(done.id))])
        admin = self.api('admin')
        day = timezone.now().astimezone(ai_labels_et()).date().isoformat()
        data = admin.get(f'/api/ai-audit/calls?date={day}').json()
        by_id = {r['id']: r for r in data['items']}
        self.assertEqual(by_id[str(waiting.id)]['ai_status'], 'pending')
        self.assertEqual((by_id[str(done.id)]['ai_status'], by_id[str(done.id)]['ai_label_name']),
                         ('checked', 'No Voice | Dead Caller'))
        self.assertEqual((data['counts']['pending'], data['counts']['dead_caller'], data['counts']['no_voice']),
                         (1, 1, 1))
        only = admin.get(f'/api/ai-audit/calls?date={day}&label=pending').json()['items']
        self.assertEqual([r['id'] for r in only], [str(waiting.id)])

    def test_correct_and_wrong_make_the_accuracy(self):
        a, b, c = self.call(), self.call(), self.call()
        self.results([two(0, 5, cid=str(a.id)), two(0, 5, cid=str(b.id)), two(0, 0, cid=str(c.id))])
        admin = self.api('admin')
        post = lambda cid, body: admin.post(f'/api/ai-audit/calls/{cid}/review', data=json.dumps(body),
                                            content_type='application/json')
        self.assertEqual(post(a.id, {'correct': True}).json()['review_correct'], True)
        r = post(b.id, {'correct': False, 'label': 'agent_beep', 'note': 'beeps under the agent'})
        self.assertEqual((r.json()['review_label'], r.json()['review_correct']), ('agent_beep', False))
        self.assertEqual(post(c.id, {'correct': False, 'label': 'banana'}).status_code, 400)
        acc = admin.get('/api/ai-audit/status').json()['accuracy']
        self.assertEqual((acc['reviewed'], acc['correct'], acc['percent']), (2, 1, 50.0))
        self.assertEqual(acc['per_label']['agent_only'], {'reviewed': 2, 'correct': 1, 'percent': 50.0})


def ai_labels_et():
    from zoneinfo import ZoneInfo
    return ZoneInfo('America/New_York')


class SplitChannelsScriptTests(TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location('split_channels', 'deploy/split_channels.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_today_on_server_a_after_ch535(self):
        sc = self.load()
        src = ('[from-carrier]\nexten => _X.,1,NoOp()\n same => n(dial),Set(RECFILE=/r/${CALL_LOG_ID}.wav)\n'
               ' same => n,ExecIf($["${RECORD_CALL}" != "0"]?MixMonitor(${RECFILE}))   ; record-from-dial (CH-535)\n'
               ' same => n,Dial(PJSIP/x)\n')
        out = sc.transform(src, with_n=True, split_dir='/s')
        self.assertIn('MixMonitor(${RECFILE},nr(/s/${CALL_LOG_ID}-caller.wav)t(/s/${CALL_LOG_ID}-agent.wav))', out)
        self.assertIn('; split-channels (CH-543)', out)
        self.assertEqual(len(src.splitlines()), len(out.splitlines()))
        self.assertEqual(sum(a != b for a, b in zip(src.splitlines(), out.splitlines())), 1)
        with self.assertRaises(sc.Stop):
            sc.transform(out)

    def test_keeps_b_and_works_without_n(self):
        sc = self.load()
        src = open('docs/asterisk/server-a-from-carrier-2026-11-04.conf').read()
        out = sc.transform(src, with_n=False, split_dir='/s')
        self.assertIn('MixMonitor(${RECFILE},br(/s/${CALL_LOG_ID}-caller.wav)t(/s/${CALL_LOG_ID}-agent.wav))', out)
        self.assertNotIn(':', out.split('MixMonitor(${RECFILE},')[1].split(')t(')[0])   # ExecIf-safe

    def test_stops_when_the_recorder_is_not_there(self):
        sc = self.load()
        with self.assertRaises(sc.Stop):
            sc.transform('[from-carrier]\nexten => _X.,1,Dial(PJSIP/x)\n')


class SplitChannelsHelpTests(TestCase):
    """CH-544: Asterisk's own help marks the words up: *receive* audio feed."""

    def test_reads_the_options_from_asterisk_help(self):
        sc = SplitChannelsScriptTests.load(self)
        help_text = ('    r(file): Use the specified file to record the *receive* audio feed. Like with\n'
                     '    t(file): Use the specified file to record the *transmit* audio feed. Like\n'
                     '    n: When the *r* or *t* option is used, MixMonitor will insert silence into the\n')
        with mock.patch.object(sc, 'run', return_value=(0, help_text)):
            self.assertEqual(sc.mixmonitor_options(), (True, True))
        with mock.patch.object(sc, 'run', return_value=(0, help_text.replace('    n: When', '    x: When')
                                                         .replace('insert silence', 'add nothing'))):
            self.assertEqual(sc.mixmonitor_options(), (True, False))
        with mock.patch.object(sc, 'run', return_value=(0, '    b: Only save audio while bridged.\n')):
            self.assertEqual(sc.mixmonitor_options(), (False, False))

    def test_colour_codes_in_the_help_and_the_version_fallback(self):
        # CH-545: Server A, Asterisk 20.6 - the help read by a program carries
        # colour codes around the marked words.
        sc = SplitChannelsScriptTests.load(self)
        e = '\x1b[1;36m'
        coloured = (f'    r(file): Use the specified file to record the {e}*receive*\x1b[0m audio feed. Like\n'
                    f'    t(file): Use the specified file to record the {e}*transmit*\x1b[0m audio feed. Like\n'
                    f'    n: When the {e}<r>\x1b[0m or {e}<t>\x1b[0m option is used, MixMonitor will insert silence into\n')
        with mock.patch.object(sc, 'run', return_value=(0, coloured)):
            self.assertEqual(sc.mixmonitor_options(), (True, True))

        def fake(*args):
            if args[0] == 'core show version':
                return 0, 'Asterisk 20.6.0~dfsg+~cs6.13.40431414-2build5 built by nobody'
            return 0, 'help we cannot read'
        with mock.patch.object(sc, 'run', side_effect=fake):
            self.assertEqual(sc.mixmonitor_options(), (True, False))


@override_settings(ALLOWED_HOSTS=['*'], VOICE_MIN_SECONDS=1.0, AI_BEEP_SECONDS=0.3,
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class BackfillAndCommentTests(AiAuditFlowTests):
    """CH-546."""

    def test_calls_checked_before_the_labels_get_one(self):
        silent = self.call(voice_label='no_voice', voice_seconds=0.0)
        talk = self.call(voice_label='voice', voice_seconds=12.0)
        waiting = self.call()                                          # not listened to yet: stays pending
        out = io.StringIO()
        call_command('ai_backfill', stdout=out)
        self.assertIn('would label 2', out.getvalue())
        self.assertEqual(CallLog.objects.exclude(ai_label='').count(), 0)      # dry run
        call_command('ai_backfill', '--apply', stdout=io.StringIO())
        silent.refresh_from_db(); talk.refresh_from_db(); waiting.refresh_from_db()
        self.assertEqual((silent.ai_label, talk.ai_label, waiting.ai_label), ('dead_caller', 'mixed', ''))
        self.assertEqual(CallRecord.objects.get(pk=silent.pk).ai_label, 'dead_caller')
        self.assertIsNone(waiting.ai_checked_at)

    @override_settings(VOICE_TELEGRAM_ALERTS=True, REPORTS_BOT_TOKEN='t')
    def test_telegram_carries_the_ai_comment(self):
        from analytics.models import TeamReportChat
        TeamReportChat.objects.create(organization=self.org, chat_id='-100', title='Team')
        c = self.call()
        with mock.patch('analytics.team_report.telegram') as tg:
            self.results([two(0, 4.0, cid=str(c.id))])
        self.assertIn('\nComment: <i>Agent speaks, caller silent, no beeping', tg.call_args[0][1]['text'])


class InstantCheckTests(AiAuditFlowTests):
    """CH-547: a call is offered to the voice check seconds after it ends."""

    def test_min_age_in_seconds(self):
        just = self.call()
        CallLog.objects.filter(pk=just.pk).update(ended_at=timezone.now() - timedelta(seconds=8))
        out = io.StringIO()
        call_command('voice_pending', stdout=out)                      # minutes: not yet
        self.assertNotIn(str(just.id), out.getvalue())
        out = io.StringIO()
        call_command('voice_pending', '--min-age-seconds', '5', stdout=out)
        self.assertIn(str(just.id), out.getvalue())
        out = io.StringIO()
        call_command('voice_pending', '--min-age-seconds', '30', stdout=out)
        self.assertNotIn(str(just.id), out.getvalue())
