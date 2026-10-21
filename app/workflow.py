"""Montagem do calendário a partir das opções dos funcionários e notificações."""

from __future__ import annotations

from datetime import date

from .alertas import enviar_email
from .consultas import funcionarios_ativos
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


_STATUS_FILA_LIDER = (
    STATUS_AGUARDANDO_LIDER,
    STATUS_AJUSTE_LIDER,
    STATUS_AGUARDANDO_FUNCIONARIO,
    STATUS_AJUSTE_GERENTE,
)
_STATUS_NAO_SOBRESCREVER = (STATUS_APROVADA, STATUS_AGUARDANDO_GERENTE)


def _ferias_do_ciclo(funcionario_id: int, ciclo_ini: date, ciclo_fim: date) -> list[Ferias]:
    return list(
        db.session.scalars(
            db.select(Ferias).where(
                Ferias.funcionario_id == funcionario_id,
                Ferias.inicio >= ciclo_ini,
                Ferias.inicio <= ciclo_fim,
            )
        )
    )


def _colocar_na_fila_do_lider(funcionario: Funcionario, opcoes: list[SugestaoFerias]) -> Ferias | None:
    """Cria ou atualiza o período com a 1ª opção e deixa na fila do líder."""
    from .consultas import ciclo_por_ano

    if not opcoes:
        return None
    primeira = opcoes[0]
    ciclo_ini, ciclo_fim = ciclo_por_ano(primeira.ciclo_ano)
    existentes = _ferias_do_ciclo(funcionario.id, ciclo_ini, ciclo_fim)
    if any(f.status in _STATUS_NAO_SOBRESCREVER for f in existentes):
        return None
    ferias = next((f for f in existentes if f.status in _STATUS_FILA_LIDER), None)
    if ferias is None:
        ferias = Ferias(funcionario=funcionario, criado_por="funcionario")
        db.session.add(ferias)
    ferias.definir_periodo(primeira.inicio, primeira.dias)
    ferias.adianta_13 = bool(primeira.adianta_13)
    ferias.vende_10 = bool(primeira.vende_10)
    ferias.status = STATUS_AGUARDANDO_LIDER
    ferias.recusou_ajuste = False
    return ferias


def enviar_ao_lider(funcionario: Funcionario, opcoes: list[SugestaoFerias]) -> Ferias | None:
    ferias = _colocar_na_fila_do_lider(funcionario, opcoes)
    if not ferias:
        return None
    db.session.commit()
    _notificar_papel(
        PAPEL_LIDER,
        "Novas datas de férias para validar",
        f"{funcionario.nome} enviou opções de férias. "
        f"A primeira data é {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}. "
        "Valide ou sugira outra data no Controle de Férias.",
    )
    return ferias


def montar_calendario(ano: int | None = None) -> dict:
    """Coloca na fila do líder quem já enviou opções e ainda não está no fluxo."""
    from .consultas import ciclo_por_ano

    ciclo_ini, ciclo_fim = ciclo_por_ano(ano)
    ativos = funcionarios_ativos()
    sugestoes = db.session.scalars(
        db.select(SugestaoFerias).where(SugestaoFerias.ciclo_ano == ciclo_ini.year).order_by(
            SugestaoFerias.funcionario_id, SugestaoFerias.ordem
        )
    ).all()
    por_func: dict[int, list[SugestaoFerias]] = {}
    for s in sugestoes:
        por_func.setdefault(s.funcionario_id, []).append(s)

    montados, ja_na_fila, sem_opcao = [], [], []
    for func in ativos:
        opcoes = por_func.get(func.id) or []
        if not opcoes:
            sem_opcao.append(func.nome)
            continue
        existentes = _ferias_do_ciclo(func.id, ciclo_ini, ciclo_fim)
        if any(f.status in _STATUS_NAO_SOBRESCREVER for f in existentes):
            ja_na_fila.append(func.nome)
            continue
        if any(f.status == STATUS_AGUARDANDO_LIDER for f in existentes):
            ja_na_fila.append(func.nome)
            continue
        ferias = _colocar_na_fila_do_lider(func, opcoes)
        if ferias:
            montados.append(func.nome)

    if montados:
        _notificar_papel(
            PAPEL_LIDER,
            "Datas de férias na fila para validar",
            "Há períodos enviados pelos funcionários, aguardando validação do líder.\n\n"
            + "\n".join(f"- {n}" for n in montados),
        )

    db.session.add(
        Historico(
            usuario="sistema",
            acao="Calendário montado",
            detalhe=f"{len(montados)} na fila do líder, {len(ja_na_fila)} já estavam no fluxo",
        )
    )
    db.session.commit()
    return {"montados": montados, "propostos": [], "sem_encaixe": []}


def aceitar_proposta(ferias: Ferias) -> None:
    ferias.status = STATUS_AGUARDANDO_LIDER
    ferias.recusou_ajuste = False
    db.session.commit()
    _notificar_papel(
        PAPEL_LIDER,
        "Funcionário aceitou a data sugerida",
        f"{ferias.funcionario.nome} aceitou {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}. "
        "Valide e envie ao gerente no Controle de Férias.",
    )


def recusar_proposta(ferias: Ferias) -> None:
    ferias.status = STATUS_AGUARDANDO_LIDER
    ferias.recusou_ajuste = True
    ferias.observacao = ((ferias.observacao or "") + " · Recusou a data proposta.").strip(" ·")
    db.session.commit()
    _notificar_papel(
        PAPEL_LIDER,
        "Funcionário recusou a data — definir e enviar ao gerente",
        f"{ferias.funcionario.nome} recusou {ferias.inicio:%d/%m/%Y} a {ferias.fim:%d/%m/%Y}. "
        "Defina a data e envie ao gerente; o funcionário não precisa responder de novo.",
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
