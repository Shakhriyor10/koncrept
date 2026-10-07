from django.db import migrations

from concerts.phones import normalize_stored_phone


def normalize_profiles(apps, schema_editor):
    Profile = apps.get_model("concerts", "CustomerProfile")
    for profile in Profile.objects.all().iterator():
        normalized = normalize_stored_phone(profile.phone)
        if normalized and profile.phone != normalized:
            Profile.objects.filter(pk=profile.pk).update(phone=normalized)


class Migration(migrations.Migration):
    dependencies = [("concerts", "0002_phone_number_login")]

    operations = [
        migrations.RunPython(normalize_profiles, migrations.RunPython.noop),
    ]
