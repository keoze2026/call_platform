from django.core.mail import send_mail
from django.conf import settings
from .models import NotificationRule, NotificationLog
from accounts.models import User


class NotificationService:

    @staticmethod
    def create_rule(data, user: User) -> NotificationRule:
        if not user.organization:
            raise ValueError("User has no organization")

        rule = NotificationRule.objects.create(
            organization=user.organization,
            created_by=user,
            name=data.name,
            event=data.event,
            channel=data.channel,
            recipients=data.recipients or [],
            is_active=data.is_active
        )
        return rule

    @staticmethod
    def get_rule(rule_id: str, user: User) -> NotificationRule:
        try:
            return NotificationRule.objects.get(
                id=rule_id,
                organization=user.organization
            )
        except NotificationRule.DoesNotExist:
            raise ValueError("Notification rule not found")

    @staticmethod
    def list_rules(user: User):
        return NotificationRule.objects.filter(
            organization=user.organization
        ).order_by('-created_at')

    @staticmethod
    def update_rule(rule_id: str, data, user: User) -> NotificationRule:
        rule = NotificationService.get_rule(rule_id, user)

        if data.name is not None:
            rule.name = data.name
        if data.event is not None:
            rule.event = data.event
        if data.channel is not None:
            rule.channel = data.channel
        if data.recipients is not None:
            rule.recipients = data.recipients
        if data.is_active is not None:
            rule.is_active = data.is_active

        rule.save()
        return rule

    @staticmethod
    def delete_rule(rule_id: str, user: User):
        rule = NotificationService.get_rule(rule_id, user)
        rule.delete()

    @staticmethod
    def list_logs(user: User):
        return NotificationLog.objects.filter(
            organization=user.organization
        ).order_by('-created_at')[:100]

    @staticmethod
    def get_preferences(user) -> dict:
        """Pop-up preferences for a user, created with the defaults if absent."""
        from notifications.models import NotificationPreference

        pref, created = NotificationPreference.objects.get_or_create(
            user=user,
            defaults={'popup_events': list(NotificationPreference.DEFAULT_POPUP_EVENTS)},
        )
        return NotificationService.format_preferences(pref)

    @staticmethod
    def update_preferences(user, body: dict) -> dict:
        from notifications.models import NotificationPreference, NotificationRule

        pref, _ = NotificationPreference.objects.get_or_create(
            user=user,
            defaults={'popup_events': list(NotificationPreference.DEFAULT_POPUP_EVENTS)},
        )

        fields = []
        if 'popups_enabled' in body:
            pref.popups_enabled = bool(body['popups_enabled'])
            fields.append('popups_enabled')

        if 'sound_enabled' in body:
            pref.sound_enabled = bool(body['sound_enabled'])
            fields.append('sound_enabled')

        if 'popup_events' in body:
            events = body['popup_events']
            if not isinstance(events, list):
                raise ValueError('popup_events must be a list')

            valid = {v for v, _ in NotificationRule.Event.choices}
            unknown = [e for e in events if e not in valid]
            if unknown:
                raise ValueError(
                    f"Unknown event types: {', '.join(map(str, unknown))}. "
                    f"See GET /api/notifications/events"
                )
            # Deduplicated, and ordered as the catalogue orders them so the list
            # reads the same however the client sent it
            order = [v for v, _ in NotificationRule.Event.choices]
            pref.popup_events = [e for e in order if e in set(events)]
            fields.append('popup_events')

        if fields:
            pref.save(update_fields=fields + ['updated_at'])

        return NotificationService.format_preferences(pref)

    @staticmethod
    def format_preferences(pref) -> dict:
        return {
            'popups_enabled': pref.popups_enabled,
            'popup_events': pref.popup_events or [],
            'sound_enabled': pref.sound_enabled,
            'updated_at': pref.updated_at.isoformat(),
        }

    @staticmethod
    def dispatch(event: str, organization, data: dict):
        rules = NotificationRule.objects.filter(
            organization=organization,
            event=event,
            is_active=True
        )

        for rule in rules:
            for recipient in rule.recipients:
                if rule.channel == 'email':
                    NotificationService._send_email(rule, recipient, event, data, organization)
                elif rule.channel == 'sms':
                    NotificationService._send_sms(rule, recipient, event, data, organization)

    @staticmethod
    def _send_email(rule, recipient: str, event: str, data: dict, organization):
        subject = NotificationService._build_subject(event, data)
        body = NotificationService._build_body(event, data)

        log = NotificationLog.objects.create(
            organization=organization,
            rule=rule,
            event=event,
            channel='email',
            recipient=recipient,
            subject=subject,
            body=body,
            status=NotificationLog.Status.PENDING
        )

        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=getattr(settings, 'PLATFORM_FROM_EMAIL', settings.DEFAULT_FROM_EMAIL),
                recipient_list=[recipient],
                fail_silently=False
            )
            log.status = NotificationLog.Status.SENT
            log.save(update_fields=['status'])
        except Exception as e:
            log.status = NotificationLog.Status.FAILED
            log.error = str(e)
            log.save(update_fields=['status', 'error'])

    @staticmethod
    def _send_sms(rule, recipient: str, event: str, data: dict, organization):
        body = NotificationService._build_subject(event, data)

        log = NotificationLog.objects.create(
            organization=organization,
            rule=rule,
            event=event,
            channel='sms',
            recipient=recipient,
            body=body,
            status=NotificationLog.Status.PENDING
        )

        try:
            from django.conf import settings
            from twilio.rest import Client
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            client.messages.create(
                body=body,
                from_=settings.TWILIO_PHONE_NUMBER,
                to=recipient
            )
            log.status = NotificationLog.Status.SENT
            log.save(update_fields=['status'])
        except Exception as e:
            log.status = NotificationLog.Status.FAILED
            log.error = str(e)
            log.save(update_fields=['status', 'error'])

    @staticmethod
    def _build_subject(event: str, data: dict) -> str:
        # Detectors send the subject under 'name'; older callers send
        # campaign_name / buyer_name. Accept either so neither produces a
        # subject line with a blank where the name should be.
        name = (
            data.get('name')
            or data.get('campaign_name')
            or data.get('buyer_name')
            or data.get('publisher_name')
            or ''
        )
        suffix = f" - {name}" if name else ""

        # Cap alerts fire twice: approaching the limit, then at it
        if data.get('level') == 'warn':
            used, limit = data.get('used'), data.get('limit')
            period = data.get('period', '')
            near = f" ({used}/{limit} {period})" if limit else ""
            cap_subjects = {
                'campaign.cap_reached': f"Campaign Cap Filling{suffix}{near}",
                'buyer.cap_reached': f"Buyer Cap Filling{suffix}{near}",
                'destination.cap_reached': f"Destination Cap Filling{suffix}{near}",
            }
            if event in cap_subjects:
                return cap_subjects[event]

        subjects = {
            'call.missed': f"Missed Call from {data.get('caller_number', 'Unknown')}",
            'call.completed': f"Call Completed{suffix}",
            'campaign.cap_reached': f"Campaign Cap Reached{suffix}",
            'buyer.cap_reached': f"Buyer Cap Reached{suffix}",
            'publisher.cap_reached': f"Publisher Cap Reached{suffix}",
            'destination.cap_reached': f"Destination Cap Reached{suffix}",
            'buyer.missed': (
                f"Buyer Missing Calls{suffix} - "
                f"{data.get('missed', 0)}/{data.get('total', 0)} in the last "
                f"{data.get('window_minutes', 0)} min"
            ),
            'aht.low': (
                f"Handle Time Dropped{suffix} - "
                f"{data.get('recent_aht_seconds', 0)}s vs "
                f"{data.get('baseline_aht_seconds', 0)}s usual"
            ),
            'low.balance': f"Low Balance Alert - {data.get('balance', '')}",
            'campaign.paused': f"Campaign Paused{suffix}",
            'daily.summary': "Daily Summary Report",
            'call.started': f"New Call{suffix}",
        }
        return subjects.get(event, f"Platform Alert - {event}")

    @staticmethod
    def _build_body(event: str, data: dict) -> str:
        body = f"Event: {event}\n\n"
        for key, value in data.items():
            body += f"{key}: {value}\n"
        return body

    @staticmethod
    def format_rule(rule: NotificationRule) -> dict:
        return {
            'id': str(rule.id),
            'name': rule.name,
            'event': rule.event,
            'channel': rule.channel,
            'recipients': rule.recipients,
            'is_active': rule.is_active,
            'organization_id': str(rule.organization_id),
            'created_at': rule.created_at.isoformat(),
            'updated_at': rule.updated_at.isoformat(),
        }