"""CH-345: the Call Log reads only the rows a page needs when live calls are
in the range - same rows, same order as the old full merge.

Run:  python manage.py test analytics.test_call_log_live_window
"""
import random
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from analytics.services import AnalyticsService


class _QS(list):
    def count(self):
        return len(self)


def _old_page(hist, live, offset, limit):
    fetch = offset + limit
    combined = sorted(hist[:fetch] + live[:fetch], key=lambda r: r.created_at, reverse=True)
    return [r.id for r in combined[offset:offset + limit]]


def _new_page(hist, live, offset, limit):
    h, l, local = AnalyticsService._live_window(_QS(hist), _QS(live), len(live), offset, limit)
    combined = sorted(h + l, key=lambda r: r.created_at, reverse=True)
    return [r.id for r in combined[local:local + limit]]


class LiveWindowTests(SimpleTestCase):
    def rows(self, prefix, n, rng, base, spread):
        out = [SimpleNamespace(id=f'{prefix}{i}', created_at=base - timedelta(seconds=rng.randint(0, spread)))
               for i in range(n)]
        return sorted(out, key=lambda r: r.created_at, reverse=True)

    def test_same_rows_and_order_as_the_full_merge(self):
        rng = random.Random(345)
        base = datetime(2026, 10, 28, 15, 0, tzinfo=dt_timezone.utc)
        for _ in range(400):
            hist = self.rows('h', rng.randint(0, 120), rng, base, rng.choice([5, 60, 3600]))
            live = self.rows('l', rng.randint(1, 15), rng, base, rng.choice([5, 60, 3600]))
            limit = rng.choice([1, 3, 7, 50])
            for offset in range(0, len(hist) + len(live) + 5, max(1, limit // 2)):
                self.assertEqual(_new_page(hist, live, offset, limit), _old_page(hist, live, offset, limit),
                                 (len(hist), len(live), offset, limit))

    def test_a_page_reads_at_most_limit_plus_live_rows(self):
        base = datetime(2026, 10, 28, 15, 0, tzinfo=dt_timezone.utc)
        hist = [SimpleNamespace(id=f'h{i}', created_at=base - timedelta(seconds=i)) for i in range(20000)]
        live = [SimpleNamespace(id=f'l{i}', created_at=base - timedelta(seconds=i * 7)) for i in range(40)]
        h, l, _ = AnalyticsService._live_window(_QS(hist), _QS(live), len(live), 19000, 500)
        self.assertLessEqual(len(h), 540)
