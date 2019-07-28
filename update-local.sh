#!/bin/bash
rm dist/*
rm build/*
python setup.py sdist
source ~/Code/virtualenv/indabom/bin/activate && pip uninstall django-bom -y && python setup.py install
