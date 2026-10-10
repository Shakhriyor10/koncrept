from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("concerts", "0007_ticket_type_details"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(
                choices=[
                    ("awaiting_payment", "Ожидает чек"),
                    ("under_review", "Проверка оплаты"),
                    ("approved", "Оплачено · билеты готовы"),
                    ("rejected", "Чек отклонён"),
                    ("canceled", "Заказ отменён · билеты аннулированы"),
                    ("expired", "Время проверки истекло"),
                ],
                default="awaiting_payment",
                max_length=24,
            ),
        ),
    ]
