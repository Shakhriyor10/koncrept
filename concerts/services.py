from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Order, Ticket


def _ensure_order_tickets(order):
    sequence = 1
    for item in order.items.all():
        for _ in range(item.quantity):
            Ticket.objects.get_or_create(
                order=order,
                sequence=sequence,
                defaults={"order_item": item},
            )
            sequence += 1


def ensure_approved_order_tickets(order_id):
    """Repair any missing ticket rows for an already approved order."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.APPROVED:
            raise ValidationError("Билеты можно восстановить только для одобренного заказа.")
        _ensure_order_tickets(order)
    return order


def approve_order(order_id, reviewer):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status not in (Order.Status.UNDER_REVIEW, Order.Status.EXPIRED):
            raise ValidationError("Одобрить можно только заказ, ожидающий решения администратора.")
        else:
            _ensure_order_tickets(order)
            order.status = Order.Status.APPROVED
            order.reviewed_at = timezone.now()
            order.reviewed_by = reviewer
            order.review_note = ""
            order.save(update_fields=("status", "reviewed_at", "reviewed_by", "review_note"))
    return order


def reject_order(order_id, reviewer, note=""):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status not in (Order.Status.UNDER_REVIEW, Order.Status.EXPIRED):
            raise ValidationError("Отклонить можно только чек, ожидающий решения администратора.")
        else:
            order.status = Order.Status.REJECTED
            order.reviewed_at = timezone.now()
            order.reviewed_by = reviewer
            order.review_note = note.strip() or "Не удалось подтвердить перевод. Проверьте чек и отправьте его ещё раз."
            order.save(update_fields=("status", "reviewed_at", "reviewed_by", "review_note"))
    return order
