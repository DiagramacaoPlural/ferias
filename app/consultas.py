from dataclasses import dataclass
from datetime import date, timedelta

from .models import Configuracao, Ferias, Funcionario, db
from .regras import DIAS_SEMANA_ABREV, MESES, MESES_ABREV, Periodo, ciclo_de, limite_ausentes, ocupacao_por_dia


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
        ausentes = total_ativos - pior_disp
        semanas.append(
            {
                "inicio": segunda,
                "rotulo": f"{segunda.day:02d}/{segunda.month:02d}",
                "disponiveis": pior_disp,
                "ausentes": ausentes,
                "conflito": pior_disp < min_disponiveis,
                "pct": round(pior_disp / total_ativos * 100) if total_ativos else 0,
                "nomes": pior_nomes,
                "pior_dia": pior_dia,
            }
        )
        segunda += timedelta(days=7)
    return semanas


def painel_cobertura(semanas: list[dict], total_ativos: int, min_disponiveis: int) -> dict:
    """Monta o resumo e o gráfico SVG da cobertura do setor."""
    if not semanas or total_ativos <= 0:
        return {}

    limite_ausentes = max(total_ativos - min_disponiveis, 0)
    pico = max(s["ausentes"] for s in semanas)
    menor = min(s["disponiveis"] for s in semanas)
    media = round(sum(s["disponiveis"] for s in semanas) / len(semanas), 1)
    em_risco = [s for s in semanas if s["conflito"]]
    concentracoes = sorted(
        (s for s in semanas if s["ausentes"] > 0),
        key=lambda s: (s["disponiveis"], -s["ausentes"], s["inicio"]),
    )[:6]

    meses, visto = [], set()
    for s in semanas:
        chave = (s["inicio"].year, s["inicio"].month)
        if chave in visto:
            mes = meses[-1]
            if s["disponiveis"] < mes["disponiveis"]:
                mes["disponiveis"] = s["disponiveis"]
                mes["ausentes"] = s["ausentes"]
                mes["nomes"] = s["nomes"]
            mes["conflito"] = mes["conflito"] or s["conflito"]
            continue
        visto.add(chave)
        meses.append(
            {
                "rotulo": f"{MESES_ABREV[s['inicio'].month - 1]}/{str(s['inicio'].year)[2:]}",
                "nome": MESES[s["inicio"].month - 1],
                "disponiveis": s["disponiveis"],
                "ausentes": s["ausentes"],
                "nomes": s["nomes"],
                "conflito": s["conflito"],
            }
        )
    meses = [m for m in meses if m["ausentes"]]

    largura, altura = 920, 280
    esq, dir_, topo, base = 48, 18, 24, 44
    plot_w, plot_h = largura - esq - dir_, altura - topo - base
    y_max = max(pico, 4)
    if pico >= limite_ausentes:
        y_max = max(pico, limite_ausentes, 1)
    n = max(len(semanas) - 1, 1)

    def px(i: int) -> float:
        return esq + i * plot_w / n

    def py(valor: int) -> float:
        return topo + plot_h - (valor / y_max) * plot_h

    pontos = [(px(i), py(s["ausentes"])) for i, s in enumerate(semanas)]
    linha = " ".join(f"{x:.1f},{y:.1f}" for x, y in pontos)
    ultimo_x = pontos[-1][0]
    area = f"{esq:.1f},{topo + plot_h:.1f} {linha} {ultimo_x:.1f},{topo + plot_h:.1f}"

    grades = []
    passo = 1 if y_max <= 8 else max(1, y_max // 5)
    for valor in range(0, y_max + 1, passo):
        grades.append({"valor": valor, "y": round(py(valor), 1)})

    meses_eixo, ja_mes, ultimo_x = [], None, -100
    for i, s in enumerate(semanas):
        chave = (s["inicio"].year, s["inicio"].month)
        x = round(px(i), 1)
        if chave != ja_mes and x - ultimo_x >= 42:
            ja_mes = chave
            ultimo_x = x
            meses_eixo.append({"x": x, "rotulo": MESES[s["inicio"].month - 1][:3]})

    marcadores = []
    for i, s in enumerate(semanas):
        anterior = semanas[i - 1]["ausentes"] if i else -1
        if s["ausentes"] == pico and pico > 0 and s["ausentes"] != anterior:
            marcadores.append(
                {
                    "x": round(px(i), 1),
                    "y": round(py(s["ausentes"]), 1),
                    "rotulo": str(s["ausentes"]),
                    "conflito": s["conflito"],
                    "titulo": (
                        f"Semana de {s['inicio']:%d/%m}: {s['ausentes']} de férias, "
                        f"{s['disponiveis']} disponíveis"
                        + (f" ({', '.join(s['nomes'])})" if s["nomes"] else "")
                    ),
                }
            )

    y_limite = round(py(limite_ausentes), 1) if 0 <= limite_ausentes <= y_max else None

    return {
        "kpi": {
            "menor": menor,
            "pico": pico,
            "media": media,
            "riscos": len(em_risco),
            "limite_ausentes": limite_ausentes,
            "folga": limite_ausentes - pico,
        },
        "svg": {
            "largura": largura,
            "altura": altura,
            "area": area,
            "linha": linha,
            "grades": grades,
            "meses": meses_eixo,
            "marcadores": marcadores,
            "y_limite": y_limite,
            "base": round(topo + plot_h, 1),
            "esq": esq,
            "dir": round(largura - dir_, 1),
        },
        "meses": meses,
        "concentracoes": concentracoes,
        "em_risco": em_risco,
    }


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


def grade_cronograma(inicio: date, fim: date) -> dict:
    """Grade diária de ausências, com o início recuado para a segunda-feira."""
    inicio = inicio - timedelta(days=inicio.weekday())
    if fim < inicio:
        return {}
    dias = [inicio + timedelta(days=i) for i in range((fim - inicio).days + 1)]
    ferias = ferias_no_intervalo(inicio, fim, apenas_ativos=True)
    ativos = funcionarios_ativos()
    min_disp = minimo_no_setor()
    limite = limite_ausentes(len(ativos), min_disp)

    meses_cabecalho = []
    for dia in dias:
        rotulo = f"{MESES[dia.month - 1]} {dia.year}"
        if meses_cabecalho and meses_cabecalho[-1]["rotulo"] == rotulo:
            meses_cabecalho[-1]["dias"] += 1
        else:
            meses_cabecalho.append({"rotulo": rotulo, "dias": 1})

    ausentes = {dia: [] for dia in dias}
    linhas = []
    for func in ativos:
        periodos = [f for f in ferias if f.funcionario_id == func.id]
        celulas = []
        for dia in dias:
            periodo = next((f for f in periodos if f.inicio <= dia <= f.fim), None)
            if periodo:
                ausentes[dia].append(func.nome)
            celulas.append({"dia": dia, "ferias": periodo})
        linhas.append({"funcionario": func, "celulas": celulas, "periodos": periodos})

    return {
        "dias": dias,
        "meses_cabecalho": meses_cabecalho,
        "linhas": linhas,
        "ausentes": ausentes,
        "limite": limite,
        "min_disponiveis": min_disp,
        "dias_semana": DIAS_SEMANA_ABREV,
        "inicio_grade": inicio,
        "fim_grade": fim,
    }
