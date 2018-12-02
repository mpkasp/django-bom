from django import forms
from django.utils.translation import gettext_lazy as _
# from django.core.validators import DecimalValidator

from .models import Part, PartClass, Manufacturer, ManufacturerPart, Subpart, Seller, SellerPart, User, UserMeta, Organization
from .validators import decimal, alphanumeric, numeric
from json import dumps


class UserModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        return user.email


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", ]


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserMeta
        exclude = ['user', ]


class OrganizationForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(OrganizationForm, self).__init__(*args, **kwargs)
        user_queryset = User.objects.filter(id__in=UserMeta.objects.filter(organization=self.instance)).order_by('first_name')
        self.fields['owner'] = UserModelChoiceField(queryset=user_queryset, label='Owner', required=True)

    class Meta:
        model = Organization
        fields = ['name', 'owner', ]


class PartInfoForm(forms.Form):
    quantity = forms.IntegerField(label='Quantity', min_value=1)


class ManufacturerForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = False

    class Meta:
        model = Manufacturer
        exclude = ['organization', ]


class ManufacturerPartForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(ManufacturerPartForm, self).__init__(*args, **kwargs)
        self.fields['manufacturer'].required = False
        self.fields['manufacturer_part_number'].required = False
        self.fields['manufacturer'].queryset = Manufacturer.objects.filter(
            organization=self.organization).order_by('name')

    class Meta:
        model = ManufacturerPart
        exclude = ['part', ]


class SellerPartForm(forms.ModelForm):
    new_seller = forms.CharField(max_length=128, label='-or- Create new seller (leave blank if selecting)', required=False)
    field_order = ['seller', 'new_seller', 'unit_cost', 'nre_cost', 'lead_time_days', 'minimum_order_quantity', 'minimum_pack_quantity', ]

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.manufacturer_part = kwargs.pop('manufacturer_part', None)
        super(SellerPartForm, self).__init__(*args, **kwargs)
        if self.manufacturer_part is not None:
            self.instance.manufacturer_part = self.manufacturer_part
        self.fields['seller'].queryset = Seller.objects.filter(
            organization=self.organization).order_by('name')
        self.fields['seller'].required = False

    class Meta:
        model = SellerPart
        exclude = ['manufacturer_part', 'data_source', ]

    def clean(self):
        cleaned_data = super(SellerPartForm, self).clean()
        seller = cleaned_data.get("seller")
        new_seller = cleaned_data.get("new_seller")

        if seller and new_seller:
            raise forms.ValidationError(
                ('Cannot have a seller and a new seller.'),
                code='invalid')
        elif new_seller:
            obj, created = Seller.objects.get_or_create(name__iexact=new_seller, organization=self.organization, defaults={'name': new_seller})
            cleaned_data['seller'] = obj
        elif not seller:
            raise forms.ValidationError(
                ('Must specify a seller.'),
                code='invalid')


class PartForm(forms.ModelForm):
    number_class = forms.ModelChoiceField(queryset=PartClass.objects.all(), empty_label="- Select Part Number Class -", label='Part Number Class*', required=True)

    def __init__(self, *args, **kwargs):
        super(PartForm, self).__init__(*args, **kwargs)
        queryset = ManufacturerPart.objects.none()
        if 'instance' in kwargs and type(kwargs['instance']) is Part:
            queryset = ManufacturerPart.objects.filter(part=kwargs['instance'])
        self.fields['primary_manufacturer_part'].queryset = queryset
        for _, value in self.fields.items():
            value.widget.attrs['placeholder'] = value.help_text
            value.help_text = ''

    class Meta:
        model = Part
        exclude = ['organization', 'subparts', ]
        help_texts = {
            'number_class': _('Select a number class.'),
            'number_item': _('Auto generated if blank.'),
            'number_variation': 'Auto generated if blank.',
            'description': 'E.g. CAPACITOR, CERAMIC, 100pF, 0402 50V, +/-5%',
        }


class SubpartForm(forms.ModelForm):
    class Meta:
        model = Subpart
        exclude = ['assembly_part', ]

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.part_id = kwargs.pop('part_id', None)
        super(SubpartForm, self).__init__(*args, **kwargs)

        part = None
        unusable_part_ids = []
        if self.part_id:
            part = Part.objects.get(id=self.part_id)
            unusable_part_ids = [p.id for p in part.where_used_full()]
            unusable_part_ids.append(part.id)
        self.fields['assembly_subpart'].queryset = Part.objects.filter(
            organization=self.organization).exclude(id__in=unusable_part_ids).order_by(
            'number_class__code', 'number_item', 'number_variation')
        self.fields['assembly_subpart'].label_from_instance = \
            lambda obj: "%s" % obj.full_part_number(
        ) + ' ' + obj.description


class AddSubpartForm(forms.Form):
    assembly_subpart = forms.ModelChoiceField(
        queryset=None, required=True, label="Subpart")
    count = forms.IntegerField(required=True, label='Quantity')
    reference = forms.CharField(required=False, label="Reference")

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        self.part_id = kwargs.pop('part_id', None)
        super(AddSubpartForm, self).__init__(*args, **kwargs)

        part = None
        unusable_part_ids = []
        if self.part_id:
            part = Part.objects.get(id=self.part_id)
            unusable_part_ids = [p.id for p in part.where_used_full()]
            unusable_part_ids.append(part.id)
        self.fields['assembly_subpart'].queryset = Part.objects.filter(
            organization=self.organization).exclude(id__in=unusable_part_ids).order_by(
            'number_class__code', 'number_item', 'number_variation')
        self.fields['assembly_subpart'].label_from_instance = \
            lambda obj: "%s" % obj.full_part_number(
        ) + ' ' + obj.description


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
        validators=[numeric], widget=forms.TextInput(attrs={'placeholder': 'None'}))
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
        seller = cleaned_data.get("seller")
        new_seller = cleaned_data.get("new_seller")

        if seller and new_seller:
            raise forms.ValidationError(
                ('Cannot have a seller and a new seller.'),
                code='invalid')
        elif new_seller:
            obj, created = Seller.objects.get_or_create(name__iexact=new_seller, organization=self.organization, defaults={'name': new_seller})
            cleaned_data['seller'] = obj
        elif not seller:
            raise forms.ValidationError(
                ('Must specify a seller.'),
                code='invalid')

class FileForm(forms.Form):
    file = forms.FileField()
