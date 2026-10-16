"""Create (or reset) a local admin login for call_platform.

Run from the backend folder (call_platform), with the virtual environment active:
    python create_dev_user.py
Safe to run again: it resets the password instead of making a duplicate.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_local")

import django  # noqa: E402

django.setup()

from accounts.models import Organization, User  # noqa: E402

EMAIL = "admin@avortyx-dev.com"
PASSWORD = "LocalAdmin123!"

org, _ = Organization.objects.get_or_create(
    slug="local-dev", defaults={"name": "Local Dev", "is_active": True}
)

user = User.objects.filter(email__iexact=EMAIL).first() or User(username=EMAIL, email=EMAIL)
user.first_name = "Local"
user.last_name = "Admin"
user.organization = org
user.role = User.Role.ADMIN
user.is_active = True
user.is_staff = True        # platform staff: owner tools, access requests
user.is_superuser = True
user.is_email_verified = True
user.mfa_enabled = False
user.set_password(PASSWORD)
user.save()

try:
    from notifications.defaults import ensure_default_rules
    ensure_default_rules(org)
except Exception as exc:  # never block the login over this
    print(f"(note) default notification rules not seeded: {exc}")

print("Local login ready")
print(f"  email:    {EMAIL}")
print(f"  password: {PASSWORD}")
print(f"  workspace: {org.name}")
