import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Concert, CustomerProfile, Order, OrderItem, Ticket, TicketType, WheelReward
from .phones import normalize_stored_phone

SPIN_INTERVAL = timedelta(hours=24)
GUEST_SESSION_KEY = "wheel_guest_reward"
PRIZES = (
    {"code": WheelReward.Code.DISCOUNT_10, "label": "Скидка 10% на один билет", "weight": 100, "percent": 10},
    {"code": WheelReward.Code.DISCOUNT_20, "label": "Скидка 20% на один билет", "weight": 80, "percent": 20},
    {"code": WheelReward.Code.DISCOUNT_30, "label": "Скидка 30% на один билет", "weight": 40, "percent": 30},
    {"code": WheelReward.Code.DISCOUNT_60, "label": "Скидка 60% на один билет", "weight": 10, "percent": 60},
    {"code": WheelReward.Code.GIFT_STANDARD, "label": "Стандартный билет в подарок", "weight": 4, "percent": 0, "gift": True},
    {"code": WheelReward.Code.GIFT_VIP, "label": "VIP-билет в подарок", "weight": 1, "percent": 0, "gift": True},
)
PRIZE_BY_CODE = {prize["code"]: prize for prize in PRIZES}


def prize_data(code):
    prize = PRIZE_BY_CODE.get(code)
    return dict(prize) if prize else None


def draw_prize():
    # The requested percentages add up to 117.5%; use them as relative weights.
    total_weight = sum(prize["weight"] for prize in PRIZES)
    draw = secrets.randbelow(total_weight)
    for prize in PRIZES:
        if draw < prize["weight"]:
            return prize
        draw -= prize["weight"]
    return PRIZES[0]


def _reward_payload(code, spun_at, expires_at, claimed=False):
    prize = prize_data(code)
    if not prize:
        return None
    return {
        "code": code,
        "label": prize["label"],
        "percent": prize["percent"],
        "gift": bool(prize.get("gift")),
        "claimed": claimed,
        "spun_at": spun_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def get_user_reward_status(user):
    now = timezone.now()
    latest = WheelReward.objects.filter(user=user).order_by("-spun_at").first()
    active = WheelReward.objects.filter(user=user, claimed_at__isnull=True, expires_at__gt=now).order_by("-spun_at").first()
    next_spin_at = latest.spun_at + SPIN_INTERVAL if latest else None
    display_reward = active or latest
    reward = _reward_payload(
        display_reward.code,
        display_reward.spun_at,
        display_reward.expires_at,
        claimed=display_reward.claimed_at is not None,
    ) if display_reward else None
    return {
        "authenticated": True,
        "can_spin": next_spin_at is None or now >= next_spin_at,
        "next_spin_at": next_spin_at.isoformat() if next_spin_at else None,
        "reward": reward,
    }


def _session_state(request):
    state = request.session.get(GUEST_SESSION_KEY)
    if not isinstance(state, dict):
        return None
    spun_at = parse_datetime(state.get("spun_at", ""))
    expires_at = parse_datetime(state.get("expires_at", ""))
    if not spun_at or not expires_at or timezone.is_naive(spun_at) or timezone.is_naive(expires_at):
        request.session.pop(GUEST_SESSION_KEY, None)
        return None
    return {**state, "spun_at_dt": spun_at, "expires_at_dt": expires_at}


def get_guest_reward_status(request):
    now = timezone.now()
    state = _session_state(request)
    if not state:
        return {"authenticated": False, "can_spin": True, "next_spin_at": None, "reward": None}
    next_spin_at = state["spun_at_dt"] + SPIN_INTERVAL
    reward = _reward_payload(
        state.get("code", ""), state["spun_at_dt"], state["expires_at_dt"],
        claimed=bool(state.get("claimed", False)),
    )
    return {
        "authenticated": False,
        "can_spin": now >= next_spin_at,
        "next_spin_at": next_spin_at.isoformat(),
        "reward": reward,
    }


def _issue_gift_ticket(user, reward):
    prize = prize_data(reward.code)
    if not prize or not prize.get("gift"):
        return None
    type_code = TicketType.Code.VIP if reward.code == WheelReward.Code.GIFT_VIP else TicketType.Code.STANDARD
    concert = Concert.objects.filter(is_active=True).prefetch_related("ticket_types").first()
    if not concert:
        return None
    ticket_type = concert.ticket_types.filter(code=type_code, active=True).first()
    if not ticket_type:
        return None
    profile = CustomerProfile.objects.filter(user=user).first()
    phone = (profile.phone if profile else "") or normalize_stored_phone(user.username) or user.username
    now = timezone.now()
    order = Order.objects.create(
        user=user,
        concert=concert,
        status=Order.Status.APPROVED,
        phone=phone,
        reviewed_at=now,
        review_note=f"{prize['label']} — подарок барабана удачи.",
        wheel_reward=reward,
    )
    item = OrderItem.objects.create(
        order=order,
        ticket_type=ticket_type,
        quantity=1,
        unit_price=ticket_type.price,
        discount_amount=ticket_type.price,
    )
    Ticket.objects.create(order=order, order_item=item, sequence=1)
    reward.claimed_at = now
    reward.save(update_fields=("claimed_at",))
    return order


def _create_reward_for_user(user, code, spun_at, expires_at):
    reward = WheelReward.objects.create(user=user, code=code, spun_at=spun_at, expires_at=expires_at)
    gift_order = _issue_gift_ticket(user, reward)
    return reward, gift_order


def spin_for_user(user):
    now = timezone.now()
    user_model = get_user_model()
    with transaction.atomic():
        user_model.objects.select_for_update().get(pk=user.pk)
        latest = WheelReward.objects.filter(user=user).order_by("-spun_at").first()
        next_spin_at = latest.spun_at + SPIN_INTERVAL if latest else None
        if next_spin_at and now < next_spin_at:
            return None, None, next_spin_at
        prize = draw_prize()
        reward, gift_order = _create_reward_for_user(user, prize["code"], now, now + SPIN_INTERVAL)
    return reward, gift_order, None


def spin_for_guest(request):
    now = timezone.now()
    state = _session_state(request)
    if state:
        next_spin_at = state["spun_at_dt"] + SPIN_INTERVAL
        if now < next_spin_at:
            return None, next_spin_at
    prize = draw_prize()
    expires_at = now + SPIN_INTERVAL
    request.session[GUEST_SESSION_KEY] = {
        "code": prize["code"],
        "spun_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "claimed": False,
    }
    request.session.modified = True
    return _reward_payload(prize["code"], now, expires_at), None


def attach_guest_reward(request, user):
    state = _session_state(request)
    if not state:
        request.session.pop(GUEST_SESSION_KEY, None)
        return None, None
    now = timezone.now()
    if state["expires_at_dt"] <= now or state.get("claimed"):
        request.session.pop(GUEST_SESSION_KEY, None)
        return None, None
    with transaction.atomic():
        user_model = get_user_model()
        user_model.objects.select_for_update().get(pk=user.pk)
        latest = WheelReward.objects.filter(user=user).order_by("-spun_at").first()
        if latest and now < latest.spun_at + SPIN_INTERVAL:
            request.session.pop(GUEST_SESSION_KEY, None)
            return None, None
        reward, gift_order = _create_reward_for_user(
            user, state["code"], state["spun_at_dt"], state["expires_at_dt"]
        )
    request.session.pop(GUEST_SESSION_KEY, None)
    request.session.modified = True
    return reward, gift_order


def active_discount_for_user(user, *, lock=False):
    rewards = WheelReward.objects.filter(
        user=user,
        claimed_at__isnull=True,
        expires_at__gt=timezone.now(),
        code__in=(
            WheelReward.Code.DISCOUNT_5,
            WheelReward.Code.DISCOUNT_10,
            WheelReward.Code.DISCOUNT_20,
            WheelReward.Code.DISCOUNT_25,
            WheelReward.Code.DISCOUNT_30,
            WheelReward.Code.DISCOUNT_50,
            WheelReward.Code.DISCOUNT_60,
        ),
    ).order_by("-spun_at")
    if lock:
        rewards = rewards.select_for_update()
    return rewards.first()
