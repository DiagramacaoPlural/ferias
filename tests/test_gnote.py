from datetime import date
from io import BytesIO
from unittest.mock import patch

from app.gnote import avisar_resultado, sincronizar_periodo
from app.models import STATUS_APROVADA, Ferias, Funcionario, db


class _Resp:
    def __init__(self, corpo=b'{"ok": true, "dias": 5}', status=200):
        self.status = status
        self._corpo = corpo

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_sem_config_nao_chama_rede(app, monkeypatch):
    monkeypatch.delenv("GNOTE_FERIAS_URL", raising=False)
    monkeypatch.delenv("GNOTE_FERIAS_SEGREDO", raising=False)
    with patch("app.gnote._urlopen") as urlopen:
        assert sincronizar_periodo("aprovar", 1, "a@b.com", "Ana", date(2026, 1, 1), date(2026, 1, 10)) is None
        urlopen.assert_not_called()


def test_envia_json_e_cabecalho(app, monkeypatch):
    monkeypatch.setenv("GNOTE_FERIAS_URL", "https://exemplo.supabase.co/functions/v1/sincronizar-ferias")
    monkeypatch.setenv("GNOTE_FERIAS_SEGREDO", "segredo-teste")
    with patch("app.gnote._urlopen", return_value=_Resp()) as urlopen:
        r = sincronizar_periodo("aprovar", 9, "ana@plural.com.br", "Ana Souza", date(2026, 2, 1), date(2026, 2, 15))
    assert r == {"ok": True, "dias": 5}
    req = urlopen.call_args[0][0]
    cabecalhos = {str(k).lower(): v for k, v in req.header_items()}
    assert cabecalhos.get("x-ferias-segredo") == "segredo-teste"
    corpo = req.data.decode("utf-8")
    assert '"ferias_id": "9"' in corpo
    assert '"acao": "aprovar"' in corpo


def test_http_404_funcao_nao_encontrada(app, monkeypatch):
    monkeypatch.setenv("GNOTE_FERIAS_URL", "https://exemplo.supabase.co/functions/v1/sincronizar-ferias")
    monkeypatch.setenv("GNOTE_FERIAS_SEGREDO", "segredo-teste")
    import urllib.error

    with patch("app.gnote._urlopen", side_effect=urllib.error.HTTPError(
        "https://x", 404, "Not Found", hdrs=None, fp=BytesIO(b'{"code":"NOT_FOUND","message":"Requested function was not found"}')
    )):
        r = sincronizar_periodo("aprovar", 1, "x@y.com", "X", date(2026, 1, 1), date(2026, 1, 2))
    assert r["ok"] is False
    assert r["erro"] == "funcao_nao_encontrada"


def test_http_401_jwt_ligado(app, monkeypatch):
    monkeypatch.setenv("GNOTE_FERIAS_URL", "https://exemplo.supabase.co/functions/v1/sincronizar-ferias")
    monkeypatch.setenv("GNOTE_FERIAS_SEGREDO", "segredo-teste")
    import urllib.error

    with patch("app.gnote._urlopen", side_effect=urllib.error.HTTPError(
        "https://x", 401, "Unauthorized", hdrs=None, fp=BytesIO(b'{"msg":"Invalid JWT"}')
    )):
        r = sincronizar_periodo("aprovar", 1, "x@y.com", "X", date(2026, 1, 1), date(2026, 1, 2))
    assert r["erro"] == "jwt_ligado"


def test_http_erro_usuario_nao_encontrado(app, monkeypatch):
    monkeypatch.setenv("GNOTE_FERIAS_URL", "https://exemplo.supabase.co/functions/v1/sincronizar-ferias")
    monkeypatch.setenv("GNOTE_FERIAS_SEGREDO", "segredo-teste")
    import urllib.error

    with patch("app.gnote._urlopen", side_effect=urllib.error.HTTPError(
        "https://x", 422, "Unprocessable", hdrs=None, fp=BytesIO(b'{"ok": false, "erro": "usuario_nao_encontrado"}')
    )):
        r = sincronizar_periodo("aprovar", 1, "x@y.com", "X", date(2026, 1, 1), date(2026, 1, 2))
    assert r["ok"] is False
    assert r["erro"] == "usuario_nao_encontrado"


def test_gerente_aprovar_chama_gnote(app, monkeypatch):
    monkeypatch.setenv("GNOTE_FERIAS_URL", "https://exemplo.supabase.co/functions/v1/sincronizar-ferias")
    monkeypatch.setenv("GNOTE_FERIAS_SEGREDO", "segredo-teste")
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        carlos.email = "carlos@plural.com.br"
        ferias = Ferias(funcionario=carlos, criado_por="admin")
        ferias.definir_periodo(date(2026, 7, 1), 10)
        db.session.add(ferias)
        db.session.commit()
        from app.workflow import gerente_aprovar

        with patch("app.gnote._urlopen", return_value=_Resp()) as urlopen:
            gerente_aprovar(ferias, "gerente")
            urlopen.assert_called_once()
            corpo = urlopen.call_args[0][0].data.decode("utf-8")
        assert '"acao": "aprovar"' in corpo
        assert '"inicio": "2026-07-01"' in corpo
        assert '"fim": "2026-07-10"' in corpo
        assert ferias.status == STATUS_APROVADA


def test_aviso_usuario_nao_encontrado(app):
    client = app.test_client()
    client.post("/login", data={"login": "admin", "senha": "admin"})
    with app.test_request_context("/"):
        avisar_resultado({"ok": False, "erro": "usuario_nao_encontrado"})
        from flask import get_flashed_messages
        msgs = get_flashed_messages(with_categories=True)
    assert any("não encontrou o usuário" in m for _, m in msgs)
