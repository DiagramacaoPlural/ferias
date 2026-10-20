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

    def pode_provas(self) -> bool:
        return self.admin or self.acessa_calendario or self.acessa_cronograma


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
    criado_por = db.Column(db.String(60))
    criado_em = db.Column(db.DateTime, default=datetime.now)
    aprovado_por = db.Column(db.String(60))
    aprovado_em = db.Column(db.DateTime)

    funcionario = db.relationship("Funcionario", back_populates="ferias")

    def definir_periodo(self, inicio, dias: int) -> None:
        self.inicio = inicio
        self.dias = dias
        self.fim = inicio + timedelta(days=dias - 1)

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
        "verba_df_trabalho": "170",
        "verba_df_especial": "200",
        "verba_thiago_trabalho": "70",
        "verba_thiago_feriado": "85",
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


class TentativaLogin(db.Model):
    """Falhas de login recentes, usadas para bloquear tentativas repetidas."""

    __tablename__ = "tentativas_login"

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(120), nullable=False, index=True)
    data = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)


equipe_etapa = db.Table(
    "equipe_etapa",
    db.Column("etapa_id", db.Integer, db.ForeignKey("etapas_prova.id"), primary_key=True),
    db.Column("funcionario_id", db.Integer, db.ForeignKey("funcionarios.id"), primary_key=True),
)


class ProjetoProva(db.Model):
    __tablename__ = "projetos_prova"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), unique=True, nullable=False)
    ordem = db.Column(db.Integer, default=0, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    etapas = db.relationship(
        "EtapaProva", back_populates="projeto", cascade="all, delete-orphan", order_by="EtapaProva.ordem"
    )

    @property
    def inicio(self):
        datas = [e.inicio for e in self.etapas if e.inicio]
        return min(datas) if datas else None

    @property
    def fim(self):
        datas = [e.fim for e in self.etapas if e.fim]
        return max(datas) if datas else None

    @property
    def dias(self) -> int:
        if self.inicio and self.fim:
            return (self.fim - self.inicio).days + 1
        return 0


class EtapaProva(db.Model):
    __tablename__ = "etapas_prova"

    id = db.Column(db.Integer, primary_key=True)
    projeto_id = db.Column(db.Integer, db.ForeignKey("projetos_prova.id"), nullable=False, index=True)
    tipo = db.Column(db.String(60), nullable=False)
    local = db.Column(db.String(10))
    inicio = db.Column(db.Date, index=True)
    fim = db.Column(db.Date, index=True)
    ordem = db.Column(db.Integer, default=0, nullable=False)
    pcs_adicionais = db.Column(db.Integer, default=0, nullable=False)
    observacao = db.Column(db.String(500))

    projeto = db.relationship("ProjetoProva", back_populates="etapas")
    equipe = db.relationship("Funcionario", secondary=equipe_etapa)
    lancamentos = db.relationship("LancamentoDiario", back_populates="etapa", cascade="all, delete-orphan")

    @property
    def rotulo(self) -> str:
        return f"{self.tipo} {self.local}".strip() if self.local else self.tipo

    @property
    def rotulo_completo(self) -> str:
        return f"{self.projeto.nome} · {self.rotulo}"

    @property
    def dias(self) -> int:
        if self.inicio and self.fim:
            return (self.fim - self.inicio).days + 1
        return 0

    def progresso(self, referencia: date | None = None) -> float:
        if not self.inicio or not self.fim:
            return 0
        hoje = referencia or date.today()
        if hoje < self.inicio:
            return 0
        if hoje > self.fim:
            return 1
        total = max((self.fim - self.inicio).days, 1)
        return min((hoje - self.inicio).days / total, 1)

    def status_em(self, referencia: date | None = None) -> str:
        if not self.inicio or not self.fim:
            return "sem_data"
        hoje = referencia or date.today()
        if hoje < self.inicio:
            return "futura"
        if hoje > self.fim:
            return "concluida"
        return "andamento"

    @property
    def total_pcs(self) -> int:
        return len(self.equipe) + (self.pcs_adicionais or 0)


TIPOS_ETAPA = ["Diagramação", "Produção", "Manuseio FGV", "Aplicação"]
LOCAIS_PROVA = ["DF", "SP"]
TIPOS_LANCAMENTO = {
    "TRABALHO": "Trabalho",
    "FOLGA": "Folga",
    "FOLGA_DF": "Folga DF",
    "FERIADO_TRABALHADO": "Feriado trabalhado",
}


class LancamentoDiario(db.Model):
    __tablename__ = "lancamentos_diarios"
    __table_args__ = (db.UniqueConstraint("etapa_id", "funcionario_id", "data", name="uk_lancamento_dia"),)

    id = db.Column(db.Integer, primary_key=True)
    etapa_id = db.Column(db.Integer, db.ForeignKey("etapas_prova.id"), nullable=False, index=True)
    funcionario_id = db.Column(db.Integer, db.ForeignKey("funcionarios.id"), nullable=False, index=True)
    data = db.Column(db.Date, nullable=False, index=True)
    tipo = db.Column(db.String(30), default="TRABALHO", nullable=False)
    tarefa = db.Column(db.String(200))
    realizada = db.Column(db.Boolean, default=True, nullable=False)
    verba = db.Column(db.Numeric(10, 2))
    observacao = db.Column(db.String(500))
    criado_por = db.Column(db.String(60))
    criado_em = db.Column(db.DateTime, default=datetime.now)

    etapa = db.relationship("EtapaProva", back_populates="lancamentos")
    funcionario = db.relationship("Funcionario")

    @property
    def tipo_rotulo(self) -> str:
        return TIPOS_LANCAMENTO.get(self.tipo, self.tipo)

    @property
    def dia_util(self) -> bool:
        return self.tipo == "TRABALHO" and self.data.weekday() < 5


class Historico(db.Model):
    __tablename__ = "historico"

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, default=datetime.now, index=True)
    usuario = db.Column(db.String(60))
    acao = db.Column(db.String(60), nullable=False)
    detalhe = db.Column(db.String(500))
