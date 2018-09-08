#!/bin/bash
python manage.py test
read -p "Continue? " -n 1 -r
if [[ $REPLY =~ ^[Yy]$ ]]
then
    vim setup.py
    rm dist/*
    rm build/*
    python setup.py sdist
    twine upload dist/*
fi
./update-local.sh
