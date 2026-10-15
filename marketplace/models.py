"""The Services directory inside Marketplace (CH-133).

Staff list partner websites - ad networks, hosting, proxies - and every
signed-in user browses them. Platform-wide on purpose: one directory for
all organizations, like the reference the owner showed.
"""
import uuid

from django.conf import settings
from django.db import models

# The only translation keys the portal renders. Anything else is ignored on
# write so the JSON can never grow surprise languages.
TRANSLATION_KEYS = ('zh', 'ja', 'ru')


class ServiceCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    name_translations = models.JSONField(default=dict, blank=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'marketplace_categories'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class MarketplaceService(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE, related_name='services')
    name = models.CharField(max_length=120)
    description = models.TextField(max_length=1000, blank=True)
    description_translations = models.JSONField(default=dict, blank=True)
    website_url = models.URLField(max_length=500)
    logo = models.ImageField(upload_to='marketplace/logos/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'marketplace_services'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class FavoriteService(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    service = models.ForeignKey(MarketplaceService, on_delete=models.CASCADE, related_name='favorites')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'marketplace_favorites'
        unique_together = ('user', 'service')
