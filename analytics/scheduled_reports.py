from django.db import models
import uuid


class ScheduledReport(models.Model):

    class Frequency(models.TextChoices):
        DAILY = 'daily', 'Daily'
        WEEKLY = 'weekly', 'Weekly'
        MONTHLY = 'monthly', 'Monthly'

    class Format(models.TextChoices):
        CSV = 'csv', 'CSV'
        PDF = 'pdf', 'PDF'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE, related_name='scheduled_reports')
    created_by = models.ForeignKey('accounts.User', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    frequency = models.CharField(max_length=20, choices=Frequency.choices)
    format = models.CharField(max_length=10, choices=Format.choices, default=Format.CSV)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    recipients = models.JSONField(default=list)
    filters = models.JSONField(default=dict)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    next_send_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'scheduled_reports'

    def __str__(self):
        return f'{self.name} - {self.frequency}'
