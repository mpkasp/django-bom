from django.db import migrations


def strip_apply_scope(apps, schema_editor):
    """The apply scope is gone and release is a wider grant, so a token that had apply keeps only read and write."""
    APIToken = apps.get_model('bom', 'APIToken')
    for token in APIToken.objects.filter(scopes__contains='apply'):
        token.scopes = ','.join(scope for scope in token.scopes.split(',') if scope and scope != 'apply')
        token.save(update_fields=['scopes'])


class Migration(migrations.Migration):

    dependencies = [
        ('bom', '0071_change_orders'),
    ]

    operations = [
        migrations.RunPython(strip_apply_scope, migrations.RunPython.noop),
    ]
