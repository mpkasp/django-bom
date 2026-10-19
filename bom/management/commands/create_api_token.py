from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from bom.models import APIToken


class Command(BaseCommand):
    help = "Issue an API/MCP bearer token for a user in their organization. The plaintext is printed once."

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--name', default='MCP client')
        parser.add_argument('--scopes', default=APIToken.SCOPE_READ,
                            help="Comma-separated: read, write (default: read)")

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(username=options['username']).first()
        if user is None:
            raise CommandError(f"No user {options['username']!r}")
        organization = user.bom_profile().organization
        if organization is None:
            raise CommandError(f"{user.username} has no organization")
        scopes = [scope.strip() for scope in options['scopes'].split(',') if scope.strip()]
        unknown = set(scopes) - {scope for scope, _ in APIToken.SCOPES}
        if unknown:
            raise CommandError(f"Unknown scopes: {sorted(unknown)}")
        token, plaintext = APIToken.issue(user, organization, options['name'], scopes)
        self.stdout.write(f"Token for {user.username} in {organization.name} ({token.scopes}):")
        self.stdout.write(plaintext)
