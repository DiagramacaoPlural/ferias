from datetime import date

from app.regras import Periodo, analisar_periodo, calcular_fim, ciclo_de, meses_do_ciclo


def analisar(inicio, dias, proprios=(), outros=(), limite=1, nome="Ana"):
    return analisar_periodo(nome, inicio, dias, list(proprios), list(outros), limite, 5)


def test_fim_conta_o_dia_de_inicio():
    assert calcular_fim(date(2026, 12, 1), 20) == date(2026, 12, 20)
    assert calcular_fim(date(2026, 12, 1), 1) == date(2026, 12, 1)
    assert calcular_fim(date(2026, 12, 20), 30) == date(2027, 1, 18)


def test_ciclo_maio_a_abril():
    assert ciclo_de(date(2026, 10, 6), 5) == (date(2026, 5, 1), date(2027, 4, 30))
    assert ciclo_de(date(2027, 2, 1), 5) == (date(2026, 5, 1), date(2027, 4, 30))
    assert ciclo_de(date(2027, 5, 1), 5) == (date(2027, 5, 1), date(2028, 4, 30))
    assert ciclo_de(date(2026, 3, 1), 1) == (date(2026, 1, 1), date(2026, 12, 31))


def test_meses_do_ciclo():
    meses = meses_do_ciclo(date(2026, 5, 1))
    assert len(meses) == 12
    assert meses[0] == date(2026, 5, 1)
    assert meses[-1] == date(2027, 4, 1)


def test_periodo_valido_sem_avisos():
    a = analisar(date(2026, 12, 1), 30)  # terça-feira
    assert a.ok and a.avisos == []


def test_dias_fora_do_intervalo_eh_erro():
    assert not analisar(date(2026, 12, 1), 0).ok
    assert not analisar(date(2026, 12, 1), 31).ok


def test_sobreposicao_com_proprias_ferias_eh_erro():
    proprio = Periodo("Ana", date(2026, 12, 1), date(2026, 12, 10), 10)
    a = analisar(date(2026, 12, 8), 10, proprios=[proprio])
    assert not a.ok
    assert "sobrepõem" in a.erros[0]


def test_aviso_menos_de_5_dias():
    a = analisar(date(2026, 12, 1), 4)
    assert a.ok
    assert any("mínimo 5" in av for av in a.avisos)


def test_aviso_inicio_sexta_ou_sabado():
    assert any("sexta ou sábado" in av for av in analisar(date(2026, 12, 4), 10).avisos)
    assert any("sexta ou sábado" in av for av in analisar(date(2026, 12, 5), 10).avisos)
    assert not any("sexta ou sábado" in av for av in analisar(date(2026, 12, 7), 10).avisos)


def test_aviso_total_acima_de_30_no_ciclo():
    proprio = Periodo("Ana", date(2026, 6, 1), date(2026, 6, 20), 20)
    a = analisar(date(2026, 12, 1), 15, proprios=[proprio])
    assert any("35 dias" in av for av in a.avisos)


def test_aviso_fracionamento_sem_periodo_de_14_dias():
    p1 = Periodo("Ana", date(2026, 6, 1), date(2026, 6, 10), 10)
    p2 = Periodo("Ana", date(2026, 8, 3), date(2026, 8, 12), 10)
    a = analisar(date(2026, 12, 1), 10, proprios=[p1, p2])
    assert any("14 dias" in av for av in a.avisos)


def test_fracionamento_ainda_pode_ter_periodo_de_14_sem_aviso():
    p1 = Periodo("Ana", date(2026, 6, 1), date(2026, 6, 10), 10)
    a = analisar(date(2026, 12, 1), 5, proprios=[p1])
    assert not any("14 dias" in av for av in a.avisos)


def test_aviso_mais_de_3_periodos():
    proprios = [
        Periodo("Ana", date(2026, 6, 1), date(2026, 6, 14), 14),
        Periodo("Ana", date(2026, 8, 3), date(2026, 8, 7), 5),
        Periodo("Ana", date(2026, 9, 1), date(2026, 9, 5), 5),
    ]
    a = analisar(date(2026, 12, 1), 5, proprios=proprios)
    assert any("4 períodos" in av for av in a.avisos)


def test_conflito_com_equipe_respeita_limite():
    outro = Periodo("Bruno", date(2026, 12, 10), date(2026, 12, 20), 11)
    a = analisar(date(2026, 12, 1), 15, outros=[outro], limite=1)
    assert any("Bruno" in av for av in a.avisos)

    a = analisar(date(2026, 12, 1), 15, outros=[outro], limite=2)
    assert not any("Bruno" in av for av in a.avisos)
