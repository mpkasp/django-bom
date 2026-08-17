#!/bin/sh

if [ "$DATABASE" = "postgres" ]
then
    echo "Waiting for postgres..."

    while ! nc -z $SQL_HOST $SQL_PORT; do
      sleep 0.1
    done

    echo "PostgreSQL started"
fi

python manage.py collectstatic --no-input --clear
# python manage.py flush --no-input
# python manage.py makemigrations

# Apply migrations only if there are unapplied migrations
# if python manage.py showmigrations --unapplied | grep '\[ \]'; then
#     echo "Applying unapplied migrations..."
#     python manage.py migrate || {
#         echo "Migration conflict detected. Marking migrations as applied..."
#         python manage.py migrate --fake
#     }
# else
#     echo "No migrations to apply."
# fi

python manage.py migrate

# python manage.py createsuperuser

if [ "$#" -gt 0 ]; then
    exec "$@"
fi