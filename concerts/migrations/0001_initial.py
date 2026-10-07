import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from concerts.identifiers import make_order_reference
from concerts.uploads import proof_upload_path


def seed_concert(apps, schema_editor):
    Concert = apps.get_model("concerts", "Concert")
    TicketType = apps.get_model("concerts", "TicketType")
    concert, _ = Concert.objects.get_or_create(
        slug="eldar-dalgatov",
        defaults={
            "artist_name": "Эльдар Далгатов",
            "event_date": "2026-10-25",
            "venue": "Silk Road Samarkand",
            "city": "Самарканд",
            "tagline": "Вечер любимых песен и живых эмоций",
            "description": "Большой сольный концерт Эльдара Далгатова в Silk Road Samarkand.",
            "is_active": True,
        },
    )
    for code, title, price, description, order in (
        ("vip", "VIP", 1_000_000, "VIP билет на концерт", 1),
        ("standard", "Стандарт", 500_000, "Стандартный билет на концерт", 2),
        ("fan_zone", "Фан-зона", 300_000, "Билет в фан-зону", 3),
    ):
        TicketType.objects.get_or_create(
            concert=concert,
            code=code,
            defaults={"title": title, "price": price, "description": description, "active": True, "sort_order": order},
        )


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Concert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True)),
                ("artist_name", models.CharField(max_length=160)),
                ("event_date", models.DateField()),
                ("venue", models.CharField(max_length=180)),
                ("city", models.CharField(default="Самарканд", max_length=100)),
                ("tagline", models.CharField(blank=True, max_length=240)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"verbose_name": "концерт", "verbose_name_plural": "концерты", "ordering": ("event_date",)},
        ),
        migrations.CreateModel(
            name="CustomerProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone", models.CharField(max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "профиль покупателя", "verbose_name_plural": "профили покупателей"},
        ),
        migrations.CreateModel(
            name="PaymentCard",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("humo", "HUMO"), ("uzcard", "UZCARD")], max_length=12)),
                ("label", models.CharField(blank=True, help_text="Например, «Карта для оплаты VIP».", max_length=80)),
                ("card_number", models.CharField(help_text="Номер карты без пробелов или с пробелами.", max_length=32)),
                ("cardholder", models.CharField(max_length=120)),
                ("instructions", models.CharField(blank=True, max_length=240)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"verbose_name": "карта для перевода", "verbose_name_plural": "карты для перевода", "ordering": ("sort_order", "provider", "id")},
        ),
        migrations.CreateModel(
            name="TicketType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(choices=[("vip", "VIP"), ("standard", "Стандарт"), ("fan_zone", "Фан-зона")], max_length=20)),
                ("title", models.CharField(max_length=80)),
                ("description", models.CharField(blank=True, max_length=240)),
                ("price", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("concert", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ticket_types", to="concerts.concert")),
            ],
            options={"verbose_name": "тип билета", "verbose_name_plural": "типы билетов", "ordering": ("sort_order", "price")},
        ),
        migrations.CreateModel(
            name="Order",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("reference", models.CharField(default=make_order_reference, editable=False, max_length=11, unique=True)),
                ("status", models.CharField(choices=[("awaiting_payment", "Ожидает чек"), ("under_review", "Проверка оплаты"), ("approved", "Оплачено · билеты готовы"), ("rejected", "Чек отклонён"), ("expired", "Время проверки истекло")], default="awaiting_payment", max_length=24)),
                ("first_name", models.CharField(max_length=80)),
                ("last_name", models.CharField(max_length=80)),
                ("phone", models.CharField(max_length=32)),
                ("email", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("proof_submitted_at", models.DateTimeField(blank=True, null=True)),
                ("review_deadline", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_note", models.CharField(blank=True, max_length=500)),
                ("concert", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="orders", to="concerts.concert")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_orders", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="concert_orders", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "заказ", "verbose_name_plural": "заказы", "ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("unit_price", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="concerts.order")),
                ("ticket_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="order_items", to="concerts.tickettype")),
            ],
            options={"verbose_name": "позиция заказа", "verbose_name_plural": "позиции заказа", "ordering": ("ticket_type__sort_order", "id")},
        ),
        migrations.CreateModel(
            name="PaymentProof",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to=proof_upload_path)),
                ("original_name", models.CharField(max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="proofs", to="concerts.order")),
                ("payment_card", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payment_proofs", to="concerts.paymentcard")),
            ],
            options={"verbose_name": "чек оплаты", "verbose_name_plural": "чеки оплаты", "ordering": ("-uploaded_at",)},
        ),
        migrations.CreateModel(
            name="Ticket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("sequence", models.PositiveSmallIntegerField()),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                ("checked_in_at", models.DateTimeField(blank=True, null=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tickets", to="concerts.order")),
                ("order_item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tickets", to="concerts.orderitem")),
            ],
            options={"verbose_name": "билет", "verbose_name_plural": "билеты", "ordering": ("order_item__ticket_type__sort_order", "sequence")},
        ),
        migrations.AddConstraint(
            model_name="tickettype",
            constraint=models.UniqueConstraint(fields=("concert", "code"), name="unique_concert_ticket_type"),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.UniqueConstraint(fields=("order", "ticket_type"), name="unique_order_ticket_type"),
        ),
        migrations.AddConstraint(
            model_name="ticket",
            constraint=models.UniqueConstraint(fields=("order", "sequence"), name="unique_ticket_sequence"),
        ),
        migrations.RunPython(seed_concert, migrations.RunPython.noop),
    ]
