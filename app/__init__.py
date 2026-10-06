import os
import secrets
from datetime import date
from pathlib import Path

from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from .models import Usuario, db
from .regras import MESES, MESES_ABREV

BASE_DIR = Path(__file__).resolve().parent.parent

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Faça login para continuar."
login_manager.login_message_category = "aviso"

csrf = CSRFProtect()


def _carregar_secret_key(instance_path: Path) -> str:
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    arquivo = instance_path / "secret_key"
    if not arquivo.exists():
        arquivo.write_text(secrets.token_hex(32), encoding="utf-8")
    return arquivo.read_text(encoding="utf-8").strip()


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_path=str(BASE_DIR / "instance"))
    instance_path = Path(app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)

    app.config.update(
        SECRET_KEY=_carregar_secret_key(instance_path),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", f"sqlite:///{instance_path / 'ferias.db'}"),
        IMPORTAR_PLANILHA_INICIAL=True,
    )
    if config:
        app.config.update(config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .views.admin import bp as admin_bp
    from .views.auth import bp as auth_bp
    from .views.cronograma import bp as cronograma_bp
    from .views.ferias import bp as ferias_bp
    from .views.main import bp as main_bp

    for blueprint in (auth_bp, main_bp, ferias_bp, cronograma_bp, admin_bp):
        app.register_blueprint(blueprint)

    _registrar_filtros(app)
    _registrar_erros(app)

    with app.app_context():
        db.create_all()
        from .seed import inicializar_banco

        inicializar_banco(BASE_DIR if app.config["IMPORTAR_PLANILHA_INICIAL"] else None)

    return app


@login_manager.user_loader
def carregar_usuario(usuario_id: str):
    return db.session.get(Usuario, int(usuario_id))


def _registrar_erros(app: Flask) -> None:
    from flask import flash, redirect, render_template, request, url_for
    from flask_login import current_user
    from flask_wtf.csrf import CSRFError

    mensagens = {
        403: ("Sem permissão", "Seu usuário não tem acesso a esta área. Fale com um administrador do sistema."),
        404: ("Página não encontrada", "O endereço acessado não existe ou o registro foi removido."),
    }

    def tratar(codigo):
        def handler(_erro):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            titulo, mensagem = mensagens[codigo]
            return render_template("sem_acesso.html", titulo=titulo, mensagem=mensagem), codigo

        return handler

    for codigo in mensagens:
        app.register_error_handler(codigo, tratar(codigo))

    @app.errorhandler(CSRFError)
    def csrf_expirado(_erro):
        flash("Sua sessão expirou. Tente novamente.", "aviso")
        return redirect(request.referrer or url_for("main.painel"))


def _registrar_filtros(app: Flask) -> None:
    @app.template_filter("data")
    def formatar_data(valor, formato="%d/%m/%Y"):
        return valor.strftime(formato) if valor else ""

    @app.template_filter("mes_ano")
    def formatar_mes_ano(valor: date):
        return f"{MESES[valor.month - 1]}/{valor.year}"

    @app.template_filter("mes_abrev")
    def formatar_mes_abrev(valor: date):
        return f"{MESES_ABREV[valor.month - 1]}/{str(valor.year)[2:]}"

    @app.context_processor
    def injetar_globais():
        return {"hoje": date.today()}
