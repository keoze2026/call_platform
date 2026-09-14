import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# 'tasks' is a flat top-level module, not a package, so autodiscover_tasks(['tasks'])
# cannot find it (that form looks for 'tasks.tasks'). include= imports it directly at
# worker startup, which is what actually registers the @app.task entries.
app = Celery('call_platform', include=['tasks'])
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
