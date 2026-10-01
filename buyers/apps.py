from django.apps import AppConfig


class BuyersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'buyers'

    def ready(self):
        # Keeps Buyer.phone_number in step with the buyer's destination. Without
        # this the migration's backfill is a one-time tidy-up that drifts again
        # the first time somebody adds or edits a destination.
        from . import sync
        sync.connect()
