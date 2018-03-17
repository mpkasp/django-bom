from __future__ import unicode_literals

from django.db import models
from django.db.models.signals import post_delete
from django.dispatch.dispatcher import receiver
from django.contrib.auth.models import User, Group
from .validators import alphanumeric, numeric
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext as _


class Organization(models.Model):
    name = models.CharField(max_length=255, default=None)
    subscription = models.CharField(
        max_length=1, choices=(
            ('F', 'Free'), ('P', 'Pro'), ))
    owner = models.ForeignKey(User)

    def __unicode__(self):
        return u'%s' % (self.name)


class UserMeta(models.Model):
    user = models.OneToOneField(User, db_index=True)
    organization = models.ForeignKey(Organization, blank=True, null=True)
    role = models.CharField(
        max_length=1, choices=(
            ('A', 'Admin'), ('V', 'Viewer'), ))


def _user_meta(self, organization=None):
    return UserMeta.objects.get_or_create(
        user=self, defaults={
            'organization': organization})[0]


User.add_to_class('bom_profile', _user_meta)


class PartClass(models.Model):
    code = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=255, default=None)
    comment = models.CharField(max_length=255, default=None, blank=True)

    def __unicode__(self):
        return u'%s' % (self.code + ': ' + self.name)


class Manufacturer(models.Model):
    name = models.CharField(max_length=128, default=None)
    organization = models.ForeignKey(Organization)

    class Meta:
        ordering = ['name']

    def __unicode__(self):
        return u'%s' % (self.name)


# Numbering scheme is hard coded for now, may want to change this to a
# setting depending on a part numbering scheme
class Part(models.Model):
    organization = models.ForeignKey(Organization)
    
    number_class = models.ForeignKey(
        PartClass, default=None, related_name='number_class')
    number_item = models.CharField(max_length=4, default=None, blank=True, validators=[numeric])
    number_variation = models.CharField(max_length=2, default=None, blank=True, validators=[alphanumeric])
    
    description = models.CharField(max_length=255, default=None)
    revision = models.CharField(max_length=2)
    manufacturer_part_number = models.CharField(
        max_length=128, default='', blank=True)
    manufacturer = models.ForeignKey(
        Manufacturer, default=None, blank=True, null=True)
    subparts = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=False,
        through='Subpart',
        through_fields=(
            'assembly_part',
            'assembly_subpart'))

    class Meta():
        unique_together = ['number_class', 'number_item', 'number_variation', 'organization', ]

    def full_part_number(self):
        return "{0}-{1}-{2}".format(self.number_class.code,
                                    self.number_item, self.number_variation)

    # def distributor_parts(self):
    #     return SellerPart.objects.filter(
    #         part=self).order_by(
    #         'seller',
    #         'minimum_order_quantity')

    def seller_parts(self):
        return SellerPart.objects.filter(
            part=self).order_by(
            'seller', 'minimum_order_quantity')

    def where_used(self):
        used_in_subparts = Subpart.objects.filter(assembly_subpart=self)
        used_in_parts = [subpart.assembly_part for subpart in used_in_subparts]
        return used_in_parts

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

    def files(self):
        partfiles = PartFile.objects.filter(part=self)
        return partfiles

    def indented(self):
        def indented_given_bom(bom, part, qty=1, indent_level=0, subpart=None):
            bom.append({
                'part': part,
                'quantity': qty,
                'indent_level': indent_level,
                'subpart': subpart,
            })

            indent_level = indent_level + 1
            if(len(part.subparts.all()) == 0):
                return
            else:
                for sp in part.subparts.all():
                    subparts = Subpart.objects.filter(
                        assembly_part=part, assembly_subpart=sp)
                    # since assembly_part and assembly_subpart are not unique together in a Subpart
                    # there is a possibility that there are two (or more) separate Subparts of the
                    # same Part, thus we filter and iterate again
                    for subpart in subparts:
                        qty = subpart.count
                        indented_given_bom(bom, sp, qty, indent_level, subpart)

        bom = []
        cost = 0
        indented_given_bom(bom, self)
        return bom

    def optimal_seller(self, quantity=1000):
        sellerparts = SellerPart.objects.filter(part=self)
        seller = None
        for sellerpart in sellerparts:
            if (sellerpart.minimum_order_quantity <= quantity and (
                    seller is None or
                    sellerpart.unit_cost < seller.unit_cost) and
                    sellerpart.unit_cost is not None):
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
            if not last_number_variation:
                self.number_variation = '01'
            else:
                self.number_variation = "{0:0=2d}".format(
                    int(last_number_variation.number_variation) + 1)
        if self.manufacturer_part_number == '' and self.manufacturer is None and self.organization is not None:
            self.manufacturer_part_number = self.full_part_number()
            self.manufacturer, c = Manufacturer.objects.get_or_create(
                name__iexact=self.organization.name)
        super(Part, self).save()

    def __unicode__(self):
        return u'%s' % (self.full_part_number())


class Subpart(models.Model):
    assembly_part = models.ForeignKey(
        Part, related_name='assembly_part', null=True)
    assembly_subpart = models.ForeignKey(
        Part, related_name='assembly_subpart', null=True)
    count = models.IntegerField(default=1)

    def clean(self):
        unusable_parts = self.assembly_part.where_used()
        if self.assembly_subpart in unusable_parts:
            raise ValidationError(_('Recursive relationship: cannot add a subpart to a part that uses itsself.'), code='invalid')
        if self.assembly_subpart == self.assembly_part:
            raise ValidationError(_('Recursive relationship: cannot add a subpart to itsself.'), code='invalid')


class Seller(models.Model):
    organization = models.ForeignKey(Organization)
    name = models.CharField(max_length=128, default=None)

    def __unicode__(self):
        return u'%s' % (self.name)


class SellerPart(models.Model):
    seller = models.ForeignKey(Seller)
    part = models.ForeignKey(Part)
    minimum_order_quantity = models.IntegerField(null=True, blank=True)
    minimum_pack_quantity = models.IntegerField(null=True, blank=True)
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
            'part',
            'minimum_order_quantity',
            'unit_cost']


class PartFile(models.Model):
    file = models.FileField(upload_to='partfiles/')
    upload_date = models.DateField(auto_now=True)
    part = models.ForeignKey(Part)


@receiver(post_delete, sender=PartFile)
def partfile_post_delete_handler(sender, instance, **kwargs):
    instance.file.delete(False)
