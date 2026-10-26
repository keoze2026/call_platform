"""CH-294: the capture reader counts a 200 OK that crossed our CANCEL either
way round - just after it (CH-225) or a moment before it went out.

Run:  python manage.py test routing.test_late_answer_capture
"""
import importlib.util
import os
from unittest import mock

from django.test import SimpleTestCase

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'docs', 'asterisk', 'late_answers.py')
spec = importlib.util.spec_from_file_location('late_answers_doc', PATH)
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

US, XOLO = '156.67.25.167:5060', '100.53.112.140:5060'


def sip(first, cid, cseq, extra=''):
    return (f'{first}\r\nCall-ID: {cid}\r\nFrom: <sip:+15712017397@x>;tag=a\r\n'
            f'To: <sip:18883413566@y>\r\nCSeq: {cseq}\r\n{extra}\r\n').encode()


def dialog(answer_offset, cid='c1'):
    """INVITE at 0, our CANCEL at 17.358, the buyer's 200 OK at 17.358 + offset."""
    t0, cancel = 1000.0, 1017.358
    yield t0, US, XOLO, sip('INVITE sip:18883413566@y SIP/2.0', cid, '1 INVITE')
    yield t0 + 3.2, XOLO, US, sip('SIP/2.0 183 Session Progress', cid, '1 INVITE')
    msgs = [(cancel, US, XOLO, sip('CANCEL sip:18883413566@y SIP/2.0', cid, '1 CANCEL')),
            (cancel + answer_offset, XOLO, US, sip('SIP/2.0 200 OK', cid, '1 INVITE'))]
    yield from sorted(msgs)


class CaptureTests(SimpleTestCase):
    def run_on(self, packets):
        with mock.patch.object(L, 'udp_payloads', return_value=list(packets)):
            return list(L.late_answers(['x.pcap']))

    def test_answer_just_after_the_cancel_counts(self):
        self.assertEqual(len(self.run_on(dialog(+0.5))), 1)

    def test_answer_23ms_before_the_cancel_counts(self):
        found = self.run_on(dialog(-0.023))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['caller'], '15712017397')

    def test_an_answer_long_before_a_cancel_is_not_a_crossing(self):
        self.assertEqual(self.run_on(dialog(-5.0)), [])

    def test_an_answer_long_after_is_not_counted(self):
        self.assertEqual(self.run_on(dialog(+3.0)), [])
