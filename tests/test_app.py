from datetime import date

import pytest

from app.models import Ferias, Funcionario, Usuario, db


def entrar(client, login="admin", senha="admin"):
    return client.post("/login", data={"login": login, "senha": senha}, follow_redirects=True)


def test_login_invalido(app):
    resp = entrar(app.test_client(), senha="errada")
    assert "Usuário ou senha inválidos" in resp.get_data(as_text=True)


def test_bloqueio_apos_varias_falhas(app):
    client = app.test_client()
    for _ in range(5):
        entrar(client, senha="errada")
    resp = client.post("/login", data={"login": "admin", "senha": "admin"})
    assert resp.status_code == 429
    assert "Muitas tentativas" in resp.get_data(as_text=True)


def test_login_com_sucesso_zera_falhas_do_usuario(app):
    client = app.test_client()
    for _ in range(3):
        entrar(client, senha="errada")
    assert "Painel" in entrar(client).get_data(as_text=True)
    client.get("/logout")
    with app.app_context():
        from app.models import TentativaLogin

        restantes = db.session.scalars(db.select(TentativaLogin.chave)).all()
        assert all(not c.startswith("login:") for c in restantes)


def test_cabecalhos_de_seguranca(app):
    resp = app.test_client().get("/login")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_redirecionamento_externo_bloqueado(app):
    client = app.test_client()
    resp = client.post("/login?next=https://site-malicioso.com", data={"login": "admin", "senha": "admin"})
    assert resp.status_code == 302
    assert "site-malicioso" not in resp.headers["Location"]


def test_paginas_principais_carregam(app):
    client = app.test_client()
    entrar(client)
    for url in ["/", "/ferias/", "/ferias/nova", "/ferias/calendario", "/cronograma/",
                "/admin/funcionarios", "/admin/usuarios", "/admin/configuracoes", "/admin/historico"]:
        assert client.get(url).status_code == 200, url


def test_criar_aprovar_e_exportar(app):
    client = app.test_client()
    entrar(client)
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))

    resp = client.post(
        "/ferias/nova",
        data={"funcionario_id": carlos.id, "inicio": "2026-12-01", "dias": "20"},
        follow_redirects=True,
    )
    assert "Férias salvas" in resp.get_data(as_text=True)

    with app.app_context():
        ferias = db.session.scalar(db.select(Ferias))
        assert ferias.fim == date(2026, 12, 20)
        assert not ferias.aprovada
        ferias_id = ferias.id

    client.post(f"/ferias/{ferias_id}/aprovar")
    with app.app_context():
        ferias = db.session.get(Ferias, ferias_id)
        assert ferias.aprovada and ferias.aprovado_por == "admin"

    resp = client.get("/ferias/exportar?ciclo=2026")
    assert resp.status_code == 200
    assert resp.data[:2] == b"PK"


def test_aviso_exige_confirmacao(app):
    client = app.test_client()
    entrar(client)
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))

    dados = {"funcionario_id": carlos.id, "inicio": "2026-12-04", "dias": "10"}  # sexta-feira
    resp = client.post("/ferias/nova", data=dados)
    assert "Salvar mesmo assim" in resp.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Ferias.id))) == 0

    client.post("/ferias/nova", data={**dados, "confirmar": "1"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Ferias.id))) == 1


def test_programacao_mostra_cobertura_e_alternativa(app):
    client = app.test_client()
    entrar(client)
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        edson = db.session.scalar(db.select(Funcionario).filter_by(nome="Edson"))
        ana = db.session.scalar(db.select(Funcionario).filter_by(nome="Ana"))
        ids = (carlos.id, edson.id, ana.id)

    for fid in ids:
        client.post("/ferias/nova", data={"funcionario_id": fid, "inicio": "2026-12-01", "dias": "10", "confirmar": "1"})

    pagina = client.get("/ferias/?ciclo=2026").get_data(as_text=True)
    assert "Cobertura do setor" in pagina
    assert "Semanas com menos de 3 no setor" in pagina
    assert "Alternativa:" in pagina
    assert pagina.find("Resumo mensal") < pagina.find("grafico-cobertura")


def test_analise_ao_vivo(app):
    client = app.test_client()
    entrar(client)
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
    resp = client.post("/ferias/analisar", json={"funcionario_id": carlos.id, "inicio": "2026-12-01", "dias": 20})
    assert resp.get_json()["fim"] == "20/12/2026"


def test_usuario_comum_nao_aprova_nem_ve_cronograma(app):
    client = app.test_client()
    entrar(client, "joao", "senha123")
    assert client.get("/cronograma/").status_code == 403
    assert client.get("/admin/usuarios").status_code == 403
    assert client.post("/ferias/1/aprovar").status_code == 403
    assert client.get("/ferias/").status_code == 200


def test_troca_de_senha_obrigatoria(app):
    with app.app_context():
        u = db.session.scalar(db.select(Usuario).filter_by(login="joao"))
        u.trocar_senha = True
        db.session.commit()
    client = app.test_client()
    entrar(client, "joao", "senha123")
    resp = client.get("/ferias/")
    assert resp.status_code == 302 and "/trocar-senha" in resp.headers["Location"]


def test_importacao_da_planilha_original(app):
    from pathlib import Path

    from app.seed import importar_planilha

    planilhas = [
        p for p in Path(__file__).resolve().parent.parent.glob("*.xlsm")
        if "prova" not in p.name.lower()
    ]
    if not planilhas:
        pytest.skip("planilha original não encontrada")
    with app.app_context():
        resumo = importar_planilha(planilhas[0])
        assert resumo["funcionarios"] >= 10
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        assert carlos.ferias and carlos.ferias[0].fim == date(2026, 12, 20)
        assert resumo["usuarios"] == 3
        assert importar_planilha(planilhas[0])["ferias"] == 0
