"""Server A's health and security report, kept on Server B (CH-472).

The status bot runs on Server B and reads what only a host can see from a
file the host writes. Since the move the phone system, the SIP capture, the
recordings and Server A's own health live on Server A. Every minute A runs
scripts/host_status.py and posts its report here over the private link. It is
kept as run/host_status_a.json (support.system_status.HOST_A_FILE), next to
B's own run/host_status.json.

    POST /api/twilio/asterisk/host-status/   (Asterisk shared secret)
"""
import json
import os
import time

from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

MAX_BYTES = 2 * 1024 * 1024


@csrf_exempt
@require_http_methods(["POST"])
def host_status_report(request):
    from routing.asterisk_handler import _check_secret
    from support.system_status import HOST_A_FILE
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)
    try:
        declared = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        declared = 0
    if declared > MAX_BYTES:
        return JsonResponse({"error": "too large"}, status=413)
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RequestDataTooBig):
        return JsonResponse({"error": "Bad JSON"}, status=400)
    if not isinstance(data, dict) or not isinstance(data.get('at'), (int, float)):
        return JsonResponse({"error": "not a host report"}, status=400)
    data['received_at'] = int(time.time())
    os.makedirs(os.path.dirname(HOST_A_FILE), exist_ok=True)
    tmp = f'{HOST_A_FILE}.{os.getpid()}.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, HOST_A_FILE)
    return JsonResponse({"ok": True})
