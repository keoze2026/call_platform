"""CH-662: quiet and choppy callers are heard (scripts/voice_check).

10 Nov, recordings listened to: callers the audit team heard came out
"caller 0.0 s" - one side at -57 dB, one choppy (speech windows never 8 in a
row). Both new readings are off by default and change nothing then.

Run:  python manage.py test routing.test_voice_soft
"""
import importlib.util

from django.test import SimpleTestCase


def load():
    spec = importlib.util.spec_from_file_location('voice_check_662', 'scripts/voice_check/voice_check.py')
    vc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vc)
    return vc


class SpeechMaskTests(SimpleTestCase):
    def setUp(self):
        self.vc = load()

    def seconds(self, probs):
        return round(sum(self.vc.speech_mask(probs)) * self.vc.CHUNK / self.vc.SR, 2)

    def test_off_is_exactly_the_old_rule(self):
        self.vc.SOFT = False
        self.assertEqual(self.seconds([0.9] * 7), 0.0)                    # under 8 in a row
        self.assertEqual(self.seconds([0.9] * 8), 0.26)
        self.assertEqual(self.seconds([0.9] * 6 + [0.4] + [0.9] * 6), 0.0)
        self.assertEqual(self.seconds([0.2] * 5 + [0.9] * 10 + [0.1] * 5 + [0.8] * 3), 0.32)

    def test_soft_hears_a_choppy_caller(self):
        # 10 Nov, 2d84c9f7: bursts of speech scores with dips, none 8 in a row.
        choppy = [0.6, 0.55, 0.4, 0.45, 0.2, 0.5, 0.6, 0.38, 0.62, 0.3, 0.52, 0.41, 0.66] * 3
        self.vc.SOFT = False
        self.assertEqual(self.seconds(choppy), 0.0)
        self.vc.SOFT = True
        self.assertGreater(self.seconds(choppy), 1.0)

    def test_soft_still_needs_a_real_start_and_ends_on_silence(self):
        self.vc.SOFT = True
        self.assertEqual(self.seconds([0.45] * 40), 0.0)                  # never reaches THRESHOLD
        self.assertEqual(self.seconds([0.9] + [0.1] * 10 + [0.9]), 0.0)   # two blips, long silence
        self.assertEqual(self.seconds([0.9] * 4), 0.0)                    # too short


class QuietGainTests(SimpleTestCase):
    def setUp(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('numpy not installed')
        self.np = np
        self.vc = load()

    def rms_at(self, db, n=100):
        return self.np.full(n, 10 ** (db / 20), dtype=self.np.float32)

    def test_loud_sides_are_never_changed(self):
        self.assertEqual(self.vc.quiet_gain(self.np, self.rms_at(-26)), 1.0)
        self.assertEqual(self.vc.quiet_gain(self.np, self.rms_at(-35)), 1.0)
        self.assertEqual(self.vc.quiet_gain(self.np, self.np.zeros(10)), 1.0)

    def test_quiet_side_raised_to_target_at_most_30_db(self):
        self.assertAlmostEqual(20 * self.np.log10(self.vc.quiet_gain(self.np, self.rms_at(-45))), 25, places=3)
        self.assertAlmostEqual(20 * self.np.log10(self.vc.quiet_gain(self.np, self.rms_at(-70))), 30, places=3)
