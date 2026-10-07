import json
import logging
import mimetypes
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Prefetch

from .models import Order, OrderItem, PaymentProof
from .services import approve_order, reject_order

logger = logging.getLogger(__name__)


class TelegramBotError(Exception):
    pass


def telegram_api(method, data=None, upload=None):
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise TelegramBotError("Telegram bot token is not configured.")

    url = f"https://api.telegram.org/bot{token}/{method}"
    headers = {}
    if upload:
        boundary = f"----koncrept-{uuid.uuid4().hex}"
        chunks = []
        for key, value in (data or {}).items():
            chunks.extend((
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ))
        field_name, filename, content, content_type = upload
        safe_filename = filename.replace('"', "_").replace("\r", "").replace("\n", "")
        chunks.extend((
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ))
        body = b"".join(chunks)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    else:
        body = json.dumps(data or {}).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise TelegramBotError(f"Telegram API request failed: {exc}") from exc
    if not result.get("ok"):
        raise TelegramBotError(result.get("description", "Telegram API returned an error."))
    return result.get("result")


def _order_message(order, proof):
    lines = [
        "Новый перевод на проверку",
        f"Заказ: {order.reference}",
        f"Телефон: {order.phone}",
        "Билеты:",
    ]
    for item in order.items.select_related("ticket_type"):
        lines.append(f"• {item.ticket_type.title} × {item.quantity} — {item.total:,} сум".replace(",", " "))
    lines.extend((
        f"Количество билетов: {order.ticket_count}",
        f"Итого: {order.total_amount:,} сум".replace(",", " "),
        f"Перевод на: {proof.payment_card.get_provider_display()} · карта **** {proof.payment_card.card_number.replace(' ', '')[-4:]}",
        f"Имя владельца карты: {proof.payment_card.cardholder}",
        f"Чек: {proof.original_name if proof.file else 'не приложен'}",
    ))
    return "\n".join(lines)


def _review_keyboard(order_id):
    return {
        "inline_keyboard": [[
            {"text": "✅ Подтвердить и выдать билеты", "callback_data": f"approve:{order_id}"},
            {"text": "❌ Отклонить", "callback_data": f"reject:{order_id}"},
        ]]
    }


def notify_order_for_review(order_id, proof_id):
    """Send a submitted payment (with an optional receipt) to the review group."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.info("Telegram order notifications are disabled; bot credentials are missing.")
        return

    try:
        proof = PaymentProof.objects.select_related("payment_card").get(pk=proof_id, order_id=order_id)
        order = Order.objects.prefetch_related(
            Prefetch("items", queryset=OrderItem.objects.select_related("ticket_type"))
        ).get(pk=order_id)
        text = _order_message(order, proof)
        keyboard = _review_keyboard(order.pk)
        if proof.file:
            with proof.file.open("rb") as uploaded_file:
                content = uploaded_file.read()
            content_type = mimetypes.guess_type(proof.original_name)[0] or "application/octet-stream"
            telegram_api(
                "sendDocument",
                {
                    "chat_id": settings.TELEGRAM_CHAT_ID,
                    "caption": text[:1024],
                    "reply_markup": json.dumps(keyboard, ensure_ascii=False),
                },
                upload=("document", proof.original_name, content, content_type),
            )
        else:
            telegram_api(
                "sendMessage",
                {
                    "chat_id": settings.TELEGRAM_CHAT_ID,
                    "text": text,
                    "reply_markup": keyboard,
                },
            )
    except Exception:
        # A Telegram outage must not undo or block a user's payment submission.
        logger.exception("Could not send order %s to the Telegram review group.", order_id)


def _answer_callback(callback_id, text, *, alert=False):
    telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text[:200],
        "show_alert": alert,
    })


def _send_group_result(text):
    telegram_api("sendMessage", {"chat_id": settings.TELEGRAM_CHAT_ID, "text": text})


def handle_review_callback(callback):
    callback_id = callback.get("id", "")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    actor = callback.get("from") or {}
    if str(chat.get("id", "")) != settings.TELEGRAM_CHAT_ID:
        _answer_callback(callback_id, "Эта кнопка работает только в настроенной группе.", alert=True)
        return

    actor_id = actor.get("id")
    if not actor_id:
        _answer_callback(callback_id, "Не удалось определить пользователя Telegram.", alert=True)
        return
    try:
        member = telegram_api("getChatMember", {
            "chat_id": settings.TELEGRAM_CHAT_ID,
            "user_id": actor_id,
        })
    except TelegramBotError:
        logger.exception("Unable to verify Telegram group administrator %s.", actor_id)
        _answer_callback(callback_id, "Не удалось проверить права администратора. Попробуйте позже.", alert=True)
        return

    if member.get("status") not in ("creator", "administrator"):
        _answer_callback(callback_id, "Подтверждать заказы могут только администраторы группы.", alert=True)
        return

    try:
        action, order_id = callback.get("data", "").split(":", 1)
        if action not in ("approve", "reject") or not order_id.isdigit():
            raise ValueError
        order = Order.objects.get(pk=int(order_id))
        actor_name = actor.get("username") or " ".join(
            part for part in (actor.get("first_name"), actor.get("last_name")) if part
        ) or str(actor_id)
        if action == "approve":
            order = approve_order(order.pk, reviewer=None)
            order.review_note = f"Подтверждено администратором Telegram: {actor_name}"[:500]
            order.save(update_fields=("review_note",))
            result = f"✅ Заказ {order.reference} подтверждён администратором {actor_name}. Билеты выданы."
        else:
            order = reject_order(
                order.pk,
                reviewer=None,
                note=f"Перевод отклонён администратором Telegram: {actor_name}. Проверьте перевод и отправьте заказ повторно.",
            )
            result = f"❌ Заказ {order.reference} отклонён администратором {actor_name}."
        _answer_callback(callback_id, "Решение сохранено.")
        telegram_api("editMessageReplyMarkup", {
            "chat_id": settings.TELEGRAM_CHAT_ID,
            "message_id": message.get("message_id"),
            "reply_markup": {"inline_keyboard": []},
        })
        _send_group_result(result)
    except (Order.DoesNotExist, ValueError, ValidationError):
        _answer_callback(callback_id, "Заказ уже обработан или больше не ожидает решения.", alert=True)
    except TelegramBotError:
        logger.exception("Could not update Telegram review message.")
    except Exception:
        logger.exception("Could not process Telegram review callback.")
        try:
            _answer_callback(callback_id, "Не удалось обработать решение. Обновите список заказов.", alert=True)
        except TelegramBotError:
            logger.exception("Could not answer Telegram callback.")


def verify_webhook_secret(received_secret):
    expected = settings.TELEGRAM_WEBHOOK_SECRET
    return bool(expected and received_secret and secrets.compare_digest(expected, received_secret))
