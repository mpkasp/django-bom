from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from bom import constants
from bom.models import Change, ChangeItem, PartRevision
from bom.services import boms, change_orders, changes, parts
from bom.services.errors import ServiceError
from bom.test_change_orders import ChangeOrderTestCase


class ChangeOrderViewTestCase(ChangeOrderTestCase):
    def setUp(self):
        super().setUp()
        self.client = self.client_for('kasper')
        self.reviewer_client = self.client_for('reviewer')

    def client_for(self, username):
        client = Client()
        client.login(username=username, password='ghostpassword')
        return client

    def url(self, change, name='change-review', **kwargs):
        return reverse(f'bom:{name}', kwargs={'number': change.number, **kwargs})

    def number(self, part_revision):
        return part_revision.part.full_part_number()


class ChangeOrderPageTest(ChangeOrderViewTestCase):
    def test_the_page_shows_what_each_revision_changes(self):
        kept, dropped, added = (self.released(name) for name in ('Kept', 'Dropped', 'Added'))
        assembly = parts.create_part(self.organization, 'Assembly', part_class_code=self.part_class_code)
        boms.add_subpart(self.organization, assembly, self.number(kept), count=1, reference='R1')
        boms.add_subpart(self.organization, assembly, self.number(dropped), count=2)
        assembly = changes.release_revision(assembly, self.user)
        draft = self.working_draft(assembly, 'Assembly, improved')
        boms.update_subpart(self.organization, draft, self.number(kept), count=3, reference='R1, R2, R3')
        boms.remove_subpart(self.organization, draft, self.number(dropped))
        boms.add_subpart(self.organization, draft, self.number(added), count=1, reference='C9')
        change = self.order(draft, title='Improve the assembly')

        page = self.client.get(self.url(change))

        self.assertContains(page, 'Change order #%d' % change.number)
        self.assertContains(page, 'Improve the assembly')
        self.assertContains(page, '1 revision')
        self.assertContains(page, 'class="card-panel change-part"', count=1)
        self.assertContains(page, f'Rev {assembly.revision} &middot; Released')
        self.assertContains(page, f'Rev {draft.revision} &middot; Draft')
        self.assertContains(page, '<span class="change-old">Assembly</span>', html=False)
        self.assertContains(page, '<span class="change-new-value">Assembly, improved</span>', html=False)
        self.assertNotContains(page, 'Configuration')
        self.assertContains(page, '<tr class="change-add">', count=1)
        self.assertContains(page, '<tr class="change-remove">', count=1)
        self.assertContains(page, '<tr class="change-change">', count=1)
        self.assertContains(page, '<span class="change-old">1</span> <span class="change-new-value">3</span>',
                            html=False)
        self.assertContains(page, '<span class="change-old">R1</span>', html=False)
        self.assertContains(page, 'C9')
        self.assertContains(page, 'Cost per unit at 100')
        self.assertContains(page, 'Submit for review')
        self.assertContains(page, '>Remove</button>')
        self.assertNotContains(page, 'Approve')

    def test_a_first_revision_and_an_unchanged_one_say_so(self):
        first = parts.create_part(self.organization, 'Brand new', part_class_code=self.part_class_code)
        unchanged = self.working_draft(self.released('Same'))
        change = self.order(first, unchanged)

        page = self.client.get(self.url(change))

        self.assertContains(page, 'First release')
        self.assertContains(page, f'Nothing differs from rev {changes._previous_revision(unchanged).revision}.')
        self.assertContains(page, '2 revisions')

    def test_review_reject_resubmit_and_approve(self):
        draft = self.working_draft(self.released('Bracket'), 'Bracket, longer')
        change = self.order(draft)

        self.client.post(self.url(change, 'change-submit'))
        change.refresh_from_db()
        self.assertEqual(change.status, Change.STATUS_OPEN)
        author_page = self.client.get(self.url(change))
        self.assertContains(author_page, 'Someone who did not write or edit this change order must approve it.')
        self.assertContains(author_page, 'Approve as admin')
        self.assertNotContains(author_page, 'Approve 1 revision')
        self.assertNotContains(author_page, 'Reject change order')
        self.assertNotContains(author_page, '>Remove</button>')
        reviewer_page = self.reviewer_client.get(self.url(change))
        self.assertContains(reviewer_page, 'Approve 1 revision')
        self.assertContains(reviewer_page, 'Reject change order')
        self.assertNotContains(reviewer_page, 'Withdraw')

        self.reviewer_client.post(self.url(change, 'change-reject'), {'reason': ''})
        change.refresh_from_db()
        self.assertEqual(change.status, Change.STATUS_OPEN)
        self.reviewer_client.post(self.url(change, 'change-reject'), {'reason': 'Make it shorter'})
        sent_back = self.client.get(self.url(change))
        self.assertContains(sent_back, 'Make it shorter')
        self.assertContains(sent_back, 'Its revisions are Drafts again')
        self.assertContains(sent_back, 'Sent back')
        self.assertContains(sent_back, 'Submit for review')
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_DRAFT)

        self.client.post(self.url(change, 'change-submit'))
        self.reviewer_client.post(self.url(change, 'change-apply'))
        change.refresh_from_db()
        self.assertEqual(change.status, Change.STATUS_APPLIED)
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_RELEASED)
        approved_on = date_format(timezone.localtime(change.resolved_at), 'M j, Y')
        approved = self.client.get(self.url(change))
        self.assertContains(approved, 'Approved by reviewer')
        self.assertContains(approved, f'>{approved_on}</span>')
        self.assertContains(approved, f'Released rev {draft.revision} of {self.number(draft)}')
        self.assertNotContains(approved, 'Make it shorter')

    def test_an_admin_author_approves_with_the_recorded_override(self):
        draft = self.working_draft(self.released('Bracket'), 'Bracket, longer')
        change = self.order(draft)
        change_orders.submit(change, self.user)

        self.client.post(self.url(change, 'change-apply'))
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_IN_REVIEW)
        self.client.post(self.url(change, 'change-apply'), {'override': '1'})

        change.refresh_from_db()
        self.assertEqual((change.status, change.protection_overridden_by), (Change.STATUS_APPLIED, self.user))

    def test_an_editor_removes_a_revision_and_the_author_withdraws(self):
        first, second = (self.working_draft(self.released(name)) for name in ('One', 'Two'))
        change = self.order(first, second)

        self.reviewer_client.post(self.url(change, 'change-revision-remove', part_revision_id=first.id))
        self.assertEqual([link.part_revision for link in change.revisions.all()], [second])

        self.reviewer_client.post(self.url(change, 'change-withdraw'))
        change.refresh_from_db()
        self.assertEqual(change.status, Change.STATUS_DRAFT)
        self.client.post(self.url(change, 'change-withdraw'))
        change.refresh_from_db()
        self.assertEqual(change.status, Change.STATUS_WITHDRAWN)
        self.assertEqual(self.configuration(second), constants.CONFIGURATION_TYPE_DRAFT)

    def test_parts_are_added_from_the_change_order_and_released_ones_are_revised(self):
        released, draft = self.released('Bracket'), parts.create_part(
            self.organization, 'Plate', part_class_code=self.part_class_code)
        change = self.order(title='Two parts')
        page = self.client.get(self.url(change))
        self.assertContains(page, 'Add part to change order…')

        for part_revision in (released, draft):
            self.client.post(self.url(change, 'change-part-add'), {'part_number': self.number(part_revision)})
        added = [link.part_revision for link in change.revisions.all()]
        self.assertEqual([(revision.part_id, revision.revision, revision.configuration) for revision in added],
                         [(released.part_id, '2', constants.CONFIGURATION_TYPE_DRAFT),
                          (draft.part_id, '1', constants.CONFIGURATION_TYPE_DRAFT)])
        page = self.client.get(self.url(change))
        self.assertContains(page, '>Edit revision</a>', count=2)
        edit_url = reverse('bom:part-edit-revision', kwargs={
            'part_id': added[0].part_id, 'part_revision_id': added[0].id})
        self.assertContains(page, f'href="{edit_url}">Edit revision</a>')
        self.assertContains(self.client.get(edit_url), f'Back to change order #{change.number}</a>')

        response = self.client.post(self.url(change, 'change-part-add'), {'part_number': 'nope'}, follow=True)
        self.assertEqual(change.revisions.count(), 2)
        change_orders.submit(change, self.user)
        page = self.client.get(self.url(change))
        self.assertNotContains(page, 'Add part to change order…')
        self.assertNotContains(page, '>Edit revision</a>')

    def test_an_approved_change_order_offers_the_cascade(self):
        leaf = self.released('Leaf')
        parent = parts.create_part(self.organization, 'Parent', part_class_code=self.part_class_code)
        boms.add_subpart(self.organization, parent, self.number(leaf), count=2)
        parent = changes.release_revision(parent, self.user)
        change = self.order(self.working_draft(leaf, 'Leaf, improved'))
        change_orders.submit(change, self.user)

        open_page = self.reviewer_client.get(self.url(change))
        self.assertContains(open_page, 'it will need a new revision')
        self.assertNotContains(open_page, 'Create new revisions for selected')

        change_orders.approve(change, self.reviewer)
        page = self.client.get(self.url(change))
        self.assertContains(page, 'Create new revisions for selected')
        self.assertContains(page, f'{self.number(parent)} rev {parent.revision}')

        page = self.client.post(self.url(change, 'change-cascade'), {'parent_revision_id': [parent.id]}, follow=True)
        new_parent = parent.part.latest()
        self.assertEqual(new_parent.configuration, constants.CONFIGURATION_TYPE_DRAFT)
        self.assertContains(page, f'Created {self.number(new_parent)} rev {new_parent.revision}')
        self.assertNotContains(page, 'Create new revisions for selected')

    def test_a_change_from_before_change_orders_lists_its_edits_and_can_only_be_withdrawn(self):
        staged = changes.start_change(self.organization, self.user, 'Staged', 'Because')
        ChangeItem.objects.create(change=staged, position=1, kind=ChangeItem.KIND_CREATE_PART,
                                  summary='Create part: Staged cap')
        Change.objects.filter(pk=staged.pk).update(status=Change.STATUS_OPEN)

        page = self.client.get(self.url(staged))
        self.assertContains(page, staged.items.get().summary)
        self.assertContains(page, 'Withdraw')
        self.assertNotContains(page, 'Approve')
        self.assertNotContains(page, 'Reject change order')

        for name in ('change-apply', 'change-reject', 'change-submit'):
            self.assertEqual(self.reviewer_client.post(self.url(staged, name), {'reason': 'No'}).status_code, 404)
        staged.refresh_from_db()
        self.assertEqual(staged.status, Change.STATUS_OPEN)

        self.client.post(self.url(staged, 'change-withdraw'))
        staged.refresh_from_db()
        self.assertEqual(staged.status, Change.STATUS_WITHDRAWN)
        self.assertNotContains(self.client.get(self.url(staged)), 'Withdraw</button>')


class ChangeOrderListTest(ChangeOrderViewTestCase):
    def test_each_filter_lists_its_change_orders_with_their_revisions(self):
        drafts = {name: self.working_draft(self.released(name)) for name in ('Draft', 'Open', 'Back', 'Done')}
        orders = {name: self.order(draft, title=f'{name} order') for name, draft in drafts.items()}
        for name in ('Open', 'Back', 'Done'):
            change_orders.submit(orders[name], self.user)
        change_orders.reject(orders['Back'], self.reviewer, 'Not yet')
        change_orders.approve(orders['Done'], self.reviewer)
        url = reverse('bom:change-list')

        expected = {'': ['Open'], '?filter=drafts': ['Draft', 'Back'], '?filter=released': ['Done'], '?filter=history': []}
        for query, names in expected.items():
            with self.subTest(query=query):
                page = self.client.get(url + query)
                for name in orders:
                    self.assertContains(page, f'{name} order', count=1 if name in names else 0)

        open_page = self.client.get(url)
        self.assertContains(open_page, '<h4>Change orders</h4>')
        self.assertContains(open_page, 'data-badge-caption="">1<')
        self.assertContains(open_page, f"{self.number(drafts['Open'])} rev {drafts['Open'].revision}")
        self.assertContains(self.client.get(url + '?filter=drafts'), 'Sent back')
        self.assertContains(self.client.get(url + '?filter=history'), 'Revise')


class ReleasePageChangeOrderTest(ChangeOrderViewTestCase):
    def release_url(self, part_revision):
        return reverse('bom:part-revision-release', kwargs={
            'part_id': part_revision.part_id, 'part_revision_id': part_revision.id})

    def test_a_draft_is_added_to_an_existing_change_order(self):
        change = self.order(self.working_draft(self.released('One')), title='Existing order')
        draft = self.working_draft(self.released('Two'))

        page = self.client.get(self.release_url(draft))
        self.assertContains(page, 'Add to change order')
        self.assertContains(page, f'#{change.number} Existing order')

        response = self.client.post(self.release_url(draft), {
            'action': 'add_to_change_order', 'change_order': change.number})

        self.assertRedirects(response, self.url(change))
        self.assertEqual(change.revisions.count(), 2)
        self.assertEqual(Change.objects.filter(organization=self.organization, is_direct_edit=False).count(), 1)

    def test_a_change_order_under_review_is_not_offered_or_accepted(self):
        change = self.order(self.working_draft(self.released('One')), title='Under review')
        change_orders.submit(change, self.user)
        draft = self.working_draft(self.released('Two'))

        self.assertNotContains(self.client.get(self.release_url(draft)), 'Under review')
        self.client.post(self.release_url(draft), {'action': 'add_to_change_order', 'change_order': change.number})

        self.assertEqual(change.revisions.count(), 1)

    def test_the_release_page_of_a_revision_in_a_change_order_leads_to_it(self):
        draft = self.working_draft(self.released('One'))
        change = self.order(draft)

        self.assertRedirects(self.client.get(self.release_url(draft)), self.url(change))
        self.client.post(self.release_url(draft), {'action': 'release'})
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_DRAFT)


class ChangeOrderGuardTest(ChangeOrderViewTestCase):
    def revision_url(self, name, part_revision):
        return reverse(f'bom:{name}', kwargs={'part_id': part_revision.part_id, 'part_revision_id': part_revision.id})

    def test_a_revision_in_a_change_order_is_released_only_by_approving_it(self):
        draft = self.working_draft(self.released('One'))
        change = self.order(draft)

        with self.assertRaisesMessage(ServiceError, f'is in change order #{change.number}; approve that'):
            changes.release_revision(draft, self.user)

        change_orders.withdraw(change, self.user)
        changes.release_revision(draft, self.user)
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_RELEASED)

    def test_a_revision_under_review_cannot_be_moved_by_hand(self):
        draft = self.working_draft(self.released('One'))
        change = self.order(draft)

        self.assertNotContains(self.client.get(self.revision_url('part-info-history', draft)), 'Send to review')

        change_orders.submit(change, self.user)
        response = self.client.get(self.revision_url('part-revision-draft', draft), follow=True)
        self.assertContains(response, f'under review in change order #{change.number}')
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_IN_REVIEW)

    def test_a_change_order_is_started_from_the_list_or_from_a_part(self):
        released = self.released('Bracket')
        self.assertContains(self.client.get(reverse('bom:change-list')), 'New change order')
        part_page = self.client.get(self.revision_url('part-info-history', released))
        self.assertContains(part_page, f"{reverse('bom:change-new')}?part={self.number(released)}")
        form = self.client.get(reverse('bom:change-new'), {'part': self.number(released)})
        self.assertContains(form, f'value="{self.number(released)}"')

        response = self.client.post(reverse('bom:change-new'), {
            'title': 'Longer bracket', 'rationale': 'It fouls the lid', 'part_number': self.number(released)})
        change = Change.objects.get(title='Longer bracket')
        self.assertRedirects(response, self.url(change))
        draft = change.revisions.get().part_revision
        self.assertEqual((draft.part_id, draft.revision, draft.configuration),
                         (released.part_id, '2', constants.CONFIGURATION_TYPE_DRAFT))
        self.assertNotContains(self.client.get(self.revision_url('part-info-history', draft)), 'Start a change order')

        self.client.post(reverse('bom:change-new'), {'title': 'Bad part', 'part_number': 'nope'})
        self.assertFalse(Change.objects.filter(title='Bad part').exists())

    def test_the_edit_form_cannot_move_a_revision_under_review(self):
        draft = self.working_draft(self.released('One'))
        change = self.order(draft)
        change_orders.submit(change, self.user)
        url = self.revision_url('part-edit-revision', draft)

        for configuration in (constants.CONFIGURATION_TYPE_DRAFT, constants.CONFIGURATION_TYPE_RELEASED):
            self.client.post(url, {'revision': draft.revision, 'configuration': configuration, 'description': 'One'})
            self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_IN_REVIEW)

        PartRevision.objects.filter(pk=draft.pk).update(configuration=constants.CONFIGURATION_TYPE_DRAFT)
        response = self.client.post(url, {'revision': draft.revision, 'description': 'One',
                                          'configuration': constants.CONFIGURATION_TYPE_RELEASED}, follow=True)
        self.assertContains(response, f'under review in change order #{change.number}')
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_DRAFT)

    def test_the_part_page_says_which_change_order_holds_the_revision(self):
        draft = self.working_draft(self.released('One'))
        other = self.released('Other')
        change = self.order(draft)
        banner = f'Open change order #{change.number}</a>'

        page = self.client.get(self.revision_url('part-info-history', draft))
        self.assertContains(page, banner)
        self.assertContains(page, 'then submit the change order for review')
        self.assertNotContains(self.client.get(self.revision_url('part-info-history', other)), 'Open change order')

        change_orders.submit(change, self.user)
        self.assertContains(self.client.get(self.revision_url('part-info-history', draft)), 'under review and locked')
        change_orders.withdraw(change, self.user)
        self.assertNotContains(self.client.get(self.revision_url('part-info-history', draft)), banner)
