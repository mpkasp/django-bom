#!/bin/bash
python manage.py test
vim setup.py
rm dist/*
python setup.py sdist
twine upload dist/*
