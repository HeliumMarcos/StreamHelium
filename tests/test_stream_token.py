"""O endereço do addon virou trocável.

Antes ele carregava o `id` da conta, que é imutável. "Rotacionar link" no
Catálogo trocava só o endereço de lá; o da Vercel continuava valendo para
sempre, e quem tivesse visto o 302 uma vez assistia indefinidamente.

O que estes testes protegem é a única coisa que faz a rotação valer
alguma coisa: depois dela, o endereço anterior tem que MORRER.
"""

import pytest

from sgd import tenancy


UID = "11111111-1111-1111-1111-111111111111"


def _conta(**extras):
    base = {"id": UID, "email": "casa@exemplo.com", "active": True}
    base.update(extras)
    return base


def _fingir(monkeypatch, por_token=None, por_id=None):
    monkeypatch.setattr("sgd.db.get_user_by_stream_token", lambda t: por_token)
    monkeypatch.setattr("sgd.db.get_user", lambda i: por_id)


def test_the_new_address_opens_the_account(monkeypatch):
    conta = _conta(stream_token="tok-novo")
    _fingir(monkeypatch, por_token=conta)

    assert tenancy._resolver("tok-novo") == conta


def test_the_old_address_dies_once_the_account_has_rotated(monkeypatch):
    """O ponto inteiro da mudança.

    Enquanto o id continuasse abrindo a conta, rotacionar não revogaria
    nada — seria trocar a fechadura da frente e deixar a dos fundos.
    """
    _fingir(monkeypatch, por_token=None, por_id=_conta(stream_token="tok-novo"))

    assert tenancy._resolver(UID) is None


def test_an_account_that_never_rotated_still_works_by_id(monkeypatch):
    """Publicar isto não pode derrubar quem já estava assistindo.

    Contas antigas ainda não têm token, e o Catálogo ainda não os conhece.
    Elas migram quando alguém rotaciona o link delas.
    """
    conta = _conta(stream_token=None)
    _fingir(monkeypatch, por_token=None, por_id=conta)

    assert tenancy._resolver(UID) == conta


def test_an_unknown_address_resolves_to_nothing(monkeypatch):
    _fingir(monkeypatch, por_token=None, por_id=None)

    assert tenancy._resolver("qualquer-coisa") is None


def test_the_token_is_not_derived_from_the_id():
    """Se derivasse, trocar não trocaria nada — daria para recalcular."""
    from sgd import db

    a, b = db.new_stream_token(), db.new_stream_token()

    assert a != b
    assert UID not in a
    assert len(a) >= 24
