from django import forms
from django.utils.safestring import mark_safe
from json import dumps


class AutocompleteTextInput(forms.TextInput):
    def __init__(self, *args, **kwargs):
        self.queryset = kwargs.pop('queryset')
        self.autocomplete_limit = kwargs.pop('autocomplete_limit', None)
        self.autocomplete_min_length = kwargs.pop('autocomplete_min_length', 0)
        self.autocomplete_submit = kwargs.pop('autocomplete_submit', False)
        self.form_name = kwargs.pop('form_name', 'form')
        super().__init__(*args, **kwargs)

    def render(self, name, value, attrs=None, renderer=None):
        # Disable chrome autocomplete..we dont want double duty here!
        if attrs is not None:
            attrs.update({'autocomplete': 'off'})
        else:
            attrs['autocomplete'] = "off"

        html = super().render(name, value, attrs)

        autocomplete_dict = {}
        for obj in self.queryset:
            autocomplete_dict.update({str(obj): None})

        autocomplete_json = dumps(autocomplete_dict).replace("'", "\\'")

        # To escape brackets in a Python 3.6 f-string we use double brackets
        inline_code = mark_safe(
            f"""<script>
            const {name}_data = JSON.parse('{autocomplete_json}');
            const {name}_input = document.getElementById("id_{name}");
            const {name}_form = {name}_input.form;
            $(document).ready(function () {{
                $('#id_{name}').autocomplete({{
                    data: {name}_data,
                    limit: {self.autocomplete_limit or 'undefined'}, // The max amount of results that can be shown at once. Default: Infinity.
                    minLength: {self.autocomplete_min_length}, // The minimum length of the input for the autocomplete to start. Default: 1.
                    onAutocomplete: function (val) {{
                        {f'{name}_form.submit()' if self.autocomplete_submit else ''}
                    }},
                }});
            }});
            </script>"""
        )
        return html + inline_code
