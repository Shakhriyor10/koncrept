from datetime import timedelta
import json
import logging
from pathlib import Path
import random
from uuid import UUID
from urllib.parse import urlparse

import qrcode
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import (
    CustomerDetailsForm,
    OrderCreateForm,
    PaymentProofForm,
    PhoneAuthenticationForm,
    RegistrationForm,
    StaffTicketCreateForm,
)
from .models import (
    Concert,
    CustomerProfile,
    Order,
    OrderItem,
    OrderDiscount,
    PaymentCard,
    PaymentProof,
    Ticket,
    TicketType,
    WheelReward,
)
from .pdf_tickets import build_order_tickets_pdf
from .phones import normalize_stored_phone
from .services import approve_order, cancel_order, ensure_approved_order_tickets, reject_order
from .telegram_bot import handle_review_callback, notify_order_for_review, verify_webhook_secret
from .ticket_urls import ticket_verification_url
from .wheel import (
    active_discounts_for_user,
    attach_guest_reward,
    get_guest_reward_status,
    get_user_reward_status,
    spin_for_guest,
    spin_for_user,
)

logger = logging.getLogger(__name__)


def _is_async(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _safe_next(request, fallback="home"):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(candidate, {request.get_host()}):
        return candidate
    return reverse(fallback)


def _active_concert():
    concert = Concert.objects.filter(is_active=True).prefetch_related("ticket_types").first()
    if not concert:
        raise Http404("Сейчас нет активного концерта.")
    return concert


def home(request):
    concert = _active_concert()
    ticket_types = list(concert.ticket_types.filter(active=True))
    return render(request, "concerts/home.html", {"concert": concert, "ticket_types": ticket_types})


def _attach_guest_prize(request, user):
    rewards, gift_orders = attach_guest_reward(request, user)
    if gift_orders:
        refs = ", ".join(order.reference for order in gift_orders)
        messages.success(request, f"Подарочные билеты добавлены в профиль: {refs}.")
    discount_count = sum(reward.is_discount_coupon for reward in rewards)
    if discount_count:
        messages.success(request, f"Сохранено скидочных купонов: {discount_count}. Их можно распределить по билетам при оформлении.")
    return rewards


def register(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request))
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        auth_login(request, user)
        guest_reward = _attach_guest_prize(request, user)
        target = _safe_next(request, fallback="dashboard" if guest_reward else "home")
        if _is_async(request):
            return JsonResponse({"redirect": target})
        return redirect(target)
    if _is_async(request):
        html = render_to_string("registration/register_form.html", {"form": form, "next": request.GET.get("next", "")}, request)
        return JsonResponse({"html": html}, status=422)
    return render(request, "registration/register.html", {"form": form, "next": request.GET.get("next", "")})


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request))
    form = PhoneAuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        auth_login(request, user)
        guest_reward = _attach_guest_prize(request, user)
        target = _safe_next(request, fallback="dashboard" if guest_reward else "home")
        if _is_async(request):
            return JsonResponse({"redirect": target})
        return redirect(target)
    if _is_async(request):
        html = render_to_string("registration/login_form.html", {"form": form, "next": request.GET.get("next", "")}, request)
        return JsonResponse({"html": html}, status=422)
    return render(request, "registration/login.html", {"form": form, "next": request.GET.get("next", "")})


def wheel_status(request):
    status = get_user_reward_status(request.user) if request.user.is_authenticated else get_guest_reward_status(request)
    return JsonResponse(status)


def wheel_spin(request):
    if request.method != "POST":
        return JsonResponse({"error": "Используйте POST для запуска барабана."}, status=405)
    if request.user.is_authenticated:
        reward, gift_order, next_spin_at = spin_for_user(request.user)
        if next_spin_at:
            return JsonResponse({"error": "Следующее вращение будет доступно через 24 часа.", "next_spin_at": next_spin_at.isoformat()}, status=429)
        return JsonResponse({
            "reward": {
                "code": reward.code,
                "label": reward.get_code_display(),
                "percent": reward.discount_percent,
                "gift": reward.code in (WheelReward.Code.GIFT_STANDARD, WheelReward.Code.GIFT_VIP),
                "expires_at": reward.expires_at.isoformat(),
                "claimed": reward.claimed_at is not None,
            },
            "gift_order_reference": gift_order.reference if gift_order else None,
            "authenticated": True,
        })
    reward, next_spin_at = spin_for_guest(request)
    if next_spin_at:
        return JsonResponse({"error": "Следующее вращение будет доступно через 24 часа.", "next_spin_at": next_spin_at.isoformat()}, status=429)
    return JsonResponse({"reward": reward, "authenticated": False})


@login_required
def logout_view(request):
    if request.method != "POST":
        raise PermissionDenied
    auth_logout(request)
    if _is_async(request):
        return JsonResponse({"redirect": reverse("home")})
    return redirect("home")


@login_required
def dashboard(request):
    orders = request.user.concert_orders.select_related("concert").prefetch_related("items", "tickets")
    for order in orders:
        order.expire_if_due()
    now = timezone.now()
    active_rewards = WheelReward.objects.filter(user=request.user, claimed_at__isnull=True).filter(
        Q(code__in=(
            WheelReward.Code.DISCOUNT_5, WheelReward.Code.DISCOUNT_10, WheelReward.Code.DISCOUNT_20,
            WheelReward.Code.DISCOUNT_25, WheelReward.Code.DISCOUNT_30, WheelReward.Code.DISCOUNT_50,
            WheelReward.Code.DISCOUNT_60,
        )) | Q(expires_at__gt=now)
    ).order_by("-spun_at")
    return render(request, "concerts/dashboard.html", {"orders": orders, "active_rewards": active_rewards})


@login_required
def profile_edit(request):
    profile = CustomerProfile.objects.filter(user=request.user).first()
    initial = {
        "phone": normalize_stored_phone(profile.phone) if profile and normalize_stored_phone(profile.phone) else "+998",
    }
    form = CustomerDetailsForm(request.POST or None, initial=initial, user=request.user)
    if request.method == "POST" and form.is_valid():
        request.user.username = form.cleaned_data["phone"]
        request.user.save(update_fields=("username",))
        CustomerProfile.objects.update_or_create(
            user=request.user,
            defaults={"phone": form.cleaned_data["phone"].strip()},
        )
        messages.success(request, "Данные профиля сохранены.")
        target = _safe_next(request, fallback="dashboard")
        if _is_async(request):
            return JsonResponse({"redirect": target})
        return redirect(target)
    context = {"form": form, "next": request.POST.get("next") or request.GET.get("next", "")}
    if _is_async(request):
        return JsonResponse({"html": render_to_string("concerts/profile_form.html", context, request)}, status=422)
    return render(request, "concerts/profile.html", context)


@login_required
def staff_orders(request):
    if not request.user.is_staff:
        raise PermissionDenied
    review_orders = Order.objects.filter(status=Order.Status.UNDER_REVIEW).only("id", "status", "review_deadline")
    for order in review_orders:
        order.expire_if_due()
    orders = (
        Order.objects.select_related("user", "concert", "reviewed_by")
        .prefetch_related("items__ticket_type", "proofs__payment_card")
        .order_by("status", "-created_at")
    )
    status_filter = request.GET.get("status", "under_review")
    if status_filter == Order.Status.UNDER_REVIEW:
        orders = orders.filter(status__in=(Order.Status.UNDER_REVIEW, Order.Status.EXPIRED))
    elif status_filter in Order.Status.values:
        orders = orders.filter(status=status_filter)
    else:
        status_filter = "all"
    active_ticket_rows = (
        Ticket.objects.filter(order__status=Order.Status.APPROVED, checked_in_at__isnull=True)
        .values("order_item__ticket_type__code")
        .annotate(total=Count("id"))
    )
    active_ticket_counts = {row["order_item__ticket_type__code"]: row["total"] for row in active_ticket_rows}
    return render(request, "concerts/staff_orders.html", {
        "orders": orders,
        "status_filter": status_filter,
        "statuses": Order.Status.choices,
        "pending_count": Order.objects.filter(
            status__in=(Order.Status.UNDER_REVIEW, Order.Status.EXPIRED)
        ).count(),
        "active_ticket_total": sum(active_ticket_counts.values()),
        "active_vip_count": active_ticket_counts.get(TicketType.Code.VIP, 0),
        "active_standard_count": active_ticket_counts.get(TicketType.Code.STANDARD, 0),
        "active_fan_zone_count": active_ticket_counts.get(TicketType.Code.FAN_ZONE, 0),
    })


@login_required
def staff_wheel_rewards(request):
    if not request.user.is_staff:
        raise PermissionDenied
    rewards = WheelReward.objects.select_related("user", "user__profile").order_by("-spun_at")
    page_obj = Paginator(rewards, 50).get_page(request.GET.get("page"))
    return render(request, "concerts/staff_wheel_rewards.html", {"page_obj": page_obj, "now": timezone.now()})


def staff_create_ticket(request):
    if not request.user.is_staff:
        raise PermissionDenied
    concert = _active_concert()
    ticket_types = {item.code: item for item in concert.ticket_types.filter(active=True)}
    form = StaffTicketCreateForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        quantities = {
            TicketType.Code.VIP: form.cleaned_data["quantity_vip"],
            TicketType.Code.STANDARD: form.cleaned_data["quantity_standard"],
            TicketType.Code.FAN_ZONE: form.cleaned_data["quantity_fan_zone"],
        }
        if any(quantity and code not in ticket_types for code, quantity in quantities.items()):
            form.add_error(None, "Один из выбранных типов билетов сейчас недоступен. Обновите страницу.")
        else:
            phone = form.cleaned_data["phone"]
            user_model = get_user_model()
            with transaction.atomic():
                customer = user_model.objects.filter(username=phone).first()
                if customer is None:
                    for profile in CustomerProfile.objects.select_related("user").all():
                        if normalize_stored_phone(profile.phone) == phone:
                            customer = profile.user
                            break

                account_created = customer is None
                if account_created:
                    customer = user_model.objects.create_user(username=phone, password="majormotors")
                CustomerProfile.objects.update_or_create(user=customer, defaults={"phone": phone})

                order = Order.objects.create(
                    user=customer,
                    concert=concert,
                    status=Order.Status.APPROVED,
                    phone=phone,
                    reviewed_at=timezone.now(),
                    reviewed_by=request.user,
                    review_note="Билет выдан администратором вручную.",
                )
                sequence = 1
                for code, quantity in quantities.items():
                    if not quantity:
                        continue
                    item = OrderItem.objects.create(
                        order=order,
                        ticket_type=ticket_types[code],
                        quantity=quantity,
                        unit_price=ticket_types[code].price,
                    )
                    for _ in range(quantity):
                        Ticket.objects.create(order=order, order_item=item, sequence=sequence)
                        sequence += 1

            request.session[f"manual-ticket-account-created:{order.reference}"] = account_created
            return redirect("staff-ticket-created", reference=order.reference)

    return render(request, "concerts/staff_ticket_create.html", {
        "concert": concert,
        "ticket_types": ticket_types,
        "form": form,
    })


@login_required
def staff_ticket_created(request, reference):
    if not request.user.is_staff:
        raise PermissionDenied
    order = get_object_or_404(
        Order.objects.select_related("concert", "user"),
        reference=reference,
        status=Order.Status.APPROVED,
    )
    account_created = request.session.pop(f"manual-ticket-account-created:{order.reference}", False)
    return render(request, "concerts/staff_ticket_created.html", {
        "order": order,
        "account_created": account_created,
    })


@login_required
def staff_ticket_scanner(request):
    if not request.user.is_staff:
        raise PermissionDenied
    return render(request, "concerts/staff_ticket_scanner.html")


@login_required
def staff_scan_ticket(request):
    if not request.user.is_staff:
        raise PermissionDenied
    if request.method != "POST":
        return JsonResponse({"valid": False, "message": "Используйте POST для сканирования."}, status=405)

    raw_value = request.POST.get("token", "").strip()
    ticket_token = None
    for part in [raw_value, *urlparse(raw_value).path.split("/")]:
        try:
            ticket_token = UUID(part)
            break
        except (ValueError, TypeError, AttributeError):
            continue
    if ticket_token is None:
        return JsonResponse({"valid": False, "message": "Не удалось распознать QR-код билета."}, status=400)

    with transaction.atomic():
        try:
            ticket = Ticket.objects.select_for_update().select_related(
                "order__concert", "order_item__ticket_type"
            ).get(token=ticket_token)
        except Ticket.DoesNotExist:
            return JsonResponse({"valid": False, "message": "Билет не найден."}, status=404)

        if ticket.order.status == Order.Status.CANCELED:
            return JsonResponse({"valid": False, "message": "Билет недействителен: заказ отменён, билет аннулирован."}, status=400)
        if ticket.order.status != Order.Status.APPROVED:
            return JsonResponse({"valid": False, "message": "Билет недействителен: заказ не одобрен."}, status=400)

        already_used = ticket.checked_in_at is not None
        if not already_used:
            ticket.checked_in_at = timezone.now()
        ticket.admin_scan_count += 1
        ticket.save(update_fields=("checked_in_at", "admin_scan_count"))

    return JsonResponse({
        "valid": True,
        "already_used": already_used,
        "admin_scan_count": ticket.admin_scan_count,
        "reference": ticket.order.reference,
        "artist": ticket.order.concert.artist_name,
        "event_date": ticket.order.concert.event_date.strftime("%d.%m.%Y"),
        "venue": ticket.order.concert.venue,
        "ticket_type": ticket.order_item.ticket_type.title,
        "ticket_id": ticket.pk,
    })


@login_required
def staff_review_order(request, reference):
    if not request.user.is_staff:
        raise PermissionDenied
    if request.method != "POST":
        raise PermissionDenied
    order = get_object_or_404(Order, reference=reference)
    action = request.POST.get("action")
    try:
        if action == "approve":
            approve_order(order.pk, request.user)
        elif action == "reject":
            reject_order(order.pk, request.user, request.POST.get("review_note", ""))
        elif action == "cancel":
            cancel_order(order.pk, request.user, request.POST.get("review_note", ""))
        else:
            raise Http404("Неизвестное действие.")
    except ValidationError as error:
        messages.error(request, str(error))
    else:
        success_message = {
            "approve": "Заказ подтверждён, билеты выпущены.",
            "reject": "Чек отклонён. Покупатель сможет отправить новый.",
            "cancel": "Заказ отменён. Выданные билеты аннулированы.",
        }[action]
        messages.success(request, success_message)
    if _is_async(request):
        return JsonResponse({"redirect": reverse("staff-orders")})
    return redirect("staff-orders")


@login_required
def order_create(request):
    concert = _active_concert()
    profile = CustomerProfile.objects.filter(user=request.user).first()
    if not (profile and profile.phone.strip()):
        messages.info(request, "Заполните контактные данные профиля перед покупкой билетов.")
        target = f"{reverse('profile-edit')}?next={reverse('order-create')}"
        if _is_async(request):
            return JsonResponse({"redirect": target})
        return redirect(target)

    available_discounts = list(active_discounts_for_user(request.user))
    discount_choices = [
        (str(reward.pk), f"Скидка {reward.discount_percent}% · получена {reward.spun_at:%d.%m.%Y}")
        for reward in available_discounts
    ]
    form = OrderCreateForm(request.POST or None, discount_choices=discount_choices)
    ticket_types = {item.code: item for item in concert.ticket_types.filter(active=True)}

    if request.method == "POST" and form.is_valid():
        quantities = {
            TicketType.Code.VIP: form.cleaned_data["quantity_vip"],
            TicketType.Code.STANDARD: form.cleaned_data["quantity_standard"],
            TicketType.Code.FAN_ZONE: form.cleaned_data["quantity_fan_zone"],
        }
        requested_missing = [code for code, qty in quantities.items() if qty and code not in ticket_types]
        if requested_missing:
            form.add_error(None, "Один из выбранных типов билетов сейчас недоступен. Обновите страницу.")
        else:
            selected_ids = {
                TicketType.Code.VIP: [value for value in form.cleaned_data["discount_vip"] if value],
                TicketType.Code.STANDARD: [value for value in form.cleaned_data["discount_standard"] if value],
                TicketType.Code.FAN_ZONE: [value for value in form.cleaned_data["discount_fan_zone"] if value],
            }
            # Automatically assign available coupons to selected ticket slots
            # unless the customer has edited the per-ticket selectors.
            if not any(selected_ids.values()) and request.POST.get("coupon_selection_touched") != "1":
                remaining_coupons = iter(available_discounts)
                for code, quantity in quantities.items():
                    assigned = []
                    for _ in range(quantity):
                        reward = next(remaining_coupons, None)
                        if reward is None:
                            break
                        assigned.append(str(reward.pk))
                    selected_ids[code] = assigned
            selected_coupon_ids = {coupon_id for ids in selected_ids.values() for coupon_id in ids}
            order = None
            with transaction.atomic():
                locked_discounts = list(active_discounts_for_user(request.user, lock=True))
                discounts_by_id = {str(reward.pk): reward for reward in locked_discounts}
                if not selected_coupon_ids.issubset(discounts_by_id):
                    form.add_error(None, "Один из выбранных купонов уже использован или ожидает проверки. Обновите страницу.")
                else:
                    if selected_coupon_ids:
                        Order.objects.filter(
                            user=request.user,
                            status__in=(Order.Status.AWAITING_PAYMENT, Order.Status.REJECTED),
                        ).filter(
                            Q(wheel_reward_id__in=selected_coupon_ids)
                            | Q(discount_allocations__reward_id__in=selected_coupon_ids)
                        ).update(
                            status=Order.Status.CANCELED,
                            reviewed_at=timezone.now(),
                            review_note="Оформление продолжено в новом заказе.",
                        )
                    order = Order.objects.create(
                        user=request.user,
                        concert=concert,
                        first_name="",
                        last_name="",
                        phone=profile.phone.strip(),
                        email="",
                    )
                    for code, quantity in quantities.items():
                        if not quantity:
                            continue
                        ticket_type = ticket_types[code]
                        coupons = [discounts_by_id[coupon_id] for coupon_id in selected_ids[code]]
                        unit_discounts = [ticket_type.price * coupon.discount_percent // 100 for coupon in coupons]
                        order_item = OrderItem.objects.create(
                            order=order,
                            ticket_type=ticket_type,
                            quantity=quantity,
                            unit_price=ticket_type.price,
                            discount_amount=sum(unit_discounts),
                        )
                        OrderDiscount.objects.bulk_create([
                            OrderDiscount(
                                order=order,
                                order_item=order_item,
                                reward=coupon,
                                unit_number=index,
                                discount_amount=unit_discounts[index - 1],
                            )
                            for index, coupon in enumerate(coupons, start=1)
                        ])

            if not form.errors and order:
                target = reverse("order-payment", kwargs={"reference": order.reference})
                if _is_async(request):
                    html = render_to_string(
                        "concerts/checkout_payment.html",
                        _payment_context(request, order),
                        request,
                    )
                    return JsonResponse({"html": html, "url": target})
                return redirect(target)

    coupon_selection = {
        code: [str(value) for value in (form[f"discount_{code}"].value() or []) if str(value).isdigit()]
        for code in ("vip", "standard", "fan_zone")
    }
    context = {
        "concert": concert,
        "ticket_types": ticket_types,
        "form": form,
        "profile": profile,
        "available_discounts": available_discounts,
        "coupon_selection": coupon_selection,
        "coupon_selection_touched": request.POST.get("coupon_selection_touched") == "1",
    }
    if _is_async(request):
        html = render_to_string("concerts/checkout_start.html", context, request)
        return JsonResponse({"html": html}, status=422)
    return render(request, "concerts/checkout.html", {"concert": concert, "flow_template": "concerts/checkout_start.html", "flow_context": context})


def _owned_order(request, reference):
    return get_object_or_404(
        Order.objects.select_related("concert", "user").prefetch_related(
            "items__ticket_type", "tickets__order_item__ticket_type", "discount_allocations__reward"
        ),
        reference=reference,
        user=request.user,
    )


def _payment_context(request, order, form=None):
    cards = list(PaymentCard.objects.filter(active=True))
    if form is None:
        form = PaymentProofForm()
    selected_provider = form["payment_provider"].value()
    selected_card_id = form["payment_card"].value()
    cards_by_provider = []
    provider_options = []
    provider_labels = dict(PaymentCard.Provider.choices)
    for provider in ("uzcard", "humo"):
        label = provider_labels[provider]
        candidates = [card for card in cards if card.provider == provider]
        provider_options.append((provider, label, bool(candidates)))
        if not candidates:
            continue
        selected_card = next(
            (card for card in candidates if str(card.pk) == str(selected_card_id)),
            None,
        ) if provider == selected_provider and selected_card_id else None
        cards_by_provider.append((provider, label, [selected_card or random.choice(candidates)]))
    return {
        "order": order,
        "cards": cards,
        "cards_by_provider": cards_by_provider,
        "provider_options": provider_options,
        "form": form,
        "concert": order.concert,
    }


def _state_template(order):
    if order.status in (Order.Status.AWAITING_PAYMENT, Order.Status.REJECTED):
        return "concerts/checkout_payment.html"
    if order.status == Order.Status.UNDER_REVIEW:
        return "concerts/checkout_review.html"
    if order.status == Order.Status.APPROVED:
        return "concerts/checkout_approved.html"
    if order.status == Order.Status.CANCELED:
        return "concerts/checkout_canceled.html"
    return "concerts/checkout_expired.html"


def _state_context(request, order, form=None):
    if order.status in (Order.Status.AWAITING_PAYMENT, Order.Status.REJECTED):
        return _payment_context(request, order, form)
    if order.status == Order.Status.APPROVED:
        ensure_approved_order_tickets(order.pk)
        getattr(order, "_prefetched_objects_cache", {}).pop("tickets", None)
    return {"order": order, "concert": order.concert, "tickets": order.tickets.select_related("order_item__ticket_type").all()}


def _render_state(request, order, form=None):
    order.expire_if_due()
    return render_to_string(_state_template(order), _state_context(request, order, form), request)


@login_required
def order_payment(request, reference):
    order = _owned_order(request, reference)
    order.expire_if_due()
    if order.status not in (Order.Status.AWAITING_PAYMENT, Order.Status.REJECTED):
        return redirect("order-detail", reference=reference)

    form = PaymentProofForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if form.is_valid():
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order.pk, user=request.user)
                if order.status not in (Order.Status.AWAITING_PAYMENT, Order.Status.REJECTED):
                    form.add_error(None, "Заказ уже отправлен на проверку или закрыт.")
                else:
                    reward_ids = set(order.discount_allocations.values_list("reward_id", flat=True))
                    if order.wheel_reward_id:
                        reward_ids.add(order.wheel_reward_id)
                    rewards = WheelReward.objects.select_for_update().filter(pk__in=reward_ids)
                    reserved_statuses = (Order.Status.UNDER_REVIEW, Order.Status.EXPIRED, Order.Status.APPROVED)
                    for reward in rewards:
                        if reward.claimed_at:
                            form.add_error(None, "Один из купонов уже использован в другом заказе.")
                            break
                        if Order.objects.filter(
                            Q(wheel_reward=reward) | Q(discount_allocations__reward=reward),
                            status__in=reserved_statuses,
                        ).exclude(pk=order.pk).exists():
                            form.add_error(None, "Один из купонов уже ожидает проверки в другом заказе.")
                            break
                    if not form.errors:
                        upload = form.cleaned_data.get("proof")
                        card = form.cleaned_data["payment_card"]
                        proof = PaymentProof.objects.create(
                            order=order,
                            payment_card=card,
                            file=upload if upload else None,
                            original_name=Path(upload.name).name[:255] if upload else "",
                        )
                        now = timezone.now()
                        order.status = Order.Status.UNDER_REVIEW
                        order.proof_submitted_at = now
                        order.review_deadline = now + timedelta(minutes=15)
                        order.reviewed_at = None
                        order.reviewed_by = None
                        order.review_note = ""
                        order.save(update_fields=(
                            "status", "proof_submitted_at", "review_deadline", "reviewed_at", "reviewed_by", "review_note"
                        ))
                        transaction.on_commit(
                            lambda order_id=order.pk, proof_id=proof.pk: notify_order_for_review(order_id, proof_id)
                        )

            if not form.errors:
                target = reverse("order-detail", kwargs={"reference": order.reference})
                if _is_async(request):
                    return JsonResponse({"html": _render_state(request, order), "url": target})
                return redirect(target)

        if _is_async(request):
            html = render_to_string("concerts/checkout_payment.html", _payment_context(request, order, form), request)
            return JsonResponse({"html": html}, status=422)

    context = _payment_context(request, order, form)
    if _is_async(request):
        return JsonResponse({"html": render_to_string("concerts/checkout_payment.html", context, request)})
    return render(request, "concerts/checkout.html", {"concert": order.concert, "flow_template": "concerts/checkout_payment.html", "flow_context": context})


@login_required
def order_detail(request, reference):
    order = _owned_order(request, reference)
    order.expire_if_due()
    if order.status in (Order.Status.AWAITING_PAYMENT, Order.Status.REJECTED):
        target = reverse("order-payment", kwargs={"reference": order.reference})
        return redirect(target)
    context = _state_context(request, order)
    template = _state_template(order)
    if _is_async(request):
        return JsonResponse({"html": render_to_string(template, context, request), "status": order.status})
    return render(request, "concerts/checkout.html", {"concert": order.concert, "flow_template": template, "flow_context": context})


@login_required
def order_tickets_pdf(request, reference):
    order = get_object_or_404(
        Order.objects.select_related("concert").prefetch_related("items__ticket_type", "tickets__order_item__ticket_type"),
        reference=reference,
        status=Order.Status.APPROVED,
    )
    if order.user_id != request.user.pk and not request.user.is_staff:
        raise Http404
    ensure_approved_order_tickets(order.pk)
    getattr(order, "_prefetched_objects_cache", {}).pop("tickets", None)
    pdf_file = build_order_tickets_pdf(request, order)
    response = HttpResponse(pdf_file, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{order.reference}-tickets.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def order_status(request, reference):
    order = _owned_order(request, reference)
    order.expire_if_due()
    html = _render_state(request, order)
    return JsonResponse({
        "html": html,
        "status": order.status,
        "expires_at": order.review_deadline.isoformat() if order.review_deadline else None,
        "terminal": order.status != Order.Status.UNDER_REVIEW,
    })


@login_required
def ticket_detail(request, token):
    ticket = get_object_or_404(
        Ticket.objects.select_related("order", "order_item__ticket_type", "order__concert"),
        token=token,
        order__user=request.user,
        order__status=Order.Status.APPROVED,
    )
    return render(request, "concerts/ticket.html", {"ticket": ticket})


def ticket_qr(request, token):
    ticket = get_object_or_404(Ticket.objects.select_related("order"), token=token, order__status=Order.Status.APPROVED)
    verify_url = ticket_verification_url(request, ticket)
    image = qrcode.make(verify_url, box_size=8, border=2)
    response = HttpResponse(content_type="image/png")
    image.save(response, format="PNG")
    response["Cache-Control"] = "private, no-store, no-cache, must-revalidate"
    return response


def ticket_verify(request, token):
    ticket = get_object_or_404(
        Ticket.objects.select_related("order__concert", "order_item__ticket_type"), token=token
    )
    valid = ticket.order.status == Order.Status.APPROVED
    admin_scan = False
    already_used = ticket.checked_in_at is not None

    if valid and request.user.is_authenticated and request.user.is_staff:
        with transaction.atomic():
            ticket = get_object_or_404(
                Ticket.objects.select_for_update().select_related(
                    "order__concert", "order_item__ticket_type"
                ),
                token=token,
            )
            valid = ticket.order.status == Order.Status.APPROVED
            if valid:
                already_used = ticket.checked_in_at is not None
                if not already_used:
                    ticket.checked_in_at = timezone.now()
                ticket.admin_scan_count += 1
                ticket.save(update_fields=("checked_in_at", "admin_scan_count"))
                admin_scan = True

    return render(request, "concerts/ticket_verify.html", {
        "ticket": ticket,
        "valid": valid,
        "admin_scan": admin_scan,
        "already_used": already_used,
    })


@user_passes_test(lambda user: user.is_staff)
def download_proof(request, proof_id):
    proof = get_object_or_404(PaymentProof.objects.select_related("order"), pk=proof_id)
    if not proof.file:
        raise Http404("К этому заказу чек не приложен.")
    try:
        upload = proof.file.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404("Файл чека не найден.")
    return FileResponse(upload, as_attachment=True, filename=proof.original_name)


@csrf_exempt
def telegram_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)
    if not verify_webhook_secret(request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")):
        return HttpResponse(status=403)
    try:
        update = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse(status=400)
    callback = update.get("callback_query")
    if callback:
        try:
            handle_review_callback(callback)
        except Exception:
            logger.exception("Could not handle Telegram bot update.")
    return JsonResponse({"ok": True})
