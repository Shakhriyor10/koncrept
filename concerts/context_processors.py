from .models import Order


def staff_pending_count(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated or not request.user.is_staff:
        return {"staff_pending_count": 0}
    return {"staff_pending_count": Order.objects.filter(status=Order.Status.UNDER_REVIEW).count()}
