from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    Concert,
    CustomerProfile,
    Order,
    OrderItem,
    PaymentCard,
    PaymentProof,
    Ticket,
    TicketType,
)
from .services import approve_order, cancel_order, reject_order


@admin.register(Concert)
class ConcertAdmin(admin.ModelAdmin):
    list_display = ("artist_name", "event_date", "venue", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("artist_name",)}


@admin.register(TicketType)
class TicketTypeAdmin(admin.ModelAdmin):
    list_display = ("title", "concert", "price", "active", "sort_order")
    list_filter = ("concert", "active")
    list_editable = ("price", "active", "sort_order")
    fields = ("concert", "code", "title", "description", "details", "price", "active", "sort_order")


@admin.register(PaymentCard)
class PaymentCardAdmin(admin.ModelAdmin):
    list_display = ("provider", "label", "masked_number", "cardholder", "active", "sort_order")
    list_filter = ("provider", "active")
    list_editable = ("active", "sort_order")
    search_fields = ("label", "cardholder")
    fields = ("provider", "label", "card_number", "cardholder", "instructions", "active", "sort_order")

    @admin.display(description="Номер")
    def masked_number(self, obj):
        digits_only = "".join(character for character in obj.card_number if character.isdigit())
        return f"•••• {digits_only[-4:]}"


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "created_at")
    search_fields = ("user__username", "phone")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("ticket_type", "quantity", "unit_price", "line_total")
    fields = readonly_fields

    @admin.display(description="Сумма")
    def line_total(self, obj):
        return f"{obj.total:,} сум".replace(",", " ")


class PaymentProofInline(admin.TabularInline):
    model = PaymentProof
    extra = 0
    can_delete = False
    fields = ("payment_card", "file_link", "uploaded_at")
    readonly_fields = fields

    @admin.display(description="Файл чека")
    def file_link(self, obj):
        url = reverse("proof-download", kwargs={"proof_id": obj.pk})
        return format_html('<a href="{}">Скачать: {}</a>', url, obj.original_name)


class TicketInline(admin.TabularInline):
    model = Ticket
    extra = 0
    can_delete = False
    fields = ("order_item", "sequence", "token", "issued_at", "checked_in_at")
    readonly_fields = fields
    show_change_link = True


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "reference", "customer", "concert", "total_display", "status", "created_at", "review_deadline"
    )
    list_filter = ("status", "concert", "created_at")
    search_fields = ("reference", "phone", "user__username")
    readonly_fields = (
        "public_id", "reference", "user", "concert", "status", "phone",
        "created_at", "proof_submitted_at", "review_deadline", "reviewed_at", "reviewed_by",
    )
    fields = (
        "public_id", "reference", "user", "concert", "status", "phone",
        "created_at", "proof_submitted_at", "review_deadline", "reviewed_at", "reviewed_by",
        "review_note",
    )
    inlines = (OrderItemInline, PaymentProofInline, TicketInline)
    actions = ("approve_selected", "reject_selected", "cancel_approved_selected")
    list_select_related = ("user", "concert")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Телефон покупателя", ordering="phone")
    def customer(self, obj):
        return obj.phone

    @admin.display(description="К оплате")
    def total_display(self, obj):
        return f"{obj.total_amount:,} сум".replace(",", " ")

    @admin.action(description="Одобрить чек и выдать билеты")
    def approve_selected(self, request, queryset):
        approved = 0
        errors = 0
        for order in queryset:
            try:
                approve_order(order.pk, request.user)
                approved += 1
            except ValidationError as error:
                errors += 1
                self.message_user(request, f"{order.reference}: {error}", level=messages.WARNING)
        if approved:
            self.message_user(request, f"Одобрено заказов: {approved}. Билеты созданы.", level=messages.SUCCESS)
        if errors:
            self.message_user(request, f"Не обработано заказов: {errors}.", level=messages.WARNING)

    @admin.action(description="Отклонить чек (покупатель сможет отправить новый)")
    def reject_selected(self, request, queryset):
        rejected = 0
        errors = 0
        for order in queryset:
            try:
                reject_order(order.pk, request.user, order.review_note)
                rejected += 1
            except ValidationError as error:
                errors += 1
                self.message_user(request, f"{order.reference}: {error}", level=messages.WARNING)
        if rejected:
            self.message_user(request, f"Отклонено заказов: {rejected}.", level=messages.SUCCESS)
        if errors:
            self.message_user(request, f"Не обработано заказов: {errors}.", level=messages.WARNING)

    @admin.action(description="Отменить одобренный заказ и аннулировать билеты")
    def cancel_approved_selected(self, request, queryset):
        canceled = 0
        errors = 0
        for order in queryset:
            try:
                cancel_order(order.pk, request.user)
                canceled += 1
            except ValidationError as error:
                errors += 1
                self.message_user(request, f"{order.reference}: {error}", level=messages.WARNING)
        if canceled:
            self.message_user(
                request,
                f"Отменено заказов: {canceled}. Билеты аннулированы, PDF и QR больше недействительны.",
                level=messages.SUCCESS,
            )
        if errors:
            self.message_user(request, f"Не обработано заказов: {errors}.", level=messages.WARNING)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("__str__", "order", "issued_at", "admin_scan_count", "checked_in_at")
    list_filter = ("order__concert", "checked_in_at")
    search_fields = ("order__reference", "token", "order__phone")
    readonly_fields = ("order", "order_item", "token", "sequence", "issued_at", "checked_in_at", "admin_scan_count")
    actions = ("check_in_tickets",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Отметить выбранные билеты как использованные")
    def check_in_tickets(self, request, queryset):
        now = timezone.now()
        changed = queryset.filter(order__status=Order.Status.APPROVED, checked_in_at__isnull=True).update(
            checked_in_at=now
        )
        self.message_user(request, f"Отмечено билетов: {changed}.", level=messages.SUCCESS)


@admin.register(PaymentProof)
class PaymentProofAdmin(admin.ModelAdmin):
    list_display = ("order", "original_name", "uploaded_at")
    list_select_related = ("order",)
    readonly_fields = ("order", "payment_card", "file_link", "original_name", "uploaded_at")
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Файл")
    def file_link(self, obj):
        if not obj.file:
            return "Чек не приложен"
        url = reverse("proof-download", kwargs={"proof_id": obj.pk})
        return format_html('<a href="{}">Скачать чек</a>', url)
