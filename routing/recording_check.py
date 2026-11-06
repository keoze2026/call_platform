"""CH-634 (customer accounts point 8): nginx on the recordings host asks this
before it serves a recording (auth_request).

    GET /api/recordings/check      header X-Original-URI: /recordings/<call>.mp3?a=..&s=..
    204  the link is signed and its holder still has an account
    403  anything else (unsigned, wrong signature, login or buyer gone, account
         suspended); X-Recording-Check says which

CH-635: a 204 also says, in X-Recording-Folder, where in B2 the file is kept
("" for our account, "<account id>/" for a customer account), so nginx fetches
an old recording from its account's folder.

No login: the signed link is the permission (routing/recording_links.py).
Reads two rows at most; nginx keeps each answer a few minutes.
"""
from urllib.parse import urlsplit

from django.http import HttpResponse
from django.views.decorators.http import require_GET

from routing.recording_links import check, folder_of_file


@require_GET
def recording_check(request):
    uri = request.headers.get('X-Original-URI', '')
    ok, why = check(uri)
    response = HttpResponse(status=204 if ok else 403)
    response['X-Recording-Check'] = why
    if ok:
        response['X-Recording-Folder'] = folder_of_file(urlsplit(uri).path)
    response['Cache-Control'] = 'no-store'
    return response
