#!/bin/bash
set -euo pipefail
cd ../indabom
pipenv uninstall django-bom --skip-lock
pipenv install -e ../django-bom
pipenv lock