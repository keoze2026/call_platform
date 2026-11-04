from django.apps import AppConfig


class RoutingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'routing'

    def ready(self):
        import routing.signals  # noqa: F401
        # CH-542: caps and lines changed -> every open portal tab reloads them.
        from routing.call_events import connect_settings_signals
        connect_settings_signals()
        # CH-564: a deleted destination leaves no line behind in any campaign.
        from routing.orphan_lines import connect as connect_orphan_lines
        connect_orphan_lines()
