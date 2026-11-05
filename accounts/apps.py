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
        # CH-578: every row carries its account.
        from . import tenancy
        tenancy.connect()
        # Point 1c: database row-level security for the portal.
        from . import rls
        rls.connect()
        from django.db.models.signals import post_migrate
        from . import rls_sql
        post_migrate.connect(rls_sql.on_post_migrate, sender=self, dispatch_uid='rls-policies')