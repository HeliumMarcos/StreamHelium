"""Central place for the admin's contact info shown to family accounts."""

import os
from urllib.parse import quote

DEFAULT_WHATSAPP_NUMBER = "5564999877087"


def admin_whatsapp_number():
    return os.environ.get("ADMIN_WHATSAPP_NUMBER", DEFAULT_WHATSAPP_NUMBER)


def admin_whatsapp_link(message: str | None = None) -> str:
    number = admin_whatsapp_number()
    if message:
        return f"https://wa.me/{number}?text={quote(message)}"
    return f"https://wa.me/{number}"


def admin_whatsapp_html(label: str = "Fale com o administrador no WhatsApp", message: str | None = None) -> str:
    href = admin_whatsapp_link(message)
    return f'<a href="{href}" target="_blank" rel="noopener">{label}</a>'
