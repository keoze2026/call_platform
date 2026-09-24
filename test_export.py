import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from accounts.models import User
from analytics.schemas import AnalyticsFilterSchema
from analytics.services import AnalyticsService

user = User.objects.first()
filters = AnalyticsFilterSchema(limit=50)

gen = AnalyticsService.export_csv(user, filters)
# count rows
rows = list(gen)
print(f"Export rows: {len(rows)}")

