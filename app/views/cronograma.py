from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request

from ..consultas import ferias_no_intervalo, funcionarios_ativos
from ..models import Configuracao
from ..regras import DIAS_SEMANA_ABREV, MESES
from . import requer

bp = Blueprint("cronograma", __name__, url_prefix="/cronograma")

OPCOES_SEMANAS = [4, 8, 13, 26]


def _segunda_feira(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


@bp.route("/")
@requer("cronograma")
def visualizar():
    try:
        inicio = datetime.strptime(request.args.get("inicio", ""), "%Y-%m-%d").date()
    except ValueError:
        inicio = date.today()
    inicio = _segunda_feira(inicio)

    semanas_padrao = max(Configuracao.obter_int("dias_cronograma") // 7, 1)
    semanas = request.args.get("semanas", type=int) or semanas_padrao
    semanas = min(max(semanas, 1), 52)
    fim = inicio + timedelta(days=semanas * 7 - 1)
    hoje = date.today()

    dias = [inicio + timedelta(days=i) for i in range((fim - inicio).days + 1)]
    ferias = ferias_no_intervalo(inicio, fim, apenas_ativos=True)
    limite = Configuracao.obter_int("max_simultaneos")

    meses_cabecalho = []
    for dia in dias:
        rotulo = f"{MESES[dia.month - 1]} {dia.year}"
        if meses_cabecalho and meses_cabecalho[-1]["rotulo"] == rotulo:
            meses_cabecalho[-1]["dias"] += 1
        else:
            meses_cabecalho.append({"rotulo": rotulo, "dias": 1})

    ausentes = {dia: [] for dia in dias}
    linhas = []
    for func in funcionarios_ativos():
        periodos = [f for f in ferias if f.funcionario_id == func.id]
        celulas = []
        for dia in dias:
            periodo = next((f for f in periodos if f.inicio <= dia <= f.fim), None)
            if periodo:
                ausentes[dia].append(func.nome)
            celulas.append({"dia": dia, "ferias": periodo})
        linhas.append({"funcionario": func, "celulas": celulas, "periodos": periodos})

    return render_template(
        "cronograma.html",
        inicio=inicio,
        fim=fim,
        dias=dias,
        semanas=semanas,
        opcoes_semanas=sorted(set(OPCOES_SEMANAS + [semanas])),
        meses_cabecalho=meses_cabecalho,
        linhas=linhas,
        ausentes=ausentes,
        limite=limite,
        dias_semana=DIAS_SEMANA_ABREV,
        anterior=inicio - timedelta(days=semanas * 7),
        proximo=inicio + timedelta(days=semanas * 7),
        semana_atual=_segunda_feira(hoje),
    )
