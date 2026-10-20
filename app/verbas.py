"""Valores de verba (auxílio Alymente) extraídos do VBA do Provas.xlsm.

No formulário Controle, a planilha grava a coluna Verba assim:
- SP: sempre 0
- DF, Thiago: 70 no dia de trabalho, 85 no feriado trabalhado, 0 em folga/folga DF
- DF, demais: 170 no dia de trabalho, 200 em folga DF ou feriado trabalhado, 0 em folga
"""

from decimal import Decimal

from .models import Configuracao

CHAVES = {
    "verba_df_trabalho": "170",
    "verba_df_especial": "200",
    "verba_thiago_trabalho": "70",
    "verba_thiago_feriado": "85",
}


def taxas(origem: dict | None = None) -> dict[str, Decimal]:
    if origem is None:
        origem = {chave: Configuracao.obter(chave) for chave in CHAVES}
    resultado = {}
    for chave, padrao in CHAVES.items():
        try:
            resultado[chave] = Decimal(str(origem.get(chave, padrao)))
        except Exception:
            resultado[chave] = Decimal(padrao)
    return resultado


def eh_thiago(nome: str | None) -> bool:
    return "thiago" in (nome or "").lower()


def calcular_verba(local: str | None, tipo: str, nome: str | None, origem: dict | None = None) -> Decimal:
    valores = taxas(origem)
    if (local or "").strip().upper() != "DF":
        return Decimal("0")
    if tipo == "FOLGA":
        return Decimal("0")
    if eh_thiago(nome):
        if tipo == "FOLGA_DF":
            return Decimal("0")
        if tipo == "FERIADO_TRABALHADO":
            return valores["verba_thiago_feriado"]
        return valores["verba_thiago_trabalho"]
    if tipo in ("FOLGA_DF", "FERIADO_TRABALHADO"):
        return valores["verba_df_especial"]
    return valores["verba_df_trabalho"]
