#!/bin/bash
vim setup.py
python setup.py sdist
twine upload dist/*
