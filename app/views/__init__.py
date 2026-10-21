from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from ..models import Historico, db


def registrar(acao: str, detalhe: str = "") -> None:
    login = current_user.login if current_user.is_authenticated else "anônimo"
    db.session.add(Historico(usuario=login, acao=acao, detalhe=detalhe[:500]))


def requer(permissao: str):
    """Exige login e uma permissão: 'admin', 'gestao', 'calendario' ou 'cronograma'."""

    def decorador(view):
        @wraps(view)
        @login_required
        def envolvida(*args, **kwargs):
            checagens = {
                "admin": lambda: current_user.admin and current_user.papel == "admin",
                "gestao": current_user.pode_gestao,
                "calendario": current_user.pode_calendario,
                "cronograma": current_user.pode_cronograma,
            }
            if not checagens[permissao]():
                abort(403)
            return view(*args, **kwargs)

        return envolvida

    return decorador
