import re

from django.core.exceptions import ValidationError


UZ_PHONE_PATTERN = r"\+998[0-9]{9}"


def validate_uzbek_phone(value):
    value = (value or "").strip()
    if not re.fullmatch(UZ_PHONE_PATTERN, value):
        raise ValidationError("Введите номер в формате +998 и ровно 9 цифр после кода страны.")
    return value


def normalize_stored_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 9:
        return "+998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return None
