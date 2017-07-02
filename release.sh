#!/bin/bash
vim setup.py
rm dist/*
python setup.py sdist
twine upload dist/*
