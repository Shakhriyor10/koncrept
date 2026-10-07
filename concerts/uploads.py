from pathlib import Path
from uuid import uuid4


def proof_upload_path(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"payment-proofs/{instance.order.reference}/{uuid4().hex}{extension}"
