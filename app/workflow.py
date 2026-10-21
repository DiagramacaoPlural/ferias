"""Montagem do calendário a partir das opções dos funcionários e notificações."""

from __future__ import annotations

from datetime import date

from .alertas import enviar_email
from .consultas import funcionarios_ativos, minimo_no_setor
from .models import (
    PAPEL_GERENTE,
    PAPEL_LIDER,
    STATUS_AGUARDANDO_FUNCIONARIO,
    STATUS_AGUARDANDO_GERENTE,
    STATUS_AGUARDANDO_LIDER,
    STATUS_AJUSTE_GERENTE,
    STATUS_AJUSTE_LIDER,
    STATUS_APROVADA,
    Ferias,
    Funcionario,
    Historico,
    SugestaoFerias,
    Usuario,
    db,
)
from .regras import Periodo, calcular_fim, cabe_no_setor, ocupacao_por_dia, sugerir_alternativas


def _notificar(funcionario: Funcionario, assunto: str, corpo: str) -> None:
    if funcionario.email:
        enviar_email(assunto, corpo, [funcionario.email])


def _emails_do_papel(papel: str) -> list[str]:
    usuarios = db.session.scalars(db.select(Usuario).filter_by(papel=papel, ativo=True)).all()
    emails = []
    for u in usuarios:
        if u.funcionario and u.funcionario.email and "@" in u.funcionario.email:
            emails.append(u.funcionario.email)
    return emails


def _notificar_papel(papel: str, assunto: str, corpo: str) -> None:
    emails = _emails_do_papel(papel)
    if papel == PAPEL_GERENTE:
        from .alertas import _destinatarios

        emails = list(dict.fromkeys(emails + _destinatarios()))
    if emails:
        enviar_email(assunto, corpo, emails)


def montar_calendario(ano: int | None = None) -> dict:
    """Escolhe uma das opções de cada funcionário; se não couber, propõe data próxima."""
    from .consultas import ciclo_por_ano

    ciclo_ini, ciclo_fim = ciclo_por_ano(ano)
    ativos = funcionarios_ativos()
    total = len(ativos)
    min_disp = minimo_no_setor()
    sugestoes = db.session.scalars(
        db.select(SugestaoFerias).where(SugestaoFerias.ciclo_ano == ciclo_ini.year).order_by(
            SugestaoFerias.funcionario_id, SugestaoFerias.ordem
        )
    ).all()
    por_func: dict[int, list[SugestaoFerias]] = {}
    for s in sugestoes:
        por_func.setdefault(s.funcionario_id, []).append(s)

    aprovadas = [
        Periodo(f.funcionario.nome, f.inicio, f.fim, f.dias, f.id)
        for f in db.session.scalars(
            db.select(Ferias).where(
                Ferias.status == STATUS_APROVADA, Ferias.inicio <= ciclo_fim, Ferias.fim >= ciclo_ini
            )
        )
    ]
    ocupacao = ocupacao_por_dia(aprovadas, ciclo_ini, ciclo_fim)

    montados, propostos, sem_encaixe = [], [], []
    for func in ativos:
        opcoes = por_func.get(func.id) or []
        ja = db.session.scalar(
            db.select(Ferias).where(
                Ferias.funcionario_id == func.id,
                Ferias.status == STATUS_APROVADA,
                Ferias.inicio >= ciclo_ini,
                Ferias.inicio <= ciclo_fim,
            )
        )
        if ja:
            continue
        for antigo in db.session.scalars(
            db.select(Ferias).where(
                Ferias.funcionario_id == func.id,
                Ferias.status != STATUS_APROVADA,
                Ferias.inicio >= ciclo_ini,
                Ferias.inicio <= ciclo_fim,
            )
        ):
            db.session.delete(antigo)
        db.session.flush()

        escolhida = None
        origem_opcao = False
        for opcao in opcoes:
            if cabe_no_setor(opcao.inicio, opcao.dias, ocupacao, total, min_disp, []):
                escolhida = opcao
                origem_opcao = True
                break
        if not escolhida and opcoes:
            alts = sugerir_alternativas(
                opcoes[0].inicio, opcoes[0].dias, ocupacao, total, min_disp, [], ciclo_ini, ciclo_fim, 1
            )
            if alts:
                class _Tmp:
                    pass

                escolhida = _Tmp()
                escolhida.inicio = alts[0].inicio
                escolhida.dias = opcoes[0].dias
                escolhida.adianta_13 = opcoes[0].adianta_13
                escolhida.vende_10 = opcoes[0].vende_10
                origem_opcao = False
        if not escolhida:
            if opcoes:
                sem_encaixe.append(func.nome)
            continue

        ferias = Ferias(
            funcionario=func,
            status=STATUS_AGUARDANDO_LIDER if origem_opcao else STATUS_AGUARDANDO_FUNCIONARIO,
            adianta_13=bool(escolhida.adianta_13),
            vende_10=bool(escolhida.vende_10),
            criado_por="montagem",
        )
        ferias.definir_periodo(escolhida.inicio, escolhida.dias)
        db.session.add(ferias)
        db.session.flush()
        ocupacao = ocupacao_por_dia(
            aprovadas + [Periodo(func.nome, ferias.inicio, ferias.fim, ferias.dias, ferias.id)],
            ciclo_ini,
            ciclo_fim,
        )
        aprovadas.append(Periodo(func.nome, ferias.inicio, ferias.fim, ferias.dias, ferias.id))
        if origem_opcao:
            montados.append(func.nome)
        else:
            propostos.append(func.nome)
            _notificar(
                func,
                "Sugestão de férias para o seu período",
                f"Olá, {func.nome}.\n\n"
                f"Não foi possível encaixar as datas que você enviou sem reduzir o mínimo do setor. "
                f"Sugerimos {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y} ({ferias.dias} dias).\n\n"
                f"Entre no Controle de Férias, abra o Calendário e aceite ou recuse esta data.",
            )

    if montados:
        _notificar_papel(
            PAPEL_LIDER,
            "Calendário de férias montado — validar",
            "Há períodos montados com as datas enviadas pelos funcionários, aguardando validação do líder.\n\n"
            + "\n".join(f"- {n}" for n in montados),
        )

    db.session.add(
        Historico(
            usuario="sistema",
            acao="Calendário montado",
            detalhe=f"{len(montados)} na opção enviada, {len(propostos)} com data próxima, {len(sem_encaixe)} sem encaixe",
        )
    )
    db.session.commit()
    return {"montados": montados, "propostos": propostos, "sem_encaixe": sem_encaixe}


def aceitar_proposta(ferias: Ferias) -> None:
    if ferias.status == STATUS_AJUSTE_LIDER:
        ferias.status = STATUS_AGUARDANDO_GERENTE
        db.session.commit()
        _notificar_papel(
            PAPEL_GERENTE,
            "Funcionário aceitou a data do líder",
            f"{ferias.funcionario.nome} aceitou {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}. "
            "Dê o aceite no Controle de Férias.",
        )
        return
    ferias.status = STATUS_AGUARDANDO_LIDER
    db.session.commit()
    _notificar_papel(
        PAPEL_LIDER,
        "Funcionário aceitou a data sugerida",
        f"{ferias.funcionario.nome} aceitou {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}. Validar no Calendário.",
    )


def recusar_proposta(ferias: Ferias) -> None:
    ferias.status = STATUS_AGUARDANDO_LIDER
    ferias.observacao = ((ferias.observacao or "") + " · Recusou a data proposta.").strip(" ·")
    db.session.commit()
    _notificar_papel(
        PAPEL_LIDER,
        "Funcionário recusou a data sugerida",
        f"{ferias.funcionario.nome} recusou {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}. "
        "Escolha a melhor data no Calendário.",
    )


def lider_validar(ferias: Ferias, login: str) -> None:
    ferias.status = STATUS_AGUARDANDO_GERENTE
    ferias.aprovado_por = login
    db.session.commit()
    _notificar_papel(
        PAPEL_GERENTE,
        "Férias aguardando aceite do gerente",
        f"{ferias.funcionario.nome}: {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y} ({ferias.dias} dias). "
        "Aceite ou sugira outra data no Calendário.",
    )


def lider_definir(ferias: Ferias, inicio: date, login: str) -> None:
    ferias.definir_periodo(inicio, ferias.dias)
    lider_validar(ferias, login)


def lider_sugerir(ferias: Ferias, inicio: date, login: str) -> None:
    ferias.definir_periodo(inicio, ferias.dias)
    ferias.status = STATUS_AJUSTE_LIDER
    ferias.observacao = ((ferias.observacao or "") + f" · Data sugerida pelo líder {login}.").strip(" ·")
    db.session.commit()
    _notificar(
        ferias.funcionario,
        "O líder sugeriu outra data para as suas férias",
        f"Olá, {ferias.funcionario.nome}.\n\n"
        f"O líder sugeriu o período {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y} ({ferias.dias} dias).\n"
        f"Entre no Controle de Férias, abra Minhas datas ou o Calendário e aceite ou recuse.",
    )


def gerente_aprovar(ferias: Ferias, login: str) -> None:
    from datetime import datetime

    ferias.status = STATUS_APROVADA
    ferias.aprovado_por = login
    ferias.aprovado_em = datetime.now()
    db.session.commit()
    _notificar(
        ferias.funcionario,
        "Férias aprovadas",
        f"Olá, {ferias.funcionario.nome}.\n\n"
        f"Seu período de férias foi aprovado: {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y} "
        f"({ferias.dias} dias).\n\nPara consultar o status, acesse o Calendário no Controle de Férias.",
    )


def gerente_sugerir(ferias: Ferias, inicio: date, login: str) -> None:
    ferias.definir_periodo(inicio, ferias.dias)
    ferias.status = STATUS_AJUSTE_GERENTE
    ferias.observacao = ((ferias.observacao or "") + f" · Ajuste sugerido por {login}.").strip(" ·")
    db.session.commit()
    _notificar(
        ferias.funcionario,
        "Alteração sugerida nas suas férias",
        f"Olá, {ferias.funcionario.nome}.\n\n"
        f"O gerente sugeriu o período {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}.\n"
        f"Entre no Calendário para aceitar ou recusar.",
    )
