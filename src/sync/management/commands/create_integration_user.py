from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

from sync.permissions import INTEGRATION_GROUP


class Command(BaseCommand):
    help = "Create the salesforce-integration group, user and token (idempotent). Prints the token."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="salesforce-integration")

    def handle(self, *args, username, **options):
        group, _ = Group.objects.get_or_create(name=INTEGRATION_GROUP)
        user, created = get_user_model().objects.get_or_create(username=username)
        if created:
            user.set_unusable_password()
            user.save()
        user.groups.add(group)
        token, _ = Token.objects.get_or_create(user=user)
        self.stdout.write(token.key)
