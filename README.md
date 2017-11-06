BOM
=====

BOM is a simple Django app to manage a bill of materials. It is
a level up from an excel spreadsheet including an indented
bill of materials, octopart price match, and more.

An example of the app in use can be seen [here](https://www.indabom.com).



Quick start
-----------

```
pip install django-bom
```

1. Add "bom" to your INSTALLED_APPS setting like this::

    INSTALLED_APPS = [
        ...
        'bom',
    ]

2. Include the bom URLconf in your project urls.py like this::

    url(r'^bom/', include('bom.urls')),

3. Run `python manage.py migrate` to create the bom models.

4. Start the development server and visit http://127.0.0.1:8000/admin/
   to manage the bom (you'll need the Admin app enabled).

5. Visit http://127.0.0.1:8000/bom/ to begin.


Octopart Integration
--------------------
For part matching, make sure to add your Octopart api key to your settings.py in 
the BOM_CONFIG dictionary.
```
BOM_CONFIG = {
    'octopart_api_key': 'supersecretkey',
}
```
You can get an Octopart api key [here](https://octopart.com/api/home).
