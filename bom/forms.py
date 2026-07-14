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
from djmoney.forms.widgets import MoneyWidget

from bom.permissions import BomPerms
from .constants import (
    NUMBER_SCHEME_INTELLIGENT,
    NUMBER_SCHEME_SEMI_INTELLIGENT,
    PART_REVISION_PROPERTY_TYPE_BOOLEAN,
    PART_REVISION_PROPERTY_TYPE_DECIMAL,
    ROLE_TYPE_VIEWER,
    CONFIGURATION_TYPE_OBSOLETE,
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
from .utils import listify_string, parse_number, stringify_list
from .validators import alphanumeric

logger = logging.getLogger(__name__)
Organization = get_organization_model()
UserMeta = get_user_meta_model()


# ==========================================
# MIXINS & BASE CLASSES
# ==========================================

class CurrentFileInput(forms.FileInput):
    """FileInput that exposes the existing filename via ``data-current-file`` so the UI can show it
    in Materialize's file-path box -- a plain file input renders nothing for an already-saved file."""

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        if value and hasattr(value, 'url'):
            context['widget']['attrs']['data-current-file'] = value.name.rsplit('/', 1)[-1]
        return context


class OrganizationFormMixin:
    """Mixin to handle organization injection."""

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)


class PlaceholderMixin:
    """Mixin to move help_text to widget placeholders automatically."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for _, field in self.fields.items():
            if field.help_text:
                field.widget.attrs['placeholder'] = field.help_text
                field.help_text = ''


class MaterializeMoneyMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field_name, field in self.fields.items():
            if isinstance(field.widget, MoneyWidget):
                amount_widget = field.widget.widgets[0]
                currency_widget = field.widget.widgets[1]
                amount_widget.attrs.update({
                    'style': 'width: 70%; display: inline-block;',
                    'step': 'any'
                })
                currency_widget.attrs.update({
                    'class': 'browser-default',
                    'style': 'width: 28%; display: inline-block; margin-left: 2%; border: none; border-bottom: 1px solid #9e9e9e; height: 3rem; background-color: transparent; outline: none; color: inherit;'
                })


class OrganizationModelForm(OrganizationFormMixin, PlaceholderMixin, forms.ModelForm):
    def save(self, commit=True):
        self.instance.organization = self.organization
        return super().save(commit=commit)

    class Meta:
        abstract = True


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
            if user_meta.organization and user_meta.organization != self.organization:
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


class UserMetaForm(OrganizationModelForm):
    class Meta:
        model = UserMeta
        exclude = ['user']

    def save(self, commit=True):
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
        can_manage = user.has_perm(BomPerms.MANAGE_MEMBERS, self.instance) if user else False

        if can_manage:
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


# Kept separate from OrganizationFormEditSettings on purpose: different save semantics (no
# "confirm org change" prompt), write-only secret handling, and provider-conditional fields.
# The per-provider field labels/help come from each provider's declared credential_fields
# (bom.third_party_apis.sourcing) so the settings form/JS hold no hardcoded provider strings.
class SourcingSettingsForm(forms.ModelForm):
    """Per-organization live-sourcing provider + BYOK credentials.

    Secrets are write-only: the stored values are never rendered back into the form. A blank
    submission keeps the existing secret; a non-blank one replaces it. ``key_is_set`` /
    ``secret_is_set`` let the template show a "already set -- replace?" hint.
    """

    # Labels are set directly on the fields (Meta.labels does not apply to declared fields).
    # The JS provider toggle relabels these per provider; these are the no-JS fallbacks.
    sourcing_api_key = forms.CharField(
        required=False,
        label='API Key / Client ID',
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
    )
    sourcing_api_secret = forms.CharField(
        required=False,
        label='Client Secret',
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
    )

    class Meta:
        model = Organization
        fields = ['sourcing_provider', 'sourcing_api_key', 'sourcing_api_secret']
        labels = {
            'sourcing_provider': 'Sourcing Provider',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The materializecss filter renders <select> without an id; set one so the JS provider
        # toggle (and Materialize change events) can target it.
        self.fields['sourcing_provider'].widget.attrs.setdefault('id', 'id_sourcing_provider')
        # Never echo stored secrets back to the page.
        self.key_is_set = bool(getattr(self.instance, 'sourcing_api_key', None))
        self.secret_is_set = bool(getattr(self.instance, 'sourcing_api_secret', None))
        self.initial['sourcing_api_key'] = ''
        self.initial['sourcing_api_secret'] = ''
        unset = 'Enter value'
        already_set = '•••••••• set — leave blank to keep'
        self.fields['sourcing_api_key'].widget.attrs['placeholder'] = already_set if self.key_is_set else unset
        self.fields['sourcing_api_secret'].widget.attrs['placeholder'] = already_set if self.secret_is_set else unset

    def clean_sourcing_api_key(self):
        # Blank submit means "keep the existing secret".
        return self.cleaned_data.get('sourcing_api_key') or self.instance.sourcing_api_key

    def clean_sourcing_api_secret(self):
        return self.cleaned_data.get('sourcing_api_secret') or self.instance.sourcing_api_secret


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
    quantity = forms.IntegerField(label='Quote Quantity', min_value=1)


class ManufacturerForm(OrganizationModelForm):
    class Meta:
        model = Manufacturer
        exclude = ['organization']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].label = "Manufacturer Name"


class ManufacturerPartForm(OrganizationFormMixin, PlaceholderMixin, forms.ModelForm):
    class Meta:
        model = ManufacturerPart
        exclude = ['part', 'manufacturer', ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sourcing_disable'].initial = True
        self.fields['manufacturer'] = forms.CharField(
            required=False,  # clean method handles logic
            widget=AutocompleteTextInput(
                attrs={'placeholder': 'Search existing or type new'},
                queryset=Manufacturer.objects.available_to(
                    organization=self.organization
                ).order_by('name')
            )
        )
        self.order_fields(['manufacturer', 'manufacturer_part_number', 'link', ])
        if self.instance.pk and self.instance.manufacturer:
            self.fields['manufacturer'].initial = self.instance.manufacturer.name

    def clean_manufacturer(self):
        raw = self.cleaned_data['manufacturer']
        if not raw:
            return None

        mfg = Manufacturer.objects.filter(organization=self.organization, name__iexact=raw.strip()).first()
        return mfg if mfg else raw.strip()

    def clean(self):
        cleaned_data = super().clean()
        mpn = cleaned_data.get('manufacturer_part_number')
        mfg = cleaned_data.get('manufacturer')
        if mpn and not mfg:
            self.add_error('manufacturer', 'Manufacturer is required if a Part Number is entered.')
        return cleaned_data

    def save(self, commit=True):
        mfg_data = self.cleaned_data.get('manufacturer')

        if isinstance(mfg_data, str):
            mfg_obj, _ = Manufacturer.objects.get_or_create(
                name__iexact=mfg_data,
                organization=self.organization,
                defaults={'name': mfg_data}
            )
        else:
            mfg_obj = mfg_data

        self.instance.manufacturer = mfg_obj

        if not self.instance.pk and self.instance.part_id:
            mpn = self.cleaned_data.get('manufacturer_part_number', '')
            obj, created = ManufacturerPart.objects.get_or_create(
                part=self.instance.part,
                manufacturer_part_number=mpn,
                manufacturer=mfg_obj,
                defaults={
                    'sourcing_disable': self.cleaned_data.get('sourcing_disable', False),
                    'link': self.cleaned_data.get('link'),
                }
            )
            if not created and commit:
                obj.sourcing_disable = self.cleaned_data.get('sourcing_disable', obj.sourcing_disable)
                obj.link = self.cleaned_data.get('link', obj.link)
                obj.save()
            return obj
        else:
            return super().save(commit=commit)


class SellerForm(OrganizationModelForm):
    class Meta:
        model = Seller
        exclude = ['organization']


class SellerPartForm(OrganizationFormMixin, MaterializeMoneyMixin, PlaceholderMixin, forms.ModelForm):
    class Meta:
        model = SellerPart
        exclude = ['manufacturer_part', 'data_source', 'seller', ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        widget = AutocompleteTextInput(attrs={'placeholder': 'Search existing or type new'},
                                       queryset=Seller.objects.available_to(organization=self.organization).order_by(
                                           'name'))
        self.fields['seller'] = forms.CharField(required=True, label='Vendor', widget=widget)
        self.fields['seller_part_number'].label = "Vendor Part Number"
        if self.instance.pk:
            self.initial['seller'] = self.instance.seller.name

        self.order_fields(
            ['seller', 'seller_part_number', 'unit_cost', 'nre_cost', 'lead_time_days', 'minimum_order_quantity',
             'minimum_pack_quantity'])

    def clean_seller(self):
        raw = self.cleaned_data['seller']
        if not raw:
            return None

        seller = Seller.objects.filter(organization=self.organization, name__iexact=raw.strip()).first()
        return seller if seller else raw.strip()

    def save(self, commit=True):
        seller_data = self.cleaned_data.get('seller')

        if isinstance(seller_data, str):
            seller, _ = Seller.objects.get_or_create(
                name__iexact=seller_data,
                organization=self.organization,
                defaults={'name': seller_data}
            )
        else:
            seller = seller_data

        self.instance.seller = seller

        return super().save(commit=commit)


class QuantityOfMeasureForm(OrganizationModelForm):
    class Meta:
        model = QuantityOfMeasure
        fields = ['name']

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if QuantityOfMeasure.objects.filter(name__iexact=name, organization=self.organization).exclude(
                pk=self.instance.pk).exists():
            raise forms.ValidationError(f"A quantity of measure with the name '{name}' already exists.")
        return name


class UnitDefinitionForm(OrganizationModelForm):
    class Meta:
        model = UnitDefinition
        fields = ['name', 'symbol', 'base_multiplier', ]


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


class PartClassForm(OrganizationModelForm):
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

class BasePartForm(OrganizationModelForm):
    """Base class for part forms to handle common init and placeholder logic."""
    add_sourcing = forms.BooleanField(
        label="Add manufacturer sourcing information immediately?",
        initial=True,
        required=False,
        help_text="Check this to skip straight to adding an MPN after saving."
    )

    def __init__(self, *args, **kwargs):
        self.ignore_part_class = kwargs.pop('ignore_part_class', False)
        self.ignore_unique_constraint = kwargs.pop('ignore_unique_constraint', False)
        super().__init__(*args, **kwargs)

        # Setup MFG Part Queryset if editing
        if self.instance.pk:
            self.fields['primary_manufacturer_part'].queryset = ManufacturerPart.objects.filter(
                part__id=self.instance.id
            ).order_by('manufacturer_part_number')
            # 'add_sourcing' only drives the post-save redirect on create; it does nothing when editing.
            self.fields.pop('add_sourcing', None)
        elif 'primary_manufacturer_part' in self.fields:
            del self.fields['primary_manufacturer_part']

        # Plain file input so it renders as a Materialize file-field (the default ClearableFileInput's
        # "Currently/Clear/Change" markup doesn't fit); CurrentFileInput surfaces the existing filename.
        if 'image' in self.fields:
            self.fields['image'].widget = CurrentFileInput(attrs={'accept': 'image/*'})


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
            widget=AutocompleteTextInput(queryset=PartClass.objects.filter(organization=self.organization))
        )
        # Use a placeholder (like the sibling fields) rather than a help_text block, which renders an
        # extra <p> that unbalances the floated column grid.
        self.fields['number_class'].widget.attrs['placeholder'] = 'Select a number class.'

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


class PartImageForm(forms.ModelForm):
    """Single-field form for the inline picture upload/replace popover on the part page."""
    class Meta:
        model = Part
        fields = ['image']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['image'].required = True


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

        if self.instance.pk and self.instance.is_immutable():
            for field_name, field in self.fields.items():
                field.disabled = True
                state_name = self.instance.get_configuration_display()
                field.help_text = f'This PartRevision is {state_name} and cannot be modified. Create a new revision, or revert this one to draft to make changes.'

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
            self.fields['part_revision'].queryset = PartRevision.objects.filter(
                part__id=self.part_id
            ).exclude(
                configuration=CONFIGURATION_TYPE_OBSOLETE
            ).order_by('-timestamp')

        if self.ignore_part_revision:
            self.fields['part_revision'].required = False

        # Disable all fields if this subpart belongs to a Released PartRevision's assembly
        if self.instance.pk:
            locked_parent = self.instance.get_parent_part_revisions().filter(
                configuration__in=PartRevision.IMMUTABLE_STATES).first()

            if locked_parent:
                msg = (
                    f"Locked by {locked_parent.get_configuration_display()} "
                    f"PartRevision {locked_parent} as it is not in draft."
                )

                for field in self.fields.values():
                    field.disabled = True
                    field.help_text = f"{msg} {field.help_text or ''}"

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
        self.part_revision_id = kwargs.pop('part_revision_id', None)
        super().__init__(*args, **kwargs)

        self.part_revision = PartRevision.objects.get(id=self.part_revision_id)
        self.part = self.part_revision.part
        # Filter logic
        self.fields['subpart_part_number'].widget = AutocompleteTextInput(
            attrs={'placeholder': 'Select a part.'},
            queryset=Part.objects.filter(organization=self.organization).exclude(id=self.part.id),
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

            unusable_ids = [pr.id for pr in self.part_revision.where_used_full()] + [self.part_revision.id]
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

        if self.part_revision.is_immutable():
            raise ValidationError(
                f"Cannot add subparts to PartRevision {self.part_revision} in {self.part_revision.get_configuration_display()}. "
                f"Create a new revision, or revert this revision to Draft to make changes."
            )

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
        # Costs may arrive with currency symbols and thousands separators (e.g. an exported
        # "A$1,190.00"); normalize to a plain decimal string that MoneyField can parse.
        unit_cost = sanitize_cost(csv_headers.get_val_from_row(row_data, 'unit_cost'))
        nre_cost = sanitize_cost(csv_headers.get_val_from_row(row_data, 'part_nre_cost'))
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
                                                                            minimum_order_quantity=moq or 1,
                                                                            minimum_pack_quantity=mpq or 1)

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
        level = self._parse_level(part_dict, row_count, csv_headers)
        if self.last_level is None:
            self.last_level = level

        parent_part_revision = self._advance_part_revision_tree(level, row_count)
        part_revision = self._process_subpart_row(part_dict, row_count, csv_headers, parent_part_revision)

        # Always record the outcome (None when skipped) so the next row's level tracking
        # and parent lookup stay consistent. Recording None for a skipped row is what lets
        # _advance_part_revision_tree mark its descendants as orphaned.
        self.last_part_revision = part_revision
        self.last_level = level

    def _parse_level(self, part_dict, row_count, csv_headers):
        try:
            return int(float(csv_headers.get_val_from_row(part_dict, 'level')))
        except ValueError:
            # TODO: May want to validate whole file has acceptable levels first.
            raise ValidationError(f"Row {row_count} - level: invalid level, can't continue.", code='invalid')
        except TypeError:
            # no level field was provided, we MUST have a parent part number to upload this way, and in this case all levels are the same
            if self.parent_part_revision is None:
                raise ValidationError(
                    f"Row {row_count} - level: must provide either level, or a parent part to upload a part.",
                    code='invalid')
            return 1

    def _advance_part_revision_tree(self, level, row_count):
        # Adjust the ancestor stack for this row's indent level. Pushing self.last_part_revision
        # (which is None when the previous row was skipped) marks skipped ancestors so their
        # descendants can be detected and skipped rather than crashing on a None parent.
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

        parent_part_revision = self.part_revision_tree[-1] if self.part_revision_tree else None
        if parent_part_revision is not None and parent_part_revision.assembly is None:
            parent_part_revision.assembly = Assembly.objects.create()
            parent_part_revision.save()
        return parent_part_revision

    def _process_subpart_row(self, part_dict, row_count, csv_headers, parent_part_revision):
        dnp = csv_headers.get_val_from_row(part_dict, 'dnp')
        reference = csv_headers.get_val_from_row(part_dict, 'reference')
        part_number = csv_headers.get_val_from_row(part_dict, 'part_number')
        manufacturer_part_number = csv_headers.get_val_from_row(part_dict, 'mpn')
        manufacturer_name = csv_headers.get_val_from_row(part_dict, 'manufacturer_name')
        manufacturer_approval_status = csv_headers.get_val_from_row(part_dict, 'manufacturer_approval_status') or 'P'

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
                return None
        elif manufacturer_part_number:
            try:
                part = Part.from_manufacturer_part_number(manufacturer_part_number, self.organization)
                if part is None:
                    self.add_error(None,
                                   f"Row {row_count} - manufacturer_part_number: Uploading of this subpart skipped. No part found for manufacturer part number.")
                    return None
                part_dict['number_class'] = part.number_class.code
                part_dict['number_item'] = part.number_item
                part_dict['number_variation'] = part.number_variation
                part_number = part.full_part_number()
            except ValueError:
                self.add_error(None,
                               f"Row {row_count} - manufacturer_part_number: Uploading of this subpart skipped. Too many parts found for manufacturer part number.")
                return None
        else:
            raise ValidationError(
                "No part_number or manufacturer_part_number found. Uploading stopped. No subparts uploaded.",
                code='invalid')

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

        if existing_part_revision and existing_part_revision.is_immutable():
            self.warnings.append(f"Row {row_count}: Skipped {part_number} because the existing revision is immutable.")
            return None

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
        # Preserve the existing name when the CSV row has no part_class column or an empty value;
        # otherwise BOMCSVForm would overwrite the stored name with an empty string.
        part_class_name = part_dict.get('part_class') or (existing_part_class.name if existing_part_class else None)
        part_class_dict = {'code': part_dict['number_class'], 'name': part_class_name}
        part_class_form = PartClassForm(part_class_dict, instance=existing_part_class, ignore_unique_constraint=True,
                                        organization=self.organization)
        if self.organization.number_scheme == NUMBER_SCHEME_SEMI_INTELLIGENT and not part_class_form.is_valid():
            add_nonfield_error_from_existing(part_class_form, self, f'Row {row_count} - ')
            return None

        PartForm = part_form_from_organization(self.organization)
        part_form = PartForm(part_dict, instance=existing_part, ignore_part_class=True, ignore_unique_constraint=True,
                             organization=self.organization)
        if not part_form.is_valid():
            add_nonfield_error_from_existing(part_form, self, f'Row {row_count} - ')
            return None

        part_revision_form = PartRevisionForm(part_dict, instance=existing_part_revision,
                                              organization=self.organization)
        if not part_revision_form.is_valid():
            add_nonfield_error_from_existing(part_revision_form, self, f'Row {row_count} - ')
            return None

        subpart_form = SubpartForm(part_dict, instance=existing_subpart, ignore_part_revision=True,
                                   organization=self.organization)
        if not subpart_form.is_valid():
            add_nonfield_error_from_existing(subpart_form, self, f'Row {row_count} - ')
            return None

        # A None parent with a non-empty tree means an ancestor row was skipped, so this
        # descendant has no valid assembly to attach to. Skip it (recording no revision below
        # also orphans its own descendants) rather than mis-nesting it under the wrong parent
        # or rooting it as a top-level part. A None parent with an empty tree is a legitimate
        # top-level part and is left alone.
        if parent_part_revision is None and self.part_revision_tree:
            self.warnings.append(
                f"Row {row_count}: Skipped {part_number} because a parent row above it was not uploaded.")
            return None

        part_class = part_class_form.save(commit=False)
        part = part_form.save(commit=False)
        part_revision = part_revision_form.save(commit=False)
        subpart = subpart_form.save(commit=False)

        reference_list = listify_string(reference) if reference else []
        if len(reference_list) != len(set(reference_list)):
            self.warnings.append(
                f"Row {row_count}: Duplicate reference designators '{reference}' for subpart.")

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
        if manufacturer_name:
            existing_manufacturer = Manufacturer.objects.filter(name=manufacturer_name,
                                                                organization=self.organization).first()
            manufacturer_form = ManufacturerForm(
                {'name': manufacturer_name, 'approval_status': manufacturer_approval_status},
                instance=existing_manufacturer, organization=self.organization)
            if not manufacturer_form.is_valid():
                add_nonfield_error_from_existing(manufacturer_form, self, f'Row {row_count} - ')

            manufacturer = manufacturer_form.save(commit=False)

            manufacturer_part_data = {'manufacturer_part_number': manufacturer_part_number,
                                      'manufacturer': manufacturer}
            manufacturer_part_form = ManufacturerPartForm(manufacturer_part_data, organization=self.organization)
            if not manufacturer_part_form.is_valid():
                add_nonfield_error_from_existing(manufacturer_part_form, self, f'Row {row_count} - ')

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

        return part_revision


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


def sanitize_cost(raw):
    """Normalize a cost cell (which may carry a currency symbol / thousands separators, e.g.
    "A$1,190.00") into a plain decimal string MoneyField can parse. Empty or unparseable
    values are returned unchanged so existing validation/skip behavior is preserved."""
    if not raw:
        return raw
    parsed = parse_number(raw)
    return str(parsed) if parsed is not None else raw


def add_nonfield_error_from_existing(from_form, to_form, prefix=''):
    for field, errors in from_form.errors.as_data().items():
        for error in errors:
            for msg in error.messages:
                to_form.add_error(None, f'{prefix}{field}: {msg}')