from django import forms
from django.test import Client, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.conf import settings

from bom.helpers import create_some_fake_parts, create_user_and_organization
from bom.templatetags.bom_forms import bom_form, grid_classes


class TestBomFormHelpers(SimpleTestCase):
    def test_grid_classes_map_materialize_tokens(self):
        self.assertEqual(grid_classes(""), "col-span-12")
        self.assertEqual(grid_classes("m6 s12"), "md:col-span-6 col-span-12")
        self.assertEqual(grid_classes("l2 m3 s12"), "lg:col-span-2 md:col-span-3 col-span-12")

    def test_render_text_and_checkbox_fields(self):
        class SampleForm(forms.Form):
            name = forms.CharField(label="Name")
            agree = forms.BooleanField(label="Agree", required=False)

        html = str(bom_form(SampleForm()))
        self.assertIn("bom-input", html)
        self.assertIn("Name", html)
        self.assertIn('type="checkbox"', html)


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestUiSmoke(TransactionTestCase):
    def setUp(self):
        self.client = Client()

    def test_login_page_uses_tailwind_not_materialize(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('lang="fa"', html)
        self.assertIn('dir="rtl"', html)
        self.assertIn("bom/css/app.css", html)
        self.assertNotIn("materialize.min.css", html)
        self.assertNotIn("materialize.min.js", html)
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)
        self.assertIn("@media print", open("bom/static/bom/css/app.css").read())

    def test_login_form_error_display(self):
        response = self.client.post(reverse("login"), {"username": "x", "password": "bad"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertTrue("error" in html.lower() or "bom-error" in html)

    def test_authenticated_navigation_and_dashboard(self):
        user, organization = create_user_and_organization()
        profile = user.bom_profile(organization=organization)
        profile.role = "A"
        profile.save()
        self.client.login(username="kasper", password="ghostpassword")
        create_some_fake_parts(organization=organization)

        home = self.client.get(reverse("bom:home"))
        self.assertEqual(home.status_code, 200)
        html = home.content.decode("utf-8")
        self.assertIn("bom/css/app.css", html)
        self.assertNotIn("materialize.min.css", html)
        self.assertIn("bom-action-bar", html)
        self.assertIn("bom-nav-menu", html)
        self.assertIn("bom-nav-link", html)
        self.assertIn("متریال جدید", html)
        self.assertIn("Raw Material", html)
        self.assertIn("Products", html)
        self.assertIn('id="bom-nav-toggle"', html)

        css = open("bom/static/bom/css/app.css").read()
        src = open("assets/src/input.css").read()
        self.assertIn(".bom-nav-menu", css)
        self.assertIn(".bom-nav-checkbox:checked", src)
        self.assertNotRegex(
            src,
            r"(?m)^\.hidden\s*\{",
            "Unlayered .hidden must not override Tailwind lg:flex / sm:inline",
        )
        # Global link color must stay in @layer base so a.bom-btn-primary keeps white text
        self.assertIn("@layer base", src)
        self.assertRegex(
            src,
            r"@layer\s+base[\s\S]*?\ba\s*\{[^}]*color:\s*var\(--color-primary\)",
            "Unlayered a { color } makes primary button links green-on-green",
        )
        self.assertIn("hover:text-white", src)
        self.assertIn(".bom-btn-primary{", css)
        self.assertIn("color:var(--color-white)", css)
        self.assertIn("a{color:var(--color-primary)}", css)

        part = organization.part_set.first()
        info = self.client.get(reverse("bom:part-info", kwargs={"part_id": part.id}))
        self.assertEqual(info.status_code, 200)
        info_html = info.content.decode("utf-8")
        self.assertIn('id="tabs"', info_html)
        self.assertIn("dropdown-trigger", info_html)
        self.assertIn("bom-table", info_html)
        self.assertIn("collection", info_html)
        self.assertNotIn("متریال جدید", info_html)
        self.assertNotIn("آپلود متریال", info_html)
        self.assertNotIn(reverse("bom:create-part"), info_html)
        self.assertNotIn(reverse("bom:upload-parts"), info_html)

        sellers = self.client.get(reverse("bom:sellers"))
        self.assertEqual(sellers.status_code, 200)
        sellers_html = sellers.content.decode("utf-8")
        self.assertIn("bom-table-wrap", sellers_html)
        self.assertIn("bom-table", sellers_html)
        self.assertIn("bom-input", sellers_html)
        self.assertIn("bom-btn-primary", sellers_html)
        self.assertNotIn("striped highlight", sellers_html)

        self.assertIn("bom-table", html)
        self.assertRegex(
            src,
            r"\.bom-table-wrap\s*\{[^}]*max-height",
            "Long tables need a scrollport so sticky thead can work",
        )
        self.assertIn("sticky", css)
        self.assertIn("bom-btn-primary", html)
        self.assertNotIn("waves-effect waves-light btn green", html)

        create = self.client.get(reverse("bom:create-part"))
        self.assertEqual(create.status_code, 200)
        self.assertIn("bom-input", create.content.decode("utf-8"))
        self.assertIn("bom/js/jquery.autocomplete-bom.js", create.content.decode("utf-8"))

    def test_settings_password_reset_uses_tailwind_form_classes(self):
        user, organization = create_user_and_organization()
        self.client.login(username="kasper", password="ghostpassword")
        response = self.client.get(reverse("bom:settings", kwargs={"tab_anchor": "user"}))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('id="password-reset"', html)
        self.assertIn("bom-panel-pad", html)
        self.assertIn('id="current_password"', html)
        self.assertIn('class="bom-input"', html)
        self.assertIn('class="bom-label"', html)
        self.assertIn("bom-btn-primary", html)
