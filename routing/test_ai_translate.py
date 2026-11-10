"""CH-656: the AI's comment on a call, in the reader's language.

Run:  python manage.py test routing.test_ai_translate
"""
import json
import os
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from routing.models import CallLog

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def llm_answer(texts):
    r = mock.Mock(ok=True)
    r.json.return_value = {'content': [{'type': 'text', 'text': json.dumps([f'ZH:{t}' for t in texts])}]}
    return r


@override_settings(ALLOWED_HOSTS=['*'], AI_AUDIT_EMAILS='audit@x.io', CACHES=CACHE)
@mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k', 'AI_LLM_MODEL': 'claude-haiku-5-5'})
class TranslateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.other = Organization.objects.create(name='Other', slug='other')
        self.user = User.objects.create(username='a', email='audit@x.io', organization=self.org,
                                        role='admin', is_superuser=True)
        self.n = 0

    def call(self, summary, org=None):
        self.n += 1
        return CallLog.objects.create(organization=org or self.org, caller_number='+15550000000',
                                      called_number='+18005550101', twilio_call_sid=f's{self.n}',
                                      status='completed', ended_at=timezone.now(), ai_summary=summary)

    def ask(self, lang, ids):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.user)}').post(
            '/api/ai-audit/translate', json.dumps({'lang': lang, 'ids': [str(i) for i in ids]}),
            content_type='application/json')

    def test_comments_come_back_translated_and_are_kept(self):
        a, b = self.call('Caller explained a locked computer'), self.call('Only the agent spoke')
        sent = []

        def post(url, **kw):
            texts = json.loads(kw['json']['messages'][0]['content'].split('\n\n', 1)[1])
            sent.append(texts)
            return llm_answer(texts)
        with mock.patch('requests.post', side_effect=post):
            r = self.ask('zh', [a.id, b.id])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['items'], {str(a.id): 'ZH:Caller explained a locked computer',
                                             str(b.id): 'ZH:Only the agent spoke'})
        # Asked again: from what was kept, nothing sent.
        with mock.patch('requests.post') as post2:
            self.assertEqual(len(self.ask('zh', [a.id, b.id]).json()['items']), 2)
        post2.assert_not_called()
        self.assertEqual(len(sent), 1)

    def test_only_the_readers_own_calls_and_languages(self):
        theirs = self.call('Their note', org=self.other)
        ours = self.call('Our note')
        with mock.patch('requests.post', side_effect=lambda url, **kw: llm_answer(['Our note'])):
            items = self.ask('ja', [theirs.id, ours.id, 'not-an-id']).json()['items']
        self.assertEqual(list(items), [str(ours.id)])
        with mock.patch('requests.post') as post:
            self.assertEqual(self.ask('en', [ours.id]).json()['items'], {})
            self.assertEqual(self.ask('fr', [ours.id]).json()['items'], {})
        post.assert_not_called()

    def test_a_failure_leaves_the_english(self):
        c = self.call('Dead air on the caller side')
        bad = mock.Mock(ok=True)
        bad.json.return_value = {'content': [{'type': 'text', 'text': 'sorry'}]}
        with mock.patch('requests.post', return_value=bad):
            self.assertEqual(self.ask('ru', [c.id]).json()['items'], {})
        with mock.patch('requests.post', side_effect=TimeoutError()):
            self.assertEqual(self.ask('ru', [c.id]).json()['items'], {})

    def test_buyers_and_publishers_are_refused(self):
        self.user.email = 'someone@x.io'
        self.user.is_superuser = False
        self.user.role = 'buyer'
        self.user.save()
        self.assertEqual(self.ask('zh', []).status_code, 403)
