from pathlib import Path

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from PIL import Image, UnidentifiedImageError

from .models import CustomerProfile, PaymentCard
from .phones import UZ_PHONE_PATTERN, normalize_stored_phone, validate_uzbek_phone


def phone_widget_attrs():
    return {
        "type": "tel",
        "inputmode": "tel",
        "autocomplete": "tel",
        "data-uz-phone": "true",
        "maxlength": "13",
        "minlength": "13",
        "pattern": UZ_PHONE_PATTERN,
        "placeholder": "+998",
    }


class RegistrationForm(UserCreationForm):
    username = None
    phone = forms.CharField(label="Номер телефона", max_length=13, min_length=13, initial="+998", widget=forms.TextInput(attrs=phone_widget_attrs()))

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(("phone", "password1", "password2"))

    def clean_phone(self):
        phone = validate_uzbek_phone(self.cleaned_data["phone"])
        if User.objects.filter(username=phone).exists():
            raise ValidationError("Аккаунт с таким номером телефона уже зарегистрирован.")
        for profile in CustomerProfile.objects.select_related("user").all():
            if normalize_stored_phone(profile.phone) == phone:
                raise ValidationError("Аккаунт с таким номером телефона уже зарегистрирован.")
        return phone

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = ""
        user.last_name = ""
        user.email = ""
        user.username = self.cleaned_data["phone"]
        if commit:
            user.save()
            CustomerProfile.objects.update_or_create(
                user=user, defaults={"phone": self.cleaned_data["phone"].strip()}
            )
        return user


class OrderCreateForm(forms.Form):
    quantity_vip = forms.IntegerField(label="VIP", min_value=0, max_value=20, required=False, initial=0)
    quantity_standard = forms.IntegerField(label="Стандарт", min_value=0, max_value=20, required=False, initial=0)
    quantity_fan_zone = forms.IntegerField(label="Фан-зона", min_value=0, max_value=20, required=False, initial=0)

    def clean(self):
        cleaned = super().clean()
        quantity_names = ("quantity_vip", "quantity_standard", "quantity_fan_zone")
        for name in quantity_names:
            cleaned[name] = cleaned.get(name) or 0
        quantities = tuple(cleaned[name] for name in quantity_names)
        if sum(quantities) < 1:
            raise ValidationError("Выберите хотя бы один билет.")
        if sum(quantities) > 20:
            raise ValidationError("В одном заказе можно оформить не более 20 билетов.")
        return cleaned


class CustomerDetailsForm(forms.Form):
    phone = forms.CharField(label="Номер телефона", max_length=13, min_length=13, widget=forms.TextInput(attrs=phone_widget_attrs()))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_phone(self):
        phone = validate_uzbek_phone(self.cleaned_data["phone"])
        if User.objects.filter(username=phone).exclude(pk=getattr(self.user, "pk", None)).exists():
            raise ValidationError("Этот номер уже используется другим аккаунтом.")
        for profile in CustomerProfile.objects.select_related("user").exclude(user=self.user):
            if normalize_stored_phone(profile.phone) == phone:
                raise ValidationError("Этот номер уже используется другим аккаунтом.")
        return phone


class PhoneAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Номер телефона"
        self.fields["username"].max_length = 13
        self.fields["username"].min_length = 13
        self.fields["username"].widget = forms.TextInput(attrs=phone_widget_attrs())
        self.fields["username"].initial = "+998"

    def clean_username(self):
        return validate_uzbek_phone(self.cleaned_data["username"])


class PaymentProofForm(forms.Form):
    payment_provider = forms.ChoiceField(
        label="Тип карты",
        choices=(),
        required=True,
    )
    payment_card = forms.ModelChoiceField(
        label="Карта, на которую отправлен перевод",
        queryset=PaymentCard.objects.none(),
        required=True,
    )
    proof = forms.FileField(
        label="Файл чека",
        required=False,
        validators=[FileExtensionValidator(
            allowed_extensions=("jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "pdf", "heic", "heif")
        )],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_providers = set(
            PaymentCard.objects.filter(active=True).values_list("provider", flat=True).distinct()
        )
        provider_labels = dict(PaymentCard.Provider.choices)
        self.fields["payment_provider"].choices = [
            ("", "Выберите тип карты"),
            *((provider, provider_labels[provider]) for provider in ("uzcard", "humo")),
        ]

        provider = self.data.get("payment_provider") if self.is_bound else self.initial.get("payment_provider")
        if provider in active_providers:
            self.fields["payment_card"].queryset = PaymentCard.objects.filter(
                active=True, provider=provider
            )

    def clean_payment_provider(self):
        provider = self.cleaned_data["payment_provider"]
        if not PaymentCard.objects.filter(active=True, provider=provider).exists():
            raise ValidationError("Для этого типа пока нет активных карт. Выберите другой тип оплаты.")
        return provider

    def clean_proof(self):
        upload = self.cleaned_data.get("proof")
        if not upload:
            return None
        if upload.size > 10 * 1024 * 1024:
            raise ValidationError("Размер файла не должен превышать 10 МБ.")

        extension = Path(upload.name).suffix.lower()
        if extension == ".pdf":
            if upload.read(5) != b"%PDF-":
                raise ValidationError("Файл с расширением PDF не является корректным PDF-документом.")
            upload.seek(0)
        elif extension in {".heic", ".heif"}:
            header = upload.read(32)
            upload.seek(0)
            if b"ftyp" not in header:
                raise ValidationError("Файл не похож на изображение HEIC/HEIF.")
        else:
            try:
                image = Image.open(upload)
                if image.format not in {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}:
                    raise ValidationError("Поддерживаются фото JPG, PNG, WebP, BMP и TIFF.")
                image.verify()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise ValidationError("Не удалось прочитать изображение. Проверьте файл и попробуйте ещё раз.")
            finally:
                upload.seek(0)
        return upload
