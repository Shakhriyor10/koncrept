from django.conf import settings
from django.urls import reverse


def ticket_verification_url(request, ticket):
    path = reverse("ticket-verify", kwargs={"token": ticket.token})
    public_site_url = getattr(settings, "PUBLIC_SITE_URL", "").rstrip("/")
    if public_site_url:
        return f"{public_site_url}{path}"
    return request.build_absolute_uri(path)
