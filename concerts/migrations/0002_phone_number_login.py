from django.conf import settings
from django.db import migrations

from concerts.phones import normalize_stored_phone


def move_logins_to_phone(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    Profile = apps.get_model("concerts", "CustomerProfile")
    profiles = list(Profile.objects.select_related("user").all())

    mapping = {}
    phone_owners = {}
    for profile in profiles:
        phone = normalize_stored_phone(profile.phone)
        if not phone:
            continue
        if phone in phone_owners and phone_owners[phone] != profile.user_id:
            raise RuntimeError("Duplicate profile phone numbers found; resolve them before migrating phone login.")
        phone_owners[phone] = profile.user_id
        mapping[profile.user_id] = phone

    migrating_ids = set(mapping)
    for user_id, phone in mapping.items():
        collision = User.objects.filter(username=phone).exclude(pk=user_id).exclude(pk__in=migrating_ids).exists()
        if collision:
            raise RuntimeError("A phone number conflicts with an existing username; resolve it before migrating.")

    for user_id in mapping:
        User.objects.filter(pk=user_id).update(username=f"__phone_login_migration__{user_id}")
    for user_id, phone in mapping.items():
        User.objects.filter(pk=user_id).update(username=phone)


class Migration(migrations.Migration):
    dependencies = [
        ("concerts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(move_logins_to_phone, migrations.RunPython.noop),
    ]
