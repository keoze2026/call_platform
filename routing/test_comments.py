"""CH-349: every blocked caller ID reads "CallerId Blocked" - new rows, old rows,
the screen, the export, the digest and the buyer Telegram report."""
from django.test import SimpleTestCase

from routing.comments import comment, is_caller_block


class CommentTests(SimpleTestCase):
    def test_every_blocked_caller_id_reads_the_same(self):
        for old in ('Caller is on a do-not-call register', 'Caller flagged as spam', 'Caller flagged as fraud',
                    'Caller is blacklisted', 'Callerid Blocked', 'Caller ID Blocked', 'CallerId Blocked'):
            self.assertEqual(comment(old), 'CallerId Blocked', old)

    def test_other_comments_are_left_alone(self):
        for other in ('Duplicate call blocked', 'Not Connected', 'Anonymous caller blocked',
                      'Insufficient Funds', 'Buyer blocked the caller', 'TFN Test', ''):
            self.assertFalse(is_caller_block(other), other)
            self.assertEqual(comment(other), other)

    def test_reports_and_exports_use_it(self):
        from types import SimpleNamespace
        from analytics.services import AnalyticsService
        from buyers.telegram_report import fail_reason_label
        self.assertEqual(fail_reason_label('Caller is on a do-not-call register'), 'CallerId Blocked')
        self.assertEqual(fail_reason_label('Callerid Blocked'), 'CallerId Blocked')
        row = SimpleNamespace(is_converted=False, block_reason='Caller is on a do-not-call register')
        self.assertEqual(AnalyticsService._export_reason(row), 'CallerId Blocked')
