from datetime import date
from pathlib import Path

import pytest

from decimal import Decimal

from app.models import EtapaProva, Funcionario, LancamentoDiario, ProjetoProva, db
from app.seed import classificar_etapa, importar_provas
from app.verbas import calcular_verba
from app.views.provas import conflitos_mesmo_local


def test_verba_segue_regra_da_planilha():
    padrao = {}
    assert calcular_verba("SP", "TRABALHO", "Carlos", padrao) == 0
    assert calcular_verba("DF", "FOLGA", "Carlos", padrao) == 0
    assert calcular_verba("DF", "TRABALHO", "Carlos", padrao) == Decimal("170")
    assert calcular_verba("DF", "FOLGA_DF", "Carlos", padrao) == Decimal("200")
    assert calcular_verba("DF", "FERIADO_TRABALHADO", "Carlos", padrao) == Decimal("200")
    assert calcular_verba("DF", "TRABALHO", "Thiago/PJ", padrao) == Decimal("70")
    assert calcular_verba("DF", "FERIADO_TRABALHADO", "Thiago", padrao) == Decimal("85")
    assert calcular_verba("DF", "FOLGA_DF", "Thiago", padrao) == 0
    assert calcular_verba(None, "TRABALHO", "Carlos", padrao) == 0


def test_classificar_etapa():
    assert classificar_etapa("Diagramação DF") == ("Diagramação", "DF")
    assert classificar_etapa("diagramacao sp") == ("Diagramação", "SP")
    assert classificar_etapa("Produção") == ("Produção", None)
    assert classificar_etapa("Manuseio FGV") == ("Manuseio FGV", None)
    assert classificar_etapa("Aplicação") == ("Aplicação", None)
    assert classificar_etapa("ENEM P1") is None


def test_paginas_provas_carregam(app):
    client = app.test_client()
    client.post("/login", data={"login": "admin", "senha": "admin"})
    for url in ["/provas/", "/provas/projetos", "/provas/projetos/novo", "/provas/etapas/nova",
                "/provas/cronograma", "/provas/diario", "/provas/diario/novo", "/provas/relatorios"]:
        assert client.get(url).status_code == 200, url


def test_criar_projeto_etapa_e_lancamento(app):
    client = app.test_client()
    client.post("/login", data={"login": "admin", "senha": "admin"})

    resp = client.post("/provas/projetos/novo", data={"nome": "ENEM P1", "ativo": "1"}, follow_redirects=True)
    assert "Projeto salvo" in resp.get_data(as_text=True)
    lista = client.get("/provas/projetos").get_data(as_text=True)
    assert "Editar" in lista
    assert "Etapa" in lista

    with app.app_context():
        projeto = db.session.scalar(db.select(ProjetoProva).filter_by(nome="ENEM P1"))
        carlos = db.session.scalar(db.select(Funcionario).filter_by(nome="Carlos"))
        projeto_id, carlos_id = projeto.id, carlos.id

    resp = client.post(
        "/provas/etapas/nova",
        data={
            "projeto_id": projeto_id,
            "tipo": "Diagramação",
            "local": "DF",
            "inicio": "2026-06-15",
            "fim": "2026-07-10",
            "pcs_adicionais": "2",
            "equipe": [carlos_id],
        },
        follow_redirects=True,
    )
    assert "Etapa salva" in resp.get_data(as_text=True)

    with app.app_context():
        etapa = db.session.scalar(db.select(EtapaProva))
        assert etapa.dias == 26
        assert etapa.total_pcs == 3
        etapa_id = etapa.id

    resp = client.post(
        "/provas/diario/novo",
        data={
            "etapa_id": etapa_id,
            "funcionario_id": carlos_id,
            "data": "2026-06-16",
            "tipo": "TRABALHO",
            "tarefa": "Miolo caderno 1",
            "realizada": "1",
            "verba": "45,50",
        },
        follow_redirects=True,
    )
    assert "Lançamento salvo" in resp.get_data(as_text=True)

    export = client.get("/provas/exportar")
    assert export.status_code == 200
    assert export.data[:2] == b"PK"

    with app.app_context():
        lanc = db.session.scalar(db.select(LancamentoDiario))
        assert lanc.tarefa == "Miolo caderno 1"
        assert float(lanc.verba) == 45.5

    resp = client.post(
        "/provas/diario/novo",
        data={
            "etapa_id": etapa_id,
            "funcionario_id": carlos_id,
            "data": "2026-06-17",
            "tipo": "TRABALHO",
            "realizada": "1",
        },
        follow_redirects=True,
    )
    assert "Lançamento salvo" in resp.get_data(as_text=True)
    with app.app_context():
        auto = db.session.scalar(db.select(LancamentoDiario).filter_by(data=date(2026, 6, 17)))
        assert float(auto.verba) == 170


def test_sobreposicao_mesmo_local(app):
    with app.app_context():
        p = ProjetoProva(nome="A")
        q = ProjetoProva(nome="B")
        db.session.add_all([p, q])
        db.session.flush()
        a = EtapaProva(projeto=p, tipo="Diagramação", local="DF", inicio=date(2026, 6, 1), fim=date(2026, 6, 20))
        b = EtapaProva(projeto=q, tipo="Diagramação", local="DF", inicio=date(2026, 6, 10), fim=date(2026, 6, 30))
        c = EtapaProva(projeto=q, tipo="Diagramação", local="SP", inicio=date(2026, 6, 10), fim=date(2026, 6, 30))
        db.session.add_all([a, b, c])
        db.session.commit()
        pares = conflitos_mesmo_local([a, b, c])
        assert len(pares) == 1
        assert {pares[0][0].local, pares[0][1].local} == {"DF"}


def test_importacao_provas_xlsm(app):
    planilha = Path(__file__).resolve().parent.parent / "Provas.xlsm"
    if not planilha.exists():
        pytest.skip("Provas.xlsm não encontrada")
    with app.app_context():
        resumo = importar_provas(planilha)
        assert resumo["projetos"] >= 8
        enceja = db.session.scalar(db.select(ProjetoProva).filter(db.func.lower(ProjetoProva.nome) == "enceja"))
        assert enceja
        df = next(e for e in enceja.etapas if e.tipo == "Diagramação" and e.local == "DF")
        assert df.inicio == date(2026, 4, 6)
        assert df.fim == date(2026, 4, 30)
        assert importar_provas(planilha)["projetos"] == 0
        assert importar_provas(planilha)["etapas"] == 0
