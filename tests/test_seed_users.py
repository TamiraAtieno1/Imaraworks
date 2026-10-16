"""
Issue #21: the seeded password123 is dev-only and must not reach the deployed demo.
"""

import os
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.payment_requests.management.commands.seed_users import DEV_PASSWORD


def seed():
    call_command('seed_users', stdout=StringIO())


class SeedPasswordTests(TestCase):
    @override_settings(DEBUG=True)
    @mock.patch.dict(os.environ, {}, clear=True)
    def test_dev_password_while_debugging(self):
        seed()
        self.assertTrue(User.objects.get(username='alice').check_password(DEV_PASSWORD))

    @override_settings(DEBUG=False)
    @mock.patch.dict(os.environ, {'SEED_USER_PASSWORD': 'demo-secret-pw'}, clear=True)
    def test_env_password_on_the_deployed_demo(self):
        seed()
        alice = User.objects.get(username='alice')
        self.assertTrue(alice.check_password('demo-secret-pw'))
        self.assertFalse(alice.check_password(DEV_PASSWORD))

    @override_settings(DEBUG=True)
    @mock.patch.dict(os.environ, {'SEED_USER_PASSWORD': 'demo-secret-pw'}, clear=True)
    def test_env_password_wins_even_while_debugging(self):
        seed()
        self.assertTrue(User.objects.get(username='alice').check_password('demo-secret-pw'))

    @override_settings(DEBUG=False)
    @mock.patch.dict(os.environ, {}, clear=True)
    def test_refuses_without_password_when_debug_is_off(self):
        with self.assertRaises(CommandError):
            seed()
        # Refused before creating anything, so no user has the dev password.
        self.assertFalse(User.objects.exists())

    @override_settings(DEBUG=False)
    @mock.patch.dict(os.environ, {'SEED_USER_PASSWORD': '  '}, clear=True)
    def test_blank_password_counts_as_unset(self):
        with self.assertRaises(CommandError):
            seed()
