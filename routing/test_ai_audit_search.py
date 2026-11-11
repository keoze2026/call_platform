"""CH-664: the AI Audit list searched by caller ID.

Run:  python manage.py test routing.test_ai_audit_search
"""
from django.test import TestCase, override_settings

from routing.models import CallLog
from routing.test_ai_labels import AiAuditFlowTests


@override_settings(ALLOWED_HOSTS=['*'], AI_AUDIT_EMAILS='audit@x.io',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class CallerSearchTests(TestCase):
    # The AI Audit tests' own way of making calls and logins (not their tests).
    setUp = AiAuditFlowTests.setUp
    call = AiAuditFlowTests.call
    api = AiAuditFlowTests.api

    def ask(self, q):
        r = self.api('admin').get('/api/ai-audit/calls', {'q': q})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_any_part_of_the_number_finds_the_caller(self):
        a = self.call()
        self.call()
        CallLog.objects.filter(pk=a.pk).update(caller_number='+14843455600')
        for q in ('+14843455600', '14843455600', '(484) 345-5600', '3455600'):
            d = self.ask(q)
            self.assertEqual([i['caller'] for i in d['items']], ['+14843455600'], q)
            self.assertEqual(d['counts']['total'], 1, q)       # the boxes count that caller only

    def test_short_or_empty_search_shows_the_whole_day(self):
        self.call()
        self.call()
        self.assertEqual(self.ask('')['total'], 2)
        self.assertEqual(self.ask('+1')['total'], 2)               # fewer than 3 digits: no search
        self.assertEqual(self.ask('9999999999')['total'], 0)
