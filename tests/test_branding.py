from sgd.branding import admin_whatsapp_html, admin_whatsapp_link, admin_whatsapp_number


def test_admin_whatsapp_number_default():
    assert admin_whatsapp_number() == "5564999877087"


def test_admin_whatsapp_number_env_override(monkeypatch):
    monkeypatch.setenv("ADMIN_WHATSAPP_NUMBER", "5511999998888")
    assert admin_whatsapp_number() == "5511999998888"


def test_admin_whatsapp_link_plain():
    assert admin_whatsapp_link() == "https://wa.me/5564999877087"


def test_admin_whatsapp_link_with_message():
    link = admin_whatsapp_link("Preciso de ajuda")
    assert link.startswith("https://wa.me/5564999877087?text=")
    assert "Preciso" in link


def test_admin_whatsapp_html_contains_link():
    html = admin_whatsapp_html()
    assert 'href="https://wa.me/5564999877087"' in html
    assert "WhatsApp" in html
