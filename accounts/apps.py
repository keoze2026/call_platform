# apps/accounts/apps.py

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'
    verbose_name = 'Accounts'

    def ready(self):
        # Importing connects the receivers. Without this the module is never
        # loaded and nothing is recorded.
        from . import activity_signals
        activity_signals.connect()