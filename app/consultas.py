from dataclasses import dataclass
from datetime import date, timedelta

from .models import Configuracao, Ferias, Funcionario, db
from .regras import Periodo, ciclo_de, ocupacao_por_dia


@dataclass
class Conflito:
    inicio: date
    fim: date
    pico: int
    nomes: list[str]


def ciclo_atual(referencia: date | None = None) -> tuple[date, date]:
    return ciclo_de(referencia or date.today(), Configuracao.obter_int("mes_inicio_ciclo"))


def ferias_no_intervalo(inicio: date, fim: date, apenas_ativos: bool = False) -> list[Ferias]:
    consulta = (
        db.select(Ferias)
        .join(Funcionario)
        .where(Ferias.inicio <= fim, Ferias.fim >= inicio)
        .order_by(Ferias.inicio, Funcionario.nome)
    )
    if apenas_ativos:
        consulta = consulta.where(Funcionario.ativo.is_(True))
    return list(db.session.scalars(consulta))


def funcionarios_ativos() -> list[Funcionario]:
    return list(db.session.scalars(db.select(Funcionario).filter_by(ativo=True).order_by(Funcionario.nome)))


def para_periodos(ferias: list[Ferias]) -> list[Periodo]:
    return [Periodo(f.funcionario.nome, f.inicio, f.fim, f.dias, f.id) for f in ferias]


def minimo_no_setor() -> int:
    return Configuracao.obter_int("min_disponiveis")


def serie_cobertura(
    ferias: list[Ferias],
    inicio: date,
    fim: date,
    total_ativos: int,
    min_disponiveis: int,
) -> list[dict]:
    """Uma barra por semana: menor número de pessoas disponíveis naquela semana."""
    ocupacao = ocupacao_por_dia(para_periodos(ferias), inicio, fim)
    segunda = inicio - timedelta(days=inicio.weekday())
    semanas: list[dict] = []
    while segunda <= fim:
        domingo = segunda + timedelta(days=6)
        pior_disp, pior_dia, pior_nomes = total_ativos, None, []
        dia = max(segunda, inicio)
        ultimo = min(domingo, fim)
        while dia <= ultimo:
            nomes = ocupacao.get(dia, set())
            disponiveis = total_ativos - len(nomes)
            if disponiveis < pior_disp:
                pior_disp, pior_dia, pior_nomes = disponiveis, dia, sorted(nomes)
            dia += timedelta(days=1)
        semanas.append(
            {
                "inicio": segunda,
                "rotulo": f"{segunda.day:02d}/{segunda.month:02d}",
                "disponiveis": pior_disp,
                "conflito": pior_disp < min_disponiveis,
                "pct": round(pior_disp / total_ativos * 100) if total_ativos else 0,
                "nomes": pior_nomes,
                "pior_dia": pior_dia,
            }
        )
        segunda += timedelta(days=7)
    return semanas


def conflitos(ferias: list[Ferias], inicio: date, fim: date, limite: int) -> list[Conflito]:
    """Agrupa dias consecutivos em que há mais pessoas de férias que o limite."""
    ocupacao = ocupacao_por_dia(para_periodos(ferias), inicio, fim)
    resultado: list[Conflito] = []
    atual: Conflito | None = None
    nomes_atual: set[str] = set()

    dia = inicio
    while dia <= fim:
        nomes = ocupacao.get(dia, set())
        if len(nomes) > limite:
            if atual and atual.fim == dia - timedelta(days=1):
                atual.fim = dia
                atual.pico = max(atual.pico, len(nomes))
                nomes_atual |= nomes
            else:
                if atual:
                    atual.nomes = sorted(nomes_atual)
                    resultado.append(atual)
                atual = Conflito(dia, dia, len(nomes), [])
                nomes_atual = set(nomes)
        dia += timedelta(days=1)

    if atual:
        atual.nomes = sorted(nomes_atual)
        resultado.append(atual)
    return resultado
