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

# Mesma chave pública do G-Note (js/config.js). Só abre a porta da Edge Function; o segredo é outro.
_ANON_GNOTE = "sb_publishable_Tk4Ky8mOoj5XwhWUnTO3WA_1aYIadiL"


def _env() -> tuple[str, str]:
    return (os.environ.get("GNOTE_FERIAS_URL") or "").strip(), (os.environ.get("GNOTE_FERIAS_SEGREDO") or "").strip()


def configurado() -> bool:
    url, segredo = _env()
    return bool(url and segredo)


def _proxy() -> str:
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or (
            "http://proxy.server:3128"
            if os.environ.get("PYTHONANYWHERE_SITE") or os.environ.get("PYTHONANYWHERE_DOMAIN")
            else ""
        )
    )


def _urlopen(req: urllib.request.Request, timeout: int = 45):
    proxy = _proxy()
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


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
    anon = (os.environ.get("GNOTE_ANON_KEY") or _ANON_GNOTE).strip()
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
            "Authorization": f"Bearer {anon}",
            "apikey": anon,
        },
    )
    try:
        with _urlopen(req, timeout=45) as resp:
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
            if isinstance(extra, dict):
                texto = json.dumps(extra).lower()
                if extra.get("code") == "NOT_FOUND" or e.code == 404:
                    return {"ok": False, "erro": "funcao_nao_encontrada"}
                if "invalid jwt" in texto or extra.get("message") == "Invalid JWT":
                    return {"ok": False, "erro": "jwt_ligado"}
                if extra.get("erro"):
                    return {"ok": False, "erro": extra["erro"]}
        except Exception:
            pass
        if e.code == 404:
            return {"ok": False, "erro": "funcao_nao_encontrada"}
        if e.code == 401:
            return {"ok": False, "erro": "jwt_ligado" if "jwt" in detalhe.lower() else "segredo_invalido"}
        log.warning("G-Note HTTP %s ao sincronizar férias %s: %s", e.code, ferias_id, detalhe or e)
        return {"ok": False, "erro": f"http_{e.code}"}
    except Exception as e:
        log.warning("Falha ao sincronizar férias %s com o G-Note: %s", ferias_id, e)
        return {"ok": False, "erro": "falha_rede"}


ERROS_INFRA = {
    "falha_rede",
    "funcao_nao_encontrada",
    "segredo_invalido",
    "jwt_ligado",
    "http_401",
    "http_403",
    "http_404",
}


def erro_infraestrutura(erro: str | None) -> bool:
    codigo = str(erro or "")
    return codigo in ERROS_INFRA or codigo.startswith("http_") or codigo.startswith("falha_rede")


def mensagem_erro(erro: str | None) -> str:
    codigo = str(erro or "")
    if codigo in ("funcao_nao_encontrada", "http_404"):
        return (
            "A função sincronizar-ferias ainda não foi publicada no G-Note. "
            "No Supabase: Edge Functions → Deploy a new function, nome sincronizar-ferias, "
            "cole o arquivo do Controle de OS, desligue Verify JWT e crie o secret FERIAS_SEGREDO."
        )
    if codigo == "jwt_ligado":
        return (
            "O G-Note recusou a chamada: na função sincronizar-ferias, desligue Verify JWT e salve. "
            "Sem isso o PythonAnywhere não consegue enviar as férias."
        )
    if codigo in ("segredo_invalido", "http_401"):
        return (
            "O segredo não conferiu. Confira FERIAS_SEGREDO no Supabase e GNOTE_FERIAS_SEGREDO no PythonAnywhere "
            "(o mesmo valor) e, na função sincronizar-ferias, desligue Verify JWT."
        )
    if codigo == "usuario_nao_encontrado":
        return "O G-Note não encontrou o usuário. Confira se o e-mail do funcionário é o mesmo do G-Note."
    if codigo == "falha_rede" or codigo.startswith("falha_rede"):
        return (
            "Não foi possível conectar ao G-Note. No PythonAnywhere gratuito, peça para incluir "
            "*.supabase.co na lista de sites de saída, ou use conta paga."
        )
    if codigo:
        return f"O G-Note recusou o envio ({codigo})."
    return "Não foi possível lançar no G-Note."


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
    flash("As férias foram salvas. " + mensagem_erro(resultado.get("erro")), "aviso")
