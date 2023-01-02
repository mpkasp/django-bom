import os

from setuptools import find_packages, setup


with open(os.path.join(os.path.dirname(__file__), 'README.md')) as readme:
    README = readme.read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setup(
    name='django-bom',
    version='1.227',
    packages=find_packages(),
    include_package_data=True,
    license='GPL 3.0 License',
    description='A simple Django app to manage a bill of materials.',
    long_description=README,
    long_description_content_type='text/markdown',
    url='https://www.indabom.com/',
    author='Mike Kasparian',
    author_email='mpkasp@gmail.com',
    classifiers=[
        'Environment :: Web Environment',
        'Framework :: Django',
        'Framework :: Django :: 3.1',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.7',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content',
    ],
    install_requires=[
        'python-social-auth',
        'social-auth-app-django',
        'google-api-python-client',
        'django-materializecss-form',
        'django-money',
    ],
)
