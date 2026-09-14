path = '/opt/call_platform/analytics/services.py'
with open(path, 'r') as f:
    content = f.read()

# We will replace the entire export_csv method and record_call method cleanly
import re

# Clean export_csv definition
new_export_csv = '''    @staticmethod
    def export_csv(user: User, filters) -> str:
        qs = AnalyticsService._base_qs(user, filters).order_by('-created_at')

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Date', 'Caller', 'State', 'Called Number',
            'Campaign', 'Buyer', 'Publisher',
            'Status', 'Duration (s)', 'Converted',
            'Revenue', 'Payout', 'Profit', 'Recording'
        ])

        for r in qs:
            raw_caller = r.caller_number or ''
            clean_caller = raw_caller.lstrip('+')
            if clean_caller.startswith('1') and len(clean_caller) == 11:
                clean_caller = clean_caller[1:]
            writer.writerow([
                r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                clean_caller, r.caller_state, r.called_number,
                r.campaign_name, r.buyer_name, r.publisher_name,
                r.status, r.duration_seconds, r.is_converted,
                r.revenue, r.payout, r.profit, r.recording_url,
            ])

        return output.getvalue()'''

# Clean record_call definition
new_record_call = '''    @staticmethod
    def record_call(data: dict, organization) -> CallRecord:
        \"\"\"
        Called from twilio/webhook after call-status update.
        data keys match Twilio's StatusCallback params.
        \"\"\"
        raw_from = data.get('From', '')
        digits_only = ''.join(filter(str.isdigit, raw_from))
        if digits_only.startswith('1') and len(digits_only) == 11:
            clean_from = digits_only[1:]
        else:
            clean_from = digits_only or raw_from.lstrip('+')

        record, _ = CallRecord.objects.update_or_create(
            twilio_call_sid=data.get('CallSid', ''),
            organization=organization,
            defaults={
                'caller_number': clean_from,
                'called_number': data.get('To', ''),
                'status': _map_twilio_status(data.get('CallStatus', '')),
                'duration_seconds': int(data.get('CallDuration', 0) or 0),
                'campaign_id': data.get('campaign_id'),
                'campaign_name': data.get('campaign_name', ''),
                'buyer_id': data.get('buyer_id'),
                'buyer_name': data.get('buyer_name', ''),
                'publisher_id': data.get('publisher_id'),
                'publisher_name': data.get('publisher_name', ''),
                'revenue': Decimal(str(data.get('revenue', '0'))),
                'payout': Decimal(str(data.get('payout', '0'))),
                'profit': Decimal(str(data.get('profit', '0'))),
                'winning_bid': Decimal(str(data.get('winning_bid', '0'))) if data.get('winning_bid') else None,
                'is_converted': bool(data.get('is_converted', False)),
                'is_duplicate': bool(data.get('is_duplicate', False)),
                'is_spam': bool(data.get('is_spam', False)),
                'recording_url': data.get('RecordingUrl', ''),
                'caller_state': data.get('caller_state', ''),
                'routing_type': data.get('routing_type', ''),
                'auction_id': data.get('auction_id'),
            }
        )
        if record.revenue and record.payout:
            record.profit = record.revenue - record.payout
            record.save(update_fields=['profit'])

        return record'''

# Replace export_csv method using regex
content = re.sub(r'    @staticmethod\s+def export_csv.*?(?=\n    @staticmethod|\Z)', new_export_csv + '\n\n', content, flags=re.DOTALL)

# Replace record_call method using regex
content = re.sub(r'    @staticmethod\s+def record_call.*?(?=\n    @staticmethod|\Z)', new_record_call + '\n\n', content, flags=re.DOTALL)

with open(path, 'w') as f:
    f.write(content)

print('Successfully applied clean rewrites to analytics/services.py!')
