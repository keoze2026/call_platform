"""CH-635 (customer accounts point 8): each customer account's recordings are
kept in a folder of their own in B2; ours stay where they are.

Run:  python manage.py test routing.test_recording_folders
"""
import importlib.util
import sys
import uuid
from io import StringIO
from unittest import mock
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from accounts.models import Organization, User
from campaigns.models import Campaign
from routing.models import CallLog
from routing.recording_links import folder_for_org, folder_of_file, holder_for
from routing.recordings import public_recording_url


def script():
    spec = importlib.util.spec_from_file_location('r2b', settings.BASE_DIR / 'scripts' / 'recordings_to_b2.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@override_settings(MAIN_ORGANIZATION_SLUG='interns', RECORDING_LINK_SECRET='test-secret',
                   RECORDING_BASE_URL='https://rec.example.com', ALLOWED_HOSTS=['*'])
class FolderTests(TestCase):
    def setUp(self):
        self.ours = Organization.objects.create(name='Interns', slug='interns')
        self.theirs = Organization.objects.create(name='Texora', slug='texora')
        self.n = 0

    def call(self, org):
        self.n += 1
        campaign = Campaign.objects.create(organization=org, name=f'C{self.n}')
        c = CallLog.objects.create(organization=org, campaign=campaign, caller_number=f'+1555000{self.n:04d}',
                                   called_number='+18005550100', twilio_call_sid=f's{self.n}', status='completed')
        CallLog.objects.filter(id=c.id).update(recording_url=f'https://app.example.com/recordings/{c.id}.mp3')
        return CallLog.objects.get(id=c.id)

    def test_our_recordings_stay_at_the_top(self):
        self.assertEqual(folder_for_org(self.ours.id), '')
        self.assertEqual(folder_of_file(f'{self.call(self.ours).id}.mp3'), '')

    def test_a_customer_accounts_recordings_have_its_own_folder(self):
        c = self.call(self.theirs)
        self.assertEqual(folder_of_file(f'{c.id}.mp3'), self.theirs.id.hex + '/')
        self.assertEqual(folder_of_file(f'/recordings/{c.id}.wav'), self.theirs.id.hex + '/')

    def test_not_a_call_stays_at_the_top_and_the_test_file_has_its_folder(self):
        self.assertEqual(folder_of_file('b2-canary.mp3'), '')
        self.assertEqual(folder_of_file(f'{uuid.uuid4()}.mp3'), '')
        self.assertEqual(folder_of_file('b2-canary-folder.mp3'), 'canary/')

    def test_the_link_check_names_the_folder(self):
        c = self.call(self.theirs)
        user = User.objects.create(username='t', email='t@x.io', organization=self.theirs, role='admin',
                                   is_active=True)
        link = public_recording_url(c.recording_url, holder_for(user))
        p = urlsplit(link)
        r = Client().get('/api/recordings/check', HTTP_X_ORIGINAL_URI=f'{p.path}?{p.query}')
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r['X-Recording-Folder'], self.theirs.id.hex + '/')
        r = Client().get('/api/recordings/check', HTTP_X_ORIGINAL_URI=p.path)
        self.assertEqual(r.status_code, 403)
        self.assertNotIn('X-Recording-Folder', r)

    def test_the_command_server_a_asks(self):
        ours, theirs = self.call(self.ours), self.call(self.theirs)
        out = StringIO()
        call_command('recording_folders', f'{ours.id}.mp3', f'{theirs.id}.mp3', stdout=out)
        self.assertEqual(out.getvalue().splitlines(),
                         [f'{ours.id}.mp3 -', f'{theirs.id}.mp3 {self.theirs.id.hex}/'])


class ServerAScriptTests(TestCase):
    def test_folders_from_server_b(self):
        m = script()
        fake = ("import sys; a=sys.argv[1:]; assert a[0]=='recording_folders'\n"
                "for n in a[1:]: print(n, '0123456789abcdef0123456789abcdef/' if n.startswith('c') else '-')")
        with mock.patch.object(m, 'MANAGE', f'{sys.executable} -c "{fake}"'):
            self.assertEqual(m.folders_for(['a.mp3', 'c.mp3']),
                             {'a.mp3': '', 'c.mp3': '0123456789abcdef0123456789abcdef/'})

    def test_nothing_is_uploaded_when_server_b_cannot_say(self):
        m = script()
        with mock.patch.object(m, 'MANAGE', ''):
            self.assertIsNone(m.folders_for(['a.mp3']))
        with mock.patch.object(m, 'MANAGE', f'{sys.executable} -c "import sys; sys.exit(3)"'):
            self.assertIsNone(m.folders_for(['a.mp3']))
        bad = "import sys; print(sys.argv[2], '../x/')"
        with mock.patch.object(m, 'MANAGE', f'{sys.executable} -c "{bad}"'):
            self.assertIsNone(m.folders_for(['a.mp3']))

    def test_the_b2_rewrite_uses_the_folder_only_when_on(self):
        import tempfile

        class FakeB2:
            bucket = 'avortyx-recordings'
            download_url = 'https://f005.backblazeb2.com'

            def download_token(self):
                return 'TOKEN'
        m = script()
        with tempfile.TemporaryDirectory() as d:
            m.NGINX_SNIPPET = f'{d}/b2.conf'
            m.write_nginx(FakeB2(), reload=False, folders=False)
            self.assertIn('/recordings/$1.mp3? break;', open(m.NGINX_SNIPPET).read())
            m.write_nginx(FakeB2(), reload=False, folders=True)
            self.assertIn('/recordings/$recording_folder$1.mp3? break;', open(m.NGINX_SNIPPET).read())
