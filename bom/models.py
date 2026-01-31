from __future__ import unicode_literals

import logging
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from djmoney.models.fields import CURRENCY_CHOICES, CurrencyField, MoneyField
from math import ceil
from social_django.models import UserSocialAuth

from .base_classes import AsDictModel
from .constants import *
from .csv_headers import PartsListCSVHeaders, PartsListCSVHeadersSemiIntelligent, BOMIndentedCSVHeaders, \
    BOMFlatCSVHeaders
from .part_bom import PartBom, PartBomItem, PartIndentedBomItem
from .utils import increment_str, listify_string, prep_for_sorting_nicely, stringify_list, strip_trailing_zeros
from .validators import alphanumeric

logger = logging.getLogger(__name__)
User = get_user_model()


def get_user_meta_model():
    from django.apps import apps
    from django.conf import settings
    return apps.get_model(settings.BOM_USER_META_MODEL)


def get_organization_model():
    from django.apps import apps
    from django.conf import settings
    return apps.get_model(settings.BOM_ORGANIZATION_MODEL)


def _user_meta(self, organization=None):
    from django.apps import apps
    from django.conf import settings
    UserMetaModel = apps.get_model(settings.BOM_USER_META_MODEL)
    meta, created = UserMetaModel.objects.get_or_create(
        user=self,
        defaults={'organization': organization}
    )
    return meta


class OrganizationManager(models.Manager):
    def available_to(self, organization):
        return self.get_queryset().filter(
            models.Q(organization=organization) |
            models.Q(organization__isnull=True)
        )


class OrganizationScopedModel(models.Model):
    organization = models.ForeignKey(settings.BOM_ORGANIZATION_MODEL, on_delete=models.CASCADE, db_index=True)

    objects = OrganizationManager()

    class Meta:
        abstract = True


class OrganizationOptionalModel(models.Model):
    organization = models.ForeignKey(settings.BOM_ORGANIZATION_MODEL, on_delete=models.CASCADE, db_index=True,
                                     null=True, blank=True, help_text="Leave empty for a Global/System record.")

    objects = OrganizationManager()

    class Meta:
        abstract = True


class AbstractOrganization(models.Model):
    name = models.CharField(max_length=255, default=None)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    number_scheme = models.CharField(max_length=1, choices=NUMBER_SCHEMES, default=NUMBER_SCHEME_SEMI_INTELLIGENT)
    number_class_code_len = models.PositiveIntegerField(default=NUMBER_CLASS_CODE_LEN_DEFAULT,
                                                        validators=[MinValueValidator(NUMBER_CLASS_CODE_LEN_MIN),
                                                                    MaxValueValidator(NUMBER_CLASS_CODE_LEN_MAX)])
    number_item_len = models.PositiveIntegerField(default=NUMBER_ITEM_LEN_DEFAULT,
                                                  validators=[MinValueValidator(NUMBER_ITEM_LEN_MIN),
                                                              MaxValueValidator(NUMBER_ITEM_LEN_MAX)])
    number_variation_len = models.PositiveIntegerField(default=NUMBER_VARIATION_LEN_DEFAULT,
                                                       validators=[MinValueValidator(NUMBER_VARIATION_LEN_MIN),
                                                                   MaxValueValidator(NUMBER_VARIATION_LEN_MAX)])
    google_drive_parent = models.CharField(max_length=128, blank=True, default=None, null=True)
    currency = CurrencyField(max_length=3, choices=CURRENCY_CHOICES, default='USD')

    subscription = models.CharField(max_length=1, choices=SUBSCRIPTION_TYPES)
    subscription_quantity = models.IntegerField(default=0)

    def number_cs(self):
        return "C" * self.number_class_code_len

    def number_ns(self):
        return "N" * self.number_item_len

    def number_vs(self):
        return "V" * self.number_variation_len

    def __str__(self):
        return u'%s' % self.name

    def seller_parts(self):
        return SellerPart.objects.filter(seller__organization=self)

    def part_list_csv_headers(self):
        if self.number_scheme == NUMBER_SCHEME_INTELLIGENT:
            headers = PartsListCSVHeaders()
        else:
            headers = PartsListCSVHeadersSemiIntelligent()

        # Add dynamic headers
        definitions = PartRevisionPropertyDefinition.objects.available_to(self).all()
        headers.add_dynamic_headers(definitions)
        return headers

    def bom_indented_csv_headers(self):
        headers = BOMIndentedCSVHeaders()
        definitions = PartRevisionPropertyDefinition.objects.available_to(self).all()
        headers.add_dynamic_headers(definitions)
        return headers

    def bom_flat_csv_headers(self):
        headers = BOMFlatCSVHeaders()
        definitions = PartRevisionPropertyDefinition.objects.available_to(self).all()
        headers.add_dynamic_headers(definitions)
        return headers

    @property
    def email(self):
        return self.owner.email

    def save(self, *args, **kwargs):
        super(AbstractOrganization, self).save()
        SellerPart.objects.filter(seller__organization=self).update(unit_cost_currency=self.currency,
                                                                    nre_cost_currency=self.currency)

    class Meta:
        abstract = True


class Organization(AbstractOrganization):
    class Meta:
        swappable = 'BOM_ORGANIZATION_MODEL'
        permissions = (
            ("manage_members", "Can manage organization members"),
        )


class AbstractUserMeta(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, db_index=True, on_delete=models.CASCADE)
    organization = models.ForeignKey(settings.BOM_ORGANIZATION_MODEL, blank=True, null=True, on_delete=models.CASCADE)
    role = models.CharField(max_length=1, choices=ROLE_TYPES)

    def get_or_create_organization(self):
        if self.organization is None:
            if self.user.first_name == '' and self.user.last_name == '':
                org_name = self.user.username
            else:
                org_name = self.user.first_name + ' ' + self.user.last_name

            OrganizationModel = apps.get_model(settings.BOM_ORGANIZATION_MODEL)
            organization, created = OrganizationModel.objects.get_or_create(owner=self.user, defaults={'name': org_name,
                                                                                                       'subscription': 'F'})

            self.organization = organization
            self.role = 'A'
            self.save()
        return self.organization

    def google_authenticated(self) -> bool:
        try:
            self.user.social_auth.get(provider='google-oauth2')
            return True
        except UserSocialAuth.DoesNotExist:
            return False

    def is_organization_owner(self) -> bool:
        return self.organization.owner == self.user if self.organization else False

    def can_manage_organization(self) -> bool:
        return self.role == ROLE_TYPE_ADMIN or self.is_organization_owner()

    class Meta:
        abstract = True


class UserMeta(AbstractUserMeta):
    class Meta:
        swappable = 'BOM_USER_META_MODEL'


class PartClass(OrganizationScopedModel):
    code = models.CharField(max_length=NUMBER_CLASS_CODE_LEN_MAX, validators=[alphanumeric])
    name = models.CharField(max_length=255, default=None)
    comment = models.CharField(max_length=255, default='', blank=True)
    mouser_enabled = models.BooleanField(default=False)
    property_definitions = models.ManyToManyField('PartRevisionPropertyDefinition', blank=True,
                                                  related_name='part_classes')

    class Meta(OrganizationScopedModel.Meta):
        unique_together = [['code', 'organization', ], ]
        ordering = ['code']
        indexes = [
            models.Index(fields=['organization', 'code']),
        ]

    def __str__(self):
        return f'{self.code}: {self.name}'


class Manufacturer(OrganizationScopedModel, AsDictModel):
    name = models.CharField(max_length=128, default=None)

    class Meta(OrganizationScopedModel.Meta):
        ordering = ['name']

    def __str__(self):
        return u'%s' % self.name


# Part contains the root information for a component. Parts have attributes that can be changed over time
# (see PartRevision). Part numbers can be changed over time, but these cannot be tracked, as it is not a practice
# that should be done often.
class Part(OrganizationScopedModel):
    number_class = models.ForeignKey(PartClass, default=None, blank=True, null=True, related_name='number_class', on_delete=models.CASCADE, db_index=True)
    number_item = models.CharField(max_length=NUMBER_ITEM_LEN_MAX, default=None, blank=True)
    number_variation = models.CharField(max_length=NUMBER_VARIATION_LEN_MAX, default=None, blank=True, null=True, validators=[alphanumeric])
    primary_manufacturer_part = models.ForeignKey('ManufacturerPart', default=None, null=True, blank=True,
                                                  on_delete=models.SET_NULL, related_name='primary_manufacturer_part')
    google_drive_parent = models.CharField(max_length=128, blank=True, default=None, null=True)

    class Meta(OrganizationScopedModel.Meta):
        unique_together = ['number_class', 'number_item', 'number_variation', 'organization', ]
        indexes = [
            models.Index(fields=['organization', 'number_class']),
        ]

    def full_part_number(self):
        if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
            if self.organization.number_variation_len > 0:
                return f"{self.number_class.code}-{self.number_item}-{self.number_variation}"
            else:
                return f"{self.number_class.code}-{self.number_item}"
        else:
            return self.number_item

    @staticmethod
    def _expect_length(value, expected_len, units_word, label):
        if len(value) != expected_len:
            raise AttributeError(f"Expect {expected_len} {units_word} for {label}")

    @staticmethod
    def verify_format_number_class(number_class, organization):
        Part._expect_length(number_class, organization.number_class_code_len, 'digits', 'number class')
        if number_class is not None:
            for c in number_class:
                if not (c.isdigit() or c.isalpha()):
                    raise AttributeError(f"{c} is not a proper character for a number class")
        return number_class

    @staticmethod
    def verify_format_number_item(number_item, organization):
        Part._expect_length(number_item, organization.number_item_len, 'digits', 'number item')
        if number_item is not None:
            for c in number_item:
                if not c.isdigit():
                    raise AttributeError(f"{c} is not a proper character for a number item")
        return number_item

    @staticmethod
    def verify_format_number_variation(number_variation, organization):
        Part._expect_length(number_variation, organization.number_variation_len, 'characters', 'number variation')
        if number_variation is not None:
            for c in number_variation:
                if not c.isalnum():
                    raise AttributeError(f"{c} is not a proper character for a number variation. Must be alphanumeric.")
        return number_variation

    @staticmethod
    def parse_part_number(part_number, organization):
        if part_number is None:
            raise AttributeError("Cannot parse empty part number")
        if organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
            try:
                (number_class, number_item, number_variation) = Part.parse_partial_part_number(part_number, organization)
            except IndexError:
                raise AttributeError("Invalid part number. Does not match organization preferences.")

            if number_class is None:
                raise AttributeError("Missing part number part class")
            if number_item is None:
                raise AttributeError("Missing part number item number")
            if number_variation is None and organization.number_class_code_len != 0 and organization.number_variation_len > 0:
                raise AttributeError("Missing part number part item variation")

            return number_class, number_item, number_variation
        else:
            return None, part_number, None

    @staticmethod
    def parse_partial_part_number(part_number, organization, validate=True):
        if organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
            elements = part_number.split('-')

            number_class = elements[0] if len(elements) >= 1 else None
            number_item = elements[1] if len(elements) >= 2 else None
            number_variation = elements[2] if len(elements) >= 3 else None

            if validate:
                if len(elements) >= 2:
                    number_class = Part.verify_format_number_class(elements[0], organization)
                    number_item = Part.verify_format_number_item(elements[1], organization)
                if len(elements) >= 3:
                    number_variation = Part.verify_format_number_variation(elements[2], organization)

            return number_class, number_item, number_variation
        else:
            return None, part_number, None

    @classmethod
    def from_part_number(cls, part_number, organization):
        (number_class, number_item, number_variation) = Part.parse_part_number(part_number, organization)
        if organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
            return Part.objects.get(
                number_class__code=number_class,
                number_class__organization=organization,
                number_item=number_item,
                number_variation=number_variation,
                organization=organization
            )
        return Part.objects.get(
            number_item=number_item,
            organization=organization
        )

    @classmethod
    def from_manufacturer_part_number(cls, manufacturer_part_number, organization):
        part = Part.objects.filter(
            primary_manufacturer_part__manufacturer_part_number=manufacturer_part_number,
            organization=organization
        )
        if len(part) == 1:
            return part[0]
        elif len(part) == 0:
            return None
        else:
            raise ValueError('Too many objects found')

    def description(self):
        return self.latest().description if self.latest() is not None else ''

    def latest(self):
        return self.revisions().order_by('-id').first()

    def revisions(self):
        return PartRevision.objects.filter(part=self)

    def seller_parts(self, exclude_primary=False):
        manufacturer_parts = ManufacturerPart.objects.filter(part=self)
        q = SellerPart.objects.filter(manufacturer_part__in=manufacturer_parts).order_by('seller', 'minimum_order_quantity')\
            .select_related('manufacturer_part').select_related('manufacturer_part__manufacturer').select_related('seller')
        if exclude_primary and self.primary_manufacturer_part is not None and self.primary_manufacturer_part.optimal_seller():
            return q.exclude(id=self.primary_manufacturer_part.optimal_seller().id)
        return q

    def manufacturer_parts(self, exclude_primary=False):
        q = ManufacturerPart.objects.filter(part=self).select_related('manufacturer')
        if exclude_primary and self.primary_manufacturer_part is not None and self.primary_manufacturer_part.id is not None:
            return q.exclude(id=self.primary_manufacturer_part.id)
        return q

    def where_used(self):
        revisions = PartRevision.objects.filter(part=self)
        used_in_subparts = Subpart.objects.filter(part_revision__in=revisions)
        used_in_assembly_ids = AssemblySubparts.objects.filter(subpart__in=used_in_subparts).values_list('assembly', flat=True)
        used_in_prs = PartRevision.objects.filter(assembly__in=used_in_assembly_ids)
        return used_in_prs

    def where_used_full(self):
        def where_used_given_part(used_in_parts, part):
            where_used = part.where_used()
            used_in_parts.update(where_used)
            for p in where_used:
                where_used_given_part(used_in_parts, p)
            return used_in_parts

        used_in_parts = set()
        where_used_given_part(used_in_parts, self)
        return list(used_in_parts)

    def indented(self, part_revision=None):
        if part_revision is None:
            return self.latest().indented() if self.latest() is not None else None
        else:
            return part_revision.indented()

    def optimal_seller(self, quantity=None):
        if not quantity:
            qty_cache_key = str(self.id) + '_qty'
            quantity = int(cache.get(qty_cache_key, 100))

        manufacturer_parts = ManufacturerPart.objects.filter(part=self)
        sellerparts = SellerPart.objects.filter(manufacturer_part__in=manufacturer_parts)
        # sellerparts = SellerPart.objects.filter(manufacturer_part__part=self)
        return SellerPart.optimal(sellerparts, int(quantity))

    def assign_part_number(self):
        if self.number_item is None or self.number_item == '':
            last_number_item = Part.objects.filter(
                number_class=self.number_class,
                organization=self.organization
            ).order_by('number_item').last()
            if not last_number_item:
                self.number_item = str(1).zfill(self.organization.number_item_len)
            else:
                next_num = int(last_number_item.number_item) + 1
                self.number_item = str(next_num).zfill(self.organization.number_item_len)
        if (self.number_variation is None or self.number_variation == '') and self.organization.number_variation_len > 0:
            last_number_variation = Part.objects.filter(
                number_class=self.number_class,
                number_item=self.number_item
            ).order_by('number_variation').last()

            if not last_number_variation:
                self.number_variation = '0'.zfill(self.organization.number_variation_len)
            else:
                try:
                    next_var = int(last_number_variation.number_variation) + 1
                    self.number_variation = str(next_var).zfill(self.organization.number_variation_len)
                except ValueError:
                    self.number_variation = f"{increment_str(last_number_variation.number_variation)}"

    def save(self, *args, **kwargs):
        if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
            self.assign_part_number()
        super(Part, self).save()

    def verbose_str(self):
        return f'{self.full_part_number()} ┆ {self.description()}'

    def __str__(self):
        return u'%s' % (self.full_part_number())


class QuantityOfMeasure(OrganizationOptionalModel):
    """
    Defines the physical dimension (e.g., Length, Voltage, Mass).
    Acts as the 'bucket' for compatible units.
    """
    name = models.CharField(max_length=64, help_text="e.g. Voltage")

    def get_base_unit(self):
        return self.units.filter(base_multiplier=1.0).first()

    class Meta:
        unique_together = (('organization', 'name',),)

    def __str__(self):
        return self.name


class UnitDefinition(OrganizationOptionalModel):
    """
    Defines valid units.
    """
    name = models.CharField(max_length=64)  # e.g. Millivolt
    symbol = models.CharField(max_length=16)  # e.g. mV
    quantity_of_measure = models.ForeignKey(QuantityOfMeasure, on_delete=models.CASCADE, related_name='units')
    base_multiplier = models.DecimalField(default=Decimal('1.0'), max_digits=40, decimal_places=20)

    class Meta:
        unique_together = (('organization', 'quantity_of_measure', 'symbol'),)
        ordering = ['base_multiplier']

    def __str__(self):
        return f"{self.symbol}"


class PartRevisionPropertyDefinition(OrganizationOptionalModel):
    code = models.CharField(max_length=64, blank=True)  # The internal slug (e.g., max_operating_temp).
    name = models.CharField(max_length=64)  # The user-friendly text displayed in the UI
    type = models.CharField(max_length=1, choices=PART_REVISION_PROPERTY_TYPES)
    required = models.BooleanField(default=False)
    quantity_of_measure = models.ForeignKey(QuantityOfMeasure, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('organization', 'code',)

    @property
    def form_field_name(self):
        return f'property_{self.code}'

    @property
    def form_unit_field_name(self):
        return f'{self.form_field_name}_unit'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = slugify(self.name)

        super(PartRevisionPropertyDefinition, self).save(*args, **kwargs)


# Below are attributes of a part that can be changed, but it's important to trace the change over time
class PartRevision(models.Model):
    part = models.ForeignKey(Part, on_delete=models.CASCADE, db_index=True)
    timestamp = models.DateTimeField(default=timezone.now)
    configuration = models.CharField(max_length=1, choices=CONFIGURATION_TYPES, default='W')
    revision = models.CharField(max_length=4, db_index=True, default='1')
    assembly = models.ForeignKey('Assembly', default=None, null=True, on_delete=models.CASCADE, db_index=True)
    displayable_synopsis = models.CharField(editable=False, default="", null=True, blank=True, max_length=255, db_index=True)
    searchable_synopsis = models.CharField(editable=False, default="", null=True, blank=True, max_length=255, db_index=True)
    description = models.CharField(max_length=255, default="", null=True, blank=True)

    class Meta:
        unique_together = (('part', 'revision'),)
        ordering = ['part']

    def generate_synopsis(self, make_searchable=False):
        def verbosify(val, units=None, pre=None, pre_whitespace=True, post=None, post_whitespace=True):
            elaborated = ""
            if val is not None and val != '':
                try:
                    elaborated = strip_trailing_zeros(str(val))
                    if units is not None and units != '': elaborated += units
                    if pre is not None and pre != '':
                        elaborated = pre + (' ' if pre_whitespace else '') + elaborated
                    if post is not None and post != '': elaborated += (' ' if post_whitespace else '') + post
                    elaborated = elaborated + ' '
                except ValueError:
                    pass
            return elaborated

        s = ""
        s += verbosify(self.description)

        # TODO: We no longer order these, in the future we will add a template / inheritance type pattern like
        #   Capacitor: {Capacitance} {Units}, {Voltage}, {Package}
        if self.id:
            for prop in self.properties.all().select_related('property_definition', 'unit_definition'):
                val = prop.value_raw
                units = prop.unit_definition.symbol if prop.unit_definition else None
                s += verbosify(val, units=units)

        return s[:255]

    def synopsis(self, return_displayable=True):
        return self.displayable_synopsis if return_displayable else self.searchable_synopsis

    def update_synopsis(self):
        self.searchable_synopsis = self.generate_synopsis(True)
        self.displayable_synopsis = self.generate_synopsis(False)
        # Use update() to avoid triggering save() again and potentially recursion
        PartRevision.objects.filter(id=self.id).update(
            searchable_synopsis=self.searchable_synopsis,
            displayable_synopsis=self.displayable_synopsis
        )

    def save(self, *args, **kwargs):
        if self.assembly is None:
            self.assembly = Assembly.objects.create()

        self.searchable_synopsis = self.generate_synopsis(True)
        self.displayable_synopsis = self.generate_synopsis(False)

        super(PartRevision, self).save(*args, **kwargs)

    def get_field_value(self, field_name):
        if hasattr(self, field_name):
            return getattr(self, field_name)

        is_unit = field_name.endswith('_units')
        prop_name = field_name[:-6] if is_unit else field_name

        for prop in self.properties.all():
            if prop.property_definition.name == prop_name:
                if is_unit:
                    return prop.unit_definition.symbol if prop.unit_definition else ''
                else:
                    return prop.value_raw
        return None

    def indented(self, top_level_quantity=100):
        def indented_given_bom(bom, part_revision, parent_id=None, parent=None, qty=1, parent_qty=1, indent_level=0, subpart=None, reference='', do_not_load=False):
            bom_item_id = (parent_id or '') + (str(part_revision.id) + '-dnl' if do_not_load else str(part_revision.id))
            extended_quantity = parent_qty * qty
            total_extended_quantity = top_level_quantity * extended_quantity

            try:
                seller_part = part_revision.part.optimal_seller(quantity=total_extended_quantity)
            except AttributeError:
                seller_part = None

            bom.append_item_and_update(PartIndentedBomItem(
                bom_id=bom_item_id,
                part=part_revision.part,
                part_revision=part_revision,
                do_not_load=do_not_load,
                references=reference,
                quantity=qty,
                extended_quantity=extended_quantity,
                parent_quantity=parent_qty,  # Do we need this?
                indent_level=indent_level,
                parent_id=parent_id,
                subpart=subpart,
                seller_part=seller_part,
            ))

            indent_level = indent_level + 1
            if part_revision is None or part_revision.assembly is None or part_revision.assembly.subparts.count() == 0:
                return
            else:
                parent_qty *= qty
                # TODO: Cache Me!
                for sp in part_revision.assembly.subparts.all():
                    qty = sp.count
                    reference = sp.reference
                    indented_given_bom(bom, sp.part_revision, parent_id=bom_item_id, parent=part_revision, qty=qty, parent_qty=parent_qty,
                                       indent_level=indent_level, subpart=sp, reference=reference, do_not_load=sp.do_not_load)

        bom = PartBom(part_revision=self, quantity=top_level_quantity)
        indented_given_bom(bom, self)

        return bom

    def flat(self, top_level_quantity=100, sort=False):
        def flat_given_bom(bom, part_revision, parent=None, qty=1, parent_qty=1, subpart=None, reference=''):
            extended_quantity = parent_qty * qty
            total_extended_quantity = top_level_quantity * extended_quantity

            try:
                seller_part = part_revision.part.optimal_seller(quantity=total_extended_quantity)
            except AttributeError:
                seller_part = None

            try:
                do_not_load = subpart.do_not_load
            except AttributeError:
                do_not_load = False

            bom_item_id = str(part_revision.id) + '-dnl' if do_not_load else str(part_revision.id)
            bom.append_item_and_update(PartBomItem(
                bom_id=bom_item_id,
                part=part_revision.part,
                part_revision=part_revision,
                do_not_load=do_not_load,
                references=reference,
                quantity=qty,
                extended_quantity=extended_quantity,
                seller_part=seller_part,
            ))

            if part_revision is None or part_revision.assembly is None or part_revision.assembly.subparts.count() == 0:
                return
            else:
                parent_qty *= qty
                for sp in part_revision.assembly.subparts.all():
                    qty = sp.count
                    reference = sp.reference
                    flat_given_bom(bom, sp.part_revision, parent=part_revision, qty=qty, parent_qty=parent_qty, subpart=sp, reference=reference)

        flat_bom = PartBom(part_revision=self, quantity=top_level_quantity)
        flat_given_bom(flat_bom, self)

        # Sort by references, if no references then use part number.
        # Note that need to convert part number to a list so can be compared with the 
        # list-ified string returned by prep_for_sorting_nicely.
        def sort_by_references(p):
            return prep_for_sorting_nicely(p.references) if p.references else p.__str__().split()
        if sort:
            flat_bom.parts = sorted(flat_bom.parts.values(), key=sort_by_references)
        return flat_bom

    def where_used(self):
        # Where is a part_revision used???
        # it gets used by being a subpart to an assembly of a part_revision
        # so we can look up subparts, then their assemblys, then their partrevisions
        used_in_subparts = Subpart.objects.filter(part_revision=self)
        used_in_assembly_ids = AssemblySubparts.objects.filter(subpart__in=used_in_subparts).values_list('assembly', flat=True)
        used_in_pr = PartRevision.objects.filter(assembly__in=used_in_assembly_ids).order_by('-revision')
        return used_in_pr

    def where_used_full(self):
        def where_used_given_part(used_in_parts, part):
            where_used = part.where_used()
            used_in_parts.update(where_used)
            for p in where_used:
                where_used_given_part(used_in_parts, p)
            return used_in_parts

        used_in_parts = set()
        where_used_given_part(used_in_parts, self)
        return list(used_in_parts)

    def next_revision(self):
        try:
            return int(self.revision) + 1
        except ValueError:
            return increment_str(self.revision)

    def __str__(self):
        return u'{}, Rev {}'.format(self.part.full_part_number(), self.revision)


class PartRevisionProperty(models.Model):
    part_revision = models.ForeignKey(PartRevision, on_delete=models.CASCADE, related_name='properties')
    property_definition = models.ForeignKey(PartRevisionPropertyDefinition, on_delete=models.CASCADE)
    value_raw = models.CharField(max_length=255)  # Base unit value, e.g. 0.01 (to describe 10mV)
    unit_definition = models.ForeignKey(UnitDefinition, null=True, blank=True, on_delete=models.SET_NULL)
    value_normalized = models.DecimalField(null=True, blank=True, max_digits=40, decimal_places=20)

    def clean(self):
        super().clean()

        if self.unit_definition and self.property_definition.quantity_of_measure:
            if self.unit_definition.quantity_of_measure != self.property_definition.quantity_of_measure:
                raise ValidationError(
                    f"Unit '{self.unit_definition}' matches {self.unit_definition.quantity_of_measure}, but property requires {self.property_definition.quantity_of_measure}")

        # Validate property is allowed for this part class
        part = self.part_revision.part
        if part.number_class:
            allowed_definitions = part.number_class.property_definitions.all()
            if not allowed_definitions.filter(id=self.property_definition.id).exists():
                raise ValidationError(
                    f"The property '{self.property_definition.name}' is not valid for the Part Class '{part.number_class.name}'."
                )

    def save(self, *args, **kwargs):
        if self.unit_definition and self.value_raw:
            try:
                val = Decimal(str(self.value_raw))
                multiplier = Decimal(str(self.unit_definition.base_multiplier))
                self.value_normalized = val * multiplier
            except (ValueError, InvalidOperation):
                self.value_normalized = None
        else:
            self.value_normalized = None
        super(PartRevisionProperty, self).save(*args, **kwargs)

        if self.part_revision:
            self.part_revision.update_synopsis()

    def __str__(self):
        unit_sym = self.unit_definition.symbol if self.unit_definition else ""
        return f"{self.property_definition.name}: {self.value_raw} {unit_sym}"


class AssemblySubparts(models.Model):
    assembly = models.ForeignKey('Assembly', models.CASCADE)
    subpart = models.ForeignKey('Subpart', models.CASCADE)

    class Meta:
        db_table = 'bom_assembly_subparts'
        unique_together = (('assembly', 'subpart'),)


class Subpart(models.Model):
    part_revision = models.ForeignKey('PartRevision', related_name='assembly_subpart', null=True, on_delete=models.CASCADE)
    count = models.FloatField(default=1, validators=[MinValueValidator(0)])
    reference = models.TextField(default='', blank=True, null=True)
    do_not_load = models.BooleanField(default=False, verbose_name='Do Not Load')

    def save(self, *args, **kwargs):
        # Make sure reference designators are formated as a string with comma-separated fields.
        try:
            reference = stringify_list(listify_string(self.reference))
            self.reference = reference
        except TypeError:
            pass
        super(Subpart, self).save(*args, **kwargs)

    def __str__(self):
        return u'{} {}'.format(self.part_revision, self.count)


class Assembly(models.Model):
    subparts = models.ManyToManyField(Subpart, related_name='assemblies', through='AssemblySubparts')


class ManufacturerPart(models.Model, AsDictModel):
    part = models.ForeignKey(Part, on_delete=models.CASCADE, db_index=True)
    manufacturer_part_number = models.CharField(max_length=128, default='', blank=True)
    manufacturer = models.ForeignKey(Manufacturer, default=None, blank=True, null=True, on_delete=models.CASCADE)
    mouser_disable = models.BooleanField(default=False)
    link = models.URLField(null=True, blank=True)

    class Meta:
        unique_together = [
            'part',
            'manufacturer_part_number',
            'manufacturer']

    def seller_parts(self):
        return SellerPart.objects.filter(manufacturer_part=self).order_by('seller', 'minimum_order_quantity')

    def optimal_seller(self, quantity=None):
        if quantity is None:
            qty_cache_key = str(self.part.id) + '_qty'
            quantity = int(cache.get(qty_cache_key, 100))
        sellerparts = SellerPart.objects.filter(manufacturer_part=self)
        return SellerPart.optimal(sellerparts, quantity)

    def as_dict_for_export(self):
        return {
            'manufacturer_name': self.manufacturer.name if self.manufacturer is not None else '',
            'manufacturer_part_number': self.manufacturer_part_number
        }

    def __str__(self):
        return u'%s' % (self.manufacturer_part_number)


class Seller(OrganizationScopedModel, AsDictModel):
    name = models.CharField(max_length=128, default=None)

    def __str__(self):
        return u'%s' % (self.name)


class SellerPart(models.Model, AsDictModel):
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE)
    seller_part_number = models.CharField(max_length=64, default='', blank=True, null=True)
    manufacturer_part = models.ForeignKey(ManufacturerPart, on_delete=models.CASCADE)
    minimum_order_quantity = models.PositiveIntegerField(default=1)
    minimum_pack_quantity = models.PositiveIntegerField(default=1)
    data_source = models.CharField(max_length=32, default=None, null=True, blank=True)
    # "To comply with certain strict accounting or financial regulations, you may consider using max_digits=19 and decimal_places=4"
    unit_cost = MoneyField(max_digits=19, decimal_places=4, default_currency='USD')
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    nre_cost = MoneyField(max_digits=19, decimal_places=4, default_currency='USD')
    link = models.URLField(null=True, blank=True)
    ncnr = models.BooleanField(default=False)

    def as_dict(self):
        d = super().as_dict()
        d['unit_cost'] = self.unit_cost.amount
        d['nre_cost'] = self.nre_cost.amount
        return d

    def as_dict_for_export(self):
        return {
            'manufacturer_name': self.manufacturer_part.manufacturer.name if self.manufacturer_part.manufacturer is not None else '',
            'manufacturer_part_number': self.manufacturer_part.manufacturer_part_number,
            'seller': self.seller.name,
            'seller_part_number': self.seller_part_number,
            'unit_cost': self.unit_cost,
            'minimum_order_quantity': self.minimum_order_quantity,
            'nre_cost': self.nre_cost
        }

    @staticmethod
    def optimal(sellerparts, quantity):
        seller = None
        for sellerpart in sellerparts:
            if seller is None:
                seller = sellerpart
            else:
                new_quantity = quantity if sellerpart.minimum_order_quantity < quantity else sellerpart.minimum_order_quantity
                new_total_cost = new_quantity * sellerpart.unit_cost
                old_quantity = quantity if seller.minimum_order_quantity < quantity else seller.minimum_order_quantity
                old_total_cost = old_quantity * seller.unit_cost
                if new_total_cost < old_total_cost:
                    seller = sellerpart
        return seller

    def order_quantity(self, extended_quantity):
        order_qty = extended_quantity
        if self.minimum_order_quantity and extended_quantity > self.minimum_order_quantity:
            order_qty = ceil(extended_quantity / float(self.minimum_order_quantity)) * self.minimum_order_quantity
        return order_qty

    def __str__(self):
        return u'%s' % (self.manufacturer_part.part.full_part_number() + ' ' + self.seller.name)


User.add_to_class('bom_profile', _user_meta)
