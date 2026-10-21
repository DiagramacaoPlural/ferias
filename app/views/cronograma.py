from flask import Blueprint, redirect, url_for

from . import requer

bp = Blueprint("cronograma", __name__, url_prefix="/cronograma")


@bp.route("/")
@requer("calendario")
def visualizar():
    return redirect(url_for("ferias.calendario"))
