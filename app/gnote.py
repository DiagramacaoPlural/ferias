"""Envio do período aprovado ao G-Note, para cobrir os avisos de apontamento."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date
from typing import Any

from .models import Ferias

log = logging.getLogger(__name__)


def _env() -> tuple[str, str]:
    return (os.environ.get("GNOTE_FERIAS_URL") or "").strip(), (os.environ.get("GNOTE_FERIAS_SEGREDO") or "").strip()


def configurado() -> bool:
    url, segredo = _env()
    return bool(url and segredo)


def sincronizar_periodo(
    acao: str,
    ferias_id: str | int,
    email: str | None,
    nome: str | None,
    inicio: date | None,
    fim: date | None,
) -> dict[str, Any] | None:
    """POST ao G-Note. Sem as variáveis de ambiente, não faz nada. Nunca levanta exceção."""
    url, segredo = _env()
    if not url or not segredo:
        return None
    corpo = {
        "acao": acao,
        "ferias_id": str(ferias_id),
        "email": (email or "").strip(),
        "nome": (nome or "").strip(),
        "inicio": inicio.isoformat() if inicio else "",
        "fim": fim.isoformat() if fim else "",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-ferias-segredo": segredo,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            bruto = resp.read().decode("utf-8")
            dados = json.loads(bruto) if bruto else {}
            if not isinstance(dados, dict):
                return {"ok": True}
            dados.setdefault("ok", True)
            return dados
    except urllib.error.HTTPError as e:
        detalhe = ""
        try:
            detalhe = e.read().decode("utf-8")[:400]
            extra = json.loads(detalhe)
            if isinstance(extra, dict) and extra.get("erro"):
                return {"ok": False, "erro": extra["erro"]}
        except Exception:
            pass
        log.warning("G-Note HTTP %s ao sincronizar férias %s: %s", e.code, ferias_id, detalhe or e)
        return {"ok": False, "erro": f"http_{e.code}"}
    except Exception as e:
        log.warning("Falha ao sincronizar férias %s com o G-Note: %s", ferias_id, e)
        return {"ok": False, "erro": "falha_rede"}


def sincronizar_ferias(ferias: Ferias, acao: str) -> dict[str, Any] | None:
    func = ferias.funcionario
    return sincronizar_periodo(
        acao,
        ferias.id,
        func.email if func else "",
        func.nome if func else "",
        ferias.inicio,
        ferias.fim,
    )


def avisar_resultado(resultado: dict[str, Any] | None, *, verboso: bool = False) -> None:
    from flask import flash, has_request_context

    if resultado is None or not has_request_context():
        return
    if resultado.get("ok"):
        if verboso:
            n = resultado.get("dias")
            flash(
                f"G-Note atualizado ({n} dia(s))." if n is not None else "G-Note atualizado.",
                "sucesso",
            )
        return
    erro = str(resultado.get("erro") or "")
    if erro == "usuario_nao_encontrado":
        flash(
            "As férias foram salvas, mas o G-Note não encontrou o usuário. "
            "Confira se o e-mail do funcionário é o mesmo do G-Note.",
            "aviso",
        )
    else:
        flash(
            "As férias foram salvas, mas não foi possível lançar no G-Note. "
            "Depois use Configurações › Enviar férias aprovadas ao G-Note.",
            "aviso",
        )
