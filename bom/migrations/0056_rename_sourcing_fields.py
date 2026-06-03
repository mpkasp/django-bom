from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('bom', '0055_manufacturer_approval_status_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='partclass',
            old_name='mouser_enabled',
            new_name='sourcing_enabled',
        ),
        migrations.RenameField(
            model_name='manufacturerpart',
            old_name='mouser_disable',
            new_name='sourcing_disable',
        ),
    ]
