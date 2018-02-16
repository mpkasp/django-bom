from django import forms

from .models import Part, PartClass, Manufacturer, Subpart, Seller
from .validators import numeric, alphanumeric


class PartInfoForm(forms.Form):
    quantity = forms.IntegerField(label='Quantity', min_value=1)


class PartForm(forms.Form):
    partclasses = PartClass.objects.all()

    number_class = forms.ModelChoiceField(
        queryset=partclasses, label='Part Class*')
    number_item = forms.CharField(
        max_length=4,
        label='Part Number',
        required=False, 
        validators=[numeric],
        widget=forms.TextInput(attrs={'placeholder': 'Auto-Generated if blank'}))
    number_variation = forms.CharField(
        max_length=2, label='Part Variation', required=False, 
        validators=[alphanumeric],
        widget=forms.TextInput(attrs={'placeholder': 'Auto-Generated if blank'}))
    description = forms.CharField(max_length=255, label='Description*')
    revision = forms.CharField(max_length=2, label='Revision*', initial=1)
    manufacturer_part_number = forms.CharField(max_length=128, required=False)
    manufacturer = forms.ModelChoiceField(queryset=None, required=False)
    new_manufacturer = forms.CharField(
        max_length=128,
        label='Create New Manufacturer',
        required=False)

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(PartForm, self).__init__(*args, **kwargs)
        self.fields['manufacturer'].queryset = Manufacturer.objects.filter(
            organization=self.organization)

    def clean(self):
        cleaned_data = super(PartForm, self).clean()
        mfg = cleaned_data.get("manufacturer")
        new_mfg = cleaned_data.get("new_manufacturer")

        if mfg and new_mfg:
            raise forms.ValidationError(
                ('Cannot have a manufacturer and a new manufacturer'),
                code='invalid')
        elif new_mfg:
            obj = Manufacturer(name=new_mfg, organization=self.organization)
            obj.save()
            cleaned_data['manufacturer'] = obj
        elif not mfg and not new_mfg:
            obj, c = Manufacturer.objects.get_or_create(name=self.organization.name.upper(), organization=self.organization)
            cleaned_data['manufacturer'] = obj


class AddSubpartForm(forms.Form):
    assembly_subpart = forms.ModelChoiceField(
        queryset=None, required=True, label="Subpart")
    count = forms.IntegerField(required=True, label='Quantity')

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
    seller = forms.ModelChoiceField(queryset=None, required=False, label="Seller")
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
        validators=[numeric],
        widget=forms.TextInput(attrs={'placeholder': '0.00'}))
    lead_time_days = forms.IntegerField(required=False, 
        label='Lead Time (days)', 
        validators=[numeric],
        widget=forms.TextInput(attrs={'placeholder': 'None'}))
    nre_cost = forms.DecimalField(required=False, 
        label='NRE Cost', 
        validators=[numeric],
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
            obj = Seller(name=new_seller, organization=self.organization)
            obj.save()
            cleaned_data['seller'] = obj
        elif not seller:
            raise forms.ValidationError(
                ('Must specify a seller.'),
                code='invalid')

class FileForm(forms.Form):
    file = forms.FileField()
