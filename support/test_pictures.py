"""CH-654: pictures in the support chat - pasted or picked in the portal, up
to 2 MB, sent to the support group as a photo; the team can answer with one.

Run:  python manage.py test support.test_pictures
"""
import json
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from support.images import MAX_BYTES, kind_of
from support.models import SupportMessage, SupportSession

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 200
JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 200


def ok_response(payload=None):
    r = mock.Mock(ok=True, text='{}', content=b'')
    r.json.return_value = payload if payload is not None else {'ok': True}
    return r


@override_settings(ALLOWED_HOSTS=['*'], TELEGRAM_BOT_TOKEN='tok', TELEGRAM_SUPPORT_CHAT_ID='-100',
                   TELEGRAM_SUPPORT_THREAD_ID='',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PictureTests(TestCase):
    def setUp(self):
        cache.clear()
        self.session = SupportSession.objects.create(name='Ann <b>x</b> · Texora · page /reports',
                                                     email='ann@texora.io')
        SupportMessage.objects.create(session=self.session, sender='visitor', message='Hi')
        self.sent = []
        p = mock.patch('requests.post', side_effect=self._post)
        p.start()
        self.addCleanup(p.stop)

    def _post(self, url, **kw):
        self.sent.append((url, kw))
        return ok_response()

    def upload(self, data, name='screen.png', message='', session=None):
        return Client().post(f'/api/support/chat/{session or self.session.id}/image',
                             {'image': SimpleUploadedFile(name, data, 'image/png'), 'message': message})

    def test_a_picture_is_kept_shown_and_sent_to_the_team(self):
        r = self.upload(PNG, message='This page is blank <script>')
        self.assertEqual(r.status_code, 200, r.content)
        m = SupportMessage.objects.get(id=r.json()['id'])
        self.assertEqual((bytes(m.image), m.image_type, m.sender), (PNG, 'image/png', 'visitor'))
        # The transcript says where the picture is; the picture is served from there.
        items = Client().get(f'/api/support/chat/{self.session.id}').json()['messages']
        url = items[-1]['image_url']
        self.assertEqual(url, f'/api/support/chat/{self.session.id}/image/{m.id}')
        self.assertIsNone(items[0]['image_url'])
        pic = Client().get(url)
        self.assertEqual((pic.status_code, pic['Content-Type'], pic.content), (200, 'image/png', PNG))
        self.assertEqual(pic['X-Content-Type-Options'], 'nosniff')
        # The team got it as a photo, with the reply line, the text made safe.
        url, kw = self.sent[-1]
        self.assertTrue(url.endswith('/sendPhoto'))
        self.assertEqual(kw['files']['photo'][1], PNG)
        self.assertIn(f'<code>{str(self.session.id)[:8]} your reply here</code>', kw['data']['caption'])
        self.assertIn('&lt;script&gt;', kw['data']['caption'])
        self.assertIn('Ann &lt;b&gt;x&lt;/b&gt;', kw['data']['caption'])

    def test_over_2_mb_is_refused(self):
        r = self.upload(PNG + b'\x00' * MAX_BYTES)
        self.assertEqual(r.status_code, 413)
        self.assertIn('2 MB', r.json()['detail'])
        self.assertFalse(SupportMessage.objects.exclude(image_type='').exists())

    def test_only_real_pictures(self):
        for data, name in ((b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', 'a.png'),
                           (b'%PDF-1.7 ...', 'scan.jpg'), (b'MZ\x90\x00', 'photo.gif')):
            self.assertEqual(self.upload(data, name=name).status_code, 415, name)
        self.assertEqual(kind_of(JPG), 'image/jpeg')
        self.assertEqual(kind_of(b'GIF89a' + b'\x00' * 10), 'image/gif')
        self.assertEqual(kind_of(b'RIFF\x00\x00\x00\x00WEBPVP8 '), 'image/webp')
        self.assertFalse(SupportMessage.objects.exclude(image_type='').exists())

    def test_unknown_chat_and_unknown_picture(self):
        self.assertEqual(self.upload(PNG, session='00000000-0000-0000-0000-000000000000').status_code, 404)
        self.assertEqual(self.upload(PNG, session='not-a-chat').status_code, 404)
        other = SupportSession.objects.create(name='B')
        r = self.upload(PNG)
        mid = r.json()['id']
        # A picture opens only under its own chat.
        self.assertEqual(Client().get(f'/api/support/chat/{other.id}/image/{mid}').status_code, 404)
        self.assertEqual(Client().get(f'/api/support/chat/{self.session.id}/image/x').status_code, 404)

    def test_a_gif_goes_as_a_file_and_a_refused_photo_is_sent_as_a_file(self):
        self.upload(b'GIF89a' + b'\x00' * 50, name='a.gif')
        self.assertTrue(self.sent[-1][0].endswith('/sendDocument'))
        self.sent.clear()
        refused = mock.Mock(ok=False, text='PHOTO_INVALID_DIMENSIONS')
        with mock.patch('requests.post', side_effect=[refused, ok_response()]) as post:
            self.assertEqual(self.upload(PNG).status_code, 200)
        self.assertEqual([c.args[0].rsplit('/', 1)[1] for c in post.call_args_list], ['sendPhoto', 'sendDocument'])

    def test_text_messages_are_unchanged(self):
        r = Client().post(f'/api/support/chat/{self.session.id}', json.dumps({'message': 'More'}),
                          content_type='application/json')
        self.assertEqual(r.status_code, 200)
        last = Client().get(f'/api/support/chat/{self.session.id}').json()['messages'][-1]
        self.assertEqual((last['message'], last['image_url']), ('More', None))


@override_settings(ALLOWED_HOSTS=['*'], TELEGRAM_BOT_TOKEN='tok',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class TeamPictureTests(TestCase):
    def setUp(self):
        self.session = SupportSession.objects.create(name='Ann')

    def telegram(self, message):
        return Client().post('/api/support/telegram-webhook', json.dumps({'message': message}),
                             content_type='application/json')

    def test_the_team_answers_with_a_photo(self):
        prefix = str(self.session.id)[:8]
        photo = {'chat': {'id': -100}, 'caption': f'{prefix} Here is where to click',
                 'photo': [{'file_id': 'small', 'file_size': 1000, 'width': 90, 'height': 90},
                           {'file_id': 'big', 'file_size': 90000, 'width': 800, 'height': 600},
                           {'file_id': 'huge', 'file_size': MAX_BYTES + 1, 'width': 4000, 'height': 3000}]}
        file_info = ok_response({'ok': True, 'result': {'file_path': 'photos/f.jpg'}})
        download = mock.Mock(ok=True, content=JPG)
        with mock.patch('requests.get', side_effect=[file_info, download]) as get:
            self.assertEqual(self.telegram(photo).status_code, 200)
        self.assertEqual(get.call_args_list[0].kwargs['params'], {'file_id': 'big'})   # largest that fits
        m = SupportMessage.objects.get(session=self.session)
        self.assertEqual((m.sender, m.message, bytes(m.image), m.image_type),
                         ('agent', 'Here is where to click', JPG, 'image/jpeg'))

    def test_a_photo_without_a_session_code_is_ignored(self):
        with mock.patch('requests.get') as get:
            self.telegram({'chat': {'id': -100}, 'caption': 'nice', 'photo': [{'file_id': 'x', 'file_size': 10}]})
        get.assert_not_called()
        self.assertFalse(SupportMessage.objects.exists())
