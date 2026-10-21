"""Regras de negócio do controle de férias.

As funções deste módulo não acessam o banco de dados diretamente (exceto as
marcadas com sufixo ``_db``), o que facilita os testes.
"""

import calendar
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
AVISO_13_FEVEREIRO = (
    "A 1ª parcela do 13º só pode ser antecipada em férias com início a partir de fevereiro."
)
EMAILS_ALERTA_LIMITE = (
    "marcelo.marcolino@plural.com.br, ricardo.rosas@plural.com.br"
)


@dataclass
class Periodo:
    funcionario: str
    inicio: date
    fim: date
    dias: int
    id: int | None = None


@dataclass
class Alternativa:
    inicio: date
    fim: date


@dataclass
class Analise:
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    alternativas: list[Alternativa] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.erros


def pascoa(ano: int) -> date:
    """Algoritmo de Meeus/Jones/Butcher (calendário gregoriano)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def feriados_brasil(ano: int) -> set[date]:
    """Feriados nacionais e pontes usuais da folha (carnaval, sexta-santa, Corpus Christi)."""
    pascoa_dt = pascoa(ano)
    return {
        date(ano, 1, 1),
        pascoa_dt - timedelta(days=48),
        pascoa_dt - timedelta(days=47),
        pascoa_dt - timedelta(days=2),
        date(ano, 4, 21),
        date(ano, 5, 1),
        pascoa_dt + timedelta(days=60),
        date(ano, 9, 7),
        date(ano, 10, 12),
        date(ano, 11, 2),
        date(ano, 11, 15),
        date(ano, 11, 20),
        date(ano, 12, 25),
    }


def calcular_fim(inicio: date, dias: int) -> date:
    """O dia de início conta como 1º dia. O fim pode cair em sábado, domingo ou feriado."""
    if dias < 1:
        return inicio
    return inicio + timedelta(days=dias - 1)


def pode_adiantar_13(inicio: date) -> bool:
    """A 1ª parcela do 13º só vale para férias que começam em fevereiro ou depois."""
    return inicio.month >= 2


def somar_anos(dia: date, anos: int) -> date:
    try:
        return dia.replace(year=dia.year + anos)
    except ValueError:
        return date(dia.year + anos, 2, 28)


def somar_meses(dia: date, meses: int) -> date:
    mes0 = dia.month - 1 + meses
    ano = dia.year + mes0 // 12
    mes = mes0 % 12 + 1
    return date(ano, mes, min(dia.day, calendar.monthrange(ano, mes)[1]))


def ajustar_dia_util(dia: date) -> date:
    """Recua sábado/domingo para a sexta anterior (como na folha de programação)."""
    if dia.weekday() == 5:
        return dia - timedelta(days=1)
    if dia.weekday() == 6:
        return dia - timedelta(days=2)
    return dia


def data_limite_folha(admissao: date, ciclo: int = 0) -> date:
    """Data limite da programação da folha: 22 meses após o início do aquisitivo."""
    bruto = somar_meses(somar_anos(admissao, ciclo), 22) - timedelta(days=1)
    return ajustar_dia_util(bruto)


def ciclo_gozo_vigente(admissao: date, hoje: date) -> int:
    """Ciclo concessivo em curso na data (a partir da admissão), ou o próximo."""
    ciclo = 0
    while ciclo < 80:
        concessivo_fim = somar_anos(admissao, ciclo + 2) - timedelta(days=1)
        if hoje <= concessivo_fim:
            return ciclo
        ciclo += 1
    return 0


@dataclass
class PrazoGozo:
    limite: date | None
    alerta_em: date | None
    dias_restantes: int | None
    situacao: str
    dias_programados: int = 0
    nome: str = ""
    funcionario_id: int | None = None
    admissao: date | None = None


def calcular_prazo_gozo(
    admissao: date | None,
    ferias: list,
    hoje: date,
    nome: str = "",
    funcionario_id: int | None = None,
) -> PrazoGozo:
    """Limite da folha no ciclo de gozo vigente: 22 meses após o aquisitivo da admissão.

    O ciclo vigente é o período concessivo em curso. Se nele já houve 30 dias
    programados, avança para o seguinte. O alerta dispara 1 mês antes do limite.
    """
    vazio = PrazoGozo(None, None, None, "sem_admissao", nome=nome, funcionario_id=funcionario_id, admissao=admissao)
    if not admissao:
        return vazio
    ciclo = ciclo_gozo_vigente(admissao, hoje)
    while ciclo < 80:
        concessivo_ini = somar_anos(admissao, ciclo + 1)
        concessivo_fim = somar_anos(admissao, ciclo + 2) - timedelta(days=1)
        limite = data_limite_folha(admissao, ciclo)
        dias = sum(p.dias for p in ferias if concessivo_ini <= p.inicio <= concessivo_fim)
        if dias < DIAS_MAX_CICLO:
            alerta_em = somar_meses(limite, -1)
            if hoje > limite:
                situacao = "vencido"
            elif hoje >= alerta_em:
                situacao = "alerta"
            else:
                situacao = "ok"
            return PrazoGozo(
                limite=limite,
                alerta_em=alerta_em,
                dias_restantes=(limite - hoje).days,
                situacao=situacao,
                dias_programados=dias,
                nome=nome,
                funcionario_id=funcionario_id,
                admissao=admissao,
            )
        ciclo += 1
    return PrazoGozo(None, None, None, "ok", nome=nome, funcionario_id=funcionario_id, admissao=admissao)


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


def limite_ausentes(total_ativos: int, min_disponiveis: int) -> int:
    """Quantas pessoas podem estar de férias sem desfalcar o setor."""
    return max(total_ativos - min_disponiveis, 0)


def nivel_cobertura(ausentes: int, total_ativos: int, min_disponiveis: int) -> str:
    """Compara disponíveis com o mínimo: verde acima, amarelo perto, vermelho abaixo."""
    if ausentes <= 0:
        return "pouco"
    disp = total_ativos - ausentes
    if disp < min_disponiveis:
        return "muito"
    folga = max(total_ativos - min_disponiveis, 0)
    acima = disp - min_disponiveis
    faixa_media = max(1, (folga + 2) // 3) if folga else 1
    if acima < faixa_media:
        return "medio"
    return "pouco"


def _inicio_recomendado(dia: date) -> bool:
    return dia.weekday() not in (4, 5)


def cabe_no_setor(
    inicio: date,
    dias: int,
    ocupacao: dict[date, set[str]],
    total_ativos: int,
    min_disponiveis: int,
    proprios: list[Periodo],
) -> bool:
    fim = calcular_fim(inicio, dias)
    for p in proprios:
        if sobrepoe(inicio, fim, p.inicio, p.fim):
            return False
    dia = inicio
    while dia <= fim:
        if total_ativos - (len(ocupacao.get(dia, set())) + 1) < min_disponiveis:
            return False
        dia += timedelta(days=1)
    return True


def sugerir_alternativas(
    inicio_original: date,
    dias: int,
    ocupacao: dict[date, set[str]],
    total_ativos: int,
    min_disponiveis: int,
    proprios: list[Periodo],
    janela_ini: date,
    janela_fim: date,
    max_opcoes: int = 3,
) -> list[Alternativa]:
    """Procura outros períodos da mesma duração que mantêm o mínimo no setor."""
    ultimo_inicio = janela_fim - timedelta(days=dias - 1)
    if ultimo_inicio < janela_ini:
        return []

    candidatos: list[tuple[int, date]] = []
    dia = janela_ini
    while dia <= ultimo_inicio:
        if dia != inicio_original and _inicio_recomendado(dia):
            if cabe_no_setor(dia, dias, ocupacao, total_ativos, min_disponiveis, proprios):
                candidatos.append((abs((dia - inicio_original).days), dia))
        dia += timedelta(days=1)
    candidatos.sort()

    escolhidos: list[date] = []
    for _, candidato in candidatos:
        if all(abs((candidato - ja).days) >= 14 for ja in escolhidos):
            escolhidos.append(candidato)
        if len(escolhidos) >= max_opcoes:
            break
    return [Alternativa(d, calcular_fim(d, dias)) for d in escolhidos]


def analisar_periodo(
    funcionario: str,
    inicio: date,
    dias: int,
    outros_do_funcionario: list[Periodo],
    periodos_de_outros: list[Periodo],
    max_simultaneos: int,
    mes_inicio_ciclo: int,
    total_ativos: int | None = None,
    min_disponiveis: int | None = None,
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
    if total_ativos is not None and min_disponiveis is not None:
        pior_disp, pico_dia, pico_nomes = total_ativos, None, set()
        dia = inicio
        while dia <= fim:
            nomes = ocupacao.get(dia, set())
            disponiveis = total_ativos - (len(nomes) + 1)
            if disponiveis < min_disponiveis and disponiveis < pior_disp:
                pior_disp, pico_dia, pico_nomes = disponiveis, dia, nomes
            dia += timedelta(days=1)
        if pico_dia:
            nomes = ", ".join(sorted(pico_nomes))
            complemento = f" Também de férias: {nomes}." if nomes else ""
            analise.avisos.append(
                f"O setor ficaria com {pior_disp} pessoa(s) disponível(is) em {pico_dia:%d/%m/%Y} "
                f"(mínimo: {min_disponiveis}).{complemento}"
            )
            ciclo_ini, ciclo_fim = ciclo_de(inicio, mes_inicio_ciclo)
            ocupacao_ciclo = ocupacao_por_dia(periodos_de_outros, ciclo_ini, ciclo_fim)
            analise.alternativas = sugerir_alternativas(
                inicio, dias, ocupacao_ciclo, total_ativos, min_disponiveis,
                outros_do_funcionario, ciclo_ini, ciclo_fim,
            )
            if analise.alternativas:
                opcoes = "; ".join(f"{a.inicio:%d/%m/%Y} a {a.fim:%d/%m/%Y}" for a in analise.alternativas)
                analise.avisos.append(f"Alternativa de programação: {opcoes}.")
            else:
                analise.avisos.append(
                    "Não há outro período da mesma duração neste ciclo que mantenha o mínimo no setor."
                )
    else:
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
    from .models import Configuracao, Ferias, Funcionario, db

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
    de_outros = [para_periodo(f) for f in existentes if f.funcionario_id != funcionario.id]
    total_ativos = db.session.scalar(db.select(db.func.count(Funcionario.id)).filter_by(ativo=True)) or 0
    min_disponiveis = Configuracao.obter_int("min_disponiveis")

    return analisar_periodo(
        funcionario.nome,
        inicio,
        dias,
        do_funcionario,
        de_outros,
        limite_ausentes(total_ativos, min_disponiveis),
        Configuracao.obter_int("mes_inicio_ciclo"),
        total_ativos=total_ativos,
        min_disponiveis=min_disponiveis,
    )
