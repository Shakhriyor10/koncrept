from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("account/", views.dashboard, name="dashboard"),
    path("account/profile/", views.profile_edit, name="profile-edit"),
    path("staff/orders/", views.staff_orders, name="staff-orders"),
    path("staff/tickets/scan/", views.staff_ticket_scanner, name="staff-ticket-scanner"),
    path("staff/tickets/scan/check/", views.staff_scan_ticket, name="staff-scan-ticket"),
    path("staff/orders/<str:reference>/review/", views.staff_review_order, name="staff-review-order"),
    path("telegram/webhook/", views.telegram_webhook, name="telegram-webhook"),
    path("orders/new/", views.order_create, name="order-create"),
    path("orders/<str:reference>/payment/", views.order_payment, name="order-payment"),
    path("orders/<str:reference>/", views.order_detail, name="order-detail"),
    path("orders/<str:reference>/status/", views.order_status, name="order-status"),
    path("orders/<str:reference>/tickets.pdf", views.order_tickets_pdf, name="order-tickets-pdf"),
    path("tickets/<uuid:token>/", views.ticket_detail, name="ticket-detail"),
    path("tickets/<uuid:token>/qr.png", views.ticket_qr, name="ticket-qr"),
    path("tickets/<uuid:token>/verify/", views.ticket_verify, name="ticket-verify"),
    path("staff/proofs/<int:proof_id>/download/", views.download_proof, name="proof-download"),
]
