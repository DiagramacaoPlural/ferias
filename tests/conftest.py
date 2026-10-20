import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.models import Funcionario, Usuario, db


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'teste.db'}",
            "IMPORTAR_PLANILHA_INICIAL": False,
        }
    )
    with app.app_context():
        admin = db.session.scalar(db.select(Usuario).filter_by(login="admin"))
        admin.trocar_senha = False
        comum = Usuario(login="joao", acessa_calendario=True, acessa_cronograma=False, trocar_senha=False)
        comum.definir_senha("senha123")
        db.session.add_all([
            comum,
            Funcionario(nome="Carlos"),
            Funcionario(nome="Edson"),
            Funcionario(nome="Ana"),
            Funcionario(nome="Bruno"),
        ])
        db.session.commit()
    yield app
