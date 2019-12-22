import csv
import codecs
import logging

from django import forms
from django.utils.translation import gettext_lazy as _
from django.db import IntegrityError
from django.core.exceptions import ValidationError

from .models import Part, PartClass, Manufacturer, ManufacturerPart, Subpart, Seller, SellerPart, User, UserMeta, \
    Organization, PartRevision, AssemblySubparts, Assembly
from .validators import decimal, numeric
from .utils import listify_string, stringify_list, check_references_for_duplicates, prep_for_sorting_nicely, get_from_dict

logger = logging.getLogger(__name__)


class UserModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        l = "[" + user.username + "]"
        if user.first_name:
            l += " " + user.first_name
        if user.last_name:
            l += " " + user.last_name
        if user.email:
            l += ", " + user.email
        return l


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'username']


class UserAddForm(forms.ModelForm):
    class Meta:
        model = UserMeta
        fields = ['role']

    username = forms.CharField(initial=None, required=False)

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(UserAddForm, self).__init__(*args, **kwargs)

    def clean_username(self):
        cleaned_data = super(UserAddForm, self).clean()
        username = cleaned_data.get('username')
        try:
            user = User.objects.get(username=username)
            user_meta = UserMeta.objects.get(user=user)
            if user_meta.organization == self.organization:
                validation_error = forms.ValidationError(
                    "User '{0}' already belongs to {1}.".format(username, self.organization),
                    code='invalid')
                self.add_error('username', validation_error)
            elif user_meta.organization:
                validation_error = forms.ValidationError(
                    "User '{}' belongs to another organization.".format(username),
                    code='invalid')
                self.add_error('username', validation_error)
        except User.DoesNotExist:
            validation_error = forms.ValidationError(
                "User '{}' does not exist.".format(username),
                code='invalid')
            self.add_error('username', validation_error)

        return username

    def save(self):
        username = self.cleaned_data.get('username')
        role = self.cleaned_data.get('role')
        user = User.objects.get(username=username)
        user_meta = UserMeta.objects.get(user=user)
        user_meta.organization = self.organization
        user_meta.role = role
        user_meta.save()
        return user_meta


class UserMetaForm(forms.ModelForm):
    class Meta:
        model = UserMeta
        exclude = ['user', ]

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(UserMetaForm, self).__init__(*args, **kwargs)

    def save(self):
        self.instance.organization = self.organization
        self.instance.save()
        return self.instance


class OrganizationForm(forms.Form):

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(OrganizationForm, self).__init__(*args, **kwargs)
        user_queryset = User.objects.filter(
            id__in=UserMeta.objects.filter(organization=self.organization, role='A').values_list('user', flat=True)).order_by(
            'first_name', 'last_name', 'email')
        self.fields['owner'] = UserModelChoiceField(queryset=user_queryset, label='Owner', initial=self.organization.owner, required=True)
        self.fields['name'] = forms.CharField(label="Name", initial=self.organization.name, required=True)

    def save(self):
        self.organization.owner = self.cleaned_data.get('owner')
        self.organization.name = self.cleaned_data.get('name')
        self.organization.save()
        return self.organization


class NumberItemLenForm(forms.Form):

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(NumberItemLenForm, self).__init__(*args, **kwargs)
        self.fields['number_item_len'] = forms.IntegerField(max_value=Part.NUMBER_ITEM_MAX_LEN, min_value=self.organization.number_item_len,
                                                            initial=self.organization.number_item_len)

    def save(self):
        self.organization.number_item_len = self.cleaned_data.get('number_item_len')
        self.organization.save()
        return self.organization.number_item_len


class PartInfoForm(forms.Form):
    quantity = forms.IntegerField(label='Quantity for Cost Estimate', min_value=1)


class ManufacturerForm(forms.ModelForm):
    class Meta:
        model = Manufacturer
        exclude = ['organization', ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = False


class ManufacturerPartForm(forms.ModelForm):
    class Meta:
        model = ManufacturerPart
        exclude = ['part', ]

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(ManufacturerPartForm, self).__init__(*args, **kwargs)
        self.fields['manufacturer'].required = False
        self.fields['manufacturer_part_number'].required = False
        self.fields['manufacturer'].queryset = Manufacturer.objects.filter(organization=self.organization).order_by('name')


class SellerPartForm(forms.ModelForm):
    class Meta:
        model = SellerPart
        exclude = ['manufacturer_part', 'data_source', ]

    new_seller = forms.CharField(max_length=128, label='-or- Create new seller (leave blank if selecting)',
                                 required=False)
    field_order = ['seller', 'new_seller', 'unit_cost', 'nre_cost', 'lead_time_days', 'minimum_order_quantity',
                   'minimum_pack_quantity', ]

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.manufacturer_part = kwargs.pop('manufacturer_part', None)
        super(SellerPartForm, self).__init__(*args, **kwargs)
        if self.manufacturer_part is not None:
            self.instance.manufacturer_part = self.manufacturer_part
        self.fields['seller'].queryset = Seller.objects.filter(
            organization=self.organization).order_by('name')
        self.fields['seller'].required = False

    def clean(self):
        cleaned_data = super(SellerPartForm, self).clean()
        seller = cleaned_data.get('seller')
        new_seller = cleaned_data.get('new_seller')

        if seller and new_seller:
            raise forms.ValidationError("Cannot have a seller and a new seller.", code='invalid')
        elif new_seller:
            obj, created = Seller.objects.get_or_create(name__iexact=new_seller, organization=self.organization,
                                                        defaults={'name': new_seller})
            cleaned_data['seller'] = obj
        elif not seller:
            raise forms.ValidationError("Must specify a seller.", code='invalid')


class PartClassForm(forms.ModelForm):
    class Meta:
        model = PartClass
        fields = ['code', 'name', 'comment']

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartClassForm, self).__init__(*args, **kwargs)
        self.fields['code'].required = False
        self.fields['name'].required = False

    def clean_code(self):
        cleaned_data = super(PartClassForm, self).clean()
        code = cleaned_data.get('code')
        if not code.isdigit() or int(code) < 0:
            validation_error = forms.ValidationError(
                "Part class code must be a positive number.",
                code='invalid')
            self.add_error('code', validation_error)

        part_class_with_code = None
        try:
            part_class_with_code = PartClass.objects.get(code=code, organization=self.organization)
            if part_class_with_code and self.instance and self.instance.id != part_class_with_code.id:
                validation_error = forms.ValidationError(
                    "Part class with code {} is already defined.".format(code),
                    code='invalid')
                self.add_error('code', validation_error)

        except PartClass.DoesNotExist:
            pass

        return code

    def clean_name(self):
        cleaned_data = super(PartClassForm, self).clean()
        name = cleaned_data.get('name')
        part_class_with_name = None
        try:
            part_class_with_name = PartClass.objects.get(name__iexact=name, organization=self.organization)
            if part_class_with_name and self.instance and self.instance.id != part_class_with_name.id:
                validation_error = forms.ValidationError(
                    "Part class with name {} is already defined.".format(name),
                    code='invalid')
                self.add_error('name', validation_error)

        except PartClass.DoesNotExist:
            pass

        return name

    def save(self):
        cleaned_data = super(PartClassForm, self).clean()
        code = cleaned_data.get('code')
        name = cleaned_data.get('name')
        comment = cleaned_data.get('comment')
        if (self.instance):
            self.instance.code = code
            self.instance.name = name
            self.instance.comment = comment
            self.instance.organization = self.organization
            self.instance.save()
        else:
            try:
                PartClass.objects.create(code=code, name__iexact=name, comment=comment, organization=self.organization)

            except IntegrityError:
                validation_error = forms.ValidationError(
                    "Part class {0} {1} is already defined.".format(code, name),
                    code='invalid')
                self.add_error(None, validation_error)

        return self.instance


class PartClassSelectionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartClassSelectionForm, self).__init__(*args, **kwargs)
        self.fields['part_class'] = forms.ModelChoiceField(queryset=PartClass.objects.filter(organization=self.organization).order_by('code'),
                                                           empty_label="- Select Part Class -", label='List parts by class', required=False)


class PartClassCSVForm(forms.Form):
    file = forms.FileField(required=False)

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartClassCSVForm, self).__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super(PartClassCSVForm, self).clean()
        file = self.cleaned_data.get('file')
        self.successes = list()
        self.warnings = list()

        try:
            csvline_decoded = file.readline().decode('utf-8')
            dialect = csv.Sniffer().sniff(csvline_decoded)
            file.open()
            reader = csv.reader(codecs.iterdecode(file, 'utf-8'), dialect)
            headers = [h.lower() for h in next(reader)]

            must_headers = ('code', 'name')
            for hdr in must_headers:
                if hdr not in headers:
                    validation_error = forms.ValidationError("Missing required column named '{}'.".format(hdr), code='invalid')
                    self.add_error(None, validation_error)

            if 'comment' in hdr and 'description' in hdr:
                validation_error = forms.ValidationError("Can only have a column named 'comment' or a column named 'description'.".format(hdr), code='invalid')
                self.add_error(None, validation_error)

            row_count = 1  # Skip over header row
            for row in reader:
                row_count += 1
                part_class_data = {}
                for idx, item in enumerate(row):
                    part_class_data[headers[idx]] = item

                if 'name' in part_class_data and 'code' in part_class_data:
                    try:
                        name = part_class_data['name']
                        code = part_class_data['code']
                        if not code.isdigit() or int(code) < 0:
                            validation_error = forms.ValidationError(
                                "Part class 'code' in row {} must be a positive number. Uploading of this part class skipped.".format(row_count),
                                code='invalid')
                            self.add_error(None, validation_error)
                            continue
                        if 'description' in part_class_data:
                            description_or_comment = part_class_data['description'] if 'description' in part_class_data else ''
                        elif 'comment' in part_class_data:
                            description_or_comment = part_class_data['comment'] if 'comment' in part_class_data else ''
                        PartClass.objects.create(code=code, name__iexact=name, comment=description_or_comment, organization=self.organization)
                        self.successes.append("Part class {0} {1} on row {2} created.".format(code, name, row_count))

                    except IntegrityError:
                        validation_error = forms.ValidationError(
                            "Part class {0} {1} on row {2} is already defined. Uploading of this part class skipped.".format(code, name, row_count),
                            code='invalid')
                        self.add_error(None, validation_error)

                else:
                    validation_error = forms.ValidationError(
                        "In row {} must specify both 'code' and 'name'. Uploading of this part class skipped.".format(row_count), code='invalid')
                    self.add_error(None, validation_error)

        except UnicodeDecodeError as e:
            self.add_error(forms.ValidationError("CSV File Encoding error, try encoding your file as utf-8, and upload again. \
                If this keeps happening, reach out to info@indabom.com with your csv file and we'll do our best to \
                fix your issue!", code='invalid'))
            logger.warning("UnicodeDecodeError: {}".format(e))
            raise ValidationError("Specific Error: {}".format(e),
                                  code='invalid')

        return cleaned_data


class PartCSVForm(forms.Form):
    file = forms.FileField(required=False)

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartCSVForm, self).__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super(PartCSVForm, self).clean()
        file = self.cleaned_data.get('file')
        self.successes = list()
        self.warnings = list()

        try:
            csvline_decoded = file.readline().decode('utf-8')
            dialect = csv.Sniffer().sniff(csvline_decoded)
            file.open()
            reader = csv.reader(codecs.iterdecode(file, 'utf-8'), dialect)
            headers = [h.lower() for h in next(reader)]

            if 'part_class' not in headers and 'part_number not in headers':
                raise ValidationError("Missing required column named 'part_class' or column named 'part_number'", code='invalid')

            if 'revision' not in headers:
                raise ValidationError("Missing required column named 'revision'", code='invalid')

            if 'description' not in headers:
                if 'value' not in headers or 'value_units' not in headers:
                    raise ValidationError("Missing required column named 'description' or columns named 'value' and 'value_units'",
                                          code='invalid')

            row_count = 1  # Skip over header row
            for row in reader:
                row_count += 1
                part_data = {}
                for idx, item in enumerate(row):
                    part_data[headers[idx]] = item
                part_number = get_from_dict(part_data, ['part_number', 'pn', 'part no', 'part number', 'part_no', ])
                part_class = get_from_dict(part_data, ['part_class', 'part class', 'class', ])
                number_item = None
                number_variation = None
                revision = get_from_dict(part_data, ['revision', 'rev', 'part_rev', 'part rev', 'part_revision', 'part revision'])
                mpn = get_from_dict(part_data, ['manufacturer_part_number', 'mpn', ])
                mfg_name = get_from_dict(part_data, ['mfg', 'manufacturer', 'mfg name', 'manufacturer name', ])
                description = get_from_dict(part_data, ['description', 'desc', 'desc.', ])
                value = get_from_dict(part_data, ['value', 'val', 'val.', ])
                value_units = get_from_dict(part_data, ['value_units', 'value units', 'val. units', 'val units', ])

                # Check part number for uniqueness. If part number not specified
                # then Part.save() will create one.
                if part_number:
                    try:
                        (part_class, number_item, number_variation) = Part.parse_part_number(part_number, self.organization.number_item_len)
                        Part.objects.get(number_class=part_class, number_item=number_item, number_variation=number_variation, organization=self.organization)
                        self.add_error(None, "Part number {0} in row {1} already exists. Uploading of this part skipped.".format(part_number, row_count))
                    except AttributeError as e:
                        self.add_error(None, str(e) + " on row {}. Creation of this part skipped.".format(row_count))
                        continue
                    except Part.DoesNotExist:
                        pass
                elif part_class:
                    try:
                        part_class = PartClass.objects.get(code=part_data['part_class'], organization=self.organization)
                    except PartClass.DoesNotExist:
                        self.add_error(None, "Part class {0} in row {1} doesn't exist. "
                                             "Uploading of this part skipped.".format(part_data['part_class'], row_count))
                        continue
                else:
                    self.add_error(None, "In row {} need to specify a part_class or a part_number. Uploading of this part skipped.".format(row_count))
                    continue

                if not revision:
                    self.add_error(None, "Missing revision in row {}. Uploading of this part skipped.", format(row_count))
                    continue
                elif len(revision) > 4:
                    self.add_error(None, "Revision {0} in row {1} is more than the maximum 4 characters. "
                                         "Uploading of this part skipped.".format(part_data['revision'], row_count))
                    continue
                elif revision.isdigit() and int(revision) < 0:
                    self.add_error(None, "Revision {0} in row {1} cannot be a negative number. "
                                         "Uploading of this part skipped.".format(part_data['revision'], row_count))
                    continue

                if mpn and mfg_name:
                    manufacturer_part = ManufacturerPart.objects.filter(manufacturer_part_number=mpn,
                                                                        manufacturer__name=mfg_name,
                                                                        manufacturer__organization=self.organization)
                    if manufacturer_part.count() > 0:
                        self.add_error(None, "Part already exists for manufacturer part {0} in row {1}. "
                                             "Uploading of this part skipped.".format(row_count, mpn, row_count))
                        continue

                skip = False
                part_revision = PartRevision()

                # Required properties:
                if description is None:
                    if value is None and value_units is None:
                        self.add_error(None, "Missing 'description' or 'value' plus 'value_units' for part in row {}. Uploading of this part skipped.".format(row_count))
                        skip = True
                        break
                    elif value is None and value_units is not None:
                        self.add_error(None, "Missing 'value' for part in row {}. Uploading of this part skipped.".format(row_count))
                        skip = True
                        break
                    elif value is not None and value_units is None:
                        self.add_error(None, "Missing 'value_units' for part in row {}. Uploading of this part skipped.".format(row_count))
                        skip = True
                        break

                part_revision.description = description

                # Part revision's value and value_units set below, after have had a chance to validate unit choice.

                def is_valid_choice(choice, choices):
                    for c in choices:
                        if choice == c[0]:
                            return True
                    return False

                # Optional properties with free-form values:
                props_free_form = ['tolerance', 'pin_count', 'color', 'material', 'finish', 'attribute']
                for prop_free_form in props_free_form:
                    if prop_free_form in part_data:
                        setattr(part_revision, prop_free_form, part_data[prop_free_form])

                # Optional properties with choices for values:
                props_with_value_choices = {'package': PartRevision.PACKAGE_TYPES, 'interface': PartRevision.INTERFACE_TYPES}
                for k, v in props_with_value_choices.items():
                    if k in part_data:
                        if is_valid_choice(part_data[k], v):
                            setattr(part_revision, k, part_data[k])
                        else:
                            self.add_warning(None, "'{0}' is an invalid choice of value for '{1}' for part in row {2} . Uploading of this property skipped. "
                                                   "Part will still be uploaded".format(part_data[k], k, row_count))

                # Optional properties with units:
                props_with_unit_choices = {
                    'value': PartRevision.VALUE_UNITS,
                    'supply_voltage': PartRevision.VOLTAGE_UNITS, 'power_rating': PartRevision.POWER_UNITS,
                    'voltage_rating': PartRevision.VOLTAGE_UNITS, 'current_rating': PartRevision.CURRENT_UNITS,
                    'temperature_rating': PartRevision.TEMPERATURE_UNITS, 'memory': PartRevision.MEMORY_UNITS,
                    'frequency': PartRevision.FREQUENCY_UNITS, 'wavelength': PartRevision.WAVELENGTH_UNITS,
                    'length': PartRevision.DISTANCE_UNITS, 'width': PartRevision.DISTANCE_UNITS,
                    'height': PartRevision.DISTANCE_UNITS, 'weight': PartRevision.WEIGHT_UNITS,
                }
                for k, v in props_with_unit_choices.items():
                    if k in part_data and k + '_units' in part_data:
                        if part_data[k] and not part_data[k + '_units']:
                            self.add_error(None, "Missing '{0}' for part in row {1}. Uploading of this part skipped.".format(k, row_count))
                            skip = True
                            break
                        elif not part_data[k] and part_data[k + '_units']:
                            self.add_error(None, "Missing '{0}' for part in row {1}. Uploading of this part skipped.".format(k + '_units', row_count))
                            skip = True
                            break
                        elif part_data[k + '_units']:
                            if is_valid_choice(part_data[k + '_units'], v):
                                setattr(part_revision, k, part_data[k])
                                setattr(part_revision, k + '_units', part_data[k + '_units'])
                            else:
                                self.add_error(None, "'{0}' is an invalid choice of units for '{1}' for part in row {2}. Uploading of this part skipped."
                                               .format(part_data[k + '_units'], k + '_units', row_count))
                                skip = True
                                break

                if skip:
                    continue

                part = Part.objects.create(number_class=part_class, number_item=number_item, number_variation=number_variation, organization=self.organization)
                part_revision.part = part
                if mfg_name and mpn:
                    mfg, created = Manufacturer.objects.get_or_create(name__iexact=mfg_name, organization=self.organization, defaults={'name': mfg_name})
                    manufacturer_part, created = ManufacturerPart.objects.get_or_create(part=part, manufacturer_part_number=mpn, manufacturer=mfg)
                    if part.primary_manufacturer_part is None and manufacturer_part is not None:
                        part.primary_manufacturer_part = manufacturer_part

                part.save()
                part_revision.save()
                self.successes.append("Part {0} on row {1} created.".format(part.full_part_number(), row_count))

        except UnicodeDecodeError as e:
            self.add_error(forms.ValidationError("CSV File Encoding error, try encoding your file as utf-8, and upload again. \
                If this keeps happening, reach out to info@indabom.com with your csv file and we'll do our best to \
                fix your issue!", code='invalid'))
            logger.warning("UnicodeDecodeError: {}".format(e))
            raise ValidationError("Specific Error: {}".format(e), code='invalid')

        return cleaned_data


class PartForm(forms.ModelForm):
    class Meta:
        model = Part
        exclude = ['organization', 'google_drive_parent', ]
        help_texts = {
            'number_class': _('Select a number class.'),
            'number_item': _('Auto generated if blank.'),
            'number_variation': 'Auto generated if blank.',
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartForm, self).__init__(*args, **kwargs)
        self.fields['number_class'] = forms.ModelChoiceField(queryset=PartClass.objects.filter(organization=self.organization),
                                                             empty_label="- Select Part Number Class -", label='Part Number Class*', required=True)
        if self.instance and self.instance.id:
            self.fields['primary_manufacturer_part'].queryset = ManufacturerPart.objects.filter(
                part__id=self.instance.id).order_by('manufacturer_part_number')
        else:
            del self.fields['primary_manufacturer_part']
        for _, value in self.fields.items():
            value.widget.attrs['placeholder'] = value.help_text
            value.help_text = ''

    def clean(self):
        cleaned_data = super(PartForm, self).clean()
        number_class = cleaned_data.get('number_class')
        number_item = cleaned_data.get('number_item')
        number_variation = cleaned_data.get('number_variation')

        try:
            Part.objects.get(
                number_class=number_class,
                number_item=number_item,
                number_variation=number_variation,
                organization=self.organization
            )
            validation_error = forms.ValidationError(
                ("Part number {0}-{1}-{2} already in use.".format(number_class, number_item, number_variation)),
                code='invalid')
            self.add_error(None, validation_error)
        except Part.DoesNotExist:
            pass

        return cleaned_data


class PartRevisionForm(forms.ModelForm):
    class Meta:
        model = PartRevision
        exclude = ['timestamp', 'assembly', 'part']
        help_texts = {
            'description': _('Additional part info, special instructions, etc.'),
            'attribute': _('Additional part attributes (free form)'),
            'value': _('Number or text'),
        }

    def __init__(self, *args, **kwargs):
        super(PartRevisionForm, self).__init__(*args, **kwargs)

        self.fields['revision'].disabled = True
        self.fields['configuration'].required = False

        self.fields['tolerance'].initial = '%'

        # Fix up field labels to be succinct for use in rendered form:
        for f in self.fields.values():
            if 'units' in f.label: f.label = 'Units'
            f.label.replace('rating', '')
            # f.value = strip_trailing_zeros(f.value) # Harmless if field is not a number
        self.fields['supply_voltage'].label = 'Vsupply'
        self.fields['attribute'].label = ''

        for _, value in self.fields.items():
            value.widget.attrs['placeholder'] = value.help_text
            value.help_text = ''

    def clean(self):
        cleaned_data = super(PartRevisionForm, self).clean()

        if not cleaned_data.get('description') and not cleaned_data.get('value'):
            validation_error = forms.ValidationError("Must specify a value and value units, or a description.", code='invalid')
            self.add_error('description', validation_error)
            self.add_error('value', validation_error)

        for key in self.fields.keys():
            if '_units' in key:
                value_name = str.replace(key, '_units', '')
                value = cleaned_data.get(value_name)
                units = cleaned_data.get(key)
                if not value and units:
                    self.add_error(key, forms.ValidationError("Cannot specify {} without an accompanying value".format(units), code='invalid'))
                elif units and not units:
                    self.add_error(key, forms.ValidationError("Cannot specify value {} without an accompanying units".format(value), code='invalid'))

        return cleaned_data


class PartRevisionNewForm(PartRevisionForm):
    copy_assembly = forms.BooleanField(label='Copy assembly from latest revision', initial=True, required=False)

    def __init__(self, *args, **kwargs):
        self.part = kwargs.pop('part', None)
        self.revision = kwargs.pop('revision', None)
        self.assembly = kwargs.pop('assembly', None)
        super(PartRevisionNewForm, self).__init__(*args, **kwargs)
        for _, value in self.fields.items():
            value.widget.attrs['placeholder'] = value.help_text
            value.help_text = ''

    def save(self):
        cleaned_data = super(PartRevisionNewForm, self).clean()
        self.instance.part = self.part
        self.instance.revision = self.revision
        self.instance.assembly = self.assembly
        self.instance.save()
        return self.instance


class SubpartForm(forms.ModelForm):
    class Meta:
        model = Subpart
        fields = ['part_revision', 'reference', 'count', 'do_not_load']

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.part_id = kwargs.pop('part_id', None)
        super(SubpartForm, self).__init__(*args, **kwargs)
        if self.part_id is None:
            self.Meta.exclude = ['part_revision']
        else:
            self.fields['part_revision'].queryset = PartRevision.objects.filter(
                part__id=self.part_id).order_by('-timestamp')

        if self.part_id:
            part = Part.objects.get(id=self.part_id)
            unusable_part_ids = [p.id for p in part.where_used_full()]
            unusable_part_ids.append(part.id)

    def clean_count(self):
        count = self.cleaned_data['count']
        if not count:
            count = 1
        if count < 1:
            validation_error = forms.ValidationError(
                ("Subpart quantity must be > 0."),
                code='invalid')
            self.add_error('count', validation_error)
        return count

    def clean_reference(self):
        reference = self.cleaned_data['reference']
        reference = stringify_list(listify_string(reference))
        return reference

    def clean(self):
        cleaned_data = super(SubpartForm, self).clean()
        reference_list = listify_string(cleaned_data.get('reference'))
        count = cleaned_data.get('count')

        if len(reference_list) > 0 and len(reference_list) != count:
            raise forms.ValidationError(
                ("The number of reference designators ({0}) did not match the subpart quantity ({1}).".format(len(reference_list), count)),
                code='invalid')

        return cleaned_data


class AddSubpartForm(forms.Form):
    subpart_part_number = forms.CharField(required=True, label="Subpart part number")
    count = forms.IntegerField(required=False, label='Quantity')
    reference = forms.CharField(required=False, label="Reference")
    do_not_load = forms.BooleanField(required=False, label="do_not_load")

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.part_id = kwargs.pop('part_id', None)
        super(AddSubpartForm, self).__init__(*args, **kwargs)

        # TODO: Clean this up, consider forcing a primary mfg part on each part
        self.fields['subpart_part_number'].label_from_instance = \
            lambda obj: "%s" % obj.full_part_number() + ' [MFR:] ' \
                        + str(obj.primary_manufacturer_part.manufacturer if obj.primary_manufacturer_part is not None
                              else '-') + ' [MFR#:] ' + \
                        str(obj.primary_manufacturer_part if obj.primary_manufacturer_part is not None else '-') \
                        + ' [SYN:] ' + str(obj.latest().synopsis() if obj.latest() else '')

    def clean_count(self):
        count = self.cleaned_data['count']
        if not count:
            count = 1
        elif count < 1:
            validation_error = forms.ValidationError(
                ("Subpart quantity must be > 0."),
                code='invalid')
            self.add_error('count', validation_error)
        return count

    def clean_reference(self):
        reference = self.cleaned_data['reference']
        reference = stringify_list(listify_string(reference))
        return reference

    def clean_subpart_part_number(self):
        subpart_part_number = self.cleaned_data['subpart_part_number']

        if not subpart_part_number:
            validation_error = forms.ValidationError(
                ("Must specify a part number."),
                code='invalid')
            self.add_error('subpart_part_number', validation_error)

        try:
            (number_class, number_item, number_variation) = Part.parse_part_number(subpart_part_number, self.organization.number_item_len)
            self.subpart_part = Part.objects.get(
                number_class=PartClass.objects.get(code=number_class, organization=self.organization),
                number_item=number_item,
                number_variation=number_variation,
                organization=self.organization
            ).latest()
        except AttributeError:
            validation_error = forms.ValidationError(
                ("Ill-formed part number {}.".format(subpart_part_number)),
                code='invalid')
            self.add_error('subpart_part_number', validation_error)
        except PartClass.DoesNotExist:
            validation_error = forms.ValidationError(
                ("No part class for in given part number {}.".format(subpart_part_number)),
                code='invalid')
            self.add_error('subpart_part_number', validation_error)
        except Part.DoesNotExist:
            validation_error = forms.ValidationError(
                ("No part found with given part number {}.".format(subpart_part_number)),
                code='invalid')
            self.add_error('subpart_part_number', validation_error)

        return subpart_part_number

    def clean(self):
        cleaned_data = super(AddSubpartForm, self).clean()
        reference_list = listify_string(cleaned_data.get('reference'))
        count = cleaned_data.get('count')

        if len(reference_list) > 0 and len(reference_list) != count:
            raise forms.ValidationError(
                ("The number of reference designators ({0}) did not match the subpart quantity ({1}).".format(len(reference_list), count)),
                code='invalid')

        return cleaned_data


class UploadBOMForm(forms.Form):
    parent_part_number = forms.CharField(required=True, label="Parent part number")

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(UploadBOMForm, self).__init__(*args, **kwargs)

    def clean_parent_part_number(self):
        parent_part_number = self.cleaned_data['parent_part_number']

        if not parent_part_number:
            validation_error = forms.ValidationError(
                ("Must specify a parent part number."),
                code='invalid')
            self.add_error('parent_part_number', validation_error)

        try:
            (number_class, number_item, number_variation) = Part.parse_part_number(parent_part_number, self.organization.number_item_len)
            self.parent_part = Part.objects.get(
                number_class=PartClass.objects.get(code=number_class, organization=self.organization),
                number_item=number_item,
                number_variation=number_variation,
                organization=self.organization
            )
        except AttributeError:
            validation_error = forms.ValidationError(
                ("Ill-formed parent part number {}.".format(parent_part_number)),
                code='invalid')
            self.add_error('parent_part_number', validation_error)
        except PartClass.DoesNotExist:
            validation_error = forms.ValidationError(
                ("No part class found for given parent part number {}.".format(parent_part_number)),
                code='invalid')
            self.add_error('parent_part_number', validation_error)
        except Part.DoesNotExist:
            validation_error = forms.ValidationError(
                ("No part found with given parent part number {}.".format(parent_part_number)),
                code='invalid')
            self.add_error('parent_part_number', validation_error)

        return parent_part_number


class BOMCSVForm(forms.Form):
    file = forms.FileField(required=False)

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.parent_part = kwargs.pop('parent_part', None)
        super(BOMCSVForm, self).__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super(BOMCSVForm, self).clean()
        file = self.cleaned_data.get('file')
        self.successes = list()
        self.warnings = list()

        try:
            csv_row_decoded = file.readline().decode('utf-8')
            dialect = csv.Sniffer().sniff(csv_row_decoded)
            file.open()

            reader = csv.reader(codecs.iterdecode(file, 'utf-8'), dialect)
            headers = [h.lower() for h in next(reader)]

            if 'part_number' not in headers and 'manufacturer_part_number' not in headers:
                raise ValidationError("Missing required column named 'part_number' or column named 'manufacturer_part_number'.", code='invalid')
            if 'quantity' not in headers:
                raise ValidationError("Missing required column named 'quantity'.", code='invalid')

            parent_part_revision = self.parent_part.latest()
            if parent_part_revision.assembly is None:
                parent_part_revision.assembly = Assembly.objects.create()
                parent_part_revision.save()

            row_count = 1  # Skip over header row
            for row in reader:
                row_count += 1
                part_dict = {}

                for idx, item in enumerate(row):
                    part_dict[headers[idx]] = item

                dnp = get_from_dict(part_dict, ['dnp', 'dnl', 'do_not_populate', 'do_not_load', 'do_not_process', 'do not load', 'do not populate'])
                do_not_load = dnp in ['y', 'x', ]

                part_number = get_from_dict(part_dict, ['part_number', 'part number', 'part no', ])
                revision = get_from_dict(part_dict, ['rev', 'revision', 'part_revision', ])
                mpn = get_from_dict(part_dict, ['manufacturer_part_number', 'mpn', 'mfg_part_number', 'mfg part number', 'manufacturer part number'])
                count = get_from_dict(part_dict, ['quantity', 'qty', ])
                reference = get_from_dict(part_dict, ['reference', 'designator', ])

                # First try to add the subpart_part based on the part number
                subpart_part = None
                if part_number:
                    try:
                        (number_class, number_item, number_variation) = Part.parse_part_number(part_dict['part_number'], self.organization.number_item_len)
                        subparts = Part.objects.filter(
                            number_class__code=number_class,
                            number_item=number_item,
                            number_variation=number_variation,
                            organization=self.organization)
                    except AttributeError as e:
                        self.add_error(None, str(e) + " on row {}. Uploading of this subpart skipped. Couldn't parse part number.".format(row_count))
                        continue

                    if len(subparts) == 0:
                        self.add_error(None,
                                       "Part {0} for subpart on row {1} does not exist, you must create the part "
                                       "before it can be added as a subpart. "
                                       "Uploading of this subpart skipped.".format(part_dict['part_number'], row_count))
                        continue
                    elif len(subparts) > 1:
                        self.add_error(None,
                                       "Found {0} entries for part {1} for subpart on row {2}. This should not happen. "
                                       "Please let info@indabom.com know. Uploading of this subpart skipped.".format(
                                           len(subparts), row_count, part_dict['part_number']))
                        continue

                    subpart_part = subparts[0]  # TODO: handle more than one subpart
                # Couldn't add the sub-part based on the part number, so try from MPN
                elif mpn:
                    manufacturer_parts = ManufacturerPart.objects.filter(manufacturer_part_number=mpn, part__organization=self.organization)
                    if len(manufacturer_parts) == 0:
                        self.add_error(None, "Manufacturer part number {0} for subpart on row {1} does not exist, "
                                             "you must create the part before it can be added as a subpart. "
                                             "Uploading of this part skipped.".format(part_dict['manufacturer_part_number'], row_count))
                        continue

                    subpart_part = manufacturer_parts[0].part
                else:
                    self.add_error(None, "Couldn't parse part on row {} with data: {}".format(row_count, row))
                    continue

                # We have the subpart_part at this point, make sure we don't have infinite recursion
                if self.parent_part == subpart_part:
                    self.add_error(None, "Subart on row {0} with part number {1} is the same as the parent part. A subpart can not be a subpart of its self. "
                                         "Uploading of this subpart skipped.".format(row_count, subpart_part.__str__()))
                    continue

                subpart_revision = subpart_part.latest()
                if revision:
                    revs = subpart_part.revisions().filter(revision=revision).order_by('-timestamp')
                    if revs.count() > 0:
                        subpart_revision = revs[0]
                    else:
                        rev_options = revs.values_list('revision', flat=True)
                        self.add_error(None, f"Found part {part_number}, but couldn't match revision {revision}. Options are: {rev_options}")
                        continue

                contains_parent = False
                for sp in subpart_revision.indented(): # Make sure the subpart does not contain the parent - infinite recursion!
                    if sp['part_revision'] == parent_part_revision:
                        contains_parent = True
                if contains_parent:
                    self.add_error(None, f"Uploaded part {part_number} contains parent part in it's assembly. Cannot add {part_number} as it would cause infinite recursion.")
                    continue

                if not count:
                    count = '1'
                elif not count.isdigit() or int(count) < 1:
                    self.add_error(None, "Quantity for subpart {0} on row {1} must be a number > 0. Uploading of this subpart skipped.".format(subpart_part.__str__(), row_count))
                    continue

                reference_list = listify_string(reference) if reference else []
                if len(reference_list) > 0 and len(reference_list) != int(count):
                    self.add_error(None, "The number of reference designators ({0}) for subpart {1} on row {2} does not match the subpart quantity ({3}). "
                                         "Uploading of this subpart skipped.".format(len(reference_list), subpart_part.__str__(), row_count, count))
                    continue
                reference = stringify_list(reference_list)

                new_subpart, created = Subpart.objects.get_or_create(
                    part_revision=subpart_revision,
                    count=count,
                    reference=reference,
                    do_not_load=do_not_load
                )

                AssemblySubparts.objects.get_or_create(assembly=parent_part_revision.assembly, subpart=new_subpart)

                if not created:
                    self.add_warning(None, f"Already created part on row {row_count}, {part_number}, rev {revision}, qty {count}, ref: {reference}. Did not create it again.")
                else:
                    info_msg = "Added subpart "
                    if reference:
                        info_msg += ' ' + reference
                    info_msg += " {} to parent part {}.".format(part_number, self.parent_part)
                    self.successes.append(info_msg)

                references_seen = set()
                duplicate_references = set()
                bom = parent_part_revision.indented()
                for item in bom:
                    check_references_for_duplicates(item['reference'], references_seen, duplicate_references)

                if len(duplicate_references) > 0:
                    sorted_duplicate_references = sorted(duplicate_references, key=prep_for_sorting_nicely)
                    self.warnings.append("The following BOM references are associated with multiple parts: " + str(sorted_duplicate_references))
        except UnicodeDecodeError as e:
            self.add_error(forms.ValidationError("CSV File Encoding error, try encoding your file as utf-8, and upload again. \
                If this keeps happening, reach out to info@indabom.com with your csv file and we'll do our best to \
                fix your issue!", code='invalid'))
            logger.warning("UnicodeDecodeError: {}".format(e))
            raise ValidationError("Specific Error: {}".format(e),
                                  code='invalid')

        return cleaned_data


class AddSellerPartForm(forms.Form):
    seller = forms.ModelChoiceField(queryset=Seller.objects.none(), required=False, label="Seller")
    new_seller = forms.CharField(max_length=128, label='Create New Seller', required=False,
                                 widget=forms.TextInput(attrs={'placeholder': 'Leave blank if selecting a seller.'}))
    minimum_order_quantity = forms.IntegerField(required=False,
                                                label='MOQ',
                                                validators=[numeric],
                                                widget=forms.TextInput(attrs={'placeholder': 'None'}))
    minimum_pack_quantity = forms.IntegerField(required=False,
                                               label='MPQ',
                                               validators=[numeric],
                                               widget=forms.TextInput(attrs={'placeholder': 'None'}))
    unit_cost = forms.DecimalField(required=True,
                                   label='Unit Cost',
                                   validators=[decimal, ],
                                   widget=forms.TextInput(attrs={'placeholder': '0.00'}))
    lead_time_days = forms.IntegerField(required=False,
                                        label='Lead Time (days)',
                                        validators=[numeric],
                                        widget=forms.TextInput(attrs={'placeholder': 'None'}))
    nre_cost = forms.DecimalField(required=False,
                                  label='NRE Cost',
                                  validators=[decimal, ],
                                  widget=forms.TextInput(attrs={'placeholder': 'None'}))
    ncnr = forms.BooleanField(required=False, label='NCNR')

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(AddSellerPartForm, self).__init__(*args, **kwargs)
        self.fields['seller'].queryset = Seller.objects.filter(
            organization=self.organization).order_by('name', )

    def clean(self):
        cleaned_data = super(AddSellerPartForm, self).clean()
        seller = cleaned_data.get('seller')
        new_seller = cleaned_data.get('new_seller')

        if seller and new_seller:
            raise forms.ValidationError(
                ("Cannot have a seller and a new seller."),
                code='invalid')
        elif new_seller:
            obj, created = Seller.objects.get_or_create(name__iexact=new_seller, organization=self.organization,
                                                        defaults={'name': new_seller})
            cleaned_data['seller'] = obj
        elif not seller:
            raise forms.ValidationError(
                ("Must specify a seller."),
                code='invalid')

        return cleaned_data


class FileForm(forms.Form):
    file = forms.FileField()
