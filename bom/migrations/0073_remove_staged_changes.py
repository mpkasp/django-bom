from django.db import migrations
from django.utils import timezone


def withdraw_pending_staged_changes(apps, schema_editor):
    """A pending change that lists staged edits and holds no revisions can no longer be applied, so it is withdrawn."""
    Change = apps.get_model('bom', 'Change')
    pending = Change.objects.filter(
        number__isnull=False, is_direct_edit=False, status__in=('draft', 'open'), revisions__isnull=True,
        items__isnull=False).values_list('pk', flat=True).distinct()
    Change.objects.filter(pk__in=list(pending)).update(status='withdrawn', resolved_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ('bom', '0072_apitoken_release_scope'),
    ]

    operations = [
        migrations.RunPython(withdraw_pending_staged_changes, migrations.RunPython.noop),
        migrations.RemoveField(model_name='change', name='base_digest'),
        migrations.RemoveField(model_name='change', name='edit_log'),
        migrations.RemoveField(model_name='changeitem', name='ref'),
        migrations.RemoveField(model_name='changeitem', name='status'),
        migrations.RemoveField(model_name='changeitem', name='reject_reason'),
        migrations.RemoveField(model_name='changesettings', name='protect_primary_manufacturer_part'),
        migrations.RemoveField(model_name='changesettings', name='protect_cost_delta'),
    ]
