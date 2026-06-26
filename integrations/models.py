from django.db import models
import uuid

class Integration(models.Model):
    id = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=500)
    category = models.CharField(max_length=50, default='other')
    color = models.CharField(max_length=20, default='#1976d2')
    mark = models.CharField(max_length=5, default='')

    class Meta:
        db_table = 'integrations'

class OrganizationIntegration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('accounts.Organization', on_delete=models.CASCADE)
    integration = models.ForeignKey(Integration, on_delete=models.CASCADE)
    connected_at = models.DateTimeField(auto_now_add=True)
    config = models.JSONField(default=dict)

    class Meta:
        db_table = 'organization_integrations'
        unique_together = ('organization', 'integration')


class IntegrationActivity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    integration = models.ForeignKey(OrganizationIntegration, on_delete=models.CASCADE, related_name='activity')
    kind = models.CharField(max_length=20, default='sync')
    label = models.CharField(max_length=255, blank=True)
    detail = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='ok')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'integration_activity'
        ordering = ['-at']
