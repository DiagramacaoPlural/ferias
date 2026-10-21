from datetime import date, timedelta

from app.consultas import painel_cobertura
from app.regras import Periodo, analisar_periodo, calcular_fim, calcular_prazo_gozo, ciclo_de, ciclo_gozo_vigente, data_limite_folha, meses_do_ciclo, nivel_cobertura, pascoa, pode_adiantar_13, somar_anos, somar_meses, sobrepoe, sugerir_alternativas


def analisar(inicio, dias, proprios=(), outros=(), limite=1, nome="Ana"):
    return analisar_periodo(nome, inicio, dias, list(proprios), list(outros), limite, 5)


def test_primeira_parcela_13_so_a_partir_de_fevereiro():
    assert not pode_adiantar_13(date(2027, 1, 31))
    assert pode_adiantar_13(date(2027, 2, 1))
    assert pode_adiantar_13(date(2026, 12, 1))


def test_limite_de_gozo_e_alerta_um_mes_antes():
    admissao = date(2025, 4, 29)
    limite = date(2027, 2, 26)
    prazo = calcular_prazo_gozo(admissao, [], date(2026, 10, 20), "Wagner", 1)
    assert prazo.limite == limite
    assert prazo.alerta_em == date(2027, 1, 26)
    assert prazo.situacao == "ok"

    alerta = calcular_prazo_gozo(admissao, [], date(2027, 1, 26), "Wagner", 1)
    assert alerta.situacao == "alerta"

    vencido = calcular_prazo_gozo(admissao, [], date(2027, 2, 27), "Wagner", 1)
    assert vencido.situacao == "vencido"
    assert vencido.limite == limite

    gozado = [Periodo("Wagner", date(2026, 5, 10), date(2026, 6, 8), 30)]
    proximo = calcular_prazo_gozo(admissao, gozado, date(2026, 10, 20), "Wagner", 1)
    assert proximo.limite == date(2028, 2, 28)


def test_limite_do_ciclo_vigente_pela_admissao():
    hoje = date(2026, 10, 21)
    assert ciclo_gozo_vigente(date(2025, 4, 29), hoje) == 0
    assert ciclo_gozo_vigente(date(2023, 4, 29), hoje) == 2
    antigo = calcular_prazo_gozo(date(2023, 4, 29), [], hoje, "Ana", 1)
    assert antigo.limite == date(2027, 2, 26)
    assert antigo.situacao == "ok"


def test_data_limite_confere_com_a_folha():
    assert data_limite_folha(date(2025, 8, 29)) == date(2027, 6, 28)
    assert data_limite_folha(date(2025, 6, 16)) == date(2027, 4, 15)
    assert data_limite_folha(date(2025, 4, 11)) == date(2027, 2, 10)
    assert data_limite_folha(date(2025, 12, 18)) == date(2027, 10, 15)
    assert data_limite_folha(date(2025, 7, 10)) == date(2027, 5, 7)
    assert data_limite_folha(date(2025, 4, 29)) == date(2027, 2, 26)


def test_somar_anos_e_meses():
    assert somar_anos(date(2024, 2, 29), 1) == date(2025, 2, 28)
    assert somar_meses(date(2027, 4, 28), -1) == date(2027, 3, 28)
    assert somar_meses(date(2027, 3, 31), -1) == date(2027, 2, 28)


def test_fim_conta_o_dia_de_inicio():
    assert calcular_fim(date(2026, 12, 1), 1) == date(2026, 12, 1)
    assert calcular_fim(date(2026, 12, 7), 5) == date(2026, 12, 11)


def test_fim_avanca_sabado_domingo_e_feriado():
    assert pascoa(2026) == date(2026, 4, 5)
    assert pascoa(2027) == date(2027, 3, 28)
    assert calcular_fim(date(2026, 12, 1), 20) == date(2026, 12, 21)
    assert calcular_fim(date(2026, 12, 14), 20) == date(2027, 1, 4)
    assert calcular_fim(date(2026, 12, 3), 30) == date(2027, 1, 4)
    assert calcular_fim(date(2027, 1, 31), 10) == date(2027, 2, 10)


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


def test_painel_cobertura_destaca_pico():
    semanas = [
        {"inicio": date(2026, 11, 3), "disponiveis": 12, "ausentes": 0, "conflito": False, "nomes": []},
        {"inicio": date(2026, 12, 1), "disponiveis": 12, "ausentes": 0, "conflito": False, "nomes": []},
        {"inicio": date(2026, 12, 8), "disponiveis": 8, "ausentes": 4, "conflito": False, "nomes": ["A", "B", "C", "D"]},
        {"inicio": date(2027, 1, 5), "disponiveis": 9, "ausentes": 3, "conflito": False, "nomes": ["A", "B", "C"]},
    ]
    painel = painel_cobertura(semanas, 12, 3)
    assert painel["kpi"]["pico"] == 4
    assert painel["kpi"]["menor"] == 8
    assert painel["kpi"]["riscos"] == 0
    assert "4" in painel["svg"]["linha"]
    assert painel["concentracoes"][0]["disponiveis"] == 8
    assert [m["rotulo"] for m in painel["meses"]] == ["dez/26", "jan/27"]


def test_nivel_cobertura_por_volume_de_ferias():
    # 12 no setor, mínimo 5: vermelho só abaixo do mínimo; 7 disponíveis é laranja
    assert nivel_cobertura(1, 12, 5) == "pouco"
    assert nivel_cobertura(4, 12, 5) == "pouco"
    assert nivel_cobertura(5, 12, 5) == "medio"
    assert nivel_cobertura(7, 12, 5) == "medio"
    assert nivel_cobertura(8, 12, 5) == "muito"
    # 4 no setor, mínimo 3: no mínimo é médio; abaixo é muito
    assert nivel_cobertura(1, 4, 3) == "medio"
    assert nivel_cobertura(2, 4, 3) == "muito"


def test_setor_desfalcado_gera_aviso_e_alternativas():
    outro = Periodo("Bruno", date(2026, 12, 1), date(2026, 12, 20), 20)
    a = analisar_periodo(
        "Ana", date(2026, 12, 7), 10, [], [outro], 1, 5,
        total_ativos=4, min_disponiveis=3,
    )
    assert any("disponível" in av for av in a.avisos)
    assert a.alternativas
    for alt in a.alternativas:
        assert not sobrepoe(alt.inicio, alt.fim, outro.inicio, outro.fim)
        assert alt.inicio.weekday() not in (4, 5)


def test_sugestao_respeita_minimo_no_setor():
    from app.regras import ocupacao_por_dia

    outro = Periodo("Bruno", date(2026, 12, 1), date(2026, 12, 20), 20)
    ocupacao = ocupacao_por_dia([outro], date(2026, 11, 1), date(2027, 1, 31))
    alts = sugerir_alternativas(
        date(2026, 12, 7), 10, ocupacao, 4, 3, [], date(2026, 11, 1), date(2027, 1, 31),
    )
    assert alts
    assert alts[0].fim == calcular_fim(alts[0].inicio, 10)


def test_conflito_com_equipe_respeita_limite():
    outro = Periodo("Bruno", date(2026, 12, 10), date(2026, 12, 20), 11)
    a = analisar(date(2026, 12, 1), 15, outros=[outro], limite=1)
    assert any("Bruno" in av for av in a.avisos)

    a = analisar(date(2026, 12, 1), 15, outros=[outro], limite=2)
    assert not any("Bruno" in av for av in a.avisos)
