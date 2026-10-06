from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..models import TentativaLogin, Usuario, db
from . import registrar

bp = Blueprint("auth", __name__)

SENHA_MIN = 6
MAX_FALHAS = 5
JANELA_BLOQUEIO = timedelta(minutes=15)


def _chaves_tentativa(login: str) -> list[str]:
    return [f"login:{login.lower()}", f"ip:{request.remote_addr or '?'}"]


def _bloqueado(chaves: list[str]) -> bool:
    limite = datetime.now() - JANELA_BLOQUEIO
    db.session.execute(db.delete(TentativaLogin).where(TentativaLogin.data < limite))
    for chave in chaves:
        falhas = db.session.scalar(
            db.select(db.func.count(TentativaLogin.id)).where(
                TentativaLogin.chave == chave, TentativaLogin.data >= limite
            )
        )
        if falhas >= MAX_FALHAS:
            return True
    return False


@bp.before_app_request
def forcar_troca_de_senha():
    if (
        current_user.is_authenticated
        and current_user.trocar_senha
        and request.endpoint not in ("auth.trocar_senha", "auth.logout", "static")
    ):
        return redirect(url_for("auth.trocar_senha"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.painel"))

    if request.method == "POST":
        login_digitado = request.form.get("login", "").strip()
        senha = request.form.get("senha", "")
        chaves = _chaves_tentativa(login_digitado)

        if _bloqueado(chaves):
            db.session.commit()
            minutos = int(JANELA_BLOQUEIO.total_seconds() // 60)
            flash(f"Muitas tentativas sem sucesso. Aguarde {minutos} minutos e tente novamente.", "erro")
            return render_template("login.html"), 429

        usuario = db.session.scalar(
            db.select(Usuario).filter(db.func.lower(Usuario.login) == login_digitado.lower())
        )
        if usuario and usuario.ativo and usuario.conferir_senha(senha):
            db.session.execute(db.delete(TentativaLogin).where(TentativaLogin.chave == chaves[0]))
            login_user(usuario, remember=bool(request.form.get("lembrar")))
            registrar("Login")
            db.session.commit()
            destino = request.args.get("next", "")
            if not destino or urlparse(destino).netloc or not destino.startswith("/"):
                destino = url_for("main.painel")
            return redirect(destino)

        db.session.add_all([TentativaLogin(chave=c) for c in chaves])
        registrar("Falha de login", f"login informado: {login_digitado[:60]} | IP {request.remote_addr}")
        db.session.commit()
        flash("Usuário ou senha inválidos.", "erro")

    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Você saiu do sistema.", "sucesso")
    return redirect(url_for("auth.login"))


@bp.route("/trocar-senha", methods=["GET", "POST"])
@login_required
def trocar_senha():
    if request.method == "POST":
        atual = request.form.get("senha_atual", "")
        nova = request.form.get("senha_nova", "")
        confirmacao = request.form.get("senha_confirmacao", "")

        if not current_user.conferir_senha(atual):
            flash("A senha atual está incorreta.", "erro")
        elif len(nova) < SENHA_MIN:
            flash(f"A nova senha deve ter pelo menos {SENHA_MIN} caracteres.", "erro")
        elif nova != confirmacao:
            flash("A confirmação não confere com a nova senha.", "erro")
        elif nova == atual:
            flash("A nova senha deve ser diferente da atual.", "erro")
        else:
            current_user.definir_senha(nova)
            current_user.trocar_senha = False
            registrar("Troca de senha")
            db.session.commit()
            flash("Senha alterada com sucesso.", "sucesso")
            return redirect(url_for("main.painel"))

    return render_template("trocar_senha.html", senha_min=SENHA_MIN)
