from datetime import timedelta
import json
import logging
from pathlib import Path
import random
from uuid import UUID
from urllib.parse import urlparse

import qrcode
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import CustomerDetailsForm, OrderCreateForm, PaymentProofForm, PhoneAuthenticationForm, RegistrationForm
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
from .pdf_tickets import build_order_tickets_pdf
from .phones import normalize_stored_phone
from .services import approve_order, ensure_approved_order_tickets, reject_order
from .telegram_bot import handle_review_callback, notify_order_for_review, verify_webhook_secret
from .ticket_urls import ticket_verification_url

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


def register(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request))
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        auth_login(request, user)
        target = _safe_next(request)
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
        auth_login(request, form.get_user())
        target = _safe_next(request)
        if _is_async(request):
            return JsonResponse({"redirect": target})
        return redirect(target)
    if _is_async(request):
        html = render_to_string("registration/login_form.html", {"form": form, "next": request.GET.get("next", "")}, request)
        return JsonResponse({"html": html}, status=422)
    return render(request, "registration/login.html", {"form": form, "next": request.GET.get("next", "")})


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
    return render(request, "concerts/dashboard.html", {"orders": orders})


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
        .order_by("status", "created_at")
    )
    status_filter = request.GET.get("status", "under_review")
    if status_filter == Order.Status.UNDER_REVIEW:
        orders = orders.filter(status__in=(Order.Status.UNDER_REVIEW, Order.Status.EXPIRED))
    elif status_filter in Order.Status.values:
        orders = orders.filter(status=status_filter)
    else:
        status_filter = "all"
    return render(request, "concerts/staff_orders.html", {
        "orders": orders,
        "status_filter": status_filter,
        "statuses": Order.Status.choices,
        "pending_count": Order.objects.filter(
            status__in=(Order.Status.UNDER_REVIEW, Order.Status.EXPIRED)
        ).count(),
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
        else:
            raise Http404("Неизвестное действие.")
    except ValidationError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            "Заказ подтверждён, билеты выпущены."
            if action == "approve"
            else "Чек отклонён. Покупатель сможет отправить новый.",
        )
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
    form = OrderCreateForm(request.POST or None)
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
            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user,
                    concert=concert,
                    first_name="",
                    last_name="",
                    phone=profile.phone.strip(),
                    email="",
                )
                for code, quantity in quantities.items():
                    if quantity:
                        ticket_type = ticket_types[code]
                        OrderItem.objects.create(
                            order=order,
                            ticket_type=ticket_type,
                            quantity=quantity,
                            unit_price=ticket_type.price,
                        )
            target = reverse("order-payment", kwargs={"reference": order.reference})
            if _is_async(request):
                html = render_to_string(
                    "concerts/checkout_payment.html",
                    _payment_context(request, order),
                    request,
                )
                return JsonResponse({"html": html, "url": target})
            return redirect(target)

    context = {"concert": concert, "ticket_types": ticket_types, "form": form, "profile": profile}
    if _is_async(request):
        html = render_to_string("concerts/checkout_start.html", context, request)
        return JsonResponse({"html": html}, status=422)
    return render(request, "concerts/checkout.html", {"concert": concert, "flow_template": "concerts/checkout_start.html", "flow_context": context})


def _owned_order(request, reference):
    return get_object_or_404(
        Order.objects.select_related("concert", "user").prefetch_related("items__ticket_type", "tickets__order_item__ticket_type"),
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
        user=request.user,
        status=Order.Status.APPROVED,
    )
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
