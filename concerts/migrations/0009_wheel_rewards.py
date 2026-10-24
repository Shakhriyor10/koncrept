from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("concerts", "0008_order_canceled"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WheelReward",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(choices=[("discount_5", "Скидка 5%"), ("discount_10", "Скидка 10%"), ("discount_25", "Скидка 25%"), ("discount_50", "Скидка 50%"), ("gift_standard", "Стандартный билет в подарок"), ("gift_vip", "VIP-билет в подарок")], max_length=24)),
                ("spun_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="wheel_rewards", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "приз барабана", "verbose_name_plural": "призы барабана", "ordering": ("-spun_at",)},
        ),
        migrations.AddField(
            model_name="orderitem",
            name="discount_amount",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="order",
            name="wheel_reward",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="orders", to="concerts.wheelreward"),
        ),
    ]
