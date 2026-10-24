from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("concerts", "0009_wheel_rewards")]

    operations = [
        migrations.AlterField(
            model_name="wheelreward",
            name="code",
            field=models.CharField(
                choices=[
                    ("discount_5", "Скидка 5% (ранее)"),
                    ("discount_10", "Скидка 10%"),
                    ("discount_20", "Скидка 20%"),
                    ("discount_25", "Скидка 25% (ранее)"),
                    ("discount_30", "Скидка 30%"),
                    ("discount_50", "Скидка 50% (ранее)"),
                    ("discount_60", "Скидка 60%"),
                    ("gift_standard", "Стандартный билет в подарок"),
                    ("gift_vip", "VIP-билет в подарок"),
                ],
                max_length=24,
            ),
        ),
    ]
