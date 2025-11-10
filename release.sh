#!/usr/bin/env bash
set -euo pipefail

# Run tests inside the Pipenv environment
pipenv run python manage.py test

read -p "Continue? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
  # Optionally bump/edit version in pyproject.toml
  ${EDITOR:-vim} pyproject.toml

  # Clean old build artifacts
  rm -rf dist build

  # Build sdist and wheel using PEP 517 via pyproject.toml
  pipenv run python -m pip install --upgrade build twine
  pipenv run python -m build

  # Validate the artifacts before upload
  pipenv run python -m twine check dist/*

  # Upload to PyPI
  pipenv run python -m twine upload dist/*

  # Post-release local update script (if any)
  ./update-local.sh
fi
