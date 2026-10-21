"""Prazos de gozo (CLT) e envio de e-mail 1 mês antes do limite."""

from __future__ import annotations

import os
import smtplib
from datetime import date, datetime
from email.message import EmailMessage

from .models import AlertaLimiteEnviado, Configuracao, Ferias, Funcionario, Historico, db
from .regras import EMAILS_ALERTA_LIMITE, Periodo, PrazoGozo, calcular_prazo_gozo


def _periodos(ferias: list[Ferias]) -> list[Periodo]:
    return [Periodo("", f.inicio, f.fim, f.dias, f.id) for f in ferias]


def prazos_dos_funcionarios(funcionarios: list[Funcionario], hoje: date | None = None) -> dict[int, PrazoGozo]:
    hoje = hoje or date.today()
    return {
        f.id: calcular_prazo_gozo(f.data_admissao, _periodos(f.ferias), hoje, f.nome, f.id)
        for f in funcionarios
    }


def prazos_que_alertam(prazos: dict[int, PrazoGozo]) -> list[PrazoGozo]:
    return [p for p in prazos.values() if p.situacao in ("alerta", "vencido")]


def _destinatarios() -> list[str]:
    bruto = os.environ.get("EMAILS_ALERTA_LIMITE") or Configuracao.obter("emails_alerta_limite") or EMAILS_ALERTA_LIMITE
    return [e.strip() for e in bruto.replace(";", ",").split(",") if e.strip() and "@" in e]


def _smtp() -> dict:
    host = os.environ.get("SMTP_HOST") or Configuracao.obter("smtp_host")
    user = os.environ.get("SMTP_USER") or Configuracao.obter("smtp_user")
    senha = os.environ.get("SMTP_PASSWORD") or Configuracao.obter("smtp_password")
    remetente = os.environ.get("SMTP_FROM") or Configuracao.obter("smtp_remetente") or user
    try:
        porta = int(os.environ.get("SMTP_PORT") or Configuracao.obter("smtp_port") or 587)
    except ValueError:
        porta = 587
    return {"host": host.strip() if host else "", "port": porta, "user": user, "password": senha, "from": remetente}


def smtp_configurado() -> bool:
    cfg = _smtp()
    return bool(cfg["host"] and cfg["from"])


def _montar_mensagem(pendentes: list[PrazoGozo], hoje: date) -> tuple[str, str]:
    linhas = []
    for p in sorted(pendentes, key=lambda x: (x.limite or hoje, x.nome)):
        if p.situacao == "vencido":
            estado = "PRAZO VENCIDO"
        elif p.dias_restantes == 0:
            estado = "vence hoje"
        else:
            estado = f"vence em {p.dias_restantes} dia(s)"
        admissao = p.admissao.strftime("%d/%m/%Y") if p.admissao else "—"
        limite = p.limite.strftime("%d/%m/%Y") if p.limite else "—"
        linhas.append(
            f"- {p.nome}: admissão {admissao} · limite para gozo {limite} ({estado}); "
            f"{p.dias_programados} dia(s) já programado(s)."
        )
    assunto = "Alerta: prazo de férias (1 mês)"
    if any(p.situacao == "vencido" for p in pendentes):
        assunto = "Alerta: prazo de férias vencido ou a vencer"
    corpo = (
        "Há funcionários com a data limite de férias da folha em até 1 mês, ou já vencida "
        "(22 meses após a admissão).\n\n"
        + "\n".join(linhas)
        + "\n\nProgramem as férias no Controle de Férias."
    )
    return assunto, corpo


def _enviar_smtp(assunto: str, corpo: str, destinatarios: list[str]) -> tuple[bool, str]:
    cfg = _smtp()
    if not cfg["host"] or not cfg["from"]:
        return False, "Configure o SMTP em Configurações (ou nas variáveis SMTP_HOST e SMTP_FROM)."
    if not destinatarios:
        return False, "Nenhum destinatário de alerta cadastrado."
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(destinatarios)
    msg.set_content(corpo)
    try:
        if cfg["port"] == 465:
            smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
        else:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
        with smtp:
            smtp.ehlo()
            if cfg["port"] != 465:
                smtp.starttls()
                smtp.ehlo()
            if cfg["user"]:
                smtp.login(cfg["user"], cfg["password"] or "")
            smtp.send_message(msg)
    except Exception as erro:  # falha de rede, autenticação ou TLS
        return False, str(erro)
    return True, ""


def enviar_alertas_pendentes(hoje: date | None = None, forcar: bool = False) -> dict:
    """Envia um e-mail conjunto para os gestores, uma vez por funcionário/limite."""
    hoje = hoje or date.today()
    funcionarios = db.session.scalars(
        db.select(Funcionario).filter_by(ativo=True).order_by(Funcionario.nome)
    ).all()
    pendentes = prazos_que_alertam(prazos_dos_funcionarios(funcionarios, hoje))
    if not pendentes:
        return {"enviados": 0, "pendentes": 0, "erro": None}

    if not forcar:
        ainda = []
        for p in pendentes:
            ja = db.session.scalar(
                db.select(AlertaLimiteEnviado).filter_by(funcionario_id=p.funcionario_id, data_limite=p.limite)
            )
            if not ja:
                ainda.append(p)
        pendentes = ainda
    if not pendentes:
        return {"enviados": 0, "pendentes": 0, "erro": None}

    destinatarios = _destinatarios()
    assunto, corpo = _montar_mensagem(pendentes, hoje)
    ok, erro = _enviar_smtp(assunto, corpo, destinatarios)
    if not ok:
        return {"enviados": 0, "pendentes": len(pendentes), "erro": erro}

    agora = datetime.now()
    for p in pendentes:
        if not db.session.scalar(
            db.select(AlertaLimiteEnviado).filter_by(funcionario_id=p.funcionario_id, data_limite=p.limite)
        ):
            db.session.add(
                AlertaLimiteEnviado(funcionario_id=p.funcionario_id, data_limite=p.limite, enviado_em=agora)
            )
    nomes = ", ".join(p.nome for p in pendentes)
    db.session.add(
        Historico(
            usuario="sistema",
            acao="Alerta de prazo enviado",
            detalhe=f"{len(pendentes)} funcionário(s) para {', '.join(destinatarios)}: {nomes}"[:500],
        )
    )
    db.session.commit()
    return {"enviados": len(pendentes), "pendentes": len(pendentes), "erro": None}
