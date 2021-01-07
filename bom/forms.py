import csv
import codecs
import logging

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.forms.models import model_to_dict
from django.utils.translation import gettext_lazy as _
from django.db import IntegrityError
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, MaxLengthValidator, MinLengthValidator
from djmoney.money import Money

from .constants import VALUE_UNITS, PACKAGE_TYPES, POWER_UNITS, INTERFACE_TYPES, TEMPERATURE_UNITS, DISTANCE_UNITS, WAVELENGTH_UNITS, \
    WEIGHT_UNITS, FREQUENCY_UNITS, VOLTAGE_UNITS, CURRENT_UNITS, MEMORY_UNITS, SUBSCRIPTION_TYPES, ROLE_TYPES, CONFIGURATION_TYPES, NUMBER_SCHEME_SEMI_INTELLIGENT, \
    NUMBER_SCHEME_INTELLIGENT, ROLE_TYPE_VIEWER, NUMBER_CLASS_CODE_LEN_DEFAULT, NUMBER_CLASS_CODE_LEN_MIN, NUMBER_CLASS_CODE_LEN_MAX, \
    NUMBER_ITEM_LEN_DEFAULT, NUMBER_ITEM_LEN_MIN, NUMBER_ITEM_LEN_MAX, NUMBER_VARIATION_LEN_DEFAULT, NUMBER_VARIATION_LEN_MIN, NUMBER_VARIATION_LEN_MAX
from .form_fields import AutocompleteTextInput
from .models import Part, PartClass, Manufacturer, ManufacturerPart, Subpart, Seller, SellerPart, User, UserMeta, \
    Organization, PartRevision, AssemblySubparts, Assembly
from .validators import decimal, numeric, alphanumeric
from .utils import listify_string, stringify_list, check_references_for_duplicates, prep_for_sorting_nicely, get_from_dict
from .csv_headers import PartsListCSVHeaders, PartClassesCSVHeaders, BOMFlatCSVHeaders, BOMIndentedCSVHeaders, CSVHeaderError

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


class UserCreateForm(UserCreationForm):
    first_name = forms.CharField(required=True)
    last_name = forms.CharField(required=True)
    email = forms.EmailField(required=True)

    def clean_email(self):
        email = self.cleaned_data['email']
        exists = User.objects.filter(email__iexact=email).count() > 0
        if exists:
            raise ValidationError('An account with this email address already exists.')
        return email

    def save(self, commit=True):
        user = super(UserCreateForm, self).save(commit=commit)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.save()
        return user


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', ]


class UserAddForm(forms.ModelForm):
    class Meta:
        model = UserMeta
        fields = ['role']

    username = forms.CharField(initial=None, required=False)

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(UserAddForm, self).__init__(*args, **kwargs)
        self.fields['role'].required = False

    def clean_username(self):
        cleaned_data = super(UserAddForm, self).clean()
        username = cleaned_data.get('username')
        try:
            user = User.objects.get(username=username)
            user_meta = UserMeta.objects.get(user=user)
            if user_meta.organization == self.organization:
                validation_error = forms.ValidationError("User '{0}' already belongs to {1}.".format(username, self.organization), code='invalid')
                self.add_error('username', validation_error)
            elif user_meta.organization:
                validation_error = forms.ValidationError("User '{}' belongs to another organization.".format(username), code='invalid')
                self.add_error('username', validation_error)
        except User.DoesNotExist:
            validation_error = forms.ValidationError("User '{}' does not exist.".format(username), code='invalid')
            self.add_error('username', validation_error)

        return username

    def clean_role(self):
        cleaned_data = super(UserAddForm, self).clean()
        role = cleaned_data.get('role', None)
        if not role:
            role = ROLE_TYPE_VIEWER
        return role

    def save(self, *args, **kwargs):
        username = self.cleaned_data.get('username')
        role = self.cleaned_data.get('role', ROLE_TYPE_VIEWER)
        user = User.objects.get(username=username)
        user_meta = user.bom_profile()
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


class OrganizationCreateForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(OrganizationCreateForm, self).__init__(*args, **kwargs)
        if self.data.get('number_scheme') == NUMBER_SCHEME_INTELLIGENT:
            # make the QueryDict mutable
            self.data = self.data.copy()
            self.data['number_class_code_len'] = 3
            self.data['number_item_len'] = 128
            self.data['number_variation_len'] = 2

    class Meta:
        model = Organization
        fields = ['name', 'number_scheme', 'number_class_code_len', 'number_item_len', 'number_variation_len', ]
        labels = {
            "name": "Organization Name",
            "number_class_code_len": "Number Class Code Length (C)",
            "number_item_len": "Number Item Length (N)",
            "number_variation_len": "Number Variation Length (V)",
        }


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        exclude = ['owner', 'subscription', 'google_drive_parent', 'number_scheme', ]
        labels = {
            "name": "Organization Name",
            "number_class_code_len": "Number Class Code Length (C)",
            "number_item_len": "Number Item Length (N)",
            "number_variation_len": "Number Variation Length (V)",
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super(OrganizationForm, self).__init__(*args, **kwargs)
        if user and self.instance.owner == user:
            user_queryset = User.objects.filter(
                id__in=UserMeta.objects.filter(organization=self.instance, role='A').values_list('user', flat=True)).order_by(
                'first_name', 'last_name', 'email')
            self.fields['owner'] = UserModelChoiceField(queryset=user_queryset, label='Owner', initial=self.instance.owner, required=True)


class OrganizationFormEditSettings(OrganizationForm):
    def __init__(self, *args, **kwargs):
        super(OrganizationFormEditSettings, self).__init__(*args, **kwargs)
        user = kwargs.get('user', None)

    class Meta:
        model = Organization
        exclude = ['subscription', 'google_drive_parent', 'number_scheme', 'number_item_len', 'number_class_code_len', 'number_variation_len', 'owner', ]
        labels = {
            "name": "Organization Name",
        }


class OrganizationNumberLenForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ['number_class_code_len', 'number_item_len', 'number_variation_len', ]
        labels = {
            "number_class_code_len": "Number Class Code Length (C)",
            "number_item_len": "Number Item Length (N)",
            "number_variation_len": "Number Variation Length (V)",
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.get('instance', None)
        super(OrganizationNumberLenForm, self).__init__(*args, **kwargs)
        self.fields['number_class_code_len'].validators.append(MinValueValidator(self.organization.number_class_code_len))
        self.fields['number_item_len'].validators.append(MinValueValidator(self.organization.number_item_len))
        self.fields['number_variation_len'].validators.append(MinValueValidator(self.organization.number_variation_len))


class PartInfoForm(forms.Form):
    quantity = forms.IntegerField(label='Quantity for Est Cost', min_value=1)


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

    field_order = ['manufacturer_part_number', 'manufacturer']

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(ManufacturerPartForm, self).__init__(*args, **kwargs)
        self.fields['manufacturer'].required = False
        self.fields['manufacturer_part_number'].required = False
        self.fields['manufacturer'].queryset = Manufacturer.objects.filter(organization=self.organization).order_by('name')
        self.fields['mouser_disable'].initial = True


class SellerPartForm(forms.ModelForm):
    class Meta:
        model = SellerPart
        exclude = ['manufacturer_part', 'data_source', ]

    new_seller = forms.CharField(max_length=128, label='-or- Create new seller (leave blank if selecting)', required=False)
    field_order = ['seller', 'new_seller', 'unit_cost', 'nre_cost', 'lead_time_days', 'minimum_order_quantity', 'minimum_pack_quantity', ]

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.manufacturer_part = kwargs.pop('manufacturer_part', None)
        self.base_fields['unit_cost'] = forms.DecimalField(required=True, decimal_places=4, max_digits=17)
        self.base_fields['nre_cost'] = forms.DecimalField(required=True, decimal_places=4, max_digits=17, label='NRE cost')

        instance = kwargs.get('instance')
        if instance:
            initial = kwargs.get('initial', {})
            initial['unit_cost'] = instance.unit_cost.amount
            initial['nre_cost'] = instance.nre_cost.amount
            kwargs['initial'] = initial
        super(SellerPartForm, self).__init__(*args, **kwargs)
        if self.manufacturer_part is not None:
            self.instance.manufacturer_part = self.manufacturer_part
        self.fields['seller'].queryset = Seller.objects.filter(organization=self.organization).order_by('name')
        self.fields['seller'].required = False

    def clean(self):
        cleaned_data = super(SellerPartForm, self).clean()
        seller = cleaned_data.get('seller')
        new_seller = cleaned_data.get('new_seller')
        unit_cost = cleaned_data.get('unit_cost')
        nre_cost = cleaned_data.get('nre_cost')
        if unit_cost is None:
            raise forms.ValidationError("Invalid unit cost.", code='invalid')
        self.instance.unit_cost = Money(unit_cost, self.organization.currency)

        if nre_cost is None:
            raise forms.ValidationError("Invalid NRE cost.", code='invalid')
        self.instance.nre_cost = Money(nre_cost, self.organization.currency)

        if seller and new_seller:
            raise forms.ValidationError("Cannot have a seller and a new seller.", code='invalid')
        elif new_seller:
            obj, created = Seller.objects.get_or_create(name__iexact=new_seller, organization=self.organization, defaults={'name': new_seller})
            self.cleaned_data['seller'] = obj
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
        self.fields['code'].validators.extend([MaxLengthValidator(self.organization.number_class_code_len), MinLengthValidator(self.organization.number_class_code_len)])

    def clean_name(self):
        cleaned_data = super(PartClassForm, self).clean()
        name = cleaned_data.get('name')
        try:
            part_class_with_name = PartClass.objects.get(name__iexact=name, organization=self.organization)
            if part_class_with_name and self.instance and self.instance.id != part_class_with_name.id:
                validation_error = forms.ValidationError("Part class with name {} is already defined.".format(name), code='invalid')
                self.add_error('name', validation_error)
        except PartClass.DoesNotExist:
            pass
        return name

    def clean_code(self):
        cleaned_data = super(PartClassForm, self).clean()
        code = cleaned_data.get('code')
        if PartClass.objects.filter(code=code, organization=self.organization).exclude(pk=self.instance.pk).count() > 0:
            validation_error = forms.ValidationError(f"Part class with code {code} is already defined.", code='invalid')
            self.add_error('code', validation_error)
        return code

    def clean(self):
        cleaned_data = super(PartClassForm, self).clean()
        cleaned_data['organization_id'] = self.organization
        self.instance.organization = self.organization
        return cleaned_data


PartClassFormSet = forms.formset_factory(PartClassForm, extra=2, can_delete=True)


class PartClassSelectionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartClassSelectionForm, self).__init__(*args, **kwargs)
        self.fields['part_class'] = forms.CharField(required=False,
                                                    widget=AutocompleteTextInput(attrs={'placeholder': 'Select a part class.'},
                                                                                 autocomplete_submit=True,
                                                                                 queryset=PartClass.objects.filter(organization=self.organization)))

    def clean_part_class(self):
        part_class = self.cleaned_data['part_class']
        if part_class == '':
            return None

        try:
            return PartClass.objects.get(organization=self.organization, code=part_class.split(':')[0])
        except PartClass.DoesNotExist:
            pc = PartClass.objects.filter(name__icontains=part_class).order_by('name').first()
            if pc is not None:
                return pc
            else:
                self.add_error('part_class', 'Select a valid part class.')
        return None


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
            headers = [h.lower().replace('\ufeff', '') for h in next(reader)]

            csv_headers = PartClassesCSVHeaders()

            try:
                # Issue warning if unrecognized column header names appear in file.
                csv_headers.validate_header_names(headers)
            except CSVHeaderError as e:
                self.warnings.append(e.__str__() + ". Column(s) ignored.")

            try:
                # Make sure that required columns appear in the file, then convert whatever
                # header synonym names were used to default header names.
                hdr_assertions = [
                    ('comment', 'description', 'mex'),  # MUTUALLY EXCLUSIVE part_class or part_number but not both
                    ('code', 'in'),  # CONTAINS revision
                    ('name', 'in'),  # CONTAINS name
                ]
                csv_headers.validate_header_assertions(headers, hdr_assertions)
                headers = csv_headers.get_defaults_list(headers)
            except CSVHeaderError as e:
                raise ValidationError(e.__str__() + ". Uploading stopped. No part classes uploaded.", code='invalid')

            row_count = 1  # Skip over header row
            for row in reader:
                row_count += 1
                part_class_data = {}

                for idx, hdr in enumerate(headers):
                    if idx == len(row): break
                    part_class_data[hdr] = row[idx]

                name = csv_headers.get_val_from_row(part_class_data, 'name')
                code = csv_headers.get_val_from_row(part_class_data, 'code')
                description = csv_headers.get_val_from_row(part_class_data, 'description')
                comment = csv_headers.get_val_from_row(part_class_data, 'comment')

                try:
                    if code is None:
                        validation_error = forms.ValidationError(
                            "Part class 'code' in row {} does not have a value. Uploading of this part class skipped.".format(row_count),
                            code='invalid')
                        self.add_error(None, validation_error)
                        continue
                    elif len(code) != self.organization.number_class_code_len:
                        validation_error = forms.ValidationError(
                            "Length of part class 'code' in row {} is different than the organization class length {}. Uploading of this part class skipped.".format(row_count, self.organization.number_class_code_len),
                            code='invalid')
                        self.add_error(None, validation_error)
                        continue

                    description_or_comment = ''
                    if description is not None:
                        description_or_comment = description
                    elif comment is not None:
                        description_or_comment = comment
                    PartClass.objects.create(code=code, name=name, comment=description_or_comment, organization=self.organization)
                    self.successes.append("Part class {0} {1} on row {2} created.".format(code, name, row_count))

                except IntegrityError:
                    validation_error = forms.ValidationError(
                        "Part class {0} {1} on row {2} is already defined. Uploading of this part class skipped.".format(code, name, row_count),
                        code='invalid')
                    self.add_error(None, validation_error)

        except UnicodeDecodeError as e:
            self.add_error(None, forms.ValidationError("CSV File Encoding error, try encoding your file as utf-8, and upload again. \
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

            # Handle utf-8-sig encoding
            if "\ufeff" in headers[0]:
                reader = csv.reader(codecs.iterdecode(file, 'utf-8-sig'), dialect)
                headers = [h.lower() for h in next(reader)]

            csv_headers = self.organization.part_list_csv_headers()

            try:
                # Issue warning if unrecognized column header names appear in file.
                csv_headers.validate_header_names(headers)
            except CSVHeaderError as e:
                self.warnings.append(e.__str__() + ". Columns ignored.")

            try:
                # Make sure that required columns appear in the file, then convert whatever
                # header synonym names were used to default header names.
                hdr_assertions = [
                    ('part_class', 'part_number', 'or'), # part_class OR part_number
                    ('revision', 'in'), # CONTAINS revision
                    ('value', 'value_units', 'and', 'description', 'or'), # (value AND value units) OR description
                ]
                csv_headers.validate_header_assertions(headers, hdr_assertions)
                headers = csv_headers.get_defaults_list(headers)
            except CSVHeaderError as e:
                raise ValidationError(e.__str__() + ". Uploading stopped. No parts uploaded.", code='invalid')

            row_count = 1  # Skip over header row
            for row in reader:
                row_count += 1
                part_data = {}

                for idx, hdr in enumerate(headers):
                    if idx == len(row): break
                    part_data[hdr] = row[idx]

                part_number = csv_headers.get_val_from_row(part_data, 'part_number')
                part_class = csv_headers.get_val_from_row(part_data, 'part_class')
                number_item = None
                number_variation = None
                revision = csv_headers.get_val_from_row(part_data, 'revision')
                mpn = csv_headers.get_val_from_row(part_data, 'mpn')
                mfg_name = csv_headers.get_val_from_row(part_data, 'mfg_name')
                description = csv_headers.get_val_from_row(part_data, 'description')
                value = csv_headers.get_val_from_row(part_data, 'value')
                value_units = csv_headers.get_val_from_row(part_data, 'value_units')
                seller_name = csv_headers.get_val_from_row(part_data, 'seller')
                unit_cost = csv_headers.get_val_from_row(part_data, 'unit_cost')
                nre_cost = csv_headers.get_val_from_row(part_data, 'part_nre_cost')
                moq = csv_headers.get_val_from_row(part_data, 'moq')
                mpq = csv_headers.get_val_from_row(part_data, 'minimum_pack_quantity')

                # Check part number for uniqueness. If part number not specified
                # then Part.save() will create one.
                if part_number:
                    if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
                        try:
                            (number_class, number_item, number_variation) = Part.parse_part_number(part_number, self.organization)
                            part_class = PartClass.objects.get(code=number_class, organization=self.organization)
                            Part.objects.get(number_class=part_class, number_item=number_item, number_variation=number_variation, organization=self.organization)
                            self.add_error(None, "Part number {0} in row {1} already exists. Uploading of this part skipped.".format(part_number, row_count))
                            continue
                        except AttributeError as e:
                            self.add_error(None, str(e) + " on row {}. Creation of this part skipped.".format(row_count))
                            continue
                        except PartClass.DoesNotExist:
                            self.add_error(None, "No part class found for part number {0} in row {1}. Creation of this part skipped.".format(part_number, row_count))
                            continue
                        except Part.DoesNotExist:
                            pass
                    else:
                        try:
                            number_item = part_number
                            Part.objects.get(number_class=None, number_item=number_item, number_variation=None, organization=self.organization)
                            self.add_error(None, f"Part number {part_number} in row {row_count} already exists. Uploading of this part skipped.")
                            continue
                        except Part.DoesNotExist:
                            pass
                elif part_class:
                    try:
                        part_class = PartClass.objects.get(code=part_data[csv_headers.get_default('part_class')], organization=self.organization)
                    except PartClass.DoesNotExist:
                        self.add_error(None, "Part class {0} in row {1} doesn't exist. Create part class on Settings > IndaBOM and try again."
                                             "Uploading of this part skipped.".format(part_data[csv_headers.get_default('part_class')], row_count))
                        continue
                else:
                    if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
                        self.add_error(None, "In row {} need to specify a part_class or part_number. Uploading of this part skipped.".format(row_count))
                    else:
                        self.add_error(None, "In row {} need to specify a part_number. Uploading of this part skipped.".format(row_count))
                    continue

                if not revision:
                    self.add_error(None, f"Missing revision in row {row_count}. Uploading of this part skipped.")
                    continue
                elif len(revision) > 4:
                    self.add_error(None, "Revision {0} in row {1} is more than the maximum 4 characters. "
                                         "Uploading of this part skipped.".format(part_data[csv_headers.get_default('revision')], row_count))
                    continue
                elif revision.isdigit() and int(revision) < 0:
                    self.add_error(None, "Revision {0} in row {1} cannot be a negative number. "
                                         "Uploading of this part skipped.".format(part_data[csv_headers.get_default('revision')], row_count))
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
                part_revision.revision = revision

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
                    prop_free_form = csv_headers.get_default(prop_free_form)
                    if prop_free_form in part_data:
                        setattr(part_revision, prop_free_form, part_data[prop_free_form])

                # Optional properties with choices for values:
                props_with_value_choices = {'package': PACKAGE_TYPES, 'interface': INTERFACE_TYPES}
                for k, v in props_with_value_choices.items():
                    k = csv_headers.get_default(k)
                    if k in part_data:
                        if is_valid_choice(part_data[k], v):
                            setattr(part_revision, k, part_data[k])
                        else:
                            self.warnings.append("'{0}' is an invalid choice of value for '{1}' for part in row {2} . Uploading of this property skipped. "
                                                       "Part will still be uploaded".format(part_data[k], k, row_count))

                # Optional properties with units:
                props_with_unit_choices = {
                    'value': VALUE_UNITS,
                    'supply_voltage': VOLTAGE_UNITS, 'power_rating': POWER_UNITS,
                    'voltage_rating': VOLTAGE_UNITS, 'current_rating': CURRENT_UNITS,
                    'temperature_rating': TEMPERATURE_UNITS, 'memory': MEMORY_UNITS,
                    'frequency': FREQUENCY_UNITS, 'wavelength': WAVELENGTH_UNITS,
                    'length': DISTANCE_UNITS, 'width': DISTANCE_UNITS,
                    'height': DISTANCE_UNITS, 'weight': WEIGHT_UNITS,
                }
                for k, v in props_with_unit_choices.items():
                    k = csv_headers.get_default(k)
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

                if self.organization.number_scheme == NUMBER_SCHEME_INTELLIGENT and number_item is None:
                    self.add_error(None, "Can't upload a part without a number_item header for part in row {}. Uploading of this part skipped.".format(row_count))
                    skip = True

                if skip:
                    continue

                PartForm = PartFormSemiIntelligent if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT else PartFormIntelligent
                part = Part(number_class=part_class, number_item=number_item, number_variation=number_variation, organization=self.organization)
                part_dict = model_to_dict(part)
                part_dict.update({'number_class': str(part.number_class)})
                pf = PartForm(data=part_dict, organization=self.organization)
                prf = PartRevisionForm(data=model_to_dict(part_revision))

                if pf.is_valid() and prf.is_valid():
                    part = pf.save(commit=False)
                    part.organization = self.organization
                    part.save()
                    part_revision = prf.save(commit=False)
                    part_revision.part = part
                    part_revision.save()

                    if mfg_name and mpn:
                        mfg, created = Manufacturer.objects.get_or_create(name__iexact=mfg_name, organization=self.organization, defaults={'name': mfg_name})
                        manufacturer_part, created = ManufacturerPart.objects.get_or_create(part=part, manufacturer_part_number=mpn, manufacturer=mfg)
                        if part.primary_manufacturer_part is None and manufacturer_part is not None:
                            part.primary_manufacturer_part = manufacturer_part
                            part.save()

                        if seller_name and unit_cost and nre_cost:
                            seller, created = Seller.objects.get_or_create(name__iexact=seller_name, organization=self.organization, defaults={'name': seller_name})
                            seller_part, created = SellerPart.objects.get_or_create(manufacturer_part=manufacturer_part, seller=seller, unit_cost=unit_cost, nre_cost=nre_cost, minimum_order_quantity=moq, minimum_pack_quantity=mpq)

                    self.successes.append("Part {0} on row {1} created.".format(part.full_part_number(), row_count))
                else:
                    for k, error in prf.errors.items():
                        for idx, msg in enumerate(error):
                            error[idx] = f"Error on Row {row_count}, {k}: " + msg
                        self.errors.update({k: error})
                    for k, error in pf.errors.items():
                        for idx, msg in enumerate(error):
                            error[idx] = f"Error on Row {row_count}, {k}: " + msg
                        self.errors.update({k: error})

                # part = Part.objects.create(number_class=part_class, number_item=number_item, number_variation=number_variation, organization=self.organization)

        except UnicodeDecodeError as e:
            self.add_error(None, forms.ValidationError("CSV File Encoding error, try encoding your file as utf-8, and upload again. \
                If this keeps happening, reach out to info@indabom.com with your csv file and we'll do our best to \
                fix your issue!", code='invalid'))
            logger.warning("UnicodeDecodeError: {}".format(e))
            raise ValidationError("Specific Error: {}".format(e), code='invalid')

        return cleaned_data


class PartFormIntelligent(forms.ModelForm):
    class Meta:
        model = Part
        exclude = ['number_class', 'number_variation', 'organization', 'google_drive_parent', ]
        help_texts = {
            'number_item': _('Enter a part number.'),
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartFormIntelligent, self).__init__(*args, **kwargs)
        self.fields['number_item'].required = True
        if self.instance and self.instance.id:
            self.fields['primary_manufacturer_part'].queryset = ManufacturerPart.objects.filter(part__id=self.instance.id).order_by('manufacturer_part_number')
        else:
            del self.fields['primary_manufacturer_part']
        for _, value in self.fields.items():
            value.widget.attrs['placeholder'] = value.help_text
            value.help_text = ''


class PartFormSemiIntelligent(forms.ModelForm):
    class Meta:
        model = Part
        exclude = ['organization', 'google_drive_parent', ]
        help_texts = {
            'number_item': _('Auto generated if blank.'),
            'number_variation': 'Auto generated if blank.',
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartFormSemiIntelligent, self).__init__(*args, **kwargs)
        self.fields['number_item'].validators.append(alphanumeric)
        self.fields['number_class'] = forms.CharField(label='Part Number Class*', required=True, help_text='Select a number class.',
                                                      widget=AutocompleteTextInput(queryset=PartClass.objects.filter(organization=self.organization)))
        if self.instance and self.instance.id:
            self.fields['primary_manufacturer_part'].queryset = ManufacturerPart.objects.filter(part__id=self.instance.id).order_by('manufacturer_part_number')
        else:
            del self.fields['primary_manufacturer_part']
        for _, value in self.fields.items():
            value.widget.attrs['placeholder'] = value.help_text
            value.help_text = ''

    def clean_number_class(self):
        number_class = self.cleaned_data['number_class']
        try:
            return PartClass.objects.get(organization=self.organization, code=number_class.split(':')[0])
        except PartClass.DoesNotExist:
            self.add_error('number_class', f'Select an existing part class, or create `{number_class}` in Settings.')
        return None

    def clean(self):
        cleaned_data = super(PartFormSemiIntelligent, self).clean()
        number_item = cleaned_data.get('number_item')
        number_class = cleaned_data.get('number_class')
        number_variation = cleaned_data.get('number_variation')

        try:
            if number_class is not None and number_class.code != '':
                Part.verify_format_number_class(number_class.code, self.organization)
        except AttributeError as e:
            validation_error = forms.ValidationError(str(e), code='invalid')
            self.add_error('number_class', validation_error)

        try:
            if number_item is not None and number_item != '':
                Part.verify_format_number_item(number_item, self.organization)
        except AttributeError as e:
            validation_error = forms.ValidationError(str(e), code='invalid')
            self.add_error('number_item', validation_error)

        try:
            if number_variation:
                Part.verify_format_number_variation(number_variation, self.organization)
        except AttributeError as e:
            validation_error = forms.ValidationError(str(e), code='invalid')
            self.add_error('number_variation', validation_error)

        try:
            Part.objects.get(
                number_class=number_class,
                number_item=number_item,
                number_variation=number_variation,
                organization=self.organization
            )
            validation_error = forms.ValidationError(
                ("Part number {0}-{1}-{2} already in use.".format(number_class.code, number_item, number_variation)),
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

        self.fields['revision'].initial = 1
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
            validation_error = forms.ValidationError("Must specify either a description or both value and value units.", code='invalid')
            self.add_error('description', validation_error)
            self.add_error('value', validation_error)

        for key in self.fields.keys():
            if '_units' in key:
                value_name = str.replace(key, '_units', '')
                value = cleaned_data.get(value_name)
                units = cleaned_data.get(key)
                if not value and units:
                    self.add_error(key, forms.ValidationError(f"Cannot specify {units} without an accompanying value", code='invalid'))
                elif units and not units:
                    self.add_error(key, forms.ValidationError(f"Cannot specify value {units} without an accompanying units", code='invalid'))

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
        self.part = Part.objects.get(id=self.part_id)
        self.part_revision = self.part.latest()
        self.unusable_part_rev_ids = [pr.id for pr in self.part_revision.where_used_full()]
        self.unusable_part_rev_ids.append(self.part_revision.id)
        super(AddSubpartForm, self).__init__(*args, **kwargs)
        self.fields['subpart_part_number'] = forms.CharField(required=True, label="Subpart part number",
                                                    widget=AutocompleteTextInput(attrs={'placeholder': 'Select a part.'},
                                                                                 queryset=Part.objects.filter(organization=self.organization).exclude(id=self.part_id),
                                                                                 verbose_string_function=Part.verbose_str))

    def clean_count(self):
        count = self.cleaned_data['count']
        if not count:
            count = 1
        elif count < 1:
            validation_error = forms.ValidationError("Subpart quantity must be > 0.", code='invalid')
            self.add_error('count', validation_error)
        return count

    def clean_reference(self):
        reference = self.cleaned_data['reference']
        reference = stringify_list(listify_string(reference))
        return reference

    def clean_subpart_part_number(self):
        subpart_part_number = self.cleaned_data['subpart_part_number']

        if not subpart_part_number:
            validation_error = forms.ValidationError("Must specify a part number.", code='invalid')
            self.add_error('subpart_part_number', validation_error)

        try:
            if self.organization.number_scheme == NUMBER_SCHEME_INTELLIGENT:
                part = Part.objects.get(number_item=subpart_part_number, organization=self.organization)
            else:
                (number_class, number_item, number_variation) = Part.parse_partial_part_number(subpart_part_number, self.organization, validate=False)
                part_class = PartClass.objects.get(code=number_class, organization=self.organization)
                part = Part.objects.get(number_class=part_class, number_item=number_item, number_variation=number_variation, organization=self.organization)
            self.subpart_part = part.latest()
            if self.subpart_part is None:
                self.add_error('subpart_part_number', f"No part revision exists for part {part.full_part_number()}. Create a revision before adding to an assembly.")
                return subpart_part_number
            if self.subpart_part.id in self.unusable_part_rev_ids:
                validation_error = forms.ValidationError("Infinite recursion! Can't add a part to its self.", code='invalid')
                self.add_error('subpart_part_number', validation_error)
        except AttributeError as e:
            validation_error = forms.ValidationError("Ill-formed subpart part number... " + str(e) + ".", code='invalid')
            self.add_error('subpart_part_number', validation_error)
        except PartClass.DoesNotExist:
            validation_error = forms.ValidationError(f"No part class for in given part number {subpart_part_number}.", code='invalid')
            self.add_error('subpart_part_number', validation_error)
        except Part.DoesNotExist:
            validation_error = forms.ValidationError(f"No part found with given part number {subpart_part_number}.", code='invalid')
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
            validation_error = forms.ValidationError(("Must specify a parent part number."), code='invalid')
            self.add_error('parent_part_number', validation_error)
        if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
            try:
                (number_class, number_item, number_variation) = Part.parse_part_number(parent_part_number, self.organization)
                self.parent_part = Part.objects.get(
                    number_class=PartClass.objects.get(code=number_class, organization=self.organization),
                    number_item=number_item,
                    number_variation=number_variation,
                    organization=self.organization
                )
            except AttributeError as e:
                validation_error = forms.ValidationError("Ill-formed parent part number... " + str(e) + ".", code='invalid')
                self.add_error('parent_part_number', validation_error)
            except PartClass.DoesNotExist:
                validation_error = forms.ValidationError(("No part class found for given parent part number {}.".format(parent_part_number)), code='invalid')
                self.add_error('parent_part_number', validation_error)
            except Part.DoesNotExist:
                validation_error = forms.ValidationError(("No part found with given parent part number {}.".format(parent_part_number)), code='invalid')
                self.add_error('parent_part_number', validation_error)
        else:
            try:
                self.parent_part = Part.objects.get(
                    number_class=None,
                    number_item=parent_part_number,
                    number_variation=None,
                    organization=self.organization
                )
            except Part.DoesNotExist:
                validation_error = forms.ValidationError(("No part found with given parent part number {}.".format(parent_part_number)), code='invalid')
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

            # Handle utf-8-sig encoding
            if len(headers) > 0 and "\ufeff" in headers[0]:
                reader = csv.reader(codecs.iterdecode(file, 'utf-8-sig'), dialect)
                headers = [h.lower() for h in next(reader)]
            elif len(headers) == 0:
                self.warnings.append("No headers found in CSV file.")

            csv_headers = BOMIndentedCSVHeaders()

            try:
                # Issue warning if unrecognized column header names appear in file.
                csv_headers.validate_header_names(headers)
            except CSVHeaderError as e:
                self.warnings.append(e.__str__() + ". Columns ignored.")

            try:
                # Make sure that required columns appear in the file, then convert whatever
                # header synonym names were used to default header names.
                hdr_assertions = [
                    ('part_number', 'manufacturer_part_number', 'or'),  # part_class OR part_number
                    ('quantity', 'in'),  # CONTAINS quantity
                ]
                csv_headers.validate_header_assertions(headers, hdr_assertions)
                headers = csv_headers.get_defaults_list(headers)
            except CSVHeaderError as e:
                raise ValidationError(e.__str__() + ". Uploading stopped. No subparts uploaded.", code='invalid')

            parent_part_revision = self.parent_part.latest()
            if parent_part_revision.assembly is None:
                parent_part_revision.assembly = Assembly.objects.create()
                parent_part_revision.save()

            row_count = 1  # Skip over header row
            for row in reader:
                row_count += 1
                part_dict = {}

                for idx, hdr in enumerate(headers):
                    if idx == len(row): break
                    part_dict[hdr] = row[idx]

                dnp = csv_headers.get_val_from_row(part_dict, 'dnp')
                do_not_load = dnp in ['y', 'x', 'dnp', 'dnl', 'yes', 'true', ]
                part_number = csv_headers.get_val_from_row(part_dict, 'part_number')
                revision = csv_headers.get_val_from_row(part_dict, 'revision')
                mpn = csv_headers.get_val_from_row(part_dict, 'mpn')
                count = csv_headers.get_val_from_row(part_dict, 'count')
                reference = csv_headers.get_val_from_row(part_dict, 'reference')
                reference_list = listify_string(reference) if reference else []

                if len(reference_list) != len(set(reference_list)):
                    self.add_error(None, f"Duplicate reference designators '{reference}' for subpart on row {row_count}. Uploading of this subpart skipped.")
                    continue

                if count is None:
                    count = 1
                elif not count.isdigit() or int(count) < 1:
                    self.add_error(None, f"Quantity for subpart on row {row_count} must be a number > 0. Uploading of this subpart skipped.")
                    continue
                else:
                    count = int(count)

                # First try to add the subpart_part based on the part number
                subpart_part = None
                if part_number:
                    if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
                        try:
                            (number_class, number_item, number_variation) = Part.parse_partial_part_number(part_number, self.organization)
                            subparts = Part.objects.filter(
                                number_class__code=number_class,
                                number_item=number_item,
                                number_variation=number_variation,
                                organization=self.organization)
                        except AttributeError as e:
                            self.add_error(None, str(e) + " on row {}. Uploading of this subpart skipped. Couldn't parse part number.".format(row_count))
                            continue
                    else:
                        subparts = Part.objects.filter(number_class=None, number_item=part_number, number_variation=None, organization=self.organization)

                    if len(subparts) == 0:
                        self.add_error(None,
                                       "Part {0} for subpart on row {1} does not exist, you must create the part "
                                       "before it can be added as a subpart. "
                                       "Uploading of this subpart skipped.".format(part_number, row_count))
                        continue
                    elif len(subparts) > 1:
                        self.add_error(None,
                                       "Found {0} entries for part {1} for subpart on row {2}. This should not happen. "
                                       "Please let info@indabom.com know. Uploading of this subpart skipped.".format(
                                           len(subparts), row_count, part_number))
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
                    self.add_error(None, f"Couldn't parse part on row {row_count} with data: {row}.")
                    continue

                # We have the subpart_part at this point, make sure we don't have infinite recursion
                if self.parent_part == subpart_part:
                    # Silently skip any rows that contain the parent part
                    continue

                subpart_revision = subpart_part.latest()
                if not subpart_revision:
                    self.add_error(None, f"Part {part_number} has no revisions. Cannot add it to a BOM. Uploading of this subpart skipped.")
                    continue

                if revision:
                    revs = subpart_part.revisions().filter(revision=revision).order_by('-timestamp')
                    if revs.count() > 0:
                        subpart_revision = revs[0]
                    else:
                        rev_options = revs.values_list('revision', flat=True)
                        self.add_error(None, f"Found part {part_number}, but could not match revision {revision}. Options are: {rev_options}. Uploading of this subpart skipped.")
                        continue

                contains_parent = False
                indented_bom = subpart_revision.indented()
                for _, sp in indented_bom.parts.items():  # Make sure the subpart does not contain the parent - infinite recursion!
                    if sp.part_revision == parent_part_revision:
                        contains_parent = True
                if contains_parent:
                    self.add_error(None, f"Uploaded part {part_number} contains parent part in its assembly. Cannot add {part_number} as it would cause infinite recursion. Uploading of this subpart skipped.")
                    continue

                # Decide if the current subpart (i.e., for current row) should:
                # a. Be combined with a previously seen subpart
                # b. Rejected because it is a duplicate of a previously seen subpart
                # c. Be imported as a new subpart
                #
                # Do (a) if current subpart's part matches a previous subpart's part except for their designators. Combine if
                # current subpart's designators either don't overlap or both subparts do not have designators. As
                # part of the combing, adjust subpart count to represent the total number of combined subparts. Reject
                # current subpart if it has a designator but the previous subpart does not (or vice versa).
                #
                # Do (b) if current subpart's part matches a previous subpart's part - AND - their designators match (this
                # includes the case where the current and previous subpart do not have any designators.
                #
                # Do (c) if the current subpart's part does not match any previous subpart's part - OR - if there is a
                # match except for their DNL flags. Want to have separate entries in the BOM for the to-be-loaded and
                # the do-not-load versions of these subparts.
                #

                existing_subpart_qs = parent_part_revision.assembly.subparts.filter(
                    part_revision=subpart_revision,
                    do_not_load=do_not_load # Include DNL flag in filter per (c) above
                )

                if len(existing_subpart_qs) == 0:

                    if len(reference_list) != count and len(reference_list) > 0:
                        self.warnings.append(
                            f"The number of reference designators ({len(reference_list)}) for subpart {subpart_part} on row {row_count} does not match the subpart quantity ({count}). Quantity automatically adjusted.")
                        count = len(reference_list)

                    new_subpart = Subpart.objects.create(
                        part_revision=subpart_revision,
                        count=count,
                        reference=reference,
                        do_not_load=do_not_load
                    )

                    AssemblySubparts.objects.get_or_create(assembly=parent_part_revision.assembly, subpart=new_subpart)

                    info_msg = f"Added subpart {part_number} on row {row_count} "
                    if reference:
                        info_msg += f"with reference designators {reference} "
                    info_msg += f"to parent part {self.parent_part}."
                    self.successes.append(info_msg)

                else:
                    existing_subpart = existing_subpart_qs[0]
                    existing_subpart_reference_list = listify_string(existing_subpart.reference)
                    if len(reference_list) == 0 or len(existing_subpart_reference_list) == 0:
                        self.add_error(None,
                            f"Cannot combine subpart {part_number} on row {row_count} with existing matching subpart because one or both subparts have empty reference designators. Subpart combining skipped.")
                    else:
                        combine = True
                        for ref in reference_list:
                            if ref in existing_subpart_reference_list:
                                combine = False
                                self.warnings.append(
                                    f"Cannot combine subpart subpart {part_number} on row {row_count} with existing subpart because one or more reference designators are the same. Subpart combining skipped.")
                                break
                        if combine:
                            info_msg = f"Combined reference designators {stringify_list(reference_list)} for subpart on row {row_count} with existing subpart {part_number}."
                            existing_subpart_reference_list = existing_subpart_reference_list + reference_list
                            existing_subpart.reference = stringify_list(existing_subpart_reference_list)
                            existing_subpart.count = len(existing_subpart_reference_list) if len(existing_subpart_reference_list) > 0 else '1'
                            existing_subpart.save()
                            self.successes.append(info_msg)

        except UnicodeDecodeError as e:
            self.add_error(None, forms.ValidationError("CSV File Encoding error, try encoding your file as utf-8, and upload again. \
                If this keeps happening, reach out to info@indabom.com with your csv file and we'll do our best to \
                fix your issue!", code='invalid'))
            logger.warning("UnicodeDecodeError: {}".format(e))
            raise ValidationError("Specific Error: {}".format(e),
                                  code='invalid')

        return cleaned_data


class FileForm(forms.Form):
    file = forms.FileField()
