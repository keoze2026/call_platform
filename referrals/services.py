from .models import ReferralProgram, Referral, ReferralEarning
from accounts.models import User


class ReferralService:

    @staticmethod
    def get_or_create_program(user: User) -> ReferralProgram:
        program, _ = ReferralProgram.objects.get_or_create(
            organization=user.organization
        )
        return program

    @staticmethod
    def get_program(user: User) -> ReferralProgram:
        try:
            return ReferralProgram.objects.get(organization=user.organization)
        except ReferralProgram.DoesNotExist:
            return ReferralService.get_or_create_program(user)

    @staticmethod
    def get_stats(user: User) -> dict:
        program = ReferralService.get_program(user)
        total = program.referrals.count()
        active = program.referrals.filter(status='active').count()
        return {
            'total_referrals': total,
            'active_referrals': active,
            'commission_rate': str(program.commission_rate),
            'this_month_earnings': str(program.this_month_earnings),
        }

    @staticmethod
    def list_referrals(user: User):
        program = ReferralService.get_program(user)
        return program.referrals.all()

    @staticmethod
    def get_spending_tracker(user: User, days: int = 30) -> list:
        from django.utils import timezone
        from datetime import timedelta
        program = ReferralService.get_program(user)
        since = timezone.now() - timedelta(days=days)
        earnings = ReferralEarning.objects.filter(
            program=program,
            created_at__gte=since
        ).order_by('created_at')

        from collections import defaultdict
        daily = defaultdict(lambda: {'spend': 0, 'commission': 0})
        for e in earnings:
            day = e.created_at.date().isoformat()
            daily[day]['commission'] += float(e.amount)
            daily[day]['spend'] += float(e.amount) / float(program.commission_rate) * 100

        return [
            {'date': date, 'spend': str(round(v['spend'], 2)), 'commission': str(round(v['commission'], 2))}
            for date, v in sorted(daily.items())
        ]

    @staticmethod
    def send_invite(user: User, email: str, name: str = '') -> None:
        from django.conf import settings
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        program = ReferralService.get_program(user)
        referrer_name = user.get_full_name() or user.email

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"{referrer_name} invited you to Avortyx"
        msg['From'] = settings.DEFAULT_FROM_EMAIL
        msg['To'] = email

        body = f"""Hi {name or 'there'},

{referrer_name} has invited you to join Avortyx — a pay-per-call platform built for serious performance marketers.

Sign up using their referral link:
{program.link}

Avortyx gives you full control over call routing, buyer management, and real-time analytics.

See you on the inside,
The Avortyx Team
"""
        msg.attach(MIMEText(body, 'plain'))

        try:
            with smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT) as server:
                server.starttls()
                server.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
                server.sendmail(settings.DEFAULT_FROM_EMAIL, email, msg.as_string())
        except Exception as e:
            raise ValueError(f"Failed to send invite: {str(e)}")

    @staticmethod
    def format_program(program: ReferralProgram) -> dict:
        return {
            'id': str(program.id),
            'code': program.code,
            'link': program.link,
            'commission_rate': str(program.commission_rate),
            'lifetime_earnings': str(program.lifetime_earnings),
            'this_month_earnings': str(program.this_month_earnings),
        }

    @staticmethod
    def format_referral(referral: Referral) -> dict:
        return {
            'id': str(referral.id),
            'client_name': referral.client_name,
            'vertical': referral.vertical,
            'status': referral.status,
            'spend_30d': str(referral.spend_30d),
            'lifetime_spend': str(referral.lifetime_spend),
            'commission_earned': str(referral.commission_earned),
            'joined_at': referral.joined_at.isoformat(),
        }
