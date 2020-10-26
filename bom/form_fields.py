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
        self.verbose_string_function = kwargs.pop('verbose_string_function', str)
        super().__init__(*args, **kwargs)

    def render(self, name, value, attrs=None, renderer=None):
        # verbose_string_function is used if we want to show verbose strings in the dropdown,
        # but autocomplete to something simpler

        # Disable chrome autocomplete..we dont want double duty here!
        if attrs is not None:
            attrs.update({'autocomplete': 'off'})
        else:
            attrs['autocomplete'] = "off"

        html = super().render(name, value, attrs)

        autocomplete_dict = {}
        autocomplete_dict_to_fill = {}
        for obj in self.queryset:
            verbose_obj_str = self.verbose_string_function(obj).replace('"', '\\"')
            autocomplete_dict.update({verbose_obj_str: None})
            autocomplete_dict_to_fill.update({verbose_obj_str: str(obj)})

        autocomplete_json = dumps(autocomplete_dict).replace("'", "\\'")
        autocomplete_json_to_fill = dumps(autocomplete_dict_to_fill).replace("'", "\\'")

        # To escape brackets in a Python 3.6 f-string we use double brackets
        inline_code = mark_safe(
            f"""<script>
            const {name}_data = JSON.parse('{autocomplete_json}');
            const {name}_data_to_fill = JSON.parse('{autocomplete_json_to_fill}');
            const {name}_input = document.getElementById("id_{name}");
            const {name}_form = {name}_input.form;
            $(document).ready(function () {{
                $('#id_{name}').autocomplete({{
                    data: {name}_data,
                    limit: {self.autocomplete_limit or 'undefined'}, // The max amount of results that can be shown at once. Default: Infinity.
                    minLength: {self.autocomplete_min_length}, // The minimum length of the input for the autocomplete to start. Default: 1.
                    onAutocomplete: function (val) {{
                        console.log({name}_data_to_fill);
                        $("#id_{name}").val({name}_data_to_fill[val]);
                        {f'{name}_form.submit()' if self.autocomplete_submit else ''}
                    }},
                }});
            }});
            </script>"""
        )
        return html + inline_code
