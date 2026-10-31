from django.apps import AppConfig


class DniConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dni'

    def ready(self):
        # CH-456: the number-pool script runs on the advertiser's own website,
        # so /api/dni/assign answers any origin; the pool's own website list
        # is checked inside the endpoint. Nothing else is opened.
        from corsheaders.signals import check_request_enabled

        def allow_number_pool_script(sender, request, **kwargs):
            path = request.path.rstrip('/')
            return path == '/api/dni/assign' or path.startswith('/api/dni/v1/')

        check_request_enabled.connect(allow_number_pool_script, weak=False,
                                      dispatch_uid='dni_assign_cors')
