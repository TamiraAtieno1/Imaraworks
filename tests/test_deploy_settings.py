"""
Issue #21: deployment settings come from environment variables and fail safe.

The rules are tested as pure functions on a plain dict, so no test has to
reload Django's settings or touch the real environment.
"""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from imaraworks.env import (
    DEV_SECRET_KEY,
    allowed_hosts_from,
    debug_from,
    secret_key_from,
)


class DebugFromTests(SimpleTestCase):
    def test_off_when_unset(self):
        self.assertFalse(debug_from({}))

    def test_on_only_for_exactly_1(self):
        self.assertTrue(debug_from({'DJANGO_DEBUG': '1'}))

    def test_other_values_stay_off(self):
        # "true", "True", "0", "" must not switch DEBUG on by accident.
        for value in ['0', '', 'true', 'True', 'yes', 'False']:
            with self.subTest(value=value):
                self.assertFalse(debug_from({'DJANGO_DEBUG': value}))


class SecretKeyFromTests(SimpleTestCase):
    def test_uses_env_key_when_set(self):
        key = secret_key_from({'DJANGO_SECRET_KEY': 'from-env'}, debug=False, testing=False)
        self.assertEqual(key, 'from-env')

    def test_env_key_wins_even_while_debugging(self):
        key = secret_key_from({'DJANGO_SECRET_KEY': 'from-env'}, debug=True, testing=False)
        self.assertEqual(key, 'from-env')

    def test_dev_key_while_debugging(self):
        self.assertEqual(secret_key_from({}, debug=True, testing=False), DEV_SECRET_KEY)

    def test_dev_key_while_testing(self):
        # CI runs `manage.py test` with no env vars and DEBUG off.
        self.assertEqual(secret_key_from({}, debug=False, testing=True), DEV_SECRET_KEY)

    def test_refuses_to_start_without_key_in_production(self):
        with self.assertRaises(ImproperlyConfigured):
            secret_key_from({}, debug=False, testing=False)

    def test_blank_key_counts_as_unset(self):
        with self.assertRaises(ImproperlyConfigured):
            secret_key_from({'DJANGO_SECRET_KEY': '   '}, debug=False, testing=False)


class AllowedHostsFromTests(SimpleTestCase):
    def test_defaults_to_local_machine_only(self):
        self.assertEqual(allowed_hosts_from({}), ['localhost', '127.0.0.1'])

    def test_reads_comma_separated_list(self):
        hosts = allowed_hosts_from({'DJANGO_ALLOWED_HOSTS': 'demo.example.com, .onrender.com'})
        self.assertEqual(hosts, ['demo.example.com', '.onrender.com'])

    def test_ignores_empty_entries(self):
        self.assertEqual(allowed_hosts_from({'DJANGO_ALLOWED_HOSTS': 'a.com,,'}), ['a.com'])
