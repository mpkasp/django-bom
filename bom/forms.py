from django import forms

from .models import Part, PartClass, Manufacturer


class PartInfoForm(forms.Form):
    quantity = forms.IntegerField(label='Quantity', min_value=1)


class PartForm(forms.Form):
    partclasses = PartClass.objects.all()

    number_class = forms.ModelChoiceField(
        queryset=partclasses, label='Part Class*')
    number_item = forms.CharField(
        max_length=4,
        label='Part Number',
        required=False)
    number_variation = forms.CharField(
        max_length=2, label='Part Variation', required=False)
    description = forms.CharField(max_length=255, label='Description*')
    revision = forms.CharField(max_length=2, label='Revision*')
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


class AddSubpartForm(forms.Form):
    assembly_subpart = forms.ModelChoiceField(
        queryset=None, required=True, label="Subpart")
    count = forms.IntegerField(required=True, label='Quantity')

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop('organization', None)
        super(AddSubpartForm, self).__init__(*args, **kwargs)
        self.fields['assembly_subpart'].queryset = Part.objects.filter(
            organization=self.organization).order_by(
            'number_class__code', 'number_item', 'number_variation')
        self.fields['assembly_subpart'].label_from_instance = \
            lambda obj: "%s" % obj.full_part_number(
        ) + ' ' + obj.description


class FileForm(forms.Form):
    file = forms.FileField()
