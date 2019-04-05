from __future__ import unicode_literals

from django.core.cache import cache
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch.dispatcher import receiver
from django.contrib.auth.models import User, Group
from .validators import alphanumeric, numeric
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext as _

from social_django.models import UserSocialAuth


class Organization(models.Model):
    name = models.CharField(max_length=255, default=None)
    subscription = models.CharField(max_length=1, choices=(('F', 'Free'), ('P', 'Pro'),))
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    google_drive_parent = models.CharField(max_length=128, blank=True, default=None, null=True)

    def __str__(self):
        return u'%s' % (self.name)


class UserMeta(models.Model):
    user = models.OneToOneField(User, db_index=True, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, blank=True, null=True, on_delete=models.PROTECT)
    role = models.CharField(max_length=1, choices=(('A', 'Admin'), ('V', 'Viewer'),))

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
    code = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=255, default=None)
    comment = models.CharField(max_length=255, default=None, blank=True)

    def __str__(self):
        return u'%s' % (self.code + ': ' + self.name)


class Manufacturer(models.Model):
    name = models.CharField(max_length=128, default=None)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return u'%s' % (self.name)


# Numbering scheme is hard coded for now, may want to change this to a
# setting depending on a part numbering scheme
# Part contains the root information for a component. Parts have attributes that can be changed over time
# (see PartChangeHistory). Part numbers can be changed over time, but these cannot be tracked, as it is not a practice
# that should be done often.
class Part(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    number_class = models.ForeignKey(PartClass, default=None, related_name='number_class', on_delete=models.PROTECT)
    number_item = models.CharField(max_length=4, default=None, blank=True, validators=[numeric])
    number_variation = models.CharField(max_length=2, default=None, blank=True, validators=[alphanumeric])
    primary_manufacturer_part = models.ForeignKey('ManufacturerPart', default=None, null=True, blank=True,
                                                  on_delete=models.SET_NULL, related_name='primary_manufacturer_part')
    google_drive_parent = models.CharField(max_length=128, blank=True, default=None, null=True)

    class Meta():
        unique_together = ['number_class', 'number_item', 'number_variation', 'organization', ]

    def full_part_number(self):
        return "{0}-{1}-{2}".format(self.number_class.code, self.number_item, self.number_variation)

    def description(self):
        return self.latest().description if self.latest() is not None else ''

    def latest(self):
        return self.revisions().order_by('-timestamp').first()

    def revisions(self):
        return PartChangeHistory.objects.filter(part=self)

    def seller_parts(self):
        manufacturer_parts = ManufacturerPart.objects.filter(part=self)
        return SellerPart.objects.filter(manufacturer_part__in=manufacturer_parts) \
            .order_by('seller', 'minimum_order_quantity')

    def manufacturer_parts(self):
        manufacturer_parts = ManufacturerPart.objects.filter(part=self)
        return manufacturer_parts

    def where_used(self):
        return self.latest().where_used() if self.latest() is not None else None

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

    def indented(self, partchangehistory=None):
        if partchangehistory is None:
            return self.latest().indented() if self.latest() is not None else None
        else:
            return partchangehistory.indented()

    def optimal_seller(self, quantity=None):
        if quantity is None:
            qty_cache_key = str(self.id) + '_qty'
            quantity = int(cache.get(qty_cache_key, 100))

        manufacturer_parts = ManufacturerPart.objects.filter(part=self)
        sellerparts = SellerPart.objects.filter(manufacturer_part__in=manufacturer_parts)
        seller = None
        for sellerpart in sellerparts:
            # TODO: Make this smarter and more readable.
            if (sellerpart.unit_cost is not None and
                    (
                            sellerpart.minimum_order_quantity is not None and sellerpart.minimum_order_quantity <= quantity) and
                    (seller is None or (seller.unit_cost is not None and sellerpart.unit_cost < seller.unit_cost))):
                seller = sellerpart
            elif seller is None:
                seller = sellerpart

        return seller

    def save(self, **kwargs):
        if self.number_item is None or self.number_item == '':
            last_number_item = Part.objects.filter(
                number_class=self.number_class,
                organization=self.organization).order_by('number_item').last()
            if not last_number_item:
                self.number_item = '0001'
            else:
                self.number_item = "{0:0=4d}".format(
                    int(last_number_item.number_item) + 1)
        if self.number_variation is None or self.number_variation == '':
            last_number_variation = Part.objects.all().filter(
                number_class=self.number_class,
                number_item=self.number_item).order_by('number_variation').last()

            def increment_char(c):
                """
                Increment an uppercase character, returning 'A' if 'Z' is given
                """
                return chr(ord(c) + 1) if c != 'Z' else 'A'

            def increment_str(s):
                lpart = s.rstrip('Z')
                num_replacements = len(s) - len(lpart)
                new_s = lpart[:-1] + increment_char(lpart[-1]) if lpart else 'A'
                new_s += 'A' * num_replacements
                return new_s

            if not last_number_variation:
                self.number_variation = '01'
            else:
                try:
                    self.number_variation = "{0:0=2d}".format(int(last_number_variation.number_variation) + 1)
                except ValueError as e:
                    self.number_variation = "{}".format(increment_str(last_number_variation.number_variation))
        super(Part, self).save()

    def __str__(self):
        return u'%s' % (self.full_part_number())


# Below are attributes of a part that can be changed, but it's important to trace the change over time
class PartChangeHistory(models.Model):
    part = models.ForeignKey(Part, on_delete=models.CASCADE, db_index=True)
    timestamp = models.DateTimeField(auto_now=True)
    description = models.CharField(max_length=255, default="")
    revision = models.CharField(max_length=2, db_index=True)
    attribute = models.CharField(max_length=255, default=None, null=True)
    value = models.CharField(max_length=255, default=None, null=True)
    assembly = models.ForeignKey('Assembly', default=None, null=True, on_delete=models.PROTECT, db_index=True)

    def indented(self):
        def indented_given_bom(bom, partchangehistory, parent=None, qty=1, indent_level=0, subpart=None, reference=''):
            bom.append({
                'part': partchangehistory.part,
                'partchangehistory': partchangehistory,
                'quantity': qty,
                'indent_level': indent_level,
                'parent_id': parent.id if parent is not None else None,
                'subpart': subpart,
                'reference': reference,
            })

            indent_level = indent_level + 1
            if partchangehistory.assembly is None or partchangehistory.assembly.subparts.count() == 0:
                return
            else:
                for sp in partchangehistory.assembly.subparts.all():
                    qty = sp.count
                    reference = sp.reference
                    indented_given_bom(bom, sp.part_revision, parent=partchangehistory, qty=qty,
                                       indent_level=indent_level, subpart=sp,
                                       reference=reference)

        bom = []
        cost = 0
        indented_given_bom(bom, self)
        return bom

    def where_used(self):
        # Where is a partchangehistory used???
        # it gets used by being a subpart to an assembly of a partchangehistory
        # so we can look up subparts, then their assemblys, then their partchangehistories
        used_in_subparts = Subpart.objects.filter(part_revision=self)
        used_in_assembly_ids = []
        for sp in used_in_subparts:
            used_in_assembly_ids.extend(sp.assemblies.values_list('id', flat=True))

        used_in_pch = PartChangeHistory.objects.filter(assembly__in=used_in_assembly_ids)
        return used_in_pch

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


    def __str__(self):
        return u'{}, Rev {}'.format(self.part.full_part_number(), self.revision)


class Subpart(models.Model):
    part_revision = models.ForeignKey(PartChangeHistory, related_name='assembly_subpart', null=True,
                                      on_delete=models.CASCADE)
    count = models.IntegerField(default=1)
    reference = models.TextField(default='', blank=True, null=True)

    def __str__(self):
        return u'{} {}'.format(self.part_revision, self.count)


class Assembly(models.Model):
    subparts = models.ManyToManyField(Subpart, related_name='assemblies')


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
        seller = None
        for sellerpart in sellerparts:
            # TODO: Make this smarter and more readable.
            if (sellerpart.unit_cost is not None and
                    (
                            sellerpart.minimum_order_quantity is not None and sellerpart.minimum_order_quantity <= quantity) and
                    (seller is None or (seller.unit_cost is not None and sellerpart.unit_cost < seller.unit_cost))):
                seller = sellerpart
            elif seller is None:
                seller = sellerpart

        return seller

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
    minimum_order_quantity = models.IntegerField(null=True, blank=True)
    minimum_pack_quantity = models.IntegerField(null=True, blank=True)
    data_source = models.CharField(max_length=32, default=None, null=True, blank=True)
    unit_cost = models.DecimalField(
        null=True,
        max_digits=8,
        decimal_places=4,
        blank=True)
    lead_time_days = models.IntegerField(null=True, blank=True)
    nre_cost = models.DecimalField(
        null=True,
        max_digits=8,
        decimal_places=4,
        blank=True)
    ncnr = models.BooleanField(default=False)

    class Meta():
        unique_together = [
            'seller',
            'manufacturer_part',
            'minimum_order_quantity',
            'unit_cost']

    def __str__(self):
        return u'%s' % (self.manufacturer_part.part.full_part_number() + ' ' + self.seller.name)
