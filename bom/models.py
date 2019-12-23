from __future__ import unicode_literals

import logging

from django.core.cache import cache
from django.db import models
from django.contrib.auth.models import User, Group
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator
from .utils import increment_str, prep_for_sorting_nicely, listify_string, stringify_list, strip_trailing_zeros
from .validators import alphanumeric, numeric, validate_pct

from social_django.models import UserSocialAuth

logger = logging.getLogger(__name__)


class Organization(models.Model):
    name = models.CharField(max_length=255, default=None)
    subscription = models.CharField(max_length=1, choices=(('F', 'Free'), ('P', 'Pro'),))
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    number_item_len = models.PositiveIntegerField(default=3, validators=[MinValueValidator(3), MaxValueValidator(10)])
    google_drive_parent = models.CharField(max_length=128, blank=True, default=None, null=True)

    def number_ns(self):
        return "N" * self.number_item_len

    def __str__(self):
        return u'%s' % self.name


class UserMeta(models.Model):
    user = models.OneToOneField(User, db_index=True, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, blank=True, null=True, on_delete=models.PROTECT)
    role = models.CharField(max_length=1, choices=(('A', 'Admin'), ('V', 'Viewer'),))

    def get_or_create_organization(self):
        if self.organization is None:
            if self.user.first_name == '' and self.user.last_name == '':
                org_name = self.user.username
            else:
                org_name = self.user.first_name + ' ' + self.user.last_name

            organization, created = Organization.objects.get_or_create(owner=self.user, defaults={'name': org_name, 'subscription': 'F'})

            self.organization = organization
            self.role = 'A'
            self.save()
        return self.organization

    def google_authenticated(self):
        try:
            self.user.social_auth.get(provider='google-oauth2')
            return True
        except UserSocialAuth.DoesNotExist:
            return False

    def _user_meta(self, organization=None):
        return UserMeta.objects.get_or_create(
            user=self, defaults={
                'organization': organization})[0]

    User.add_to_class('bom_profile', _user_meta)


class PartClass(models.Model):
    CODE_LEN = 3

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, db_index=True)
    code = models.CharField(max_length=CODE_LEN)
    name = models.CharField(max_length=255, default=None)
    comment = models.CharField(max_length=255, default=None, blank=True)

    class Meta:
        unique_together = ['code', 'name', 'organization', ]
        ordering = ['code']
        index_together = ['organization', 'code', ]

    def __str__(self):
        return u'%s' % (self.code + ': ' + self.name)


class Manufacturer(models.Model):
    name = models.CharField(max_length=128, default=None)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, db_index=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return u'%s' % self.name


# Part contains the root information for a component. Parts have attributes that can be changed over time
# (see PartRevision). Part numbers can be changed over time, but these cannot be tracked, as it is not a practice
# that should be done often.
class Part(models.Model):
    NUMBER_ITEM_MIN_LEN = 3
    NUMBER_ITEM_MAX_LEN = 10
    NUMBER_VARIATION_LEN = 2

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, db_index=True)
    number_class = models.ForeignKey(PartClass, default=None, related_name='number_class', on_delete=models.PROTECT, db_index=True)
    number_item = models.CharField(max_length=10, default=None, blank=True, validators=[numeric])
    number_variation = models.CharField(max_length=2, default=None, blank=True, validators=[alphanumeric])
    primary_manufacturer_part = models.ForeignKey('ManufacturerPart', default=None, null=True, blank=True,
                                                  on_delete=models.SET_NULL, related_name='primary_manufacturer_part')
    google_drive_parent = models.CharField(max_length=128, blank=True, default=None, null=True)

    class Meta():
        unique_together = ['number_class', 'number_item', 'number_variation', 'organization', ]
        index_together = ['organization', 'number_class']

    def full_part_number(self):
        return "{0}-{1}-{2}".format(self.number_class.code, self.number_item, self.number_variation)

    @staticmethod
    def parse_part_number(part_number, number_item_len):
        if not part_number is None:
            for c in part_number:
                if not c.isdigit() and c != '-':
                    raise AttributeError("{} is not a proper character for a part number string".format(c))
        else:
            raise AttributeError("Cannot parse empty part number".format(part_number))

        elements = part_number.split('-')
        if len(elements) != 3: raise AttributeError("Ill-formed part number")
        number_class = elements[0]
        if len(number_class) != PartClass.CODE_LEN or not number_class.isdigit(): raise AttributeError("Expect " + str(PartClass.CODE_LEN) + " digits for part class")
        number_item = elements[1]
        if len(number_item) != number_item_len or not number_item.isdigit(): raise AttributeError("Expect " + str(number_item_len) + " digits for item number")
        number_variation = elements[2]
        if len(number_variation) != Part.NUMBER_VARIATION_LEN: raise AttributeError("Expect " + str(Part.NUMBER_VARIATION_LEN) + " digits for number variation")
        return number_class, number_item, number_variation

    @staticmethod
    def parse_partial_part_number(part_number, number_item_len):
        if part_number is not None:
            for c in part_number:
                if not c.isdigit() and c != '-':
                    raise AttributeError("{} is not a proper character for a part number string".format(c))
        else:
            return None, None, None

        elements = part_number.split('-')
        number_class = None
        number_item = None
        number_variation = None

        def verify_format_number_class(number_class):
            if len(number_class) != PartClass.CODE_LEN or not number_class.isdigit():
                raise AttributeError("Expect " + str(PartClass.CODE_LEN) + " digits for part class")
            else:
                return number_class

        def verify_format_number_item(number_item):
            if len(number_item) != number_item_len or not number_item.isdigit():
                raise AttributeError("Expect " + number_item_len + " digits for item number")
            else:
                return number_item

        def verify_format_number_variation(number_variation):
            if len(number_variation) != Part.NUMBER_VARIATION_LEN:
                raise AttributeError("Expect " + str(Part.NUMBER_VARIATION_LEN) + " digits for number variation")
            else:
                return number_variation

        if len(elements) == 3:
            number_class = verify_format_number_class(elements[0])
            number_item = verify_format_number_item(elements[1])
            number_variation = verify_format_number_variation(elements[2])
        elif len(elements) == 2:
            number_class = verify_format_number_class(elements[0])
            number_item = verify_format_number_item(elements[1])

        return number_class, number_item, number_variation

    def description(self):
        return self.latest().description if self.latest() is not None else ''

    def latest(self):
        return self.revisions().order_by('-id').first()

    def revisions(self):
        return PartRevision.objects.filter(part=self)

    def seller_parts(self):
        manufacturer_parts = ManufacturerPart.objects.filter(part=self)
        return SellerPart.objects.filter(manufacturer_part__in=manufacturer_parts) \
            .order_by('seller', 'minimum_order_quantity')

    def manufacturer_parts(self):
        manufacturer_parts = ManufacturerPart.objects.filter(part=self)
        return manufacturer_parts

    def where_used(self):
        revisions = PartRevision.objects.filter(part=self)
        used_in_subparts = Subpart.objects.filter(part_revision__in=revisions)
        used_in_assembly_ids = AssemblySubparts.objects.filter(subpart__in=used_in_subparts).values_list('assembly',
                                                                                                         flat=True)
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
        if quantity is None:
            qty_cache_key = str(self.id) + '_qty'
            quantity = int(cache.get(qty_cache_key, 100))

        manufacturer_parts = ManufacturerPart.objects.filter(part=self)
        sellerparts = SellerPart.objects.filter(manufacturer_part__in=manufacturer_parts)
        return SellerPart.optimal(sellerparts, quantity)

    def assign_part_number(self):
        if self.number_item is None or self.number_item == '':
            last_number_item = Part.objects.filter(
                number_class=self.number_class,
                organization=self.organization).order_by('number_item').last()
            if not last_number_item:
                self.number_item = '1'
                for i in range(self.organization.number_item_len - 1):
                    self.number_item = '0' + self.number_item
            else:
                FORMATS = {
                    1: '{0:0=1d}', 2: '{0:0=2d}', 3: '{0:0=3d}', 4: '{0:0=4d}', 5: '{0:0=5d}',
                    6: '{0:0=6d}', 7: '{0:0=7d}', 8: '{0:0=8d}', 9: '{0:0=9d}', 10: '{0:0=10d}'
                }
                self.number_item = FORMATS[self.organization.number_item_len].format(
                    int(last_number_item.number_item) + 1)
        if self.number_variation is None or self.number_variation == '':
            last_number_variation = Part.objects.all().filter(
                number_class=self.number_class,
                number_item=self.number_item).order_by('number_variation').last()

            if not last_number_variation:
                self.number_variation = '01'
            else:
                try:
                    self.number_variation = "{0:0=2d}".format(int(last_number_variation.number_variation) + 1)
                except ValueError as e:
                    self.number_variation = "{}".format(increment_str(last_number_variation.number_variation))

    def save(self, *args, **kwargs):
        no_part_revision = kwargs.pop('no_part_revision', False)
        self.assign_part_number()
        super(Part, self).save(*args, **kwargs)

    def __str__(self):
        return u'%s' % (self.full_part_number())


# Below are attributes of a part that can be changed, but it's important to trace the change over time
class PartRevision(models.Model):
    part = models.ForeignKey(Part, on_delete=models.CASCADE, db_index=True)
    timestamp = models.DateTimeField(default=timezone.now)
    configuration = models.CharField(max_length=1, choices=(('R', 'Released'), ('W', 'Working'),), default='W')
    revision = models.CharField(max_length=4, db_index=True, default='1')
    assembly = models.ForeignKey('Assembly', default=None, null=True, on_delete=models.PROTECT, db_index=True)
    displayable_synopsis = models.CharField(editable=False, default="", null=True, blank=True, max_length=255, db_index=True)
    searchable_synopsis = models.CharField(editable=False, default="", null=True, blank=True, max_length=255, db_index=True)

    class Meta:
        unique_together = (('part', 'revision'),)
        ordering = ['part']

    NO_CHOICE = ('', '-----')

    # Part Revision Specification Properties:

    description = models.CharField(max_length=255, default="", null=True, blank=True)

    # By convention for IndaBOM, for part revision properties below, if a property value has
    # an associated units of measure, and if the property value field name is 'vvv' then the
    # associated units of measure field name must be 'vvv_units'.

    VALUE_UNITS = (
        NO_CHOICE,
        ('Ohms', '\u03A9'),
        ('mOhms', 'm\u03A9'),
        ('kOhms', 'k\u03A9'),
        ('F', 'F'),
        ('pF', 'pF'),
        ('nF', 'nF'),
        ('uF', '\u03BCF'),
        ('V', 'V'),
        ('uV', '\u03BCV'),
        ('mV', 'mV'),
        ('A', 'A'),
        ('uA', '\u03BCA'),
        ('mA', 'mA'),
        ('C', '\u00B0C'),
        ('F', '\u00B0F'),
        ('Other', 'Other'),
    )

    value_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=VALUE_UNITS)
    value = models.CharField(max_length=255, default=None, null=True, blank=True)

    attribute = models.CharField(max_length=255, default=None, null=True, blank=True)

    pin_count = models.DecimalField(max_digits=3, decimal_places=0, default=None, null=True, blank=True)

    tolerance = models.CharField(max_length=6, validators=[validate_pct], default=None, null=True, blank=True)

    PACKAGE_TYPES = (
        NO_CHOICE,
        ('0201 smd', '0201 smd'),
        ('0402 smd', '0402 smd'),
        ('0603 smd', '0603 smd'),
        ('0805 smd', '0805 smd'),
        ('1206 smd', '1206 smd'),
        ('1210 smd', '1210 smd'),
        ('1812 smd', '1812 smd'),
        ('2010 smd', '2010 smd'),
        ('2512 smd', '2512 smd'),
        ('1/8 radial', '1/8 radial'),
        ('1/4 radial', '1/4 radial'),
        ('1/2 radial', '1/2 radial'),
        ('Size A', 'Size A'),
        ('Size B', 'Size B'),
        ('Size C', 'Size C'),
        ('Size D', 'Size D'),
        ('Size E', 'Size E'),
        ('SOT-23', 'SOT-23'),
        ('SOT-223', 'SOT-233'),
        ('DIL', 'DIL'),
        ('SOP', 'SOP'),
        ('SOIC', 'SOIC'),
        ('QFN', 'QFN'),
        ('QFP', 'QFP'),
        ('QFT', 'QFT'),
        ('PLCC', 'PLCC'),
        ('VGA', 'VGA'),
        ('Other', 'Other'),
    )

    package = models.CharField(max_length=16, default=None, null=True, blank=True, choices=PACKAGE_TYPES)

    material = models.CharField(max_length=32, default=None, null=True, blank=True)
    finish = models.CharField(max_length=32, default=None, null=True, blank=True)
    color = models.CharField(max_length=32, default=None, null=True, blank=True)

    DISTANCE_UNITS = (
        NO_CHOICE,
        ('mil', 'mil'),
        ('in', 'in'),
        ('ft', 'ft'),
        ('yd', 'yd'),
        ('km', 'km'),
        ('m', 'm'),
        ('cm', 'cm'),
        ('um', '\u03BCm'),
        ('nm', 'nm'),
        ('Other', 'Other'),
    )

    length_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=DISTANCE_UNITS)
    length = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    width_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=DISTANCE_UNITS)
    width = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    height_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=DISTANCE_UNITS)
    height = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    WEIGHT_UNITS = (
        NO_CHOICE,
        ('mg', 'mg'),
        ('g', 'g'),
        ('kg', 'kg'),
        ('oz', 'oz'),
        ('lb', 'lb'),
        ('Other', 'Other'),
    )

    weight_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=WEIGHT_UNITS)
    weight = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    TEMPERATURE_UNITS = (
        NO_CHOICE,
        ('C', '\u00B0C'),
        ('F', '\u00B0F'),
        ('Other', 'Other'),
    )

    temperature_rating_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=TEMPERATURE_UNITS)
    temperature_rating = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    WAVELENGTH_UNITS = (
        NO_CHOICE,
        ('km', 'km'),
        ('m', 'm'),
        ('cm', 'cm'),
        ('um', '\u03BCm'),
        ('nm', 'nm'),
        ('A', '\u212B'),
        ('Other', 'Other'),
    )

    wavelength_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=WAVELENGTH_UNITS)
    wavelength = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    FREQUENCY_UNITS = (
        NO_CHOICE,
        ('Hz', 'Hz'),
        ('kHz', 'kHz'),
        ('MHz', 'MHz'),
        ('GHz', 'GHz'),
        ('Other', 'Other'),
    )

    frequency_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=FREQUENCY_UNITS)
    frequency = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    MEMORY_UNITS = (
        NO_CHOICE,
        ('KB', 'KB'),
        ('MB', 'MB'),
        ('GB', 'GB'),
        ('TB', 'TB'),
        ('Other', 'Other'),
    )

    memory_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=MEMORY_UNITS)
    memory = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    INTERFACE_TYPES = (
        NO_CHOICE,
        ('I2C', 'I2C'),
        ('SPI', 'SPI'),
        ('CAN', 'CAN'),
        ('One-Wire', '1-Wire'),
        ('RS485', 'RS-485'),
        ('RS232', 'RS-232'),
        ('WiFi', 'Wi-Fi'),
        ('4G', '4G'),
        ('BT', 'Bluetooth'),
        ('BTLE', 'Bluetooth LE'),
        ('Z_Wave', 'Z-Wave'),
        ('Zigbee', 'Zigbee'),
        ('LAN', 'LAN'),
        ('USB', 'USB'),
        ('HDMI', 'HDMI'),
        ('Other', 'Other'),
    )

    interface = models.CharField(max_length=12, default=None, null=True, blank=True, choices=INTERFACE_TYPES)

    POWER_UNITS = (
        NO_CHOICE,
        ('W', 'W'),
        ('uW', '\u03BCW'),
        ('mW', 'mW'),
        ('kW', 'kW'),
        ('MW', 'MW'),
        ('Other', 'Other'),
    )

    power_rating_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=POWER_UNITS)
    power_rating = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    VOLTAGE_UNITS = (
        NO_CHOICE,
        ('V', 'V'),
        ('uV', '\u03BCV'),
        ('mV', 'mV'),
        ('kV', 'kV'),
        ('MV', 'MV'),
        ('Other', 'Other'),
    )

    supply_voltage_units = models.CharField(max_length=5, default=None, null=True, blank=True, choices=VOLTAGE_UNITS)
    supply_voltage = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    voltage_rating_units = models.CharField(max_length=2, default=None, null=True, blank=True, choices=VOLTAGE_UNITS)
    voltage_rating = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    CURRENT_UNITS = (
        NO_CHOICE,
        ('A', 'A'),
        ('uA', '\u03BCV'),
        ('mA', 'mA'),
        ('kA', 'kA'),
        ('MA', 'MA'),
        ('Other', 'Other'),
    )

    current_rating_units = models.CharField(max_length=2, default=None, null=True, blank=True, choices=CURRENT_UNITS)
    current_rating = models.DecimalField(max_digits=7, decimal_places=3, default=None, null=True, blank=True)

    def generate_synopsis(self, make_searchable=False):
        def verbosify(val, units=None, pre=None, pre_whitespace=True, post=None, post_whitespace=True):
            elaborated = ""
            if val is not None and val is not '':
                try:
                    elaborated = strip_trailing_zeros(str(val))
                    if units is not None and units is not '': elaborated += units
                    if pre is not None and pre is not '':
                        elaborated = pre + (' ' if pre_whitespace else '') + elaborated
                    if post is not None and post is not '': elaborated += (' ' if post_whitespace else '') + post
                    elaborated = elaborated + ' '
                except ValueError:
                    pass
            return elaborated

        s = ""
        s += verbosify(self.value, units=self.value_units if make_searchable else self.get_value_units_display())
        s += verbosify(self.description)
        tolerance = self.tolerance.replace('%', '') if self.tolerance else ''
        s += verbosify(tolerance, post='%', post_whitespace=False)
        s += verbosify(self.attribute)
        s += verbosify(self.package if make_searchable else self.get_package_display())
        s += verbosify(self.pin_count, post='pins')
        s += verbosify(self.frequency, units=self.frequency_units if make_searchable else self.get_frequency_units_display())
        s += verbosify(self.wavelength, units=self.wavelength_units if make_searchable else self.get_wavelength_units_display())
        s += verbosify(self.memory, units=self.memory_units if make_searchable else self.get_memory_units_display())
        s += verbosify(self.interface if make_searchable else self.get_interface_display())
        s += verbosify(self.supply_voltage, units=self.supply_voltage_units if make_searchable else self.get_supply_voltage_units_display(), post='supply')
        s += verbosify(self.temperature_rating, units=self.temperature_rating_units if make_searchable else self.get_temperature_rating_units_display(), post='rating')
        s += verbosify(self.power_rating, units=self.power_rating_units if make_searchable else self.get_power_rating_units_display(), post='rating')
        s += verbosify(self.voltage_rating, units=self.voltage_rating_units if make_searchable else self.get_voltage_rating_units_display(), post='rating')
        s += verbosify(self.current_rating, units=self.current_rating_units if make_searchable else self.get_current_rating_units_display(), post='rating')
        s += verbosify(self.material)
        s += verbosify(self.color)
        s += verbosify(self.finish)
        s += verbosify(self.length, units=self.length_units if make_searchable else self.get_length_units_display(), pre='L')
        s += verbosify(self.width, units=self.width_units if make_searchable else self.get_width_units_display(), pre='W')
        s += verbosify(self.height, units=self.height_units if make_searchable else self.get_height_units_display(), pre='H')
        s += verbosify(self.weight, units=self.weight_units if make_searchable else self.get_weight_units_display())
        return s

    def synopsis(self, return_displayable=True):
        return self.displayable_synopsis if return_displayable else self.searchable_synopsis

    def save(self, *args, **kwargs):
        if self.tolerance:
            self.tolerance = self.tolerance.replace('%', '')
        if self.assembly is None:
            assy = Assembly.objects.create()
            self.assembly = assy
        if self.id:
            previous_configuration = PartRevision.objects.get(id=self.id).configuration
            if self.configuration != previous_configuration:
                self.timestamp = timezone.now()
        self.searchable_synopsis = self.generate_synopsis(True)
        self.displayable_synopsis = self.generate_synopsis(False)
        super(PartRevision, self).save(*args, **kwargs)

    def indented(self):
        def indented_given_bom(bom, part_revision, parent_id=None, parent=None, qty=1, parent_qty=1, indent_level=0, subpart=None,
                               reference='', do_not_load=False):
            if part_revision is None:  # hopefully this never happens
                logger.warning("Indented bom part_revision is None, this shouldn't happen, parent part_revision id: {}".format(parent.id))
                return

            bom_id = str(part_revision.id) + '-dnl' if do_not_load else str(part_revision.id)

            bom.append({
                'id': bom_id,
                'part': part_revision.part,
                'part_revision': part_revision,
                'quantity': qty,
                'parent_quantity': parent_qty,
                'total_quantity': parent_qty * qty,
                'indent_level': indent_level,
                'parent_id': parent_id,
                'subpart': subpart,
                'do_not_load': do_not_load,
                'reference': reference,
            })

            indent_level = indent_level + 1
            if part_revision is None or part_revision.assembly is None or part_revision.assembly.subparts.count() == 0:
                return
            else:
                parent_qty *= qty
                for sp in part_revision.assembly.subparts.all():
                    qty = sp.count
                    reference = sp.reference
                    indented_given_bom(bom, sp.part_revision, parent_id=bom_id, parent=part_revision, qty=qty, parent_qty=parent_qty,
                                       indent_level=indent_level, subpart=sp, reference=reference, do_not_load=sp.do_not_load)

        bom = []
        indented_given_bom(bom, self)

        # For each indent level, sort by reference, if no reference then use part number.
        # Note that need to convert part number to a list so can be compared with the 
        # list-ified string returned by prep_for_sorting_nicely.
        # def sort_by_reference(p):
        #     return prep_for_sorting_nicely(p['reference']) if p['reference'] else p.__str__().split()
        #
        # def sort_by_indent_level(p):
        #     return p['indent_level']
        #
        # bom = sorted(bom, key=sort_by_reference)
        # bom = sorted(bom, key=sort_by_indent_level)
        return bom

    def flat(self):
        def flat_given_bom(bom, part_revision, parent=None, qty=1, parent_qty=1, subpart=None, reference=''):
            if part_revision is None:  # hopefully this never happens
                logger.warning("Flat bom part_revision is None, this shouldn't happen, parent "
                               "part_revision id: {}".format(parent.id))
                return

            if part_revision.id in bom:
                bom[part_revision.id]['quantity'] += parent_qty * qty
                ref = ', ' + reference if reference != '' else ''
                bom[part_revision.id]['references'] += ref
            else:
                bom[part_revision.id] = {
                    'part': part_revision.part,
                    'part_revision': part_revision,
                    'quantity': qty,
                    'references': reference,
                }

            if part_revision is None or part_revision.assembly is None or part_revision.assembly.subparts.count() == 0:
                return
            else:
                parent_qty *= qty
                for sp in part_revision.assembly.subparts.all():
                    qty = sp.count
                    reference = sp.reference
                    flat_given_bom(bom, sp.part_revision, parent=part_revision, qty=qty, parent_qty=parent_qty,
                                   subpart=sp, reference=reference)

        bom = {}
        flat_given_bom(bom, self)

        # Sort by references, if no references then use part number.
        # Note that need to convert part number to a list so can be compared with the 
        # list-ified string returned by prep_for_sorting_nicely.
        def sort_by_references(p):
            return prep_for_sorting_nicely(p['references']) if p['references'] else p.__str__().split()

        bom = sorted(bom.values(), key=sort_by_references)
        return bom

    def where_used(self):
        # Where is a part_revision used???
        # it gets used by being a subpart to an assembly of a part_revision
        # so we can look up subparts, then their assemblys, then their partrevisions
        used_in_subparts = Subpart.objects.filter(part_revision=self)
        used_in_assembly_ids = AssemblySubparts.objects.filter(subpart__in=used_in_subparts).values_list('assembly',
                                                                                                         flat=True)
        used_in_pr = PartRevision.objects.filter(assembly__in=used_in_assembly_ids)
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


class AssemblySubparts(models.Model):
    assembly = models.ForeignKey('Assembly', models.CASCADE)
    subpart = models.ForeignKey('Subpart', models.CASCADE)

    class Meta:
        db_table = 'bom_assembly_subparts'
        unique_together = (('assembly', 'subpart'),)


class Subpart(models.Model):
    part_revision = models.ForeignKey('PartRevision', related_name='assembly_subpart', null=True, on_delete=models.CASCADE)
    count = models.PositiveIntegerField(default=1)
    reference = models.TextField(default='', blank=True, null=True)
    do_not_load = models.BooleanField(default=False, verbose_name='Do Not Load')

    def save(self, *args, **kwargs):
        # Make sure reference designators are formated as a string with comma-separated
        # fields.
        reference = stringify_list(listify_string(self.reference))
        self.reference = reference
        super(Subpart, self).save(*args, **kwargs)

    def __str__(self):
        return u'{} {}'.format(self.part_revision, self.count)


class Assembly(models.Model):
    subparts = models.ManyToManyField(Subpart, related_name='assemblies', through='AssemblySubparts')


class ManufacturerPart(models.Model):
    part = models.ForeignKey(Part, on_delete=models.CASCADE)
    manufacturer_part_number = models.CharField(
        max_length=128, default='', blank=True)
    manufacturer = models.ForeignKey(
        Manufacturer, default=None, blank=True, null=True, on_delete=models.PROTECT)

    class Meta():
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

    def __str__(self):
        return u'%s' % (self.manufacturer_part_number)


class Seller(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    name = models.CharField(max_length=128, default=None)

    def __str__(self):
        return u'%s' % (self.name)


class SellerPart(models.Model):
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE)
    manufacturer_part = models.ForeignKey(ManufacturerPart, on_delete=models.CASCADE)
    minimum_order_quantity = models.PositiveIntegerField(default=1)
    minimum_pack_quantity = models.PositiveIntegerField(default=1)
    data_source = models.CharField(max_length=32, default=None, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=8, decimal_places=4)
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    nre_cost = models.DecimalField(max_digits=8, decimal_places=4)
    ncnr = models.BooleanField(default=False)

    class Meta():
        unique_together = [
            'seller',
            'manufacturer_part',
            'minimum_order_quantity',
            'unit_cost']

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

    def __str__(self):
        return u'%s' % (self.manufacturer_part.part.full_part_number() + ' ' + self.seller.name)
