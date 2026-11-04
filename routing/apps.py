from django.apps import AppConfig


class RoutingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'routing'

    def ready(self):
        import routing.signals  # noqa: F401
        # CH-542: caps and lines changed -> every open portal tab reloads them.
        from routing.call_events import connect_settings_signals
        connect_settings_signals()
