from datetime import date

import pytest

from app.models import (
    PAPEL_GERENTE,
    PAPEL_LIDER,
    PAPEL_SIMPLES,
    STATUS_AGUARDANDO_FUNCIONARIO,
    STATUS_AGUARDANDO_GERENTE,
    STATUS_AGUARDANDO_LIDER,
    STATUS_APROVADA,
    Configuracao,
    Ferias,
    Funcionario,
    SugestaoFerias,
    Usuario,
    db,
)


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
    for url in ["/", "/ferias/", "/ferias/nova", "/ferias/calendario", "/ferias/opcoes",
                "/ferias/validacao/lider", "/ferias/validacao/gerente",
                "/admin/funcionarios", "/admin/usuarios", "/admin/configuracoes", "/admin/historico"]:
        assert client.get(url).status_code == 200, url
    cronograma = client.get("/cronograma/")
    assert cronograma.status_code == 302
    assert "/ferias/calendario" in cronograma.headers["Location"]


def test_painel_e_calendario_filtram_por_ciclo(app):
    client = app.test_client()
    entrar(client)
    painel = client.get("/").get_data(as_text=True)
    assert 'name="ciclo"' in painel
    assert 'aria-label="Ciclo"' in painel
    cal = client.get("/ferias/calendario").get_data(as_text=True)
    assert 'name="ciclo"' in cal
    assert 'aria-label="Ciclo"' in cal

    with app.app_context():
        cid = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos")).id
    client.post("/ferias/nova", data={"funcionario_id": cid, "inicio": "2026-12-01", "dias": "20"})
    assert "Carlos" in client.get("/?ciclo=2026").get_data(as_text=True)
    outro = client.get("/?ciclo=2024").get_data(as_text=True)
    assert "Nada pendente" in outro
    assert "Dezembro/2026" in client.get("/ferias/calendario?ciclo=2026").get_data(as_text=True)
    assert "Dezembro/2026" not in client.get("/ferias/calendario?ciclo=2024").get_data(as_text=True)


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


def test_primeira_parcela_13_no_formulario_e_na_lista(app):
    client = app.test_client()
    entrar(client)
    pagina = client.get("/ferias/nova").get_data(as_text=True)
    assert "Antecipar a 1ª parcela do 13º" in pagina
    assert "Vender 10 dias" in pagina
    assert "Somente para férias com início a partir de fevereiro" in pagina
    assert 'id="adianta_13_sim"' in pagina
    assert 'id="adianta_13_nao"' in pagina
    assert 'id="vende_10_sim"' in pagina

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
        data={"funcionario_id": carlos.id, "inicio": "2027-02-01", "dias": "10", "adianta_13": "1", "vende_10": "1", "confirmar": "1"},
        follow_redirects=True,
    )
    assert "Férias salvas" in resp.get_data(as_text=True)
    with app.app_context():
        ferias = db.session.scalar(db.select(Ferias))
        assert ferias.adianta_13 is True
        assert ferias.vende_10 is True
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

    client.post("/ferias/lote", data={"acao": "vende_sim", "ids": ids}, follow_redirects=True)
    with app.app_context():
        assert all(f.vende_10 for f in db.session.scalars(db.select(Ferias)))


def test_calendario_mostra_so_meses_com_ferias_e_o_cronograma(app):
    client = app.test_client()
    entrar(client)
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        edson = db.session.scalar(db.select(Funcionario).filter_by(nome="Edson"))
        ana = db.session.scalar(db.select(Funcionario).filter_by(nome="Ana"))
        bruno = db.session.scalar(db.select(Funcionario).filter_by(nome="Bruno"))
        cid, eid, aid, bid = carlos.id, edson.id, ana.id, bruno.id
    client.post("/ferias/nova", data={"funcionario_id": cid, "inicio": "2026-12-01", "dias": "20"})
    client.post("/ferias/nova", data={"funcionario_id": eid, "inicio": "2026-12-01", "dias": "20", "confirmar": "1"})
    client.post("/ferias/nova", data={"funcionario_id": aid, "inicio": "2026-12-21", "dias": "5"})
    client.post("/ferias/nova", data={"funcionario_id": bid, "inicio": "2026-12-28", "dias": "10"})
    pagina = client.get("/ferias/calendario?ciclo=2026").get_data(as_text=True)
    assert "Dezembro/2026" in pagina
    assert "Novembro/2026" in pagina
    assert "Outubro/2026" not in pagina
    assert "Cronograma" in pagina
    assert 'class="gantt"' in pagina
    gantt = pagina.split("Cronograma", 1)[1]
    assert "Dezembro 2026" in gantt
    assert "Novembro 2026" not in gantt
    assert "20 dias" in pagina
    assert "no setor" in pagina
    assert "2 de férias" in pagina
    assert "2 disponíveis" in pagina
    cards = pagina.split("Cronograma")[0]
    assert "Janeiro/2027" in cards
    dezembro = cards.split("Dezembro/2026")[1].split("Janeiro/2027")[0]
    janeiro = cards.split("Janeiro/2027")[1]
    assert "Bruno" in dezembro
    assert "Bruno" not in janeiro
    assert "disponíveis" in pagina
    assert "De férias" in pagina
    assert "Disponíveis no setor" in pagina
    assert "Pouco de férias" in pagina
    assert "qtd-disp muito" in pagina
    assert "Período crítico" in pagina
    assert "critico" in pagina


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
    assert resp.get_json()["fim"] == "20/12/2026"


def test_usuario_comum_nao_aprova_nem_acessa_admin(app):
    client = app.test_client()
    entrar(client, "joao", "senha123")
    assert client.get("/admin/usuarios").status_code == 403
    assert client.post("/ferias/1/aprovar").status_code == 403
    assert client.get("/ferias/").status_code == 403
    assert client.get("/ferias/nova").status_code == 403
    assert client.get("/ferias/calendario").status_code == 200
    assert client.get("/ferias/opcoes").status_code == 200
    assert client.get("/ferias/validacao/lider").status_code == 403
    assert client.get("/ferias/validacao/gerente").status_code == 403
    assert client.get("/").status_code == 302


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
    assert "smtp.gmail.com" in config
    assert "brevo.com" in config
    assert "conta gratuita do PythonAnywhere bloqueia SMTP" in config

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


def test_alerta_por_api_https_brevo(app, monkeypatch):
    import json

    chamadas = []

    class Resposta:
        status = 201

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(pedido, timeout=20):
        chamadas.append(json.loads(pedido.data.decode()))
        return Resposta()

    monkeypatch.setattr("app.alertas.urllib.request.urlopen", urlopen)
    with app.app_context():
        db.session.add(Funcionario(nome="Wagner", data_admissao=date(2025, 4, 29), cargo="Diagramador"))
        Configuracao.definir("email_api_provedor", "brevo")
        Configuracao.definir("email_api_chave", "xkeysib-teste")
        Configuracao.definir("smtp_remetente", "ferias@plural.com.br")
        db.session.commit()
        from app.alertas import enviar_alertas_pendentes

        resultado = enviar_alertas_pendentes(hoje=date(2027, 3, 28))
        assert resultado["erro"] is None and resultado["enviados"] == 1
    assert chamadas
    assert chamadas[0]["subject"].startswith("Alerta")
    assert chamadas[0]["sender"]["email"] == "ferias@plural.com.br"


def test_painel_mostra_quem_ja_gozou(app):
    client = app.test_client()
    entrar(client)
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        ferias = Ferias(funcionario=carlos, status=STATUS_APROVADA, criado_por="admin")
        ferias.definir_periodo(date(2026, 6, 1), 10)
        db.session.add(ferias)
        db.session.commit()
    html = client.get("/?ciclo=2026").get_data(as_text=True)
    assert "Já gozaram" in html
    assert "Carlos" in html


def test_importar_usuarios_xlsx_define_papeis(app, tmp_path):
    from openpyxl import Workbook

    from app.seed import importar_usuarios_xlsx

    with app.app_context():
        db.session.add_all(
            [
                Funcionario(nome="Gercilio Ferreira"),
                Funcionario(nome="Marcelo Marcolino"),
                Funcionario(nome="Ricardo Rosas"),
            ]
        )
        db.session.commit()
        wb = Workbook()
        ws = wb.active
        ws.append(["Nome", "E-mail"])
        ws.append(["Carlos Augusto", "carlos@ex.com"])
        ws.append(["Gercilio Ferreira", "gercilio@ex.com"])
        ws.append(["Marcelo Marcolino", "marcelo@ex.com"])
        ws.append(["Ricardo Rosas", "ricardo@ex.com"])
        caminho = tmp_path / "Usuarios.xlsx"
        wb.save(caminho)
        resumo = importar_usuarios_xlsx(caminho)
        assert resumo["usuarios"] >= 3
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        assert carlos.email == "carlos@ex.com"
        gercilio = db.session.scalar(db.select(Usuario).filter(db.func.lower(Usuario.login) == "gercilio"))
        marcelo = db.session.scalar(db.select(Usuario).filter(db.func.lower(Usuario.login) == "marcelo"))
        ricardo = db.session.scalar(db.select(Usuario).filter(db.func.lower(Usuario.login) == "ricardo"))
        comum = db.session.scalar(db.select(Usuario).filter(db.func.lower(Usuario.login) == "carlos"))
        assert gercilio.papel == PAPEL_SIMPLES and not gercilio.pode_gestao()
        assert marcelo.papel == PAPEL_GERENTE and marcelo.pode_gerente()
        assert ricardo.papel == PAPEL_GERENTE
        assert comum.papel == PAPEL_SIMPLES and not comum.pode_gestao()


def test_vincula_usuario_existente_ao_funcionario_pelo_nome(app):
    from app.seed import garantir_usuarios_dos_funcionarios

    with app.app_context():
        func = Funcionario(nome="Gercilio Ferreira")
        usuario = Usuario(login="Gercilio", admin=True, acessa_calendario=True, trocar_senha=False)
        usuario.definir_senha("123456")
        db.session.add_all([func, usuario])
        db.session.commit()
        assert usuario.funcionario_id is None
        resumo = garantir_usuarios_dos_funcionarios()
        assert resumo["ligados"] >= 1
        usuario = db.session.scalar(db.select(Usuario).filter_by(login="Gercilio"))
        assert usuario.funcionario_id == func.id
        assert usuario.papel == PAPEL_SIMPLES


def test_cria_usuario_para_cada_funcionario_ativo(app):
    from app.seed import garantir_usuarios_dos_funcionarios

    with app.app_context():
        resumo = garantir_usuarios_dos_funcionarios()
        assert resumo["criados"] >= 4
        for nome in ("Carlos", "Edson", "Ana", "Bruno"):
            usuario = db.session.scalar(db.select(Usuario).filter(db.func.lower(Usuario.login) == nome.lower()))
            func = db.session.scalar(db.select(Funcionario).filter_by(nome=nome))
            assert usuario is not None
            assert usuario.funcionario_id == func.id
            assert usuario.papel == PAPEL_SIMPLES
            assert usuario.conferir_senha("123")


def test_fluxo_opcoes_montar_lider_gerente(app, monkeypatch):
    enviados = []

    def fake_email(assunto, corpo, destinatarios):
        enviados.append((assunto, corpo, list(destinatarios)))
        return True, ""

    monkeypatch.setattr("app.workflow.enviar_email", fake_email)
    client = app.test_client()
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        carlos.email = "carlos@ex.com"
        joao = db.session.scalar(db.select(Usuario).filter_by(login="joao"))
        joao.funcionario_id = carlos.id
        db.session.commit()
        cid = carlos.id

    entrar(client, "joao", "senha123")
    nav = client.get("/ferias/calendario").get_data(as_text=True)
    assert "Minhas datas" in nav
    assert "Painel</a>" not in nav
    assert "Programação</a>" not in nav
    envio = client.post(
        "/ferias/opcoes",
        data={"inicio_1": "2026-12-01", "dias_1": "10", "vende_10_1": "1", "inicio_2": "2027-01-12", "dias_2": "10"},
        follow_redirects=True,
    )
    assert "opções de datas foram enviadas" in envio.get_data(as_text=True)
    with app.app_context():
        opcoes = db.session.scalars(db.select(SugestaoFerias).filter_by(funcionario_id=cid)).all()
        assert len(opcoes) == 2
        assert opcoes[0].vende_10 is True

    client.get("/logout")
    entrar(client)
    fila = client.get("/ferias/validacao/lider?ciclo=2026").get_data(as_text=True)
    assert "Validação do líder" in fila
    assert "Carlos" in fila
    assert "01/12/2026" in fila
    montado = client.post("/ferias/montar", data={"ciclo": "2026"}, follow_redirects=True)
    html_montado = montado.get_data(as_text=True)
    assert "Calendário montado" in html_montado
    assert "Fila para validar" in html_montado
    with app.app_context():
        ferias = db.session.scalar(db.select(Ferias).filter_by(funcionario_id=cid))
        assert ferias.status == STATUS_AGUARDANDO_LIDER
        assert ferias.inicio == date(2026, 12, 1)
        assert ferias.vende_10 is True
        fid = ferias.id

    client.post(f"/ferias/{fid}/lider", data={"inicio": "2026-12-01"}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Ferias, fid).status == STATUS_AGUARDANDO_GERENTE
    gerente = client.get("/ferias/validacao/gerente").get_data(as_text=True)
    assert "Aceite do gerente" in gerente
    assert "Carlos" in gerente
    assert "01/12/2026" in gerente

    client.post(f"/ferias/{fid}/gerente", data={"acao": "aprovar"}, follow_redirects=True)
    with app.app_context():
        ferias = db.session.get(Ferias, fid)
        assert ferias.status == STATUS_APROVADA
    assert any("Férias aprovadas" in a[0] and "carlos@ex.com" in a[2] for a in enviados)


def test_lider_nao_e_gerente_e_a_fila_do_gerente_recebe_a_validacao(app):
    client = app.test_client()
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        lider = Usuario(login="Lider", papel=PAPEL_LIDER, acessa_calendario=True, acessa_cronograma=True, trocar_senha=False)
        lider.definir_senha("senha123")
        gerente = Usuario(login="Gerente", papel=PAPEL_GERENTE, acessa_calendario=True, acessa_cronograma=True, trocar_senha=False)
        gerente.definir_senha("senha123")
        ferias = Ferias(funcionario=carlos, status=STATUS_AGUARDANDO_LIDER, criado_por="montagem")
        ferias.definir_periodo(date(2026, 12, 1), 10)
        db.session.add_all([lider, gerente, ferias])
        db.session.commit()
        fid = ferias.id

    entrar(client, "Lider", "senha123")
    assert client.get("/ferias/validacao/gerente").status_code == 403
    resp = client.post(f"/ferias/{fid}/lider", data={"inicio": "2026-12-01"}, follow_redirects=True)
    assert "encaminhado ao gerente" in resp.get_data(as_text=True).casefold()
    with app.app_context():
        assert db.session.get(Ferias, fid).status == STATUS_AGUARDANDO_GERENTE
    client.get("/logout")
    entrar(client, "Gerente", "senha123")
    html = client.get("/ferias/validacao/gerente").get_data(as_text=True)
    assert "Carlos" in html
    assert "01/12/2026" in html


def test_montar_propoe_data_proxima_e_funcionario_responde(app, monkeypatch):
    enviados = []
    monkeypatch.setattr(
        "app.workflow.enviar_email",
        lambda assunto, corpo, dest: enviados.append((assunto, dest)) or (True, ""),
    )
    client = app.test_client()
    with app.app_context():
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        ana = db.session.scalar(db.select(Funcionario).filter_by(nome="Ana"))
        carlos.email = "carlos@ex.com"
        joao = db.session.scalar(db.select(Usuario).filter_by(login="joao"))
        joao.funcionario_id = carlos.id
        ocupada = Ferias(funcionario=ana, status=STATUS_APROVADA, criado_por="admin")
        ocupada.definir_periodo(date(2026, 12, 1), 10)
        db.session.add(ocupada)
        db.session.commit()
        cid = carlos.id

    entrar(client, "joao", "senha123")
    client.post("/ferias/opcoes", data={"inicio_1": "2026-12-01", "dias_1": "10"}, follow_redirects=True)
    client.get("/logout")
    entrar(client)
    client.post("/ferias/montar", data={"ciclo": "2026"}, follow_redirects=True)
    with app.app_context():
        ferias = db.session.scalar(db.select(Ferias).filter_by(funcionario_id=cid))
        assert ferias.status == STATUS_AGUARDANDO_FUNCIONARIO
        assert ferias.inicio != date(2026, 12, 1)
        fid = ferias.id
    assert any("Sugestão de férias" in a[0] for a in enviados)

    client.get("/logout")
    entrar(client, "joao", "senha123")
    cal = client.get("/ferias/calendario?ciclo=2026").get_data(as_text=True)
    assert "Sua data precisa de resposta" in cal
    client.post(f"/ferias/{fid}/responder", data={"acao": "recusar"}, follow_redirects=True)
    with app.app_context():
        recusada = db.session.get(Ferias, fid)
        assert recusada.status == STATUS_AGUARDANDO_LIDER
        assert "Recusou" in (recusada.observacao or "")
