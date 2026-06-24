
# config/settings.py

from pathlib import Path
from decouple import config, AutoConfig
config = AutoConfig(search_path='/opt/call_platform')
from datetime import timedelta
import dj_database_url

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.celery import CeleryIntegration
from django.templatetags.static import static

sentry_sdk.init(
    dsn=config('SENTRY_DSN', default=''),
    integrations=[
        DjangoIntegration(),
        CeleryIntegration(),
    ],
    traces_sample_rate=0.1,
    send_default_pii=False,
    environment=config('ENVIRONMENT', default='development'),
)

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-ud69xs!-nnj@2o99m9fv^wosqcp*bb73+*g&n0%dy&l($pxb#y')
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost').split(',')
DEBUG = config("DEBUG", default=False, cast=bool)
APPEND_SLASH = True
CSRF_TRUSTED_ORIGINS = ['https://avortyx.io', 'https://www.avortyx.io']

# Application definition
INSTALLED_APPS = [

    'channels',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third party
    'corsheaders',

    # Local apps
    'accounts',
    'campaigns',
    'integrations',
    'buyers',
    'publishers',
    'phone_numbers',
    'routing',
    'spam_protection',
    'referrals',
    'support',
    'ivr',
    'dni',
    'analytics',
    'rtb',
    'white_label',
    'webhooks',
    'notifications',
    'billing',
    'call_queue',
]


ASGI_APPLICATION = 'config.asgi.application'


CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [config('REDIS_URL', default='redis://127.0.0.1:6379/0')],
        },
    },
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware', 
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': False,
        'OPTIONS': {
            'loaders': [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database - PostgreSQL
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': config('DB_NAME', default='call_platform'),
#         'USER': config('DB_USER', default='postgres'),
#         'PASSWORD': config('DB_PASSWORD', default='password'),
#         'HOST': config('DB_HOST', default='localhost'),
#         'PORT': config('DB_PORT', default='5432'),
#     }
# }

DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}'),
        conn_max_age=600,
    )
}


# Custom user model
AUTH_USER_MODEL = 'accounts.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# JWT Settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# CORS
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    'https://avortyx.com',
    'https://www.avortyx.com',
    'https://avortyx.io',
    'http://localhost:3000',
    'http://localhost:3001',
]  # Change in production

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'



from decouple import config

TWILIO_ACCOUNT_SID = config('TWILIO_ACCOUNT_SID', default='')
TWILIO_AUTH_TOKEN = config('TWILIO_AUTH_TOKEN', default='')

CELERY_BROKER_URL =  config('CELERY_BROKER_URL')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND')

CELERY_BROKER_URL = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_BEAT_SCHEDULE = {
    'reset-daily-caps': {
        'task': 'tasks.reset_daily_caps',
        'schedule': 86400.0,  # every 24 hours
    },
    'send-daily-summary': {
        'task': 'tasks.send_daily_summary',
        'schedule': 86400.0,  # every 24 hours
    },
    'retry-failed-webhooks': {
        'task': 'tasks.retry_failed_webhooks',
        'schedule': 300.0,  # every 5 minutes
    },
    'expire-dni-sessions': {
        'task': 'tasks.expire_dni_sessions',
        'schedule': 300.0,  # every 5 minutes
    },
    'generate-monthly-invoices': {
        'task': 'tasks.generate_monthly_invoices',
        'schedule': 86400.0,  # every 24 hours
    },
    'check-auto-recharge': {
        'task': 'tasks.check_auto_recharge',
        'schedule': 3600.0,  # every 1 hour
    },
}




EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@example.com')
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=False, cast=bool)

ASSEMBLYAI_API_KEY = config('ASSEMBLYAI_API_KEY', default='')




COINGATE_API_KEY = config('COINGATE_API_KEY', default='')
COINGATE_ENVIRONMENT = config('COINGATE_ENVIRONMENT', default='sandbox')  # 'sandbox' or 'live'


CAPITALIST_MERCHANT_ID = config('CAPITALIST_MERCHANT_ID', default='')
CAPITALIST_SECRET = config('CAPITALIST_SECRET', default='')


ASTERISK_SHARED_SECRET = config('ASTERISK_SHARED_SECRET', default='')

IPQS_API_KEY = config('IPQS_API_KEY', default='')

UNFOLD = {
    "SITE_TITLE": "Call Platform Admin",
    "SITE_HEADER": "Call Platform",
    "SITE_URL": "/",
    "SITE_ICON": None,
    "STYLES": [],
    "SCRIPTS": [],
    "FONTS": {
        "google_fonts": "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap",
    },
    "DASHBOARD_CALLBACK": "config.dashboard.dashboard_callback",
    "COLORS": {
        "primary": {
            "50": "240 253 244",
            "100": "220 252 231",
            "200": "187 247 208",
            "300": "134 239 172",
            "400": "74 222 128",
            "500": "34 197 94",
            "600": "22 163 74",
            "700": "15 118 110",
            "800": "6 78 59",
            "900": "2 44 34",
            "950": "0 20 15",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Dashboard",
                "icon": "dashboard",
                "items": [
                    {"title": "Dashboard", "icon": "dashboard", "link": "/admin/"},
                ],
            },
            {
                "title": "Users & Orgs",
                "icon": "group",
                "items": [
                    {"title": "Organizations", "icon": "corporate_fare", "link": "/admin/accounts/organization/"},
                    {"title": "Users", "icon": "person", "link": "/admin/accounts/user/"},
                ],
            },
            {
                "title": "Campaigns",
                "icon": "campaign",
                "items": [
                    {"title": "Campaigns", "icon": "rocket_launch", "link": "/admin/campaigns/campaign/"},
                ],
            },
            {
                "title": "Call Routing",
                "icon": "call_split",
                "items": [
                    {"title": "Call Logs", "icon": "list_alt", "link": "/admin/routing/calllog/"},
                    {"title": "Routing Rules", "icon": "rule", "link": "/admin/routing/routingrule/"},
                    {"title": "Destinations", "icon": "call_made", "link": "/admin/routing/ruledestination/"},
                ],
            },
            {
                "title": "Buyers",
                "icon": "people",
                "items": [
                    {"title": "Buyers", "icon": "person_pin", "link": "/admin/buyers/buyer/"},
                    {"title": "Buyer Caps", "icon": "data_usage", "link": "/admin/buyers/buyercap/"},
                    {"title": "Campaign Assignments", "icon": "assignment", "link": "/admin/buyers/buyercampaign/"},
                    {"title": "Schedules", "icon": "schedule", "link": "/admin/buyers/buyerschedule/"},
                ],
            },
            {
                "title": "Publishers",
                "icon": "share",
                "items": [
                    {"title": "Publishers", "icon": "cell_tower", "link": "/admin/publishers/publisher/"},
                    {"title": "Publisher Caps", "icon": "data_usage", "link": "/admin/publishers/publishercap/"},
                    {"title": "Campaign Assignments", "icon": "assignment", "link": "/admin/publishers/publishercampaign/"},
                ],
            },
            {
                "title": "Phone Numbers",
                "icon": "dialpad",
                "items": [
                    {"title": "Phone Numbers", "icon": "phone", "link": "/admin/phone_numbers/phonenumber/"},
                ],
            },
            {
                "title": "Billing",
                "icon": "payments",
                "items": [
                    {"title": "Billing Accounts", "icon": "account_balance", "link": "/admin/billing/billingaccount/"},
                    {"title": "Transactions", "icon": "receipt_long", "link": "/admin/billing/transaction/"},
                    {"title": "Invoices", "icon": "description", "link": "/admin/billing/invoice/"},
                ],
            },
            {
                "title": "Spam Protection",
                "icon": "security",
                "items": [
                    {"title": "Blacklist", "icon": "block", "link": "/admin/spam_protection/blacklist/"},
                    {"title": "Whitelist", "icon": "verified", "link": "/admin/spam_protection/whitelist/"},
                    {"title": "Spam Reports", "icon": "report", "link": "/admin/spam_protection/spamreport/"},
                ],
            },
            {
                "title": "RTB",
                "icon": "bid_landscape",
                "items": [
                    {"title": "Auctions", "icon": "bid_landscape", "link": "/admin/rtb/rtbauction/"},
                    {"title": "Bids", "icon": "payments", "link": "/admin/rtb/rtbbid/"},
                ],
            },
            {
                "title": "IVR",
                "icon": "phone_in_talk",
                "items": [
                    {"title": "IVR Flows", "icon": "account_tree", "link": "/admin/ivr/ivrflow/"},
                    {"title": "IVR Nodes", "icon": "device_hub", "link": "/admin/ivr/ivrnode/"},
                    {"title": "Transitions", "icon": "alt_route", "link": "/admin/ivr/ivrnodeTransition/"},
                ],
            },
            {
                "title": "DNI",
                "icon": "track_changes",
                "items": [
                    {"title": "DNI Pools", "icon": "workspaces", "link": "/admin/dni/dnipool/"},
                    {"title": "DNI Numbers", "icon": "pin", "link": "/admin/dni/dninumber/"},
                    {"title": "DNI Sessions", "icon": "sensors", "link": "/admin/dni/dnisession/"},
                ],
            },
            {
                "title": "Webhooks",
                "icon": "webhook",
                "items": [
                    {"title": "Webhooks", "icon": "cable", "link": "/admin/webhooks/webhook/"},
                    {"title": "Deliveries", "icon": "send", "link": "/admin/webhooks/webhookdelivery/"},
                    {"title": "Conversion Pixels", "icon": "track_changes", "link": "/admin/webhooks/conversionpixel/"},
                    {"title": "Conversion Events", "icon": "bolt", "link": "/admin/webhooks/conversionevent/"},
                ],
            },
            {
                "title": "Notifications",
                "icon": "notifications",
                "items": [
                    {"title": "Rules", "icon": "rule", "link": "/admin/notifications/notificationrule/"},
                    {"title": "Logs", "icon": "list_alt", "link": "/admin/notifications/notificationlog/"},
                ],
            },
            {
                "title": "Analytics",
                "icon": "analytics",
                "items": [
                    {"title": "Call Records", "icon": "bar_chart", "link": "/admin/analytics/callrecord/"},
                ],
            },
            {
                "title": "Call Queue",
                "icon": "queue",
                "items": [
                    {"title": "Queue", "icon": "line_weight", "link": "/admin/call_queue/callqueue/"},
                ],
            },
            {
                "title": "White Label",
                "icon": "style",
                "items": [
                    {"title": "White Labels", "icon": "palette", "link": "/admin/white_label/whitelabel/"},
                    {"title": "Domains", "icon": "language", "link": "/admin/white_label/whitelabeldomain/"},
                ],
            },
        ],
    },
}

# Security Headers
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
CAPITALIST_API_KEY = config('CAPITALIST_API_KEY', default='')
CAPITALIST_API_SECRET = config('CAPITALIST_API_SECRET', default='')

TELEGRAM_BOT_TOKEN = config('TELEGRAM_BOT_TOKEN', default='')
TELEGRAM_CHAT_ID = config('TELEGRAM_CHAT_ID', default='')

MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = '/media/'

TELEGRAM_SUPPORT_CHAT_ID = config('TELEGRAM_SUPPORT_CHAT_ID', default='')
