#!/bin/bash
rm dist/*
rm build/*
python setup.py sdist
source ~/Code/mpkasp/indabom/venv/bin/activate && pip uninstall django_bom -y && python setup.py install
