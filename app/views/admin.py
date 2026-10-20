import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..models import Configuracao, Funcionario, Historico, Usuario, db
from ..regras import MESES
from ..seed import importar_planilha, importar_provas, nomes_abas
from . import registrar, requer
from .auth import SENHA_MIN

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------- funcionários

@bp.route("/funcionarios")
@requer("admin")
def funcionarios():
    lista = db.session.scalars(db.select(Funcionario).order_by(Funcionario.ativo.desc(), Funcionario.nome)).all()
    return render_template("admin/funcionarios.html", funcionarios=lista)


@bp.route("/funcionarios/novo", methods=["GET", "POST"])
@bp.route("/funcionarios/<int:funcionario_id>", methods=["GET", "POST"])
@requer("admin")
def funcionario_form(funcionario_id: int | None = None):
    funcionario = db.get_or_404(Funcionario, funcionario_id) if funcionario_id else None

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        admissao_txt = request.form.get("data_admissao", "")
        duplicado = db.session.scalar(
            db.select(Funcionario).filter(
                db.func.lower(Funcionario.nome) == nome.lower(), Funcionario.id != (funcionario_id or 0)
            )
        )
        if not nome:
            flash("Informe o nome.", "erro")
        elif duplicado:
            flash("Já existe um funcionário com esse nome.", "erro")
        else:
            novo = funcionario is None
            if novo:
                funcionario = Funcionario()
                db.session.add(funcionario)
            funcionario.nome = nome
            funcionario.cargo = request.form.get("cargo", "").strip() or None
            funcionario.data_admissao = (
                datetime.strptime(admissao_txt, "%Y-%m-%d").date() if admissao_txt else None
            )
            funcionario.ativo = request.form.get("ativo") == "1"
            registrar("Funcionário criado" if novo else "Funcionário alterado", nome)
            db.session.commit()
            flash("Funcionário salvo.", "sucesso")
            return redirect(url_for("admin.funcionarios"))

    return render_template("admin/funcionario_form.html", funcionario=funcionario)


@bp.route("/funcionarios/<int:funcionario_id>/excluir", methods=["POST"])
@requer("admin")
def funcionario_excluir(funcionario_id: int):
    funcionario = db.get_or_404(Funcionario, funcionario_id)
    if funcionario.ferias:
        funcionario.ativo = False
        flash(
            f"{funcionario.nome} tem férias registradas e foi apenas inativado (o histórico foi mantido).",
            "aviso",
        )
        registrar("Funcionário inativado", funcionario.nome)
    else:
        db.session.delete(funcionario)
        registrar("Funcionário excluído", funcionario.nome)
        flash("Funcionário excluído.", "sucesso")
    db.session.commit()
    return redirect(url_for("admin.funcionarios"))


# -------------------------------------------------------------------- usuários

def _admins_ativos(excluir_id: int | None = None) -> int:
    consulta = db.select(db.func.count(Usuario.id)).filter_by(admin=True, ativo=True)
    if excluir_id:
        consulta = consulta.where(Usuario.id != excluir_id)
    return db.session.scalar(consulta)


@bp.route("/usuarios")
@requer("admin")
def usuarios():
    lista = db.session.scalars(db.select(Usuario).order_by(Usuario.ativo.desc(), Usuario.login)).all()
    return render_template("admin/usuarios.html", usuarios=lista)


@bp.route("/usuarios/novo", methods=["GET", "POST"])
@bp.route("/usuarios/<int:usuario_id>", methods=["GET", "POST"])
@requer("admin")
def usuario_form(usuario_id: int | None = None):
    usuario = db.get_or_404(Usuario, usuario_id) if usuario_id else None

    if request.method == "POST":
        login = request.form.get("login", "").strip()
        senha = request.form.get("senha", "")
        admin = request.form.get("admin") == "1"
        ativo = request.form.get("ativo") == "1"
        duplicado = db.session.scalar(
            db.select(Usuario).filter(db.func.lower(Usuario.login) == login.lower(), Usuario.id != (usuario_id or 0))
        )
        perde_admin = usuario and usuario.admin and usuario.ativo and (not admin or not ativo)

        if not login:
            flash("Informe o login.", "erro")
        elif duplicado:
            flash("Já existe um usuário com esse login.", "erro")
        elif usuario is None and len(senha) < SENHA_MIN:
            flash(f"A senha inicial deve ter pelo menos {SENHA_MIN} caracteres.", "erro")
        elif senha and len(senha) < SENHA_MIN:
            flash(f"A nova senha deve ter pelo menos {SENHA_MIN} caracteres.", "erro")
        elif perde_admin and _admins_ativos(excluir_id=usuario.id) == 0:
            flash("É preciso manter pelo menos um administrador ativo.", "erro")
        else:
            novo = usuario is None
            if novo:
                usuario = Usuario()
                db.session.add(usuario)
            usuario.login = login
            usuario.admin = admin
            usuario.ativo = ativo
            usuario.acessa_calendario = request.form.get("acessa_calendario") == "1"
            usuario.acessa_cronograma = request.form.get("acessa_cronograma") == "1"
            if senha:
                usuario.definir_senha(senha)
                usuario.trocar_senha = usuario.id != current_user.id
            registrar("Usuário criado" if novo else "Usuário alterado", login)
            db.session.commit()
            flash("Usuário salvo." + (" Ele deverá trocar a senha no próximo acesso." if senha and usuario.trocar_senha else ""), "sucesso")
            return redirect(url_for("admin.usuarios"))

    return render_template("admin/usuario_form.html", usuario=usuario, senha_min=SENHA_MIN)


# --------------------------------------------------------------- configurações

@bp.route("/configuracoes", methods=["GET", "POST"])
@requer("admin")
def configuracoes():
    if request.method == "POST":
        mes = request.form.get("mes_inicio_ciclo", type=int)
        minimo = request.form.get("min_disponiveis", type=int)
        dias_crono = request.form.get("dias_cronograma", type=int)
        verbas = {
            "verba_df_trabalho": request.form.get("verba_df_trabalho", type=int),
            "verba_df_especial": request.form.get("verba_df_especial", type=int),
            "verba_thiago_trabalho": request.form.get("verba_thiago_trabalho", type=int),
            "verba_thiago_feriado": request.form.get("verba_thiago_feriado", type=int),
        }
        if not mes or not 1 <= mes <= 12:
            flash("Mês de início do ciclo inválido.", "erro")
        elif minimo is None or minimo < 1:
            flash("O mínimo de pessoas disponíveis no setor deve ser pelo menos 1.", "erro")
        elif not dias_crono or not 7 <= dias_crono <= 364:
            flash("A janela do cronograma deve ter entre 7 e 364 dias.", "erro")
        elif any(v is None or v < 0 for v in verbas.values()):
            flash("Os valores de verba devem ser números inteiros a partir de 0.", "erro")
        else:
            Configuracao.definir("mes_inicio_ciclo", mes)
            Configuracao.definir("min_disponiveis", minimo)
            Configuracao.definir("dias_cronograma", dias_crono)
            for chave, valor in verbas.items():
                Configuracao.definir(chave, valor)
            registrar(
                "Configurações alteradas",
                f"ciclo inicia em {MESES[mes - 1]}, mín. {minimo} disponíveis no setor, cronograma {dias_crono} dias",
            )
            db.session.commit()
            flash("Configurações salvas.", "sucesso")
            return redirect(url_for("admin.configuracoes"))

    return render_template(
        "admin/configuracoes.html",
        meses=MESES,
        mes_inicio_ciclo=Configuracao.obter_int("mes_inicio_ciclo"),
        min_disponiveis=Configuracao.obter_int("min_disponiveis"),
        dias_cronograma=Configuracao.obter_int("dias_cronograma"),
        verba_df_trabalho=Configuracao.obter_int("verba_df_trabalho"),
        verba_df_especial=Configuracao.obter_int("verba_df_especial"),
        verba_thiago_trabalho=Configuracao.obter_int("verba_thiago_trabalho"),
        verba_thiago_feriado=Configuracao.obter_int("verba_thiago_feriado"),
    )


@bp.route("/importar", methods=["POST"])
@requer("admin")
def importar():
    arquivo = request.files.get("planilha")
    if not arquivo or not arquivo.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("Selecione uma planilha .xlsx ou .xlsm.", "erro")
        return redirect(url_for("admin.configuracoes"))

    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / Path(arquivo.filename).name
        arquivo.save(caminho)
        try:
            abas = nomes_abas(caminho)
            partes = []
            if "CALENDARIO" in abas or "USUARIOS" in abas:
                resumo = importar_planilha(caminho)
                partes.append(
                    f"{resumo['funcionarios']} funcionário(s), {resumo['ferias']} férias "
                    f"e {resumo['usuarios']} usuário(s) novos"
                )
            if "Projetos" in abas or "Controle_Diario" in abas:
                resumo = importar_provas(caminho)
                partes.append(
                    f"{resumo['projetos']} projeto(s), {resumo['etapas']} etapa(s) "
                    f"e {resumo['lancamentos']} lançamento(s) de provas"
                )
        except Exception as erro:  # planilha corrompida ou fora do formato esperado
            db.session.rollback()
            flash(f"Não foi possível importar a planilha: {erro}", "erro")
            return redirect(url_for("admin.configuracoes"))

    if not partes:
        flash("A planilha não tem as abas esperadas (CALENDARIO/USUARIOS ou Projetos).", "aviso")
    else:
        flash("Importação concluída: " + "; ".join(partes) + ".", "sucesso")
    return redirect(url_for("admin.configuracoes"))


@bp.route("/historico")
@requer("admin")
def historico():
    registros = db.session.scalars(db.select(Historico).order_by(Historico.data.desc()).limit(500)).all()
    return render_template("admin/historico.html", registros=registros)
