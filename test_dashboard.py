import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from analytics.services import AnalyticsService
from accounts.models import User
from decimal import Decimal

# Pick a random admin user
user = User.objects.filter(role='admin').first()
if user:
    class DummyFilters:
        date_from = None
        date_to = None
        campaign_id = None
        buyer_id = None
        publisher_id = None
        status = None

    filters = DummyFilters()
    try:
        data = AnalyticsService.get_dashboard(user, filters)
        print("Dashboard query successful!")
        print(f"Total Calls: {data.get('total_calls')}")
        print(f"Total Revenue: {data.get('total_revenue')}")
        print(f"Total Payout: {data.get('total_payout')}")
        print(f"Total Profit: {data.get('total_profit')}")
    except Exception as e:
        import traceback
        traceback.print_exc()
else:
    print("No admin user found.")
