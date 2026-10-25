import re
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .identifiers import make_order_reference
from .uploads import proof_upload_path


class Concert(models.Model):
    slug = models.SlugField(unique=True)
    artist_name = models.CharField(max_length=160)
    event_date = models.DateField()
    venue = models.CharField(max_length=180)
    city = models.CharField(max_length=100, default="Самарканд")
    tagline = models.CharField(max_length=240, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("event_date",)
        verbose_name = "концерт"
        verbose_name_plural = "концерты"

    def __str__(self):
        return f"{self.artist_name} — {self.event_date:%d.%m.%Y}"


class TicketType(models.Model):
    class Code(models.TextChoices):
        VIP = "vip", "VIP"
        STANDARD = "standard", "Стандарт"
        FAN_ZONE = "fan_zone", "Фан-зона"

    concert = models.ForeignKey(Concert, on_delete=models.CASCADE, related_name="ticket_types")
    code = models.CharField(max_length=20, choices=Code.choices)
    title = models.CharField(max_length=80)
    description = models.CharField(max_length=240, blank=True)
    details = models.TextField(blank=True, help_text="Подробности, которые откроются в окне по кнопке ! на карточке.")
    price = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "price")
        constraints = [
            models.UniqueConstraint(fields=("concert", "code"), name="unique_concert_ticket_type")
        ]
        verbose_name = "тип билета"
        verbose_name_plural = "типы билетов"

    def __str__(self):
        return f"{self.title} — {self.price:,} сум".replace(",", " ")


class CustomerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "профиль покупателя"
        verbose_name_plural = "профили покупателей"

    def __str__(self):
        return self.phone


class PaymentCard(models.Model):
    class Provider(models.TextChoices):
        HUMO = "humo", "HUMO"
        UZCARD = "uzcard", "UZCARD"

    provider = models.CharField(max_length=12, choices=Provider.choices)
    label = models.CharField(max_length=80, blank=True, help_text="Например, «Карта для оплаты VIP».")
    card_number = models.CharField(max_length=32, help_text="Номер карты без пробелов или с пробелами.")
    cardholder = models.CharField(max_length=120)
    instructions = models.CharField(max_length=240, blank=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "provider", "id")
        verbose_name = "карта для перевода"
        verbose_name_plural = "карты для перевода"

    def clean(self):
        super().clean()
        raw_number = self.card_number or ""
        if not re.fullmatch(r"[0-9\s-]+", raw_number):
            raise ValidationError({"card_number": "Используйте в номере карты только цифры, пробелы или дефисы."})
        digits_only = re.sub(r"\D", "", raw_number)
        if len(digits_only) != 16:
            raise ValidationError({"card_number": "Укажите номер карты HUMO или UZCARD из 16 цифр."})
        self.card_number = " ".join(digits_only[index:index + 4] for index in range(0, 16, 4))

    def __str__(self):
        return f"{self.get_provider_display()} •••• {self.card_number.replace(' ', '')[-4:]}"


class WheelReward(models.Model):
    class Code(models.TextChoices):
        DISCOUNT_5 = "discount_5", "Скидка 5% (ранее)"
        DISCOUNT_10 = "discount_10", "Скидка 10%"
        DISCOUNT_20 = "discount_20", "Скидка 20%"
        DISCOUNT_25 = "discount_25", "Скидка 25% (ранее)"
        DISCOUNT_30 = "discount_30", "Скидка 30%"
        DISCOUNT_50 = "discount_50", "Скидка 50% (ранее)"
        DISCOUNT_60 = "discount_60", "Скидка 60%"
        GIFT_STANDARD = "gift_standard", "Стандартный билет в подарок"
        GIFT_VIP = "gift_vip", "VIP-билет в подарок"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="wheel_rewards"
    )
    guest_key = models.CharField(max_length=64, blank=True, db_index=True)
    code = models.CharField(max_length=24, choices=Code.choices)
    spun_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-spun_at",)
        verbose_name = "приз барабана"
        verbose_name_plural = "призы барабана"

    def __str__(self):
        customer = self.user if self.user_id else "Гость"
        return f"{customer} · {self.get_code_display()}"

    @property
    def customer_label(self):
        if not self.user_id:
            return "Гость"
        try:
            profile = self.user.profile
        except CustomerProfile.DoesNotExist:
            profile = None
        return (profile.phone if profile else "") or self.user.get_username()

    @property
    def is_discount_coupon(self):
        return self.code in {
            self.Code.DISCOUNT_5,
            self.Code.DISCOUNT_10,
            self.Code.DISCOUNT_20,
            self.Code.DISCOUNT_25,
            self.Code.DISCOUNT_30,
            self.Code.DISCOUNT_50,
            self.Code.DISCOUNT_60,
        }

    @property
    def state_label(self):
        if self.claimed_at:
            return "Использован / билет выдан"
        reserved_statuses = (Order.Status.UNDER_REVIEW, Order.Status.EXPIRED)
        if (
            self.orders.filter(status__in=reserved_statuses).exists()
            or self.redemptions.filter(order__status__in=reserved_statuses).exists()
        ):
            return "Оплата на проверке"
        if not self.is_discount_coupon and self.expires_at <= timezone.now():
            return "Срок истёк"
        return "Активен"

    @property
    def discount_percent(self):
        return {
            self.Code.DISCOUNT_5: 5,
            self.Code.DISCOUNT_10: 10,
            self.Code.DISCOUNT_20: 20,
            self.Code.DISCOUNT_25: 25,
            self.Code.DISCOUNT_30: 30,
            self.Code.DISCOUNT_50: 50,
            self.Code.DISCOUNT_60: 60,
        }.get(self.code, 0)


class Order(models.Model):
    class Status(models.TextChoices):
        AWAITING_PAYMENT = "awaiting_payment", "Ожидает чек"
        UNDER_REVIEW = "under_review", "Проверка оплаты"
        APPROVED = "approved", "Оплачено · билеты готовы"
        REJECTED = "rejected", "Чек отклонён"
        CANCELED = "canceled", "Заказ отменён · билеты аннулированы"
        EXPIRED = "expired", "Время проверки истекло"

    public_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    reference = models.CharField(max_length=11, unique=True, default=make_order_reference, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="concert_orders")
    concert = models.ForeignKey(Concert, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.AWAITING_PAYMENT)
    first_name = models.CharField(max_length=80, blank=True, default="")
    last_name = models.CharField(max_length=80, blank=True, default="")
    phone = models.CharField(max_length=32)
    email = models.EmailField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    proof_submitted_at = models.DateTimeField(null=True, blank=True)
    review_deadline = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_orders",
    )
    review_note = models.CharField(max_length=500, blank=True)
    wheel_reward = models.ForeignKey(
        WheelReward, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "заказ"
        verbose_name_plural = "заказы"

    def __str__(self):
        return f"{self.reference} · {self.get_status_display()}"

    @property
    def total_amount(self):
        return sum(item.total for item in self.items.all())

    @property
    def gross_amount(self):
        return sum(item.quantity * item.unit_price for item in self.items.all())

    @property
    def discount_amount(self):
        return sum(item.discount_amount for item in self.items.all())

    @property
    def ticket_count(self):
        return sum(item.quantity for item in self.items.all())

    def expire_if_due(self):
        if self.status == self.Status.UNDER_REVIEW and self.review_deadline and timezone.now() >= self.review_deadline:
            self.status = self.Status.EXPIRED
            self.save(update_fields=("status",))
            return True
        return False


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name="order_items")
    quantity = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    discount_amount = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("ticket_type__sort_order", "id")
        constraints = [models.UniqueConstraint(fields=("order", "ticket_type"), name="unique_order_ticket_type")]
        verbose_name = "позиция заказа"
        verbose_name_plural = "позиции заказа"

    @property
    def total(self):
        return max(0, self.quantity * self.unit_price - self.discount_amount)

    def __str__(self):
        return f"{self.ticket_type.title} × {self.quantity}"


class OrderDiscount(models.Model):
    """One wheel coupon assigned to one individual ticket in an order."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="discount_allocations")
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="discount_allocations")
    reward = models.ForeignKey(WheelReward, on_delete=models.PROTECT, related_name="redemptions")
    unit_number = models.PositiveSmallIntegerField()
    discount_amount = models.PositiveIntegerField()

    class Meta:
        ordering = ("order_item_id", "unit_number")
        constraints = [
            models.UniqueConstraint(fields=("order", "reward"), name="unique_order_wheel_reward_redemption"),
            models.UniqueConstraint(fields=("order_item", "unit_number"), name="unique_order_ticket_discount"),
        ]
        verbose_name = "скидка на билет"
        verbose_name_plural = "скидки на билеты"

    def __str__(self):
        return f"{self.reward.get_code_display()} · {self.order.reference}"


class PaymentProof(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="proofs")
    payment_card = models.ForeignKey(
        PaymentCard, on_delete=models.PROTECT, related_name="payment_proofs", null=True, blank=True
    )
    file = models.FileField(upload_to=proof_upload_path, blank=True, null=True)
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-uploaded_at",)
        verbose_name = "чек оплаты"
        verbose_name_plural = "чеки оплаты"

    def __str__(self):
        return f"{self.order.reference} · {self.original_name}"


class Ticket(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="tickets")
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="tickets")
    token = models.UUIDField(default=uuid4, unique=True, editable=False)
    sequence = models.PositiveSmallIntegerField()
    issued_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    admin_scan_count = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ("order_item__ticket_type__sort_order", "sequence")
        constraints = [
            models.UniqueConstraint(fields=("order", "sequence"), name="unique_ticket_sequence")
        ]
        verbose_name = "билет"
        verbose_name_plural = "билеты"

    def __str__(self):
        return f"{self.order.reference} · {self.order_item.ticket_type.title} №{self.pk}"
