"""
Read deployment settings from environment variables (issue #21).

The rules live here, not inline in settings.py, so they can be unit-tested
with a plain dict instead of reloading Django's settings.

Everything fails safe: if a variable is forgotten on the deployed demo, the
app either runs with DEBUG off or refuses to start. It never falls back to a
development value in production.
"""

from django.core.exceptions import ImproperlyConfigured

# Used only on a developer's machine (DJANGO_DEBUG=1) or by the test runner.
# It is not a secret: without DEBUG, startup fails unless DJANGO_SECRET_KEY is set.
DEV_SECRET_KEY = 'django-insecure-dev-only-not-for-the-deployed-demo'

DEFAULT_ALLOWED_HOSTS = 'localhost,127.0.0.1'


def debug_from(environ):
    """DEBUG is on only when DJANGO_DEBUG is exactly '1'. Anything else, including unset, means off."""
    return environ.get('DJANGO_DEBUG') == '1'


def secret_key_from(environ, *, debug, testing):
    """Use DJANGO_SECRET_KEY if set; the dev key only while debugging or testing; otherwise refuse to start."""
    key = environ.get('DJANGO_SECRET_KEY', '').strip()
    if key:
        return key
    if debug or testing:
        return DEV_SECRET_KEY
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY is not set. For local development set DJANGO_DEBUG=1; '
        'for the deployed demo set DJANGO_SECRET_KEY to a new random key.'
    )


def allowed_hosts_from(environ):
    """Comma-separated DJANGO_ALLOWED_HOSTS, defaulting to the local machine only."""
    raw = environ.get('DJANGO_ALLOWED_HOSTS', DEFAULT_ALLOWED_HOSTS)
    return [host.strip() for host in raw.split(',') if host.strip()]
