import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from . import constants
from .helpers import create_a_fake_organization, create_user_and_organization


class TestOrganizationInvites(TestCase):
    def setUp(self):
        self.admin, self.organization = create_user_and_organization()
        self.organization.subscription_quantity = 3
        self.organization.save()
        self.invitee = User.objects.create_user('bob', 'Bob@Example.com', 'pw')

    def send_invite(self, email='bob@example.com', role=constants.ROLE_TYPE_EDITOR, sender=None):
        self.client.force_login(sender or self.admin)
        return self.client.post(reverse('bom:settings'), {'submit-invite-member': '1', 'email': email, 'role': role},
                                follow=True)

    def invite_path(self):
        return re.search(r'https?://[^/]+(/\S+)', mail.outbox[-1].body).group(1)

    def test_accepting_invite_joins_with_invited_role(self):
        response = self.send_invite()
        self.assertContains(response, 'Invitation sent to bob@example.com')
        self.assertEqual(mail.outbox[-1].to, ['bob@example.com'])

        self.client.force_login(self.invitee)
        response = self.client.get(self.invite_path())
        self.assertContains(response, 'Join organization')
        self.assertIsNone(self.invitee.bom_profile().organization)

        self.client.post(self.invite_path())
        profile = self.invitee.bom_profile()
        self.assertEqual(profile.organization, self.organization)
        self.assertEqual(profile.role, constants.ROLE_TYPE_EDITOR)

    def test_only_the_invited_email_can_accept(self):
        self.send_invite()
        other_user = User.objects.create_user('carol', 'carol@example.com', 'pw')
        self.client.force_login(other_user)
        response = self.client.post(self.invite_path())
        self.assertContains(response, 'This invitation was sent to bob@example.com')
        self.assertIsNone(other_user.bom_profile().organization)

    def test_member_of_another_organization_cannot_accept(self):
        self.send_invite()
        other_organization = create_a_fake_organization(User.objects.create_user('dan', 'dan@example.com', 'pw'))
        profile = self.invitee.bom_profile()
        profile.organization = other_organization
        profile.save()

        self.client.force_login(self.invitee)
        response = self.client.post(self.invite_path())
        self.assertContains(response, "already a member of")
        self.assertEqual(self.invitee.bom_profile().organization, other_organization)

    def test_expired_invite_is_rejected(self):
        self.send_invite()
        self.client.force_login(self.invitee)
        with patch('bom.services.membership.INVITE_MAX_AGE', timedelta(seconds=-1)):
            response = self.client.post(self.invite_path())
        self.assertContains(response, 'This invitation has expired')
        self.assertIsNone(self.invitee.bom_profile().organization)

    def test_tampered_invite_is_rejected(self):
        self.send_invite()
        self.client.force_login(self.invitee)
        response = self.client.post(self.invite_path().rstrip('/') + 'x/')
        self.assertContains(response, 'This invitation link is not valid')
        self.assertIsNone(self.invitee.bom_profile().organization)

    def test_seats_are_checked_again_on_accept(self):
        self.send_invite()
        self.organization.subscription_quantity = 1
        self.organization.save()
        self.client.force_login(self.invitee)
        response = self.client.post(self.invite_path())
        self.assertContains(response, 'member capacity')
        self.assertIsNone(self.invitee.bom_profile().organization)

    def test_anonymous_visitor_is_sent_to_login(self):
        self.send_invite()
        self.client.logout()
        response = self.client.get(self.invite_path())
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response.url)

    def test_non_admin_cannot_invite(self):
        viewer = User.objects.create_user('erin', 'erin@example.com', 'pw')
        profile = viewer.bom_profile(organization=self.organization)
        profile.role = constants.ROLE_TYPE_VIEWER
        profile.save()
        response = self.send_invite(sender=viewer)
        self.assertContains(response, 'You are not allowed to manage users')
        self.assertEqual(len(mail.outbox), 0)

    def test_existing_member_is_not_invited(self):
        response = self.send_invite(email='KASPER@mcfadden.com')
        self.assertContains(response, 'already a member of')
        self.assertEqual(len(mail.outbox), 0)

    def test_editor_invite_needs_a_paid_seat(self):
        self.organization.subscription_quantity = 1
        self.organization.save()
        response = self.send_invite()
        self.assertContains(response, 'member capacity')
        self.assertEqual(len(mail.outbox), 0)

    def test_free_organization_cannot_invite(self):
        self.organization.subscription = constants.SUBSCRIPTION_TYPE_FREE
        self.organization.save()
        response = self.send_invite()
        self.assertContains(response, 'You need a Pro subscription')
        self.assertEqual(len(mail.outbox), 0)


class TestMemberManagementPermissions(TestCase):
    def setUp(self):
        self.admin, self.organization = create_user_and_organization()
        self.viewer = User.objects.create_user('erin', 'erin@example.com', 'pw')
        self.viewer_profile = self.viewer.bom_profile(organization=self.organization)
        self.viewer_profile.role = constants.ROLE_TYPE_VIEWER
        self.viewer_profile.save()

    def test_viewer_cannot_change_roles(self):
        self.client.force_login(self.viewer)
        self.client.post(reverse('bom:user-meta-edit', kwargs={'user_meta_id': self.viewer_profile.id}),
                         {'role': constants.ROLE_TYPE_ADMIN})
        self.viewer_profile.refresh_from_db()
        self.assertEqual(self.viewer_profile.role, constants.ROLE_TYPE_VIEWER)

    def test_admin_of_another_organization_cannot_change_roles(self):
        outsider = User.objects.create_user('frank', 'frank@example.com', 'pw')
        outsider_profile = outsider.bom_profile(organization=create_a_fake_organization(outsider))
        outsider_profile.role = constants.ROLE_TYPE_ADMIN
        outsider_profile.save()
        self.client.force_login(outsider)
        response = self.client.post(reverse('bom:user-meta-edit', kwargs={'user_meta_id': self.viewer_profile.id}),
                                    {'role': constants.ROLE_TYPE_ADMIN})
        self.assertEqual(response.status_code, 404)
        self.viewer_profile.refresh_from_db()
        self.assertEqual(self.viewer_profile.role, constants.ROLE_TYPE_VIEWER)

    def test_admin_can_change_roles(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('bom:user-meta-edit', kwargs={'user_meta_id': self.viewer_profile.id}),
                         {'role': constants.ROLE_TYPE_EDITOR})
        self.viewer_profile.refresh_from_db()
        self.assertEqual(self.viewer_profile.role, constants.ROLE_TYPE_EDITOR)

    def test_viewer_cannot_remove_members(self):
        other_viewer = User.objects.create_user('gail', 'gail@example.com', 'pw')
        other_profile = other_viewer.bom_profile(organization=self.organization)
        self.client.force_login(self.viewer)
        self.client.post(reverse('bom:settings'),
                         {'submit-remove-user': '1', f'remove_user_meta_id_{other_profile.id}': 'on'})
        other_profile.refresh_from_db()
        self.assertEqual(other_profile.organization, self.organization)
