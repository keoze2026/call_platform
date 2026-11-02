"""CH-468: the load test uses SIPp 3.7.7, not Ubuntu's 3.7.2, whose audio
playback leaks memory on every call and crashes the test callers."""
import os
import re
import subprocess

from django.test import SimpleTestCase

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(BASE, 'scripts/loadtest/install_sipp.sh')


class InstallSippTests(SimpleTestCase):

    def read(self):
        with open(SCRIPT) as f:
            return f.read()

    def test_valid_bash(self):
        subprocess.run(['bash', '-n', SCRIPT], check=True)

    def test_version_has_the_pcap_fix(self):
        """3.7.3 fixed 'crash when using PCAP play with more than one call'."""
        m = re.search(r'^VERSION=v(\d+)\.(\d+)\.(\d+)$', self.read(), re.M)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(tuple(map(int, m.groups())), (3, 7, 3))

    def test_builds_with_audio_and_leaves_ubuntu_sipp(self):
        s = self.read()
        self.assertIn('-DUSE_PCAP=1', s)
        self.assertIn('DEST=/usr/local/bin/sipp', s)
        self.assertNotIn('apt-get remove', s)
        self.assertNotIn('/usr/bin/sipp"', s)

    def test_version_check_survives_sipp_exit_code(self):
        """`sipp -v` exits with 99: the check must not run it in a pipeline
        whose status set -o pipefail would turn into a failure."""
        s = self.read()
        self.assertIn('set -euo pipefail', s)
        self.assertIn("grep -o 'SIPp v[^ ]*' || true", s)
        self.assertNotRegex(s, r'"\$DEST" -v 2>&1 \| grep -q')
