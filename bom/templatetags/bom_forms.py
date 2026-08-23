from django import forms
from django.forms.boundfield import BoundField
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django import template

register = template.Library()

INPUT_CLASS = "bom-input"
CHECKBOX_CLASS = "rounded border-border text-primary focus:ring-primary"

COL_MAP = {
    "s1": "col-span-1",
    "s2": "col-span-2",
    "s3": "col-span-3",
    "s4": "col-span-4",
    "s5": "col-span-5",
    "s6": "col-span-6",
    "s7": "col-span-7",
    "s8": "col-span-8",
    "s9": "col-span-9",
    "s10": "col-span-10",
    "s11": "col-span-11",
    "s12": "col-span-12",
    "m1": "md:col-span-1",
    "m2": "md:col-span-2",
    "m3": "md:col-span-3",
    "m4": "md:col-span-4",
    "m5": "md:col-span-5",
    "m6": "md:col-span-6",
    "m7": "md:col-span-7",
    "m8": "md:col-span-8",
    "m9": "md:col-span-9",
    "m10": "md:col-span-10",
    "m11": "md:col-span-11",
    "m12": "md:col-span-12",
    "l1": "lg:col-span-1",
    "l2": "lg:col-span-2",
    "l3": "lg:col-span-3",
    "l4": "lg:col-span-4",
    "l5": "lg:col-span-5",
    "l6": "lg:col-span-6",
    "l7": "lg:col-span-7",
    "l8": "lg:col-span-8",
    "l9": "lg:col-span-9",
    "l10": "lg:col-span-10",
    "l11": "lg:col-span-11",
    "l12": "lg:col-span-12",
    "hidden": "hidden",
}


def grid_classes(spec):
    if not spec:
        return "col-span-12"
    parts = []
    for token in spec.split():
        parts.append(COL_MAP.get(token, token))
    return " ".join(parts)


def widget_kind(field):
    widget = field.field.widget
    name = widget.__class__.__name__.lower()
    input_type = getattr(widget, "input_type", "")
    if input_type == "hidden" or "hidden" in name:
        return "hidden"
    if input_type == "checkbox" or "checkbox" in name:
        return "checkbox"
    if input_type == "radio" or "radio" in name:
        return "radio"
    if "select" in name:
        return "select"
    if "textarea" in name:
        return "textarea"
    if "file" in name or "clearablefile" in name:
        return "file"
    return "input"


def merge_attrs(existing, extra):
    merged = dict(existing or {})
    extra = dict(extra or {})
    extra_class = extra.pop("class", "")
    if extra_class:
        merged["class"] = f"{merged.get('class', '')} {extra_class}".strip()
    merged.update(extra)
    return merged


def render_bound_field(field, spec=""):
    kind = widget_kind(field)
    cols = grid_classes(spec)
    attrs = {}
    if kind in ("input", "textarea", "select", "file"):
        attrs["class"] = INPUT_CLASS
    elif kind == "checkbox":
        attrs["class"] = CHECKBOX_CLASS
    widget = field.as_widget(attrs=merge_attrs(field.field.widget.attrs, attrs)) if kind != "hidden" else field.as_widget()
    if kind == "hidden":
        return mark_safe(str(field))
    return mark_safe(
        render_to_string(
            "bom/ui/field.html",
            {
                "field": field,
                "kind": kind,
                "cols": cols,
                "widget": mark_safe(widget),
            },
        )
    )


@register.filter(name="bom_form")
def bom_form(element, spec=""):
    if isinstance(element, BoundField):
        return render_bound_field(element, spec)
    if isinstance(element, (forms.BaseForm, forms.BaseFormSet)):
        parts = []
        form = element
        if hasattr(form, "non_field_errors"):
            errors = form.non_field_errors()
            if errors:
                parts.append(
                    render_to_string(
                        "bom/ui/alert.html",
                        {"variant": "error", "message": errors},
                    )
                )
        visible = []
        if hasattr(form, "visible_fields"):
            for field in form.visible_fields():
                visible.append(render_bound_field(field, spec))
        hidden = ""
        if hasattr(form, "hidden_fields"):
            hidden = "".join(str(f) for f in form.hidden_fields())
        group = render_to_string(
            "bom/ui/field-group.html",
            {"fields": mark_safe("".join(visible))},
        )
        return mark_safe("".join(parts) + group + hidden)
    return mark_safe(str(element))
