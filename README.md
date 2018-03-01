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

```
    INSTALLED_APPS = [
        ...
        'bom',
    ]
```

2. Update your URLconf in your project urls.py like this::

```
    url(r'^bom/', include('bom.urls')),
```

And don't forget to import include:

```
    from django.conf.urls import include
```

3. Add the bom context processor `'bom.context_processors.bom_config',` to your TEMPLATES variable in settings:

```
TEMPLATES = [
    {
        ...
        'OPTIONS': {
            'context_processors': [
                ...
                'bom.context_processors.bom_config',
            ],
        },
    },
]
```

4. Run `python manage.py migrate` to create the bom models.

5. Start the development server and visit http://127.0.0.1:8000/admin/
   to manage the bom (you'll need the Admin app enabled).

6. Visit http://127.0.0.1:8000/bom/ to begin.

Customize Base Template
--------------------
The base template can be customized to your pleasing. Just add the following configuration to your settings.py:

```
BOM_CONFIG = {
    'base_template': 'base.html',
}
```

where `base.html` is your base template.

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
