from datetime import date, datetime, timedelta

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

STATUS_PROGRAMADA = "PROGRAMADA"
STATUS_APROVADA = "APROVADA"
STATUS_ROTULOS = {STATUS_PROGRAMADA: "Programada", STATUS_APROVADA: "Aprovada"}


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(60), unique=True, nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)
    admin = db.Column(db.Boolean, default=False, nullable=False)
    acessa_calendario = db.Column(db.Boolean, default=True, nullable=False)
    acessa_cronograma = db.Column(db.Boolean, default=True, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    trocar_senha = db.Column(db.Boolean, default=True, nullable=False)

    def definir_senha(self, senha: str) -> None:
        self.senha_hash = generate_password_hash(senha)

    def conferir_senha(self, senha: str) -> bool:
        return check_password_hash(self.senha_hash, senha)

    @property
    def is_active(self) -> bool:
        return self.ativo

    def pode_calendario(self) -> bool:
        return self.admin or self.acessa_calendario

    def pode_cronograma(self) -> bool:
        return self.admin or self.acessa_cronograma


class Funcionario(db.Model):
    __tablename__ = "funcionarios"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), unique=True, nullable=False)
    cargo = db.Column(db.String(120))
    data_admissao = db.Column(db.Date)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    ferias = db.relationship(
        "Ferias", back_populates="funcionario", cascade="all, delete-orphan", order_by="Ferias.inicio"
    )


class Ferias(db.Model):
    __tablename__ = "ferias"

    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.Integer, db.ForeignKey("funcionarios.id"), nullable=False, index=True)
    inicio = db.Column(db.Date, nullable=False, index=True)
    dias = db.Column(db.Integer, nullable=False)
    fim = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(20), default=STATUS_PROGRAMADA, nullable=False)
    observacao = db.Column(db.String(500))
    adianta_13 = db.Column(db.Boolean, default=False, nullable=False)
    criado_por = db.Column(db.String(60))
    criado_em = db.Column(db.DateTime, default=datetime.now)
    aprovado_por = db.Column(db.String(60))
    aprovado_em = db.Column(db.DateTime)

    funcionario = db.relationship("Funcionario", back_populates="ferias")

    def definir_periodo(self, inicio, dias: int) -> None:
        from .regras import calcular_fim

        self.inicio = inicio
        self.dias = dias
        self.fim = calcular_fim(inicio, dias)

    @property
    def aprovada(self) -> bool:
        return self.status == STATUS_APROVADA

    @property
    def status_rotulo(self) -> str:
        return STATUS_ROTULOS.get(self.status, self.status)


class Configuracao(db.Model):
    __tablename__ = "configuracoes"

    chave = db.Column(db.String(60), primary_key=True)
    valor = db.Column(db.String(255), nullable=False)

    PADROES = {
        "mes_inicio_ciclo": "5",
        "min_disponiveis": "3",
        "dias_cronograma": "56",
        "emails_alerta_limite": "marcelo.marcolino@plural.com.br, ricardo.rosas@plural.com.br",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_remetente": "",
        "email_api_provedor": "brevo",
        "email_api_chave": "",
    }

    @classmethod
    def obter(cls, chave: str) -> str:
        item = db.session.get(cls, chave)
        return item.valor if item else cls.PADROES[chave]

    @classmethod
    def obter_int(cls, chave: str) -> int:
        try:
            return int(cls.obter(chave))
        except (TypeError, ValueError):
            return int(cls.PADROES[chave])

    @classmethod
    def definir(cls, chave: str, valor) -> None:
        item = db.session.get(cls, chave)
        if item:
            item.valor = str(valor)
        else:
            db.session.add(cls(chave=chave, valor=str(valor)))


def garantir_esquema() -> None:
    """Inclui colunas novas em bancos já existentes (create_all não altera tabela)."""
    inspector = db.inspect(db.engine)
    if "ferias" not in inspector.get_table_names():
        return
    colunas = {c["name"] for c in inspector.get_columns("ferias")}
    if "adianta_13" not in colunas:
        with db.engine.begin() as conn:
            conn.execute(db.text("ALTER TABLE ferias ADD COLUMN adianta_13 BOOLEAN NOT NULL DEFAULT 0"))
    _alinhar_fins_ao_periodo()


def _alinhar_fins_ao_periodo() -> None:
    """Recalcula o fim gravado para coincidir com início + dias − 1."""
    from .regras import calcular_fim

    mudou = False
    for ferias in db.session.scalars(db.select(Ferias)).all():
        novo = calcular_fim(ferias.inicio, ferias.dias)
        if ferias.fim != novo:
            ferias.fim = novo
            mudou = True
    if mudou:
        db.session.commit()


class TentativaLogin(db.Model):
    """Falhas de login recentes, usadas para bloquear tentativas repetidas."""

    __tablename__ = "tentativas_login"

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(120), nullable=False, index=True)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


class AlertaLimiteEnviado(db.Model):
    """Evita reenviar o mesmo aviso de prazo (funcionário + data limite)."""

    __tablename__ = "alertas_limite_enviados"

    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.Integer, db.ForeignKey("funcionarios.id"), nullable=False, index=True)
    data_limite = db.Column(db.Date, nullable=False)
    enviado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)

    __table_args__ = (db.UniqueConstraint("funcionario_id", "data_limite", name="uk_alerta_limite"),)


class Historico(db.Model):
    __tablename__ = "historico"

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, default=datetime.now, index=True)
    usuario = db.Column(db.String(60))
    acao = db.Column(db.String(60), nullable=False)
    detalhe = db.Column(db.String(500))
