import os

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Profile
from apps.vendors.models import Vendor
from apps.payment_requests.models import Project

# The shared dev password from the case study. Only used while DEBUG is on, so
# it can never reach the deployed demo (issue #21).
DEV_PASSWORD = 'password123'


def seed_password(environ, *, debug):
    """SEED_USER_PASSWORD if set; the dev password only while debugging; otherwise refuse."""
    password = environ.get('SEED_USER_PASSWORD', '').strip()
    if password:
        return password
    if debug:
        return DEV_PASSWORD
    raise CommandError(
        'SEED_USER_PASSWORD is not set. The deployed demo must not use the dev '
        'password; set SEED_USER_PASSWORD, or set DJANGO_DEBUG=1 for local development.'
    )


class Command(BaseCommand):
    help = 'Seeds sample users, vendors and projects from the case study'

    def handle(self, *args, **kwargs):
        # Decide the password before creating anything, so a refused run seeds nothing.
        password = seed_password(os.environ, debug=settings.DEBUG)
        users_data = [
            ('alice', 'Alice Mwangi', Profile.EMPLOYEE),
            ('brian', 'Brian Otieno', Profile.EMPLOYEE),
            ('carol', 'Carol Wanjiku', Profile.MANAGER),
            ('david', 'David Mutua', Profile.MANAGER),
            ('faith', 'Faith Njeri', Profile.FINANCE),
            ('grace', 'Grace Kamau', Profile.ADMIN),
        ]
        for username, full_name, role in users_data:
            first, last = full_name.split(' ', 1)
            user, created = User.objects.get_or_create(
                username=username, defaults={'first_name': first, 'last_name': last}
            )
            if created:
                user.set_password(password)
                user.save()
            Profile.objects.get_or_create(user=user, defaults={'role': role})
            self.stdout.write(f"{'Created' if created else 'Already exists'}: {username} ({role})")

        vendors_data = [
            ('Metro Hardware Ltd.', 'Construction materials'),
            ('Prime Cement Supplies', 'Cement'),
            ('SwiftHaul Logistics', 'Transport'),
            ('PowerHire Kenya', 'Equipment rental'),
            ('Apex Electricals', 'Electrical materials'),
            ('BlueLine Plumbing', 'Plumbing services'),
        ]
        for name, service in vendors_data:
            _, created = Vendor.objects.get_or_create(name=name, defaults={'service': service})
            self.stdout.write(f"{'Created' if created else 'Already exists'}: vendor {name}")

        for name in ['Westlands Project', 'Mombasa Road Project', 'Kisumu Project']:
            _, created = Project.objects.get_or_create(name=name)
            self.stdout.write(f"{'Created' if created else 'Already exists'}: project {name}")