from datetime import date

import pytest

from app.models import Configuracao, Ferias, Funcionario, Usuario, db


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
        assert ferias.fim == date(2026, 12, 21)
        assert not ferias.aprovada
        ferias_id = ferias.id

    client.post(f"/ferias/{ferias_id}/aprovar")
    with app.app_context():
        ferias = db.session.get(Ferias, ferias_id)
        assert ferias.aprovada and ferias.aprovado_por == "admin"

    resp = client.get("/ferias/exportar?ciclo=2026")
    assert resp.status_code == 200
    assert resp.data[:2] == b"PK"


def test_primeira_parcela_13_no_formulario_e_na_lista(app):
    client = app.test_client()
    entrar(client)
    pagina = client.get("/ferias/nova").get_data(as_text=True)
    assert "Antecipar a 1ª parcela do 13º" in pagina
    assert "Somente para férias com início a partir de fevereiro" in pagina
    assert 'id="adianta_13_sim"' in pagina
    assert 'id="adianta_13_nao"' in pagina

    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))

    recusado = client.post(
        "/ferias/nova",
        data={"funcionario_id": carlos.id, "inicio": "2027-01-11", "dias": "10", "adianta_13": "1", "confirmar": "1"},
    )
    html = recusado.get_data(as_text=True)
    assert "a partir de fevereiro" in html
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Ferias.id))) == 0

    resp = client.post(
        "/ferias/nova",
        data={"funcionario_id": carlos.id, "inicio": "2027-02-01", "dias": "10", "adianta_13": "1", "confirmar": "1"},
        follow_redirects=True,
    )
    assert "Férias salvas" in resp.get_data(as_text=True)
    with app.app_context():
        ferias = db.session.scalar(db.select(Ferias))
        assert ferias.adianta_13 is True
        assert ferias.inicio == date(2027, 2, 1)

    lista = client.get("/ferias/?ciclo=2026").get_data(as_text=True)
    assert ">Sim</span>" in lista

    ao_vivo = client.post(
        "/ferias/analisar",
        json={"funcionario_id": carlos.id, "inicio": "2027-01-11", "dias": 10, "adianta_13": True},
    )
    assert any("fevereiro" in e for e in ao_vivo.get_json()["erros"])


def test_acoes_em_lote_aprova_e_define_13(app):
    client = app.test_client()
    entrar(client)
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        edson = db.session.scalar(db.select(Funcionario).filter_by(nome="Edson"))
        ids_func = (carlos.id, edson.id)

    for fid, inicio in zip(ids_func, ("2027-02-01", "2027-02-15")):
        client.post("/ferias/nova", data={"funcionario_id": fid, "inicio": inicio, "dias": "10", "confirmar": "1"})

    with app.app_context():
        ids = [f.id for f in db.session.scalars(db.select(Ferias)).all()]
        assert len(ids) == 2

    lista = client.get("/ferias/?ciclo=2026").get_data(as_text=True)
    assert "13º Sim" in lista and "Aprovar" in lista

    resp = client.post("/ferias/lote", data={"acao": "aprovar", "ids": ids}, follow_redirects=True)
    assert "2 período(s) aprovado(s)" in resp.get_data(as_text=True)
    with app.app_context():
        assert all(f.aprovada for f in db.session.scalars(db.select(Ferias)))

    client.post("/ferias/lote", data={"acao": "13_sim", "ids": ids}, follow_redirects=True)
    with app.app_context():
        assert all(f.adianta_13 for f in db.session.scalars(db.select(Ferias)))

    client.post("/ferias/lote", data={"acao": "13_nao", "ids": [ids[0]]}, follow_redirects=True)
    with app.app_context():
        ferias = {f.id: f for f in db.session.scalars(db.select(Ferias))}
        assert ferias[ids[0]].adianta_13 is False
        assert ferias[ids[1]].adianta_13 is True


def test_dados_do_funcionario_no_formulario(app):
    client = app.test_client()
    entrar(client)
    pagina = client.get("/ferias/nova").get_data(as_text=True)
    assert 'id="resumo-funcionario"' in pagina
    assert "/funcionario/0/dados" in pagina

    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        edson = db.session.scalar(db.select(Funcionario).filter_by(nome="Edson"))
        cid, eid = carlos.id, edson.id

    client.post(
        "/ferias/nova",
        data={"funcionario_id": cid, "inicio": "2026-12-28", "dias": "8", "observacao": "parada de Thiago", "confirmar": "1"},
    )
    client.post(
        "/ferias/nova",
        data={"funcionario_id": eid, "inicio": "2027-02-10", "dias": "10", "adianta_13": "1", "confirmar": "1"},
    )

    dados = client.get(f"/ferias/funcionario/{eid}/dados?inicio=2026-12-28").get_json()
    assert dados["nome"] == "Edson"
    assert dados["usados"] == 10
    assert dados["restante"] == 20
    assert dados["periodos"][0]["inicio"] == "2027-02-10"
    assert dados["periodos"][0]["dias"] == 10
    assert dados["periodos"][0]["adianta_13"] is True

    with app.app_context():
        ana = db.session.scalar(db.select(Funcionario).filter_by(nome="Ana"))
        aid = ana.id
    vazio = client.get(f"/ferias/funcionario/{aid}/dados?inicio=2026-12-28").get_json()
    assert vazio["nome"] == "Ana"
    assert vazio["periodos"] == []

    atual = client.get(f"/ferias/funcionario/{cid}/dados?inicio=2026-12-28").get_json()
    assert atual["periodos"][0]["observacao"] == "parada de Thiago"
    assert atual["periodos"][0]["dias"] == 8


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
    assert resp.get_json()["fim"] == "21/12/2026"


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
        assert carlos.ferias and carlos.ferias[0].fim == date(2026, 12, 21)
        assert resumo["usuarios"] == 3
        assert importar_planilha(planilhas[0])["ferias"] == 0


def test_importacao_do_pdf_de_programacao(app):
    from pathlib import Path

    from app.seed import importar_programacao_pdf, ler_programacao_pdf

    pdfs = list(Path(__file__).resolve().parent.parent.glob("*Férias*.pdf")) + list(
        Path(__file__).resolve().parent.parent.glob("*Ferias*.pdf")
    )
    if not pdfs:
        pytest.skip("PDF de programação não encontrado")
    pessoas = ler_programacao_pdf(pdfs[0])
    assert {p["nome"].split()[0] for p in pessoas} >= {"CLAUDINEI", "EDSON", "GABRIEL", "WAGNER"}
    wagner = next(p for p in pessoas if p["nome"].startswith("WAGNER"))
    assert wagner["admissao"] == date(2025, 4, 29)
    assert wagner["limite"] == date(2027, 2, 26)

    with app.app_context():
        db.session.add(Funcionario(nome="Wagner"))
        db.session.commit()
        resumo = importar_programacao_pdf(pdfs[0])
        assert resumo["atualizados"] >= 1
        salvo = db.session.scalar(db.select(Funcionario).filter_by(nome="Wagner"))
        assert salvo.data_admissao == date(2025, 4, 29)


def test_coluna_limite_e_email_de_alerta(app, monkeypatch):
    enviados = []

    class SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            enviados.append(msg)

    monkeypatch.setattr("app.alertas.smtplib.SMTP", SMTP)
    client = app.test_client()
    entrar(client)
    with app.app_context():
        db.session.add(Funcionario(nome="Wagner", data_admissao=date(2025, 4, 29), cargo="Diagramador"))
        Configuracao.definir("smtp_host", "smtp.exemplo.com")
        Configuracao.definir("smtp_user", "ferias@plural.com.br")
        Configuracao.definir("smtp_remetente", "ferias@plural.com.br")
        Configuracao.definir("smtp_password", "x")
        db.session.commit()

    lista = client.get("/admin/funcionarios").get_data(as_text=True)
    assert "Limite para férias" in lista
    assert "26/02/2027" in lista

    config = client.get("/admin/configuracoes").get_data(as_text=True)
    assert "marcelo.marcolino@plural.com.br" in config
    assert "ricardo.rosas@plural.com.br" in config

    from app.alertas import enviar_alertas_pendentes

    with app.app_context():
        primeiro = enviar_alertas_pendentes(hoje=date(2027, 3, 28))
        assert primeiro["erro"] is None and primeiro["enviados"] == 1
        segundo = enviar_alertas_pendentes(hoje=date(2027, 3, 28))
        assert segundo["enviados"] == 0

    assert enviados
    assert "Wagner" in enviados[0].get_content()
    destinos = enviados[0]["To"]
    assert "marcelo.marcolino@plural.com.br" in destinos
    assert "ricardo.rosas@plural.com.br" in destinos
