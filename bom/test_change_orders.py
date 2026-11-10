from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from bom import constants
from bom.helpers import create_some_fake_parts, create_user_and_organization
from bom.models import Change, ChangeItem, PartRevision, PartRevisionPropertyDefinition
from bom.services import boms, change_orders, changes, parts
from bom.services.errors import ServiceError


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class ChangeOrderTestCase(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        PartRevisionPropertyDefinition.objects.update(required=False)
        self.part_class_code = str(self.parts[0].number_class.code)
        self.reviewer = self.member('reviewer', constants.ROLE_TYPE_ADMIN)

    def member(self, username, role):
        member = User.objects.create_user(username, f'{username}@example.com', 'ghostpassword')
        profile = member.bom_profile(organization=self.organization)
        profile.role = role
        profile.save()
        return member

    def released(self, description):
        revision = parts.create_part(self.organization, description, part_class_code=self.part_class_code)
        return changes.release_revision(revision, self.user)

    def working_draft(self, released_revision, description=None):
        draft = changes.revise_revision(released_revision, self.user)
        if description:
            parts.update_revision(self.organization, draft, description=description)
        return draft

    def configuration(self, part_revision):
        return PartRevision.objects.get(pk=part_revision.pk).configuration

    def order(self, *drafts, title='Rename'):
        return change_orders.create_change_order(self.organization, self.user, title, 'Because', drafts)


class ChangeOrderFlowTest(ChangeOrderTestCase):
    def test_submitting_locks_the_drafts_and_approval_releases_them(self):
        draft = self.working_draft(self.released('Bracket'), 'Bracket, longer')
        change = self.order(draft)

        change_orders.submit(change, self.user)
        self.assertEqual(change.status, Change.STATUS_OPEN)
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_IN_REVIEW)
        with self.assertRaises(ServiceError):
            parts.update_revision(self.organization, PartRevision.objects.get(pk=draft.pk), description='Sneaky')

        report, = change_orders.revision_reports(change)
        self.assertEqual(report['base_revision'].description, 'Bracket')
        self.assertTrue(any((entry['after'] or {}).get('description') == 'Bracket, longer' for entry in report['diff']))

        change_orders.approve(change, self.reviewer)
        self.assertEqual(change.status, Change.STATUS_APPLIED)
        self.assertEqual(change.resolved_by, self.reviewer)
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_RELEASED)
        item = change.items.get()
        self.assertEqual((item.kind, item.part_revision_id), (ChangeItem.KIND_RELEASE_REVISION, draft.id))
        self.assertIn(change, [entry['change'] for entry in changes.part_history(draft.part)])

    def test_only_a_draft_that_is_in_no_other_change_order_can_be_added(self):
        released = self.released('Bracket')
        with self.assertRaisesMessage(ServiceError, 'only a Draft revision'):
            self.order(released)
        self.assertFalse(Change.objects.filter(title='Rename').exists())

        draft = self.working_draft(released)
        first = self.order(draft)
        with self.assertRaisesMessage(ServiceError, f'already in change order #{first.number}'):
            self.order(draft, title='Second')

        change_orders.withdraw(first, self.user)
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_DRAFT)
        second = self.order(draft, title='Second')
        self.assertEqual(change_orders.change_order_for(draft), second)

    def test_revisions_change_only_while_the_change_order_is_a_draft_or_was_sent_back(self):
        draft = self.working_draft(self.released('Bracket'))
        other = self.working_draft(self.released('Plate'))
        change = self.order(draft)
        change_orders.submit(change, self.user)

        with self.assertRaisesMessage(ServiceError, 'while it is a draft or after it was sent back'):
            change_orders.add_revision(change, self.user, other)
        with self.assertRaisesMessage(ServiceError, 'no revisions to release'):
            change_orders.submit(self.order(title='Empty'), self.user)

    def test_a_rejected_change_order_is_fixed_and_resubmitted_under_the_same_number(self):
        draft = self.working_draft(self.released('Bracket'), 'Brakcet')
        change = self.order(draft)
        change_orders.submit(change, self.user)

        with self.assertRaises(ServiceError):
            change_orders.reject(change, self.reviewer, ' ')
        change_orders.reject(change, self.reviewer, 'Typo in the description')
        self.assertEqual((change.status, change.rejection_reason), (Change.STATUS_REJECTED, 'Typo in the description'))
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_DRAFT)
        self.assertEqual(change_orders.change_order_for(draft), change)

        parts.update_revision(self.organization, PartRevision.objects.get(pk=draft.pk), description='Bracket v2')
        number = change.number
        change_orders.submit(change, self.user)
        self.assertEqual((change.number, change.status, change.rejection_reason), (number, Change.STATUS_OPEN, ''))
        change_orders.approve(change, self.reviewer)
        self.assertEqual(PartRevision.objects.get(pk=draft.pk).description, 'Bracket v2')

    def test_withdrawing_an_open_change_order_leaves_its_revisions_as_drafts(self):
        draft = self.working_draft(self.released('Bracket'))
        change = self.order(draft)
        change_orders.submit(change, self.user)

        with self.assertRaises(ServiceError):
            change_orders.withdraw(change, self.reviewer)
        change_orders.withdraw(change, self.user)

        self.assertEqual(change.status, Change.STATUS_WITHDRAWN)
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_DRAFT)


class ChangeOrderApprovalTest(ChangeOrderTestCase):
    def test_whoever_wrote_or_edited_it_cannot_approve_it(self):
        editor = self.member('editor', constants.ROLE_TYPE_EDITOR)
        draft = changes.revise_revision(self.released('Bracket'), editor)
        change = self.order(draft)
        change_orders.submit(change, self.user)

        self.assertEqual(change_orders.writer_ids(change), {self.user.id, editor.id})
        for writer in (self.user, editor):
            with self.assertRaisesMessage(ServiceError, 'did not write or edit'):
                change_orders.approve(change, writer)
        self.assertTrue(change_orders.approval_status(change, self.reviewer)['can_apply'])

    def test_an_admin_author_can_override_and_the_override_is_recorded(self):
        draft = self.working_draft(self.released('Bracket'))
        change = self.order(draft)
        change_orders.submit(change, self.user)

        approval = change_orders.approval_status(change, self.user)
        self.assertEqual((approval['can_apply'], approval['can_override']), (False, True))
        change_orders.approve(change, self.user, override_protection=True)
        self.assertEqual(change.protection_overridden_by, self.user)

    def test_the_only_member_approves_their_own_change_order(self):
        self.reviewer.is_active = False
        self.reviewer.save()
        draft = self.working_draft(self.released('Bracket'))
        change = self.order(draft)
        change_orders.submit(change, self.user)

        change_orders.approve(change, self.user)
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_RELEASED)

    def test_a_viewer_cannot_approve(self):
        viewer = self.member('viewer', constants.ROLE_TYPE_VIEWER)
        change = self.order(self.working_draft(self.released('Bracket')))
        change_orders.submit(change, self.user)

        with self.assertRaisesMessage(ServiceError, 'cannot release revisions'):
            change_orders.approve(change, viewer)
        self.assertEqual(change.status, Change.STATUS_OPEN)


class ChangeOrderAssemblyTest(ChangeOrderTestCase):
    def assembly_using(self, child_revision, release=True):
        assembly = parts.create_part(self.organization, 'Board', part_class_code=self.part_class_code)
        boms.add_subpart(self.organization, assembly, child_revision.part.full_part_number(), count=1)
        return changes.release_revision(assembly, self.user) if release else assembly

    def child_revision_ids(self, assembly_revision):
        return list(PartRevision.objects.get(pk=assembly_revision.pk).assembly.subparts.values_list(
            'part_revision_id', flat=True))

    def test_a_parent_and_child_in_one_change_order_are_released_child_first(self):
        child = self.released('Cap')
        parent = self.assembly_using(child)
        parent_draft = self.working_draft(parent)
        child_draft = self.working_draft(child, 'Cap, 10V')
        change = self.order(parent_draft, child_draft)
        change_orders.submit(change, self.user)

        change_orders.approve(change, self.reviewer)

        self.assertEqual([item.part_revision_id for item in change.items.order_by('position')],
                         [child_draft.id, parent_draft.id])
        self.assertEqual(self.child_revision_ids(parent_draft), [child_draft.id])
        self.assertEqual(self.child_revision_ids(parent), [child.id])

    def test_released_assemblies_outside_the_change_order_are_offered_a_new_revision(self):
        child = self.released('Cap')
        parent = self.assembly_using(child)
        child_draft = self.working_draft(child, 'Cap, 10V')
        change = self.order(child_draft)
        change_orders.submit(change, self.user)
        self.assertEqual(change_orders.cascade_candidates(change), [])
        change_orders.approve(change, self.reviewer)

        candidate, = change_orders.cascade_candidates(change)
        self.assertEqual(candidate['parent'], parent)
        new_parent, = change_orders.cascade(change, self.reviewer, [parent.id])

        self.assertEqual(new_parent.configuration, constants.CONFIGURATION_TYPE_DRAFT)
        self.assertEqual(self.child_revision_ids(new_parent), [child_draft.id])
        self.assertEqual(change_orders.cascade_candidates(change), [])
