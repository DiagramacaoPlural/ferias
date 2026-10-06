"""Regras de negócio do controle de férias.

As funções deste módulo não acessam o banco de dados diretamente (exceto as
marcadas com sufixo ``_db``), o que facilita os testes.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]
MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
DIAS_SEMANA_ABREV = ["S", "T", "Q", "Q", "S", "S", "D"]

DIAS_MIN_PERIODO = 5
DIAS_MIN_PERIODO_PRINCIPAL = 14
DIAS_MAX_CICLO = 30
MAX_PERIODOS = 3


@dataclass
class Periodo:
    funcionario: str
    inicio: date
    fim: date
    dias: int
    id: int | None = None


@dataclass
class Analise:
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.erros


def calcular_fim(inicio: date, dias: int) -> date:
    """O dia de início conta como 1º dia de férias."""
    return inicio + timedelta(days=dias - 1)


def ciclo_de(dia: date, mes_inicio: int) -> tuple[date, date]:
    ano = dia.year if dia.month >= mes_inicio else dia.year - 1
    inicio = date(ano, mes_inicio, 1)
    fim = date(ano + 1, mes_inicio, 1) - timedelta(days=1)
    return inicio, fim


def rotulo_ciclo(inicio_ciclo: date) -> str:
    fim = date(inicio_ciclo.year + 1, inicio_ciclo.month, 1) - timedelta(days=1)
    return f"{MESES_ABREV[inicio_ciclo.month - 1]}/{inicio_ciclo.year} a {MESES_ABREV[fim.month - 1]}/{fim.year}"


def meses_do_ciclo(inicio_ciclo: date) -> list[date]:
    meses = []
    ano, mes = inicio_ciclo.year, inicio_ciclo.month
    for _ in range(12):
        meses.append(date(ano, mes, 1))
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    return meses


def ultimo_dia_mes(primeiro: date) -> date:
    if primeiro.month == 12:
        return date(primeiro.year, 12, 31)
    return date(primeiro.year, primeiro.month + 1, 1) - timedelta(days=1)


def sobrepoe(a_inicio: date, a_fim: date, b_inicio: date, b_fim: date) -> bool:
    return a_inicio <= b_fim and b_inicio <= a_fim


def ocupacao_por_dia(periodos: list[Periodo], inicio: date, fim: date) -> dict[date, set[str]]:
    """Para cada dia do intervalo, o conjunto de funcionários de férias."""
    ocupacao: dict[date, set[str]] = defaultdict(set)
    for p in periodos:
        dia = max(p.inicio, inicio)
        ultimo = min(p.fim, fim)
        while dia <= ultimo:
            ocupacao[dia].add(p.funcionario)
            dia += timedelta(days=1)
    return ocupacao


def analisar_periodo(
    funcionario: str,
    inicio: date,
    dias: int,
    outros_do_funcionario: list[Periodo],
    periodos_de_outros: list[Periodo],
    max_simultaneos: int,
    mes_inicio_ciclo: int,
) -> Analise:
    """Valida um período de férias.

    Erros impedem o salvamento; avisos apenas alertam (regras da CLT e
    conflitos com a equipe), pois podem existir acordos específicos.
    """
    analise = Analise()

    if dias < 1 or dias > DIAS_MAX_CICLO:
        analise.erros.append(f"A quantidade de dias deve estar entre 1 e {DIAS_MAX_CICLO}.")
        return analise

    fim = calcular_fim(inicio, dias)

    for p in outros_do_funcionario:
        if sobrepoe(inicio, fim, p.inicio, p.fim):
            analise.erros.append(
                f"{funcionario} já tem férias de {p.inicio:%d/%m/%Y} a {p.fim:%d/%m/%Y}, "
                "que se sobrepõem a este período."
            )
    if analise.erros:
        return analise

    if dias < DIAS_MIN_PERIODO:
        analise.avisos.append(
            f"Períodos de férias devem ter no mínimo {DIAS_MIN_PERIODO} dias corridos (CLT, art. 134, §1º)."
        )

    if inicio.weekday() in (4, 5):
        analise.avisos.append(
            "Início numa sexta ou sábado: a CLT veda o início das férias nos 2 dias que antecedem "
            "o repouso semanal ou feriado (art. 134, §3º)."
        )

    ciclo_ini, ciclo_fim = ciclo_de(inicio, mes_inicio_ciclo)
    do_ciclo = [p for p in outros_do_funcionario if ciclo_ini <= p.inicio <= ciclo_fim]
    duracoes = [p.dias for p in do_ciclo] + [dias]
    total = sum(duracoes)
    rotulo = rotulo_ciclo(ciclo_ini)

    if len(duracoes) > MAX_PERIODOS:
        analise.avisos.append(
            f"{funcionario} ficaria com {len(duracoes)} períodos no ciclo {rotulo}; "
            f"a CLT permite dividir as férias em até {MAX_PERIODOS} (art. 134, §1º)."
        )
    if total > DIAS_MAX_CICLO:
        analise.avisos.append(
            f"{funcionario} somaria {total} dias de férias no ciclo {rotulo} (máximo usual: {DIAS_MAX_CICLO})."
        )
    if len(duracoes) > 1 and max(duracoes) < DIAS_MIN_PERIODO_PRINCIPAL:
        restante = DIAS_MAX_CICLO - total
        if restante < DIAS_MIN_PERIODO_PRINCIPAL or len(duracoes) >= MAX_PERIODOS:
            analise.avisos.append(
                f"Quando as férias são fracionadas, um dos períodos deve ter pelo menos "
                f"{DIAS_MIN_PERIODO_PRINCIPAL} dias (CLT, art. 134, §1º)."
            )

    ocupacao = ocupacao_por_dia(periodos_de_outros, inicio, fim)
    pico_dia, pico_nomes = None, set()
    for dia, nomes in sorted(ocupacao.items()):
        if len(nomes) + 1 > max_simultaneos and len(nomes) > len(pico_nomes):
            pico_dia, pico_nomes = dia, nomes
    if pico_dia:
        nomes = ", ".join(sorted(pico_nomes))
        analise.avisos.append(
            f"Conflito na equipe: em {pico_dia:%d/%m/%Y} haveria {len(pico_nomes) + 1} pessoas de férias "
            f"(limite configurado: {max_simultaneos}). Também de férias: {nomes}."
        )

    return analise


def analisar_periodo_db(funcionario, inicio: date, dias: int, ferias_id: int | None = None) -> Analise:
    from .models import Configuracao, Ferias, db

    def para_periodo(f):
        return Periodo(f.funcionario.nome, f.inicio, f.fim, f.dias, f.id)

    fim = calcular_fim(inicio, max(dias, 1))
    ciclo_ini, ciclo_fim = ciclo_de(inicio, Configuracao.obter_int("mes_inicio_ciclo"))
    janela_ini, janela_fim = min(ciclo_ini, inicio), max(ciclo_fim, fim)

    consulta = db.select(Ferias).where(Ferias.inicio <= janela_fim, Ferias.fim >= janela_ini)
    if ferias_id:
        consulta = consulta.where(Ferias.id != ferias_id)
    existentes = db.session.scalars(consulta).all()

    do_funcionario = [para_periodo(f) for f in existentes if f.funcionario_id == funcionario.id]
    de_outros = [
        para_periodo(f) for f in existentes
        if f.funcionario_id != funcionario.id and sobrepoe(inicio, fim, f.inicio, f.fim)
    ]

    return analisar_periodo(
        funcionario.nome,
        inicio,
        dias,
        do_funcionario,
        de_outros,
        Configuracao.obter_int("max_simultaneos"),
        Configuracao.obter_int("mes_inicio_ciclo"),
    )
