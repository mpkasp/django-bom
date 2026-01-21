import codecs
import csv
import logging

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import IntegrityError
from django.forms.models import model_to_dict
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from .constants import (
    NUMBER_SCHEME_INTELLIGENT,
    NUMBER_SCHEME_SEMI_INTELLIGENT,
    ROLE_TYPE_VIEWER,
    PART_REVISION_PROPERTY_TYPE_DECIMAL,
    PART_REVISION_PROPERTY_TYPE_BOOLEAN,
)
from .csv_headers import (
    CSVHeaderError,
    PartClassesCSVHeaders,
)
from .form_fields import AutocompleteTextInput
from .models import (
    Assembly,
    AssemblySubparts,
    Manufacturer,
    ManufacturerPart,
    Part,
    PartClass,
    PartRevision,
    PartRevisionProperty,
    PartRevisionPropertyDefinition,
    QuantityOfMeasure,
    Seller,
    SellerPart,
    Subpart,
    UnitDefinition,
    User,
    get_user_meta_model,
    get_organization_model,
)
from .utils import listify_string, stringify_list
from .validators import alphanumeric

logger = logging.getLogger(__name__)
Organization = get_organization_model()
UserMeta = get_user_meta_model()


# ==========================================
# MIXINS & BASE CLASSES
# ==========================================

class OrganizationFormMixin:
    """Mixin to handle organization injection."""

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super().__init__(*args, **kwargs)


class PlaceholderMixin:
    """Mixin to move help_text to widget placeholders automatically."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for _, field in self.fields.items():
            if field.help_text:
                field.widget.attrs['placeholder'] = field.help_text
                field.help_text = ''


class BaseCSVForm(OrganizationFormMixin, forms.Form):
    """Abstract Base Class for CSV Import Forms to DRY up file handling."""
    file = forms.FileField(required=False)

    def get_csv_headers_handler(self):
        """Subclasses must return the CSV headers handler instance."""
        raise NotImplementedError

    def get_header_assertions(self):
        """Subclasses must return a list of header assertions."""
        raise NotImplementedError

    def process_row(self, row_data, row_count, headers_handler):
        """Subclasses implement specific row logic here."""
        raise NotImplementedError

    def clean(self):
        cleaned_data = super().clean()
        file = self.cleaned_data.get('file')
        self.successes = []
        self.warnings = []

        if not file:
            return cleaned_data

        try:
            # Decode and Sniff
            csvline_decoded = file.readline().decode('utf-8')
            dialect = csv.Sniffer().sniff(csvline_decoded)
            file.open()

            # handle BOM
            reader = csv.reader(codecs.iterdecode(file, 'utf-8'), dialect)
            headers = [h.lower() for h in next(reader)]

            if headers and "\ufeff" in headers[0]:
                file.seek(0)
                reader = csv.reader(codecs.iterdecode(file, 'utf-8-sig'), dialect)
                headers = [h.lower() for h in next(reader)]

            # Header Validation
            csv_headers = self.get_csv_headers_handler()

            try:
                csv_headers.validate_header_names(headers)
            except CSVHeaderError as e:
                self.warnings.append(f"{e}. Columns ignored.")

            try:
                csv_headers.validate_header_assertions(headers, self.get_header_assertions())
                headers = csv_headers.get_defaults_list(headers)
            except CSVHeaderError as e:
                raise ValidationError(f"{e}. Uploading stopped.", code='invalid')

            # Row Processing
            row_count = 1
            for row in reader:
                row_count += 1
                row_data = {}
                for idx, hdr in enumerate(headers):
                    if idx < len(row):
                        row_data[hdr] = row[idx]

                self.process_row(row_data, row_count, csv_headers)

        except UnicodeDecodeError as e:
            self.add_error(None, forms.ValidationError(
                "CSV File Encoding error. Please encode as utf-8.", code='invalid'
            ))
            logger.warning(f"UnicodeDecodeError: {e}")
            raise ValidationError(f"Specific Error: {e}", code='invalid')
        except Exception as e:
            # Catch-all for unexpected errors during processing to ensure they bubble up cleanly
            if isinstance(e, ValidationError):
                raise e
            logger.error(f"Error processing CSV: {e}")
            self.add_error(None, f"An unexpected error occurred: {str(e)}")

        return cleaned_data


# ==========================================
# USER & AUTH FORMS
# ==========================================

class UserModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        parts = [f"[{user.username}]"]
        if user.first_name: parts.append(user.first_name)
        if user.last_name: parts.append(user.last_name)
        if user.email: parts.append(f", {user.email}")
        return " ".join(parts)


class UserCreateForm(UserCreationForm):
    first_name = forms.CharField(required=True)
    last_name = forms.CharField(required=True)
    email = forms.EmailField(required=True)

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account with this email address already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=commit)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        if commit:
            user.save()
        return user


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']


class UserAddForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = UserMeta
        fields = ['role']

    field_order = ['username', 'role']
    username = forms.CharField(initial=None, required=False)

    def __init__(self, *args, **kwargs):
        hide_username = kwargs.pop('exclude_username', False)
        super().__init__(*args, **kwargs)
        self.fields['role'].required = False
        if hide_username and self.instance.pk:
            self.fields['username'].widget = forms.HiddenInput()
            self.fields['username'].initial = self.instance.user.username

    def clean_username(self):
        username = self.cleaned_data.get('username')
        try:
            user = User.objects.get(username=username)
            user_meta = user.bom_profile()
            if user_meta.organization == self.organization:
                self.add_error('username', f"User '{username}' already belongs to {self.organization}.")
            elif user_meta.organization:
                self.add_error('username', f"User '{username}' belongs to another organization.")
        except User.DoesNotExist:
            self.add_error('username', f"User '{username}' does not exist.")
        return username

    def clean_role(self):
        return self.cleaned_data.get('role') or ROLE_TYPE_VIEWER

    def save(self, commit=True):
        username = self.cleaned_data.get('username')
        user = User.objects.get(username=username)
        user_meta = user.bom_profile()
        user_meta.organization = self.organization
        user_meta.role = self.cleaned_data.get('role')
        user_meta.save()
        return user_meta


class UserMetaForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = UserMeta
        exclude = ['user']

    def save(self, commit=True):
        self.instance.organization = self.organization
        if commit:
            self.instance.save()
        return self.instance


# ==========================================
# ORGANIZATION FORMS
# ==========================================

class OrganizationBaseForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ['name', 'number_class_code_len', 'number_item_len', 'number_variation_len']
        labels = {
            "name": "Organization Name",
            "number_class_code_len": "Number Class Code Length (C)",
            "number_item_len": "Number Item Length (N)",
            "number_variation_len": "Number Variation Length (V)",
        }


class OrganizationCreateForm(OrganizationBaseForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.data.get('number_scheme') == NUMBER_SCHEME_INTELLIGENT:
            self.data = self.data.copy()
            self.data.update({
                'number_class_code_len': 3,
                'number_item_len': 128,
                'number_variation_len': 2
            })

    class Meta(OrganizationBaseForm.Meta):
        fields = OrganizationBaseForm.Meta.fields + ['number_scheme']


class OrganizationForm(OrganizationBaseForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user and self.instance.owner == user:
            # Only show owner selection if current user is owner
            admin_ids = UserMeta.objects.filter(
                organization=self.instance, role='A'
            ).values_list('user', flat=True)
            user_qs = User.objects.filter(id__in=admin_ids).order_by('first_name', 'last_name')

            self.fields['owner'] = UserModelChoiceField(
                queryset=user_qs, label='Owner', initial=self.instance.owner, required=True
            )


class OrganizationFormEditSettings(OrganizationForm):
    class Meta(OrganizationBaseForm.Meta):
        fields = ['name', 'owner', 'currency']


class OrganizationNumberLenForm(OrganizationBaseForm):
    class Meta(OrganizationBaseForm.Meta):
        fields = ['number_class_code_len', 'number_item_len', 'number_variation_len']

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.get('instance')
        super().__init__(*args, **kwargs)


# ==========================================
# PART & MFG FORMS
# ==========================================

class PartInfoForm(forms.Form):
    quantity = forms.IntegerField(label='Quantity for Est Cost', min_value=1)


class ManufacturerForm(forms.ModelForm):
    class Meta:
        model = Manufacturer
        exclude = ['organization']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = False


class ManufacturerPartForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = ManufacturerPart
        exclude = ['part']

    field_order = ['manufacturer_part_number', 'manufacturer']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['manufacturer'].required = False
        self.fields['manufacturer_part_number'].required = False
        self.fields['manufacturer'].queryset = Manufacturer.objects.filter(
            organization=self.organization
        ).order_by('name')
        self.fields['mouser_disable'].initial = True


class SellerForm(forms.ModelForm):
    class Meta:
        model = Seller
        exclude = ['organization']


class SellerPartForm(OrganizationFormMixin, forms.ModelForm):
    new_seller = forms.CharField(max_length=128, label='-or- Create new seller', required=False)

    class Meta:
        model = SellerPart
        exclude = ['manufacturer_part', 'data_source']

    field_order = ['seller', 'new_seller', 'unit_cost', 'nre_cost', 'lead_time_days', 'minimum_order_quantity',
                   'minimum_pack_quantity']

    def __init__(self, *args, **kwargs):
        self.manufacturer_part = kwargs.pop('manufacturer_part', None)
        super().__init__(*args, **kwargs)

        # Currency formatting
        self.fields['unit_cost'] = forms.DecimalField(required=True, decimal_places=4, max_digits=17)
        self.fields['nre_cost'] = forms.DecimalField(required=True, decimal_places=4, max_digits=17, label='NRE cost')

        if self.instance.pk:
            self.initial['unit_cost'] = self.instance.unit_cost.amount
            self.initial['nre_cost'] = self.instance.nre_cost.amount

        if self.manufacturer_part:
            self.instance.manufacturer_part = self.manufacturer_part

        self.fields['seller'].queryset = Seller.objects.filter(organization=self.organization).order_by('name')
        self.fields['seller'].required = False

    def clean(self):
        cleaned_data = super().clean()
        seller = cleaned_data.get('seller')
        new_seller = cleaned_data.get('new_seller')

        # Handle Money fields
        for field in ['unit_cost', 'nre_cost']:
            val = cleaned_data.get(field)
            if val is None:
                self.add_error(field, "Invalid cost.")
            else:
                setattr(self.instance, field, Money(val, self.organization.currency))

        if seller and new_seller:
            raise forms.ValidationError("Cannot have a seller and a new seller.")
        elif new_seller:
            obj, _ = Seller.objects.get_or_create(
                name__iexact=new_seller, organization=self.organization,
                defaults={'name': new_seller}
            )
            cleaned_data['seller'] = obj
        elif not seller:
            raise forms.ValidationError("Must specify a seller.")

        return cleaned_data


class QuantityOfMeasureForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = QuantityOfMeasure
        fields = ['name']

    def clean(self):
        cleaned_data = super().clean()
        self.instance.organization = self.organization
        return cleaned_data


class UnitDefinitionForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = UnitDefinition
        fields = ['name', 'symbol', 'base_multiplier', ]

    def clean(self):
        cleaned_data = super().clean()
        self.instance.organization = self.organization
        return cleaned_data


UnitDefinitionFormSet = forms.modelformset_factory(
    UnitDefinition,
    form=UnitDefinitionForm,
    can_delete=True,
    extra=0
)


class PartRevisionPropertyDefinitionForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = PartRevisionPropertyDefinition
        fields = ['name', 'type', 'required', 'quantity_of_measure']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['quantity_of_measure'].queryset = QuantityOfMeasure.objects.available_to(
            self.organization).order_by('name')


class PartRevisionPropertyDefinitionSelectForm(OrganizationFormMixin, forms.Form):
    property_definition = forms.ModelChoiceField(queryset=PartRevisionPropertyDefinition.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['property_definition'].queryset = PartRevisionPropertyDefinition.objects.available_to(
            self.organization).order_by('name')


PartRevisionPropertyDefinitionFormSet = forms.formset_factory(
    PartRevisionPropertyDefinitionSelectForm,
    can_delete=True,
    extra=0
)


class PartClassForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = PartClass
        fields = ['code', 'name', 'comment']

    def __init__(self, *args, **kwargs):
        self.ignore_unique_constraint = kwargs.pop('ignore_unique_constraint', False)
        super().__init__(*args, **kwargs)
        self.fields['code'].required = False
        self.fields['name'].required = False
        self.fields['code'].validators.extend([
            MaxLengthValidator(self.organization.number_class_code_len),
            MinLengthValidator(self.organization.number_class_code_len)
        ])

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if not self.ignore_unique_constraint:
            if PartClass.objects.filter(name__iexact=name, organization=self.organization).exclude(
                    pk=self.instance.pk).exists():
                self.add_error('name', f"Part class with name {name} is already defined.")
        return name

    def clean_code(self):
        code = self.cleaned_data.get('code')
        if not self.ignore_unique_constraint:
            if PartClass.objects.filter(code=code, organization=self.organization).exclude(
                    pk=self.instance.pk).exists():
                self.add_error('code', f"Part class with code {code} is already defined.")
        return code

    def clean(self):
        cleaned_data = super().clean()
        self.instance.organization = self.organization
        return cleaned_data


PartClassFormSet = forms.formset_factory(PartClassForm, extra=2, can_delete=True)


class PartClassSelectionForm(OrganizationFormMixin, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['part_class'] = forms.CharField(
            required=False,
            widget=AutocompleteTextInput(
                attrs={'placeholder': 'Select a part class.'},
                autocomplete_submit=True,
                queryset=PartClass.objects.filter(organization=self.organization)
            )
        )

    def clean_part_class(self):
        pc_input = self.cleaned_data['part_class']
        if not pc_input:
            return None

        try:
            return PartClass.objects.get(organization=self.organization, code=pc_input.split(':')[0])
        except PartClass.DoesNotExist:
            pc = PartClass.objects.filter(name__icontains=pc_input).order_by('name').first()
            if pc:
                return pc
            self.add_error('part_class', 'Select a valid part class.')
        return None


# ==========================================
# PART FORMS
# ==========================================

class BasePartForm(OrganizationFormMixin, PlaceholderMixin, forms.ModelForm):
    """Base class for part forms to handle common init and placeholder logic."""

    def __init__(self, *args, **kwargs):
        self.ignore_part_class = kwargs.pop('ignore_part_class', False)
        self.ignore_unique_constraint = kwargs.pop('ignore_unique_constraint', False)
        super().__init__(*args, **kwargs)

        # Setup MFG Part Queryset if editing
        if self.instance.pk:
            self.fields['primary_manufacturer_part'].queryset = ManufacturerPart.objects.filter(
                part__id=self.instance.id
            ).order_by('manufacturer_part_number')
        elif 'primary_manufacturer_part' in self.fields:
            del self.fields['primary_manufacturer_part']


class PartFormIntelligent(BasePartForm):
    class Meta:
        model = Part
        exclude = ['number_class', 'number_variation', 'organization', 'google_drive_parent']
        help_texts = {'number_item': _('Enter a part number.')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['number_item'].required = True


class PartFormSemiIntelligent(BasePartForm):
    class Meta:
        model = Part
        exclude = ['organization', 'google_drive_parent', ]
        help_texts = {
            'number_item': _('Auto generated if blank.'),
            'number_variation': 'Auto generated if blank.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['number_item'].validators.append(alphanumeric)
        self.fields['number_class'] = forms.CharField(
            label='Part Number Class*', required=True,
            help_text='Select a number class.',
            widget=AutocompleteTextInput(queryset=PartClass.objects.filter(organization=self.organization))
        )

        # Convert ID to string for Autocomplete
        if self.initial.get('number_class'):
            try:
                self.initial['number_class'] = str(PartClass.objects.get(id=self.initial['number_class']))
            except PartClass.DoesNotExist:
                self.initial['number_class'] = ""

        if self.ignore_part_class:
            self.fields['number_class'].required = False

    def clean_number_class(self):
        if self.ignore_part_class: return None
        nc = self.cleaned_data['number_class']
        try:
            return PartClass.objects.get(organization=self.organization, code=nc.split(':')[0])
        except PartClass.DoesNotExist:
            self.add_error('number_class', f'Select an existing part class, or create `{nc}` in Settings.')
        return None

    def clean(self):
        cleaned_data = super().clean()
        n_item = cleaned_data.get('number_item')
        n_class = cleaned_data.get('number_class')
        n_var = cleaned_data.get('number_variation')

        # Format Verification
        try:
            if n_class and n_class.code: Part.verify_format_number_class(n_class.code, self.organization)
        except AttributeError as e:
            self.add_error('number_class', str(e))

        try:
            if n_item: Part.verify_format_number_item(n_item, self.organization)
        except AttributeError as e:
            self.add_error('number_item', str(e))

        try:
            if n_var: Part.verify_format_number_variation(n_var, self.organization)
        except AttributeError as e:
            self.add_error('number_variation', str(e))

        # Uniqueness Check
        if not self.ignore_unique_constraint:
            qs = Part.objects.filter(number_class=n_class, number_item=n_item, number_variation=n_var,
                                     organization=self.organization)
            if self.instance.pk: qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                self.add_error(None, f"Part number {n_class.code}-{n_item}-{n_var} already in use.")

        return cleaned_data


class PartRevisionForm(OrganizationFormMixin, PlaceholderMixin, forms.ModelForm):
    class Meta:
        model = PartRevision
        exclude = ['timestamp', 'assembly', 'part']
        help_texts = {'description': _('Additional part info, special instructions, etc.')}

    def __init__(self, *args, **kwargs):
        self.part_class = kwargs.pop('part_class', None)
        super().__init__(*args, **kwargs)
        self.fields['revision'].initial = 1
        self.fields['configuration'].required = False
        if not self.part_class and self.instance.pk:
            self.part_class = self.instance.part.number_class

        if self.part_class:
            self.property_definitions = self.part_class.property_definitions.all().order_by('name')
        elif self.organization.number_scheme == NUMBER_SCHEME_INTELLIGENT:
            self.property_definitions = PartRevisionPropertyDefinition.objects.available_to(self.organization).order_by(
                'name')
        else:
            self.property_definitions = PartRevisionPropertyDefinition.objects.none()

        self._init_dynamic_properties()

    def _init_dynamic_properties(self):
        """Dynamically add fields based on Property Definitions."""
        model_field = PartRevisionProperty._meta.get_field('value_raw')
        for pd in self.property_definitions:
            field_name = pd.form_field_name
            if self.organization.number_scheme == NUMBER_SCHEME_INTELLIGENT:
                req = False
            else:
                req = pd.required

            if pd.type == PART_REVISION_PROPERTY_TYPE_DECIMAL:
                self.fields[field_name] = forms.DecimalField(label=pd.name, required=req)
            elif pd.type == PART_REVISION_PROPERTY_TYPE_BOOLEAN:
                self.fields[field_name] = forms.BooleanField(label=pd.name, required=req)
            else:
                self.fields[field_name] = forms.CharField(label=pd.name, required=req,
                                                          max_length=model_field.max_length,
                                                          widget=forms.TextInput(
                                                              attrs={'maxlength': str(model_field.max_length)}))

            # Pre-fill
            prop = None
            if self.instance.pk:
                prop = self.instance.properties.filter(property_definition=pd).first()
                if prop: self.fields[field_name].initial = prop.value_raw

            # Unit Logic
            if pd.quantity_of_measure:
                unit_field = pd.form_unit_field_name
                units = UnitDefinition.objects.filter(quantity_of_measure=pd.quantity_of_measure)
                choices = [('', '---------')] + [(u.id, u.symbol) for u in units]
                self.fields[unit_field] = forms.ChoiceField(choices=choices, required=False, label=f"{pd.name} Unit")
                if self.instance.pk and prop and prop.unit_definition:
                    self.fields[unit_field].initial = prop.unit_definition.id

    def property_fields(self):
        """Yields property fields grouped with their corresponding unit fields."""
        for pd in self.property_definitions:
            yield {
                'property': self[pd.form_field_name],
                'unit': self[pd.form_unit_field_name] if pd.quantity_of_measure else None,
            }

    @property
    def property_field_names(self):
        """Returns a list of all field names associated with dynamic properties."""
        names = []
        for pd in self.property_definitions:
            names.append(pd.form_field_name)
            if pd.quantity_of_measure:
                names.append(pd.form_unit_field_name)
        return names

    def save_properties(self):
        for defn in self.property_definitions:
            val = self.cleaned_data.get(defn.form_field_name)
            if val not in (None, ''):
                unit_id = self.cleaned_data.get(defn.form_unit_field_name)
                unit = UnitDefinition.objects.get(id=unit_id) if unit_id else None
                PartRevisionProperty.objects.update_or_create(
                    part_revision=self.instance, property_definition=defn,
                    defaults={'value_raw': str(val), 'unit_definition': unit}
                )
            else:
                PartRevisionProperty.objects.filter(part_revision=self.instance, property_definition=defn).delete()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            self.save_m2m()
            self.save_properties()
        return instance


class PartRevisionNewForm(PartRevisionForm):
    copy_assembly = forms.BooleanField(label='Copy assembly from latest revision', initial=True, required=False)

    def __init__(self, *args, **kwargs):
        self.part = kwargs.pop('part', None)
        self.revision = kwargs.pop('revision', None)
        self.assembly = kwargs.pop('assembly', None)
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        if not self.instance.pk:
            self.instance.part = self.part
            self.instance.revision = self.revision
            self.instance.assembly = self.assembly
        else:
            # If we are incrementing from an existing instance, we want to create a NEW record
            self.instance.pk = None
            self.instance.part = self.part
            self.instance.revision = self.revision
            self.instance.assembly = self.assembly
        return super().save(commit=commit)


# ==========================================
# SUBPART / BOM FORMS
# ==========================================

class SubpartForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = Subpart
        fields = ['part_revision', 'reference', 'count', 'do_not_load']

    def __init__(self, *args, **kwargs):
        self.part_id = kwargs.pop('part_id', None)
        self.ignore_part_revision = kwargs.pop('ignore_part_revision', False)
        super().__init__(*args, **kwargs)

        if not self.part_id:
            self.Meta.exclude = ['part_revision']
        else:
            self.fields['part_revision'].queryset = PartRevision.objects.filter(part__id=self.part_id).order_by(
                '-timestamp')

        if self.ignore_part_revision:
            self.fields['part_revision'].required = False

    def clean_count(self):
        return self.cleaned_data['count'] or 0

    def clean_reference(self):
        return stringify_list(listify_string(self.cleaned_data['reference']))

    def clean(self):
        cleaned_data = super().clean()
        refs = listify_string(cleaned_data.get('reference'))
        count = cleaned_data.get('count')
        if len(refs) > 0 and len(refs) != count:
            raise ValidationError(f"Reference designators count ({len(refs)}) mismatch subpart quantity ({count}).")
        return cleaned_data


class AddSubpartForm(OrganizationFormMixin, forms.Form):
    subpart_part_number = forms.CharField(label="Subpart part number", required=True)
    count = forms.FloatField(required=False, label='Quantity')
    reference = forms.CharField(required=False, label="Reference")
    do_not_load = forms.BooleanField(required=False, label="do_not_load")

    def __init__(self, *args, **kwargs):
        self.part_id = kwargs.pop('part_id', None)
        super().__init__(*args, **kwargs)

        self.part = Part.objects.get(id=self.part_id)
        # Filter logic
        self.fields['subpart_part_number'].widget = AutocompleteTextInput(
            attrs={'placeholder': 'Select a part.'},
            queryset=Part.objects.filter(organization=self.organization).exclude(id=self.part_id),
            verbose_string_function=Part.verbose_str
        )

    def clean_subpart_part_number(self):
        subpart_part_number = self.cleaned_data['subpart_part_number']
        if not subpart_part_number:
            raise ValidationError("Must specify a part number.")

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

            unusable_ids = [pr.id for pr in self.part.latest().where_used_full()] + [self.part.latest().id]
            if self.subpart_part.id in unusable_ids:
                raise ValidationError("Infinite recursion! Can't add a part to itself.")

        except (AttributeError, PartClass.DoesNotExist, Part.DoesNotExist) as e:
            raise ValidationError(f"Invalid part number: {e}")

        return subpart_part_number

    def clean_count(self):
        return self.cleaned_data.get('count') or 0

    def clean_reference(self):
        return stringify_list(listify_string(self.cleaned_data.get('reference')))

    def clean(self):
        cleaned = super().clean()
        refs = listify_string(cleaned.get('reference'))
        count = cleaned.get('count')
        if len(refs) > 0 and len(refs) != count:
            raise ValidationError(f"Reference count ({len(refs)}) mismatch quantity ({count}).")
        return cleaned


# ==========================================
# CSV IMPORT FORMS
# ==========================================

class PartClassCSVForm(BaseCSVForm):
    def get_csv_headers_handler(self):
        return PartClassesCSVHeaders()

    def get_header_assertions(self):
        return [
            ('comment', 'description', 'mex'),
            ('code', 'in'),
            ('name', 'in'),
        ]

    def process_row(self, row_data, row_count, csv_headers):
        name = csv_headers.get_val_from_row(row_data, 'name')
        code = csv_headers.get_val_from_row(row_data, 'code')
        desc = csv_headers.get_val_from_row(row_data, 'description')
        comment = csv_headers.get_val_from_row(row_data, 'comment')

        if not code:
            self.add_error(None, f"Row {row_count}: Missing code.")
            return

        if len(code) != self.organization.number_class_code_len:
            self.add_error(None, f"Row {row_count}: Invalid code length.")
            return

        description = desc or comment or ''
        try:
            PartClass.objects.create(code=code, name=name, comment=description, organization=self.organization)
            self.successes.append(f"Part class {code} {name} on row {row_count} created.")
        except IntegrityError:
            self.add_error(None, f"Row {row_count}: Part class {code} {name} already defined.")


class PartCSVForm(BaseCSVForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-fetch valid units
        self.valid_units = {u.symbol: u.id for u in UnitDefinition.objects.available_to(self.organization)}

    def get_csv_headers_handler(self):
        return self.organization.part_list_csv_headers()

    def get_header_assertions(self):
        return [
            ('part_class', 'part_number', 'or'),
            ('revision', 'in'),
            ('value', 'value_units', 'and', 'description', 'or'),
        ]

    def process_row(self, row_data, row_count, csv_headers):
        part_number = csv_headers.get_val_from_row(row_data, 'part_number')
        part_class = csv_headers.get_val_from_row(row_data, 'part_class')
        number_item = None
        number_variation = None
        revision = csv_headers.get_val_from_row(row_data, 'revision')
        mpn = csv_headers.get_val_from_row(row_data, 'mpn')
        mfg_name = csv_headers.get_val_from_row(row_data, 'mfg_name')
        description = csv_headers.get_val_from_row(row_data, 'description')
        seller_name = csv_headers.get_val_from_row(row_data, 'seller')
        seller_part_number = csv_headers.get_val_from_row(row_data, 'seller_part_number')
        unit_cost = csv_headers.get_val_from_row(row_data, 'unit_cost')
        nre_cost = csv_headers.get_val_from_row(row_data, 'part_nre_cost')
        moq = csv_headers.get_val_from_row(row_data, 'moq')
        mpq = csv_headers.get_val_from_row(row_data, 'minimum_pack_quantity')

        # Check part number for uniqueness. If part number not specified
        # then Part.save() will create one.
        if part_number:
            if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
                try:
                    (number_class, number_item, number_variation) = Part.parse_part_number(part_number,
                                                                                           self.organization)
                    part_class = PartClass.objects.get(code=number_class, organization=self.organization)
                    Part.objects.get(number_class=part_class, number_item=number_item,
                                     number_variation=number_variation, organization=self.organization)
                    self.add_error(None,
                                   "Part number {0} in row {1} already exists. Uploading of this part skipped.".format(
                                       part_number, row_count))
                    return
                except AttributeError as e:
                    self.add_error(None, str(e) + " on row {}. Creation of this part skipped.".format(row_count))
                    return
                except PartClass.DoesNotExist:
                    self.add_error(None,
                                   "No part class found for part number {0} in row {1}. Creation of this part skipped.".format(
                                       part_number, row_count))
                    return
                except Part.DoesNotExist:
                    pass
            else:
                try:
                    number_item = part_number
                    Part.objects.get(number_class=None, number_item=number_item, number_variation=None,
                                     organization=self.organization)
                    self.add_error(None,
                                   f"Part number {part_number} in row {row_count} already exists. Uploading of this part skipped.")
                    return
                except Part.DoesNotExist:
                    pass
        elif part_class:
            try:
                part_class = PartClass.objects.get(code=row_data[csv_headers.get_default('part_class')],
                                                   organization=self.organization)
            except PartClass.DoesNotExist:
                self.add_error(None,
                               "Part class {0} in row {1} doesn't exist. Create part class on Settings > IndaBOM and try again."
                               "Uploading of this part skipped.".format(
                                   row_data[csv_headers.get_default('part_class')], row_count))
                return
        else:
            if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
                self.add_error(None,
                               "In row {} need to specify a part_class or part_number. Uploading of this part skipped.".format(
                                   row_count))
            else:
                self.add_error(None, "In row {} need to specify a part_number. Uploading of this part skipped.".format(
                    row_count))
            return

        if not revision:
            self.add_error(None, f"Missing revision in row {row_count}. Uploading of this part skipped.")
            return
        elif len(revision) > 4:
            self.add_error(None, "Revision {0} in row {1} is more than the maximum 4 characters. "
                                 "Uploading of this part skipped.".format(
                row_data[csv_headers.get_default('revision')], row_count))
            return
        elif revision.isdigit() and int(revision) < 0:
            self.add_error(None, "Revision {0} in row {1} cannot be a negative number. "
                                 "Uploading of this part skipped.".format(
                row_data[csv_headers.get_default('revision')], row_count))
            return

        if mpn and mfg_name:
            manufacturer_part = ManufacturerPart.objects.filter(manufacturer_part_number=mpn,
                                                                manufacturer__name=mfg_name,
                                                                manufacturer__organization=self.organization)
            if manufacturer_part.count() > 0:
                self.add_error(None, "Part already exists for manufacturer part {0} in row {1}. "
                                     "Uploading of this part skipped.".format(row_count, mpn, row_count))
                return

        skip = False
        row_data['revision'] = revision
        row_data['description'] = description

        if self.organization.number_scheme == NUMBER_SCHEME_INTELLIGENT and number_item is None:
            self.add_error(None,
                           "Can't upload a part without a number_item header for part in row {}. Uploading of this part skipped.".format(
                               row_count))
            skip = True

        if skip:
            return

        PartForm = part_form_from_organization(self.organization)
        part = Part(number_class=part_class, number_item=number_item, number_variation=number_variation,
                    organization=self.organization)
        part_dict = model_to_dict(part)
        part_dict.update({'number_class': str(part.number_class)})
        pf = PartForm(data=part_dict, organization=self.organization)
        prf = PartRevisionForm(data=row_data, part_class=part_class, organization=self.organization)

        if pf.is_valid() and prf.is_valid():
            part = pf.save(commit=False)
            part.organization = self.organization
            part.save()
            part_revision = prf.save(commit=False)
            part_revision.part = part
            part_revision.save()

            if mfg_name and mpn:
                mfg, created = Manufacturer.objects.get_or_create(name__iexact=mfg_name, organization=self.organization,
                                                                  defaults={'name': mfg_name})
                manufacturer_part, created = ManufacturerPart.objects.get_or_create(part=part,
                                                                                    manufacturer_part_number=mpn,
                                                                                    manufacturer=mfg)
                if part.primary_manufacturer_part is None and manufacturer_part is not None:
                    part.primary_manufacturer_part = manufacturer_part
                    part.save()

                if seller_name and unit_cost and nre_cost:
                    seller, created = Seller.objects.get_or_create(name__iexact=seller_name,
                                                                   organization=self.organization,
                                                                   defaults={'name': seller_name})
                    seller_part, created = SellerPart.objects.get_or_create(manufacturer_part=manufacturer_part,
                                                                            seller=seller,
                                                                            seller_part_number=seller_part_number,
                                                                            unit_cost=unit_cost, nre_cost=nre_cost,
                                                                            minimum_order_quantity=moq,
                                                                            minimum_pack_quantity=mpq)

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


class BOMCSVForm(BaseCSVForm):
    def __init__(self, *args, **kwargs):
        self.parent_part = kwargs.pop('parent_part', None)
        super().__init__(*args, **kwargs)
        self.parent_part_revision = self.parent_part.latest() if self.parent_part else None
        self.part_revision_tree = [self.parent_part_revision] if self.parent_part_revision else []
        self.last_level = None
        self.last_part_revision = self.parent_part_revision

    def get_csv_headers_handler(self):
        return self.organization.bom_indented_csv_headers()

    def get_header_assertions(self):
        return [
            ('part_number', 'manufacturer_part_number', 'or'),
            ('quantity', 'in'),
        ]

    def process_row(self, part_dict, row_count, csv_headers):
        dnp = csv_headers.get_val_from_row(part_dict, 'dnp')
        reference = csv_headers.get_val_from_row(part_dict, 'reference')
        part_number = csv_headers.get_val_from_row(part_dict, 'part_number')
        manufacturer_part_number = csv_headers.get_val_from_row(part_dict, 'mpn')
        manufacturer_name = csv_headers.get_val_from_row(part_dict, 'manufacturer_name')

        try:
            level = int(float(csv_headers.get_val_from_row(part_dict, 'level')))
        except ValueError as e:
            # TODO: May want to validate whole file has acceptable levels first.
            raise ValidationError(f"Row {row_count} - level: invalid level, can't continue.", code='invalid')
        except TypeError as e:
            # no level field was provided, we MUST have a parent part number to upload this way, and in this case all levels are the same
            if self.parent_part_revision is None:
                raise ValidationError(
                    f"Row {row_count} - level: must provide either level, or a parent part to upload a part.",
                    code='invalid')
            else:
                level = 1

        if self.last_level is None:
            self.last_level = level

        # Extract some values
        part_dict['reference'] = reference
        part_dict['do_not_load'] = dnp in ['y', 'x', 'dnp', 'dnl', 'yes', 'true', ]
        part_dict['revision'] = csv_headers.get_val_from_row(part_dict, 'revision') or 1
        part_dict['count'] = csv_headers.get_val_from_row(part_dict, 'count')
        part_dict['number_class'] = None
        part_dict['number_variation'] = None

        if part_number:
            # TODO: Should this be in a clean function?
            try:
                (part_dict['number_class'], part_dict['number_item'],
                 part_dict['number_variation']) = Part.parse_partial_part_number(part_number, self.organization)
            except AttributeError as e:
                self.add_error(None,
                               f"Row {row_count} - part_number: Uploading of this subpart skipped. Couldn't parse part number.")
                return
        elif manufacturer_part_number:
            try:
                part = Part.from_manufacturer_part_number(manufacturer_part_number, self.organization)
                if part is None:
                    self.add_error(None,
                                   f"Row {row_count} - manufacturer_part_number: Uploading of this subpart skipped. No part found for manufacturer part number.")
                    return
                part_dict['number_class'] = part.number_class.code
                part_dict['number_item'] = part.number_item
                part_dict['number_variation'] = part.number_variation
                part_number = part.full_part_number()
            except ValueError:
                self.add_error(None,
                               f"Row {row_count} - manufacturer_part_number: Uploading of this subpart skipped. Too many parts found for manufacturer part number.")
                return
        else:
            raise ValidationError(
                "No part_number or manufacturer_part_number found. Uploading stopped. No subparts uploaded.",
                code='invalid')

        # Handle indented bom level changes
        level_change = level - self.last_level
        if level_change == 1:  # Level decreases, must only decrease by 1
            self.part_revision_tree.append(self.last_part_revision)
        elif level_change <= -1:  # Level increases, going up in assembly; intentionally empty tree if level change is very negative
            self.part_revision_tree = self.part_revision_tree[:level_change]
        elif level_change == 0:
            pass
        elif level - self.last_level > 1:
            raise ValidationError(
                f'Row {row_count} - level: Assembly levels must decrease by no more than 1 from sequential rows.',
                code='invalid')
        else:
            raise ValidationError(f'Row {row_count} - level: Invalid assembly level.', code='invalid')

        try:
            parent_part_revision = self.part_revision_tree[-1]
            if parent_part_revision.assembly is None:
                parent_part_revision.assembly = Assembly.objects.create()
                parent_part_revision.save()
        except IndexError:
            parent_part_revision = None

        # Check for existing objects
        existing_part_class = PartClass.objects.filter(code=part_dict['number_class'],
                                                       organization=self.organization).first()

        existing_part = None
        if existing_part_class or self.organization.number_scheme == NUMBER_SCHEME_INTELLIGENT:
            existing_part = Part.objects.filter(number_class=existing_part_class, number_item=part_dict['number_item'],
                                                number_variation=part_dict['number_variation'],
                                                organization=self.organization).first()

        existing_part_revision = None
        if existing_part:
            existing_part_revision = PartRevision.objects.filter(part=existing_part,
                                                                 revision=part_dict['revision']).first()

        if existing_part_revision and parent_part_revision:  # Check for infinite recursion
            contains_parent = False
            indented_bom = existing_part_revision.indented()
            for _, sp in indented_bom.parts.items():  # Make sure the subpart does not contain the parent - infinite recursion!
                if sp.part_revision == parent_part_revision:
                    contains_parent = True
            if contains_parent:
                raise ValidationError(
                    f"Row {row_count} - Uploaded part {part_number} contains parent part in its assembly. Cannot add {part_number} as it would cause infinite recursion. Uploading of this subpart skipped.",
                    code='invalid')

        existing_subpart = None
        existing_subpart_count = 0
        existing_subpart_references = None
        if existing_part_revision and parent_part_revision:
            existing_subpart = parent_part_revision.assembly.subparts.all().filter(part_revision=existing_part_revision,
                                                                                   do_not_load=part_dict[
                                                                                       'do_not_load']).first()
            existing_subpart_count = existing_subpart.count if existing_subpart else 0
            existing_subpart_references = existing_subpart.reference if existing_subpart else None

        # Now validate & save PartClass, Part, PartRevision, Subpart
        part_class_dict = {'code': part_dict['number_class'], 'name': part_dict.get('part_class', None)}
        part_class_form = PartClassForm(part_class_dict, instance=existing_part_class, ignore_unique_constraint=True,
                                        organization=self.organization)
        if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT and not part_class_form.is_valid():
            add_nonfield_error_from_existing(part_class_form, self, f'Row {row_count} - ')
            return

        PartForm = part_form_from_organization(self.organization)
        part_form = PartForm(part_dict, instance=existing_part, ignore_part_class=True, ignore_unique_constraint=True,
                             organization=self.organization)
        if not part_form.is_valid():
            add_nonfield_error_from_existing(part_form, self, f'Row {row_count} - ')
            return

        part_revision_form = PartRevisionForm(part_dict, instance=existing_part_revision,
                                              organization=self.organization)
        if not part_revision_form.is_valid():
            add_nonfield_error_from_existing(part_revision_form, self, f'Row {row_count} - ')
            return

        subpart_form = SubpartForm(part_dict, instance=existing_subpart, ignore_part_revision=True,
                                   organization=self.organization)
        if not subpart_form.is_valid():
            add_nonfield_error_from_existing(subpart_form, self, f'Row {row_count} - ')
            return

        part_class = part_class_form.save(commit=False)
        part = part_form.save(commit=False)
        part_revision = part_revision_form.save(commit=False)
        subpart = subpart_form.save(commit=False)

        reference_list = listify_string(reference) if reference else []
        if len(reference_list) != len(set(reference_list)):
            self.add_warning(None,
                             f"Row {row_count} -Duplicate reference designators '{reference}' for subpart on row {row_count}.")
        if len(reference_list) != subpart.count and len(reference_list) > 0:
            self.add_warning(None,
                             f"Row {row_count} -The quantity of reference designators for {part_number} on row {row_count} does not match the subpart quantity ({len(reference_list)} != {subpart.count})")

        if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
            part_class.save()
            part.number_class = part_class

        part.organization = self.organization
        part.save()
        part_revision.part = part
        part_revision.save()
        if parent_part_revision:
            subpart.count += existing_subpart_count  # append or create
            subpart.reference = existing_subpart_references + ', ' + subpart.reference if existing_subpart_references else subpart.reference
            subpart.part_revision = part_revision
            subpart.save()
            AssemblySubparts.objects.get_or_create(assembly=parent_part_revision.assembly, subpart=subpart)

        info_msg = f"Row {row_count}: Added subpart {part_number}"
        if reference:
            info_msg += f" with reference designators {reference}"
        if parent_part_revision:
            info_msg += f" to parent part {parent_part_revision.part.full_part_number()}"
        self.successes.append(info_msg + ".")

        # Now validate & save optional fields - Manufacturer, ManufacturerPart, SellerParts
        existing_manufacturer = Manufacturer.objects.filter(name=manufacturer_name,
                                                            organization=self.organization).first()
        manufacturer_form = ManufacturerForm({'name': manufacturer_name}, instance=existing_manufacturer)
        if not manufacturer_form.is_valid():
            add_nonfield_error_from_existing(manufacturer_form, self, f'Row {row_count} - ')

        manufacturer_part_data = {'manufacturer_part_number': manufacturer_part_number}
        manufacturer_part_form = ManufacturerPartForm(manufacturer_part_data)
        if not manufacturer_part_form.is_valid():
            add_nonfield_error_from_existing(manufacturer_part_form, self, f'Row {row_count} - ')

        manufacturer = manufacturer_form.save(commit=False)
        manufacturer.organization = self.organization
        manufacturer.save()

        manufacturer_part = manufacturer_part_form.save(commit=False)
        existing_manufacturer_part = ManufacturerPart.objects.filter(part=part, manufacturer=manufacturer,
                                                                     manufacturer_part_number=manufacturer_part.manufacturer_part_number).first()
        manufacturer_part.id = existing_manufacturer_part.id if existing_manufacturer_part else None
        manufacturer_part.manufacturer = manufacturer
        manufacturer_part.part = part
        manufacturer_part.save()

        part.primary_manufacturer_part = manufacturer_part
        part.save()

        self.last_part_revision = part_revision
        self.last_level = level


class UploadBOMForm(OrganizationFormMixin, forms.Form):
    parent_part_number = forms.CharField(required=False, label="Parent part number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent_part = None

    def clean_parent_part_number(self):
        ppn = self.cleaned_data['parent_part_number']
        if ppn:
            try:
                self.parent_part = Part.from_part_number(ppn, self.organization)
            except (AttributeError, Part.DoesNotExist) as e:
                raise ValidationError(f"Invalid parent part: {e}")
        return ppn


class FileForm(forms.Form):
    file = forms.FileField()


# ==========================================
# HELPERS
# ==========================================

def part_form_from_organization(organization):
    if organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT:
        return PartFormSemiIntelligent
    return PartFormIntelligent


def add_nonfield_error_from_existing(from_form, to_form, prefix=''):
    for field, errors in from_form.errors.as_data().items():
        for error in errors:
            for msg in error.messages:
                to_form.add_error(None, f'{prefix}{field}: {msg}')