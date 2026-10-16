from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.payments'

    def ready(self):
        # Register the mock gateway (import triggers registration via mock_gateway.py)
        from . import mock_gateway  # noqa: F401
