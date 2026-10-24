from django.db import migrations


def release_rewards_from_unapproved_orders(apps, schema_editor):
    Order = apps.get_model("concerts", "Order")
    WheelReward = apps.get_model("concerts", "WheelReward")
    db = schema_editor.connection.alias
    approved_reward_ids = Order.objects.using(db).filter(
        wheel_reward__isnull=False, status="approved"
    ).values_list("wheel_reward_id", flat=True)
    pending_reward_ids = Order.objects.using(db).filter(
        wheel_reward__isnull=False,
        status__in=("awaiting_payment", "under_review", "expired", "rejected"),
    ).exclude(wheel_reward_id__in=approved_reward_ids).values_list("wheel_reward_id", flat=True)
    WheelReward.objects.using(db).filter(pk__in=pending_reward_ids).update(claimed_at=None)


class Migration(migrations.Migration):
    dependencies = [("concerts", "0012_restore_unconfirmed_order_wheel_rewards")]
    operations = [migrations.RunPython(release_rewards_from_unapproved_orders, migrations.RunPython.noop)]
