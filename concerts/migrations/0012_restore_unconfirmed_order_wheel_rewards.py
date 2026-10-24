from django.db import migrations


def restore_rewards_for_unconfirmed_orders(apps, schema_editor):
    Order = apps.get_model("concerts", "Order")
    WheelReward = apps.get_model("concerts", "WheelReward")
    db = schema_editor.connection.alias
    reserved_reward_ids = Order.objects.using(db).filter(
        wheel_reward__isnull=False,
        status__in=("under_review", "expired", "approved"),
    ).values_list("wheel_reward_id", flat=True)
    recoverable_reward_ids = Order.objects.using(db).filter(
        wheel_reward__isnull=False,
        status__in=("awaiting_payment", "rejected"),
    ).exclude(wheel_reward_id__in=reserved_reward_ids).values_list("wheel_reward_id", flat=True)
    WheelReward.objects.using(db).filter(pk__in=recoverable_reward_ids).update(claimed_at=None)


class Migration(migrations.Migration):
    dependencies = [("concerts", "0011_persist_guest_wheel_rewards")]

    operations = [migrations.RunPython(restore_rewards_for_unconfirmed_orders, migrations.RunPython.noop)]
