from datetime import date, datetime
from io import BytesIO

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from ..consultas import ciclo_atual, ferias_no_intervalo, funcionarios_ativos, minimo_no_setor, serie_cobertura
from ..models import STATUS_APROVADA, STATUS_PROGRAMADA, Configuracao, Ferias, Funcionario, db
from ..regras import (
    DIAS_MAX_CICLO,
    analisar_periodo_db,
    calcular_fim,
    meses_do_ciclo,
    rotulo_ciclo,
    sobrepoe,
    ultimo_dia_mes,
)
from . import registrar, requer

bp = Blueprint("ferias", __name__, url_prefix="/ferias")


def _ciclo_da_requisicao() -> tuple[date, date]:
    mes_inicio = Configuracao.obter_int("mes_inicio_ciclo")
    ano = request.args.get("ciclo", type=int)
    if ano:
        return ciclo_atual(date(ano, mes_inicio, 1))
    return ciclo_atual()


def _ciclos_disponiveis(ciclo_ini: date) -> list[dict]:
    anos = {ciclo_ini.year, ciclo_atual()[0].year}
    limites = db.session.execute(db.select(db.func.min(Ferias.inicio), db.func.max(Ferias.inicio))).one()
    for valor in limites:
        if valor:
            anos.add(ciclo_atual(valor)[0].year)
    anos.add(max(anos) + 1)
    mes_inicio = Configuracao.obter_int("mes_inicio_ciclo")
    return [
        {"ano": ano, "rotulo": rotulo_ciclo(date(ano, mes_inicio, 1))}
        for ano in range(min(anos), max(anos) + 1)
    ]


def _pode_editar(ferias: Ferias) -> bool:
    return current_user.admin or not ferias.aprovada


def _ler_formulario():
    erros = []
    funcionario = db.session.get(Funcionario, request.form.get("funcionario_id", type=int) or 0)
    if not funcionario:
        erros.append("Selecione o funcionário.")
    try:
        inicio = datetime.strptime(request.form.get("inicio", ""), "%Y-%m-%d").date()
    except ValueError:
        inicio = None
        erros.append("Informe uma data de início válida.")
    dias = request.form.get("dias", type=int)
    if not dias:
        erros.append("Informe a quantidade de dias.")
    return funcionario, inicio, dias, erros


@bp.route("/")
@requer("calendario")
def lista():
    ciclo_ini, ciclo_fim = _ciclo_da_requisicao()
    funcionario_id = request.args.get("funcionario", type=int)
    status = request.args.get("status", "")

    todas = [f for f in ferias_no_intervalo(ciclo_ini, ciclo_fim) if ciclo_ini <= f.inicio <= ciclo_fim]
    ferias = todas
    if funcionario_id:
        ferias = [f for f in ferias if f.funcionario_id == funcionario_id]
    if status in (STATUS_APROVADA, STATUS_PROGRAMADA):
        ferias = [f for f in ferias if f.status == status]

    avisos, alternativas = {}, {}
    for f in ferias:
        analise = analisar_periodo_db(f.funcionario, f.inicio, f.dias, f.id)
        avisos[f.id] = analise.avisos
        if analise.alternativas:
            alternativas[f.id] = analise.alternativas

    ativos = funcionarios_ativos()
    min_disp = minimo_no_setor()
    cobertura = serie_cobertura(todas, ciclo_ini, ciclo_fim, len(ativos), min_disp)

    return render_template(
        "ferias/lista.html",
        ferias=ferias,
        avisos=avisos,
        alternativas=alternativas,
        cobertura=cobertura,
        total_ativos=len(ativos),
        min_disponiveis=min_disp,
        ciclo_ini=ciclo_ini,
        ciclos=_ciclos_disponiveis(ciclo_ini),
        rotulo_ciclo=rotulo_ciclo(ciclo_ini),
        funcionarios=db.session.scalars(db.select(Funcionario).order_by(Funcionario.nome)).all(),
        filtro_funcionario=funcionario_id,
        filtro_status=status,
    )


@bp.route("/nova", methods=["GET", "POST"])
@bp.route("/<int:ferias_id>/editar", methods=["GET", "POST"])
@requer("calendario")
def formulario(ferias_id: int | None = None):
    ferias = db.session.get(Ferias, ferias_id) if ferias_id else None
    if ferias_id and not ferias:
        abort(404)
    if ferias and not _pode_editar(ferias):
        flash("Férias aprovadas só podem ser alteradas por um administrador.", "aviso")
        return redirect(url_for("ferias.lista"))

    avisos, erros, alternativas = [], [], []
    if request.method == "POST":
        funcionario, inicio, dias, erros = _ler_formulario()
        if not erros:
            analise = analisar_periodo_db(funcionario, inicio, dias, ferias_id)
            erros, avisos, alternativas = analise.erros, analise.avisos, analise.alternativas

        confirmado = request.form.get("confirmar") == "1"
        if not erros and (not avisos or confirmado):
            novo = ferias is None
            if novo:
                ferias = Ferias(criado_por=current_user.login)
                db.session.add(ferias)
            ferias.funcionario = funcionario
            ferias.definir_periodo(inicio, dias)
            ferias.observacao = request.form.get("observacao", "").strip() or None

            if current_user.admin:
                aprovar = request.form.get("aprovada") == "1"
                if aprovar and not ferias.aprovada:
                    ferias.aprovado_por, ferias.aprovado_em = current_user.login, datetime.now()
                ferias.status = STATUS_APROVADA if aprovar else STATUS_PROGRAMADA
                if not aprovar:
                    ferias.aprovado_por = ferias.aprovado_em = None

            descricao = f"{funcionario.nome}: {inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y} ({dias} dias)"
            if avisos:
                descricao += f" | salvo com {len(avisos)} aviso(s)"
            registrar("Férias criadas" if novo else "Férias alteradas", descricao)
            db.session.commit()
            flash("Férias salvas com sucesso.", "sucesso")
            return redirect(url_for("ferias.lista", ciclo=ciclo_atual(inicio)[0].year))

    funcionarios = funcionarios_ativos()
    if ferias and ferias.funcionario not in funcionarios:
        funcionarios.append(ferias.funcionario)

    return render_template(
        "ferias/formulario.html",
        ferias=ferias,
        funcionarios=funcionarios,
        erros=erros,
        avisos=avisos,
        alternativas=alternativas,
        dados=request.form if request.method == "POST" else None,
        dias_max=DIAS_MAX_CICLO,
    )


@bp.route("/analisar", methods=["POST"])
@requer("calendario")
def analisar():
    """Validação ao vivo usada pelo formulário enquanto o usuário digita."""
    dados = request.get_json(silent=True) or {}
    funcionario = db.session.get(Funcionario, int(dados.get("funcionario_id") or 0))
    try:
        inicio = datetime.strptime(dados.get("inicio", ""), "%Y-%m-%d").date()
        dias = int(dados.get("dias"))
    except (TypeError, ValueError):
        return jsonify(fim=None, erros=[], avisos=[])

    fim = calcular_fim(inicio, dias) if dias > 0 else None
    if not funcionario:
        return jsonify(fim=fim.strftime("%d/%m/%Y") if fim else None, erros=[], avisos=[])

    ferias_id = int(dados["ferias_id"]) if str(dados.get("ferias_id") or "").isdigit() else None
    analise = analisar_periodo_db(funcionario, inicio, dias, ferias_id)
    return jsonify(
        fim=fim.strftime("%d/%m/%Y") if fim else None,
        erros=analise.erros,
        avisos=analise.avisos,
        alternativas=[
            {"inicio": a.inicio.isoformat(), "fim": a.fim.strftime("%d/%m/%Y"), "rotulo": a.inicio.strftime("%d/%m/%Y")}
            for a in analise.alternativas
        ],
    )


@bp.route("/<int:ferias_id>/aprovar", methods=["POST"])
@requer("admin")
def alternar_aprovacao(ferias_id: int):
    ferias = db.get_or_404(Ferias, ferias_id)
    if ferias.aprovada:
        ferias.status, ferias.aprovado_por, ferias.aprovado_em = STATUS_PROGRAMADA, None, None
        acao = "Aprovação removida"
    else:
        ferias.status, ferias.aprovado_por, ferias.aprovado_em = STATUS_APROVADA, current_user.login, datetime.now()
        acao = "Férias aprovadas"
    registrar(acao, f"{ferias.funcionario.nome}: {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}")
    db.session.commit()
    flash(f"{acao}: {ferias.funcionario.nome}.", "sucesso")
    return redirect(request.referrer or url_for("ferias.lista"))


@bp.route("/<int:ferias_id>/mover", methods=["POST"])
@requer("calendario")
def mover(ferias_id: int):
    ferias = db.get_or_404(Ferias, ferias_id)
    if not _pode_editar(ferias):
        flash("Férias aprovadas só podem ser alteradas por um administrador.", "aviso")
        return redirect(url_for("ferias.lista"))
    try:
        inicio = datetime.strptime(request.form.get("inicio", ""), "%Y-%m-%d").date()
    except ValueError:
        flash("Data alternativa inválida.", "erro")
        return redirect(url_for("ferias.lista"))

    analise = analisar_periodo_db(ferias.funcionario, inicio, ferias.dias, ferias.id)
    if analise.erros:
        flash(analise.erros[0], "erro")
        return redirect(url_for("ferias.lista"))

    anterior = f"{ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}"
    ferias.definir_periodo(inicio, ferias.dias)
    registrar(
        "Férias reagendadas",
        f"{ferias.funcionario.nome}: {anterior} → {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}",
    )
    db.session.commit()
    flash(f"Férias de {ferias.funcionario.nome} movidas para {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}.", "sucesso")
    return redirect(request.referrer or url_for("ferias.lista"))


@bp.route("/<int:ferias_id>/excluir", methods=["POST"])
@requer("calendario")
def excluir(ferias_id: int):
    ferias = db.get_or_404(Ferias, ferias_id)
    if not _pode_editar(ferias):
        flash("Férias aprovadas só podem ser excluídas por um administrador.", "aviso")
        return redirect(url_for("ferias.lista"))
    registrar(
        "Férias excluídas",
        f"{ferias.funcionario.nome}: {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y} ({ferias.dias} dias)",
    )
    db.session.delete(ferias)
    db.session.commit()
    flash("Férias excluídas.", "sucesso")
    return redirect(request.referrer or url_for("ferias.lista"))


@bp.route("/calendario")
@requer("calendario")
def calendario():
    ciclo_ini, ciclo_fim = _ciclo_da_requisicao()
    ferias = ferias_no_intervalo(ciclo_ini, ciclo_fim, apenas_ativos=True)

    meses = []
    for primeiro in meses_do_ciclo(ciclo_ini):
        ultimo = ultimo_dia_mes(primeiro)
        do_mes = [f for f in ferias if sobrepoe(f.inicio, f.fim, primeiro, ultimo)]
        meses.append(
            {
                "data": primeiro,
                "aprovadas": [f for f in do_mes if f.aprovada],
                "programadas": [f for f in do_mes if not f.aprovada],
                "atual": primeiro.year == date.today().year and primeiro.month == date.today().month,
            }
        )

    return render_template(
        "ferias/calendario.html",
        meses=meses,
        ciclo_ini=ciclo_ini,
        ciclos=_ciclos_disponiveis(ciclo_ini),
        rotulo_ciclo=rotulo_ciclo(ciclo_ini),
    )


@bp.route("/exportar")
@requer("calendario")
def exportar():
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    ciclo_ini, ciclo_fim = _ciclo_da_requisicao()
    ferias = [f for f in ferias_no_intervalo(ciclo_ini, ciclo_fim) if ciclo_ini <= f.inicio <= ciclo_fim]

    wb = Workbook()
    cabecalho_fonte = Font(bold=True, color="FFFFFF")
    cabecalho_fundo = PatternFill("solid", fgColor="1F4E79")
    verde = PatternFill("solid", fgColor="E2F0D9")

    def formatar_cabecalho(ws, larguras):
        for col, largura in enumerate(larguras, start=1):
            celula = ws.cell(row=1, column=col)
            celula.font, celula.fill = cabecalho_fonte, cabecalho_fundo
            celula.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[get_column_letter(col)].width = largura
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = "Férias"
    ws.append(["Funcionário", "Início", "Fim", "Dias", "Status", "Observação", "Aprovado por", "Aprovado em"])
    for f in ferias:
        ws.append([
            f.funcionario.nome, f.inicio, f.fim, f.dias, f.status_rotulo,
            f.observacao or "", f.aprovado_por or "", f.aprovado_em,
        ])
        linha = ws.max_row
        ws.cell(row=linha, column=2).number_format = "DD/MM/YYYY"
        ws.cell(row=linha, column=3).number_format = "DD/MM/YYYY"
        ws.cell(row=linha, column=8).number_format = "DD/MM/YYYY HH:MM"
        if f.aprovada:
            for col in range(1, 9):
                ws.cell(row=linha, column=col).fill = verde
    formatar_cabecalho(ws, [24, 13, 13, 8, 13, 40, 16, 18])

    resumo = wb.create_sheet("Resumo por funcionário")
    resumo.append(["Funcionário", "Períodos", "Dias aprovados", "Dias programados", "Saldo (de 30)"])
    for func in db.session.scalars(db.select(Funcionario).order_by(Funcionario.nome)):
        do_func = [f for f in ferias if f.funcionario_id == func.id]
        if not func.ativo and not do_func:
            continue
        aprovados = sum(f.dias for f in do_func if f.aprovada)
        programados = sum(f.dias for f in do_func if not f.aprovada)
        resumo.append([func.nome, len(do_func), aprovados, programados, DIAS_MAX_CICLO - aprovados - programados])
    formatar_cabecalho(resumo, [24, 10, 16, 18, 14])

    arquivo = BytesIO()
    wb.save(arquivo)
    arquivo.seek(0)
    nome = f"ferias_{ciclo_ini:%Y-%m}_a_{ciclo_fim:%Y-%m}.xlsx"
    return send_file(
        arquivo,
        as_attachment=True,
        download_name=nome,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
