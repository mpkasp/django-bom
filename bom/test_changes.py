from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bom import constants
from bom.helpers import create_a_fake_organization, create_some_fake_parts, create_user_and_organization
from bom.models import Change, ChangeItem
from bom.services import boms, change_display, change_orders, changes, parts
from bom.services.errors import ServiceError


def disable_review(organization):
    return False


def make_assembly(organization, parts_list, description='Assembly'):
    part_class = parts_list[0].number_class
    return parts.create_part(organization, description, part_class_code=str(part_class.code),
                             properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'})


class ChangesServiceTest(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        self.assembly = make_assembly(self.organization, self.parts)
        self.assembly_number = self.assembly.part.full_part_number()
        self.primary_part = self.parts[0]

    def add_member(self, role):
        member = User.objects.create_user(f'member-{role}', f'{role}@example.com', 'ghostpassword')
        profile = member.bom_profile(organization=self.organization)
        profile.role = role
        profile.save()
        return member

    def test_numbers_increment_per_organization(self):
        first = changes.start_change(self.organization, self.user, 'One')
        second = changes.start_change(self.organization, self.user, 'Two')
        self.assertEqual((first.number, second.number), (1, 2))

    def test_only_the_author_withdraws_a_pending_change(self):
        editor = self.add_member(constants.ROLE_TYPE_EDITOR)
        change = changes.start_change(self.organization, editor, 'Changed my mind')
        with self.assertRaises(ServiceError):
            changes.withdraw_change(change, self.user)
        changes.withdraw_change(change, editor)
        self.assertEqual(change.status, Change.STATUS_WITHDRAWN)
        with self.assertRaises(ServiceError):
            changes.withdraw_change(change, editor)

    def test_part_history_lists_only_applied_changes(self):
        released = changes.release_revision(self.assembly, self.user)
        draft = changes.revise_revision(released, self.user)
        change_order = change_orders.create_change_order(self.organization, self.user, 'Rework', 'Because', [draft])
        change_orders.submit(change_order, self.user)
        changes.start_change(self.organization, self.user, 'Untouched')

        history = changes.part_history(self.assembly.part)
        self.assertEqual([entry['change'].items.get().kind for entry in history],
                         [ChangeItem.KIND_CREATE_PART_REVISION, ChangeItem.KIND_RELEASE_REVISION])
        self.assertEqual(history[0]['diff_revision_ids'], (released.id, draft.id))
        self.assertEqual(changes.part_history(self.parts[3]), [])

        change_orders.approve(change_order, self.user)
        self.assertEqual(changes.part_history(self.assembly.part)[0]['change'], change_order)


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class ChangesViewTest(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        self.client = Client()
        self.client.login(username='kasper', password='ghostpassword')
        self.change = changes.start_change(self.organization, self.user, 'Add a cap', 'Because')
        ChangeItem.objects.create(change=self.change, position=1, kind=ChangeItem.KIND_CREATE_PART,
                                  summary='Create part: Cap')

    def new_cap(self):
        return parts.create_part(self.organization, 'Other cap', part_class_code=str(self.parts[0].number_class.code),
                                 properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'}).part

    def review_url(self, name='change-review'):
        return reverse(f'bom:{name}', kwargs={'number': self.change.number})

    def test_pages_require_login_and_the_host_flag(self):
        self.assertEqual(Client().get(reverse('bom:change-list')).status_code, 302)
        config = dict(settings.BOM_CONFIG_DEFAULT, change_orders_enabled='bom.test_changes.disable_review')
        with override_settings(BOM_CONFIG=config):
            self.assertEqual(self.client.get(reverse('bom:change-list')).status_code, 404)
            self.assertEqual(self.client.get(self.review_url()).status_code, 404)

    def test_list_filters_and_nav_badge(self):
        Change.objects.filter(pk=self.change.pk).update(status=Change.STATUS_OPEN)
        response = self.client.get(reverse('bom:change-list'))
        self.assertContains(response, 'Add a cap')
        self.assertContains(response, 'new badge')
        self.assertNotContains(self.client.get(reverse('bom:change-list') + '?filter=released'), 'Add a cap')

    def test_part_page_history_tab_lists_applied_changes_newest_first_and_skips_open_ones(self):
        draft = self.new_cap().latest()
        for summary in ('Edit one', 'Edit two'):
            changes.record_direct_edit(self.organization, self.user, draft.part, ChangeItem.KIND_UPDATE_PART, summary,
                                       part_revision=draft)
        change_orders.submit(
            change_orders.create_change_order(self.organization, self.user, 'Open one', 'Because', [draft]), self.user)

        response = self.client.get(reverse('bom:part-info', kwargs={'part_id': draft.part_id}))
        content = response.content.decode()
        self.assertIn('id="history-tab"', content)
        tab = content[content.index('<div id="history"'):]
        self.assertLess(tab.index('Edit two'), tab.index('Edit one'))
        self.assertNotIn('Open one', tab)

        unrelated = self.client.get(reverse('bom:part-info', kwargs={'part_id': self.parts[3].id}))
        self.assertContains(unrelated, 'No history yet.')

    def test_part_page_shows_history_even_when_review_is_off(self):
        with override_settings(BOM_CONFIG=dict(settings.BOM_CONFIG_DEFAULT, change_orders_enabled=disable_review)):
            response = self.client.get(reverse('bom:part-info', kwargs={'part_id': self.parts[2].id}))
        self.assertContains(response, 'id="history-tab"')

    def test_revision_diff_lists_added_removed_and_changed_lines_and_properties(self):
        old = make_assembly(self.organization, self.parts, description='Assembly')
        boms.add_subpart(self.organization, old, self.parts[0].full_part_number(), count=1, reference='A1')
        boms.add_subpart(self.organization, old, self.parts[1].full_part_number(), count=2)
        new = parts.create_next_revision(old)
        boms.update_subpart(self.organization, new, self.parts[0].full_part_number(), count=5, reference='A1, A2, A3, A4, A5')
        boms.remove_subpart(self.organization, new, self.parts[1].full_part_number())
        boms.add_subpart(self.organization, new, self.parts[2].full_part_number(), count=1)
        new.description = 'Assembly v2'
        new.save()
        sheen = new.properties.get(property_definition__code='sheen')
        sheen.value_raw = 'gloss'
        sheen.save()

        response = self.client.get(reverse('bom:part-diff', kwargs={
            'part_id': old.part_id, 'revision_a_id': old.id, 'revision_b_id': new.id}))

        self.assertEqual(response.status_code, 200)
        labels = {(item.get_kind_display, item.summary): item for item in response.context['items']}
        self.assertEqual(labels[('Update line', self.parts[0].full_part_number())].payload,
                         {'count': 5, 'reference': 'A1, A2, A3, A4, A5'})
        self.assertEqual(labels[('Update line', self.parts[0].full_part_number())].before,
                         {'count': 1, 'reference': 'A1'})
        self.assertIn(('Remove line', self.parts[1].full_part_number()), labels)
        self.assertIn(('Add line', self.parts[2].full_part_number()), labels)
        property_item = next(item for item in response.context['items'] if item.get_kind_display == 'Update property')
        self.assertEqual((property_item.before, property_item.payload), ({'value': 'matte'}, {'value': 'gloss'}))
        self.assertEqual(next(item for item in response.context['items'] if item.get_kind_display == 'Revision').payload,
                         {'description': 'Assembly v2'})

    def test_revision_diff_is_scoped_to_the_part_and_organization(self):
        first, second = self.parts[0].latest(), self.parts[1].latest()
        url = reverse('bom:part-diff', kwargs={
            'part_id': first.part_id, 'revision_a_id': first.id, 'revision_b_id': second.id})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_part_page_offers_compare_with_when_the_part_has_several_revisions(self):
        assembly = make_assembly(self.organization, self.parts)
        self.assertNotContains(self.client.get(reverse('bom:part-info', kwargs={'part_id': assembly.part_id})),
                               'Compare with')
        newer = parts.create_next_revision(assembly)
        response = self.client.get(reverse('bom:part-info', kwargs={'part_id': assembly.part_id}))
        self.assertContains(response, 'Compare with')
        self.assertContains(response, reverse('bom:part-diff', kwargs={
            'part_id': assembly.part_id, 'revision_a_id': assembly.id, 'revision_b_id': newer.id}))

    def test_other_organizations_changes_are_not_found(self):
        stranger = User.objects.create_user('stranger', 'stranger@example.com', 'ghostpassword')
        other_organization = create_a_fake_organization(stranger)
        profile = stranger.bom_profile(organization=other_organization)
        profile.role = constants.ROLE_TYPE_ADMIN
        profile.save()
        client = Client()
        client.login(username='stranger', password='ghostpassword')
        self.assertEqual(client.get(self.review_url()).status_code, 404)

    def test_confirmed_csv_import_records_an_applied_change(self):
        part_class_code = self.parts[0].number_class.code
        csv_text = f'part_class,description,revision,property_sheen,property_voltage\n{part_class_code},Imported cap,1,matte,3.3\n'
        upload = SimpleUploadedFile('parts.csv', csv_text.encode(), content_type='text/csv')
        preview = self.client.post(reverse('bom:import'), {'type': 'parts', 'file': upload})
        self.assertTrue(preview.context['token'], preview.context['errors'])
        self.client.post(reverse('bom:import-confirm'), {'token': preview.context['token']})

        change = Change.objects.get(organization=self.organization, status=Change.STATUS_APPLIED)
        item = change.items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_CSV_IMPORT)
        self.assertIn('Imported parts', item.summary)
        self.assertTrue(item.payload['changes_digest'])


class ChangeDisplayTest(TestCase):
    def test_quantities_drop_trailing_zeros(self):
        self.assertEqual([change_display.quantity(value) for value in (1.0, '2.50', 3)], ['1', '2.5', '3'])

    def test_money_shows_cents_unless_the_price_has_fractions_of_a_cent(self):
        self.assertEqual(change_display.money(Decimal('3.33000'), 'USD'), '$3.33')
        self.assertEqual(change_display.money(Decimal('0.2'), 'USD'), '$0.20')
        self.assertEqual(change_display.money(Decimal('0.0123'), 'USD'), '$0.0123')
        self.assertEqual(change_display.money(Decimal('-1.5'), 'EUR'), '-1.50 EUR')
        self.assertIsNone(change_display.money(None, 'USD'))
