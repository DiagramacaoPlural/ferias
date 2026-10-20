from datetime import date, timedelta

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..consultas import ciclo_atual, conflitos, ferias_no_intervalo, funcionarios_ativos, minimo_no_setor
from ..regras import DIAS_MAX_CICLO, limite_ausentes, rotulo_ciclo

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def painel():
    if not current_user.pode_calendario():
        if current_user.pode_cronograma():
            return redirect(url_for("cronograma.visualizar"))
        return render_template("sem_acesso.html")

    hoje = date.today()
    ciclo_ini, ciclo_fim = ciclo_atual()
    ferias_ciclo = ferias_no_intervalo(ciclo_ini, ciclo_fim, apenas_ativos=True)
    funcionarios = funcionarios_ativos()

    dias_por_funcionario = {f.id: {"aprovados": 0, "programados": 0} for f in funcionarios}
    for f in ferias_ciclo:
        if ciclo_ini <= f.inicio <= ciclo_fim and f.funcionario_id in dias_por_funcionario:
            chave = "aprovados" if f.aprovada else "programados"
            dias_por_funcionario[f.funcionario_id][chave] += f.dias

    saldos = []
    for func in funcionarios:
        d = dias_por_funcionario[func.id]
        usados = d["aprovados"] + d["programados"]
        saldos.append(
            {
                "funcionario": func,
                "aprovados": d["aprovados"],
                "programados": d["programados"],
                "restante": max(DIAS_MAX_CICLO - usados, 0),
                "pct_aprovado": min(d["aprovados"] / DIAS_MAX_CICLO * 100, 100),
                "pct_programado": min(d["programados"] / DIAS_MAX_CICLO * 100, 100),
            }
        )

    min_disp = minimo_no_setor()
    limite = limite_ausentes(len(funcionarios), min_disp)
    contexto = {
        "rotulo_ciclo": rotulo_ciclo(ciclo_ini),
        "total_funcionarios": len(funcionarios),
        "total_aprovadas": sum(1 for f in ferias_ciclo if f.aprovada),
        "total_pendentes": sum(1 for f in ferias_ciclo if not f.aprovada),
        "sem_ferias": [s["funcionario"] for s in saldos if s["aprovados"] + s["programados"] == 0],
        "em_ferias_hoje": [f for f in ferias_ciclo if f.inicio <= hoje <= f.fim],
        "proximas": [f for f in ferias_ciclo if hoje < f.inicio <= hoje + timedelta(days=60)],
        "pendentes": [f for f in ferias_ciclo if not f.aprovada and f.fim >= hoje],
        "conflitos": conflitos(ferias_ciclo, ciclo_ini, ciclo_fim, limite),
        "min_disponiveis": min_disp,
        "saldos": saldos,
    }
    return render_template("painel.html", **contexto)
