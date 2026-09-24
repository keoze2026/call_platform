import os
import sys
import django
from datetime import timedelta

def main():
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()

    from routing.models import CallLog
    from analytics.models import CallRecord

    # Find all completed calls that were marked as duplicates
    duplicate_completed_calls = CallLog.objects.filter(
        status='completed',
        is_duplicate=True
    ).order_by('created_at')

    fixed_count = 0
    total = duplicate_completed_calls.count()

    print(f"Found {total} completed calls marked as duplicates. Verifying...")

    for call in duplicate_completed_calls:
        # Check if they ACTUALLY had a prior CONNECTED/ACTIVE call in their duplicate window
        campaign = call.campaign
        if not campaign:
            continue
        
        block_hours = campaign.duplicate_call_block_hours or 24
        cutoff = call.created_at - timedelta(hours=block_hours)

        # Did they have a *connected* or *in_progress* call before this one?
        had_prior_connected = CallLog.objects.filter(
            campaign_id=call.campaign_id,
            caller_number=call.caller_number,
            created_at__gte=cutoff,
            created_at__lt=call.created_at,
            status__in=['completed', 'in_progress', 'ringing', 'initiated']
        ).exists()

        if not had_prior_connected:
            print(f"Call {call.id} from {call.caller_number} was incorrectly marked duplicate (prior calls failed). Fixing...")
            
            # Fix CallLog
            call.is_duplicate = False
            call.save(update_fields=['is_duplicate'])

            # Fix Analytics CallRecord if it exists
            try:
                record = CallRecord.objects.get(id=call.id)
                record.is_duplicate = False
                record.is_qualified = True # Since it is completed and now not duplicate
                record.save(update_fields=['is_duplicate', 'is_qualified'])
                fixed_count += 1
            except CallRecord.DoesNotExist:
                pass

    print(f"Fixed {fixed_count} calls!")

if __name__ == '__main__':
    main()
