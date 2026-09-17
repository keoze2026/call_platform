import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from analytics.models import CallRecord
from routing.models import CallLog
from django.utils import timezone

today = timezone.now().date()
today_records = CallRecord.objects.filter(created_at__date=today)
print(f"Total CallRecords today: {today_records.count()}")

mismatch_count = 0
for cr in today_records:
    # Does a CallLog exist with the same ID?
    if not CallLog.objects.filter(id=cr.id).exists():
        mismatch_count += 1

print(f"CallRecords with ID not in CallLog: {mismatch_count}")
