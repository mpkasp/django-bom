import os

from setuptools import find_packages, setup


with open(os.path.join(os.path.dirname(__file__), 'README.md')) as readme:
    README = readme.read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setup(
    name='django-bom',
    version='1.234',
    packages=find_packages(),
    include_package_data=True,
    license='GPL-3.0-only',
    description='A simple Django app to manage a bill of materials.',
    long_description=README,
    long_description_content_type='text/markdown',
    url='https://www.indabom.com/',
    author='Mike Kasparian',
    author_email='mike@indabom.com',
    python_requires='>=3.10',
    classifiers=[
        'Environment :: Web Environment',
        'Framework :: Django',
        'Framework :: Django :: 5',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content',
    ],
    install_requires=[
        'Django>=5.2,<5.3',
        'social-auth-app-django>=5,<6',
        'social-auth-core>=4,<6',
        'google-api-python-client>=2',
        'django-materializecss-form',
        'django-money>=3,<5',
    ],
)
