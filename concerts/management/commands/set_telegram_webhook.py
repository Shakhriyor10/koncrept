from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from concerts.telegram_bot import TelegramBotError, telegram_api


class Command(BaseCommand):
    help = "Register the public HTTPS endpoint for Telegram order review callbacks."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("Set TELEGRAM_BOT_TOKEN in .env first.")
        if not settings.TELEGRAM_CHAT_ID:
            raise CommandError("Set TELEGRAM_CHAT_ID in .env first.")
        if not settings.TELEGRAM_WEBHOOK_SECRET:
            raise CommandError("Set TELEGRAM_WEBHOOK_SECRET in .env first.")

        public_url = f"{settings.PUBLIC_SITE_URL}{reverse('telegram-webhook')}"
        if urlparse(public_url).scheme != "https":
            raise CommandError("PUBLIC_SITE_URL must be an HTTPS address reachable by Telegram.")
        try:
            telegram_api("setWebhook", {
                "url": public_url,
                "secret_token": settings.TELEGRAM_WEBHOOK_SECRET,
                "allowed_updates": ["callback_query"],
            })
        except TelegramBotError as exc:
            raise CommandError(f"Could not register Telegram webhook: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Telegram webhook registered: {public_url}"))
