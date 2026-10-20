"""Carga inicial do banco a partir das planilhas originais (Férias_Final e Provas)."""

import os
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from .models import (
    STATUS_APROVADA,
    STATUS_PROGRAMADA,
    EtapaProva,
    Ferias,
    Funcionario,
    Historico,
    LancamentoDiario,
    ProjetoProva,
    Usuario,
    db,
)


def _sim(valor) -> bool:
    return str(valor or "").strip().upper() in ("SIM", "S", "TRUE", "1", "VERDADEIRO")


def _para_data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str) and valor.strip():
        try:
            return datetime.strptime(valor.strip(), "%d/%m/%Y").date()
        except ValueError:
            return None
    return None


def importar_planilha(caminho: Path) -> dict:
    """Importa funcionários, férias (aba CALENDARIO) e usuários (aba USUARIOS).

    Registros já existentes (mesmo nome/login) são ignorados, então a
    importação pode ser repetida sem duplicar dados.
    """
    from openpyxl import load_workbook

    wb = load_workbook(caminho, data_only=True, read_only=True)
    resumo = {"funcionarios": 0, "ferias": 0, "usuarios": 0}

    if "CALENDARIO" in wb.sheetnames:
        for linha in wb["CALENDARIO"].iter_rows(min_row=2, values_only=True):
            if not linha or not linha[0]:
                continue
            nome = str(linha[0]).strip()
            inicio = _para_data(linha[1] if len(linha) > 1 else None)
            dias = linha[3] if len(linha) > 3 else None
            aprovado = bool(linha[4]) if len(linha) > 4 else False

            funcionario = db.session.scalar(db.select(Funcionario).filter_by(nome=nome))
            if not funcionario:
                funcionario = Funcionario(nome=nome)
                db.session.add(funcionario)
                db.session.flush()
                resumo["funcionarios"] += 1

            if inicio and isinstance(dias, (int, float)) and dias > 0:
                ja_existe = db.session.scalar(
                    db.select(Ferias).filter_by(funcionario_id=funcionario.id, inicio=inicio)
                )
                if not ja_existe:
                    ferias = Ferias(
                        funcionario=funcionario,
                        status=STATUS_APROVADA if aprovado else STATUS_PROGRAMADA,
                        criado_por="importação",
                        aprovado_por="importação" if aprovado else None,
                        aprovado_em=datetime.now() if aprovado else None,
                    )
                    ferias.definir_periodo(inicio, int(dias))
                    db.session.add(ferias)
                    resumo["ferias"] += 1

    if "USUARIOS" in wb.sheetnames:
        for linha in wb["USUARIOS"].iter_rows(min_row=2, values_only=True):
            if not linha or not linha[0]:
                continue
            login = str(linha[0]).strip()
            if db.session.scalar(db.select(Usuario).filter(db.func.lower(Usuario.login) == login.lower())):
                continue
            usuario = Usuario(
                login=login,
                admin=_sim(linha[2] if len(linha) > 2 else None),
                acessa_calendario=_sim(linha[3] if len(linha) > 3 else None),
                acessa_cronograma=_sim(linha[4] if len(linha) > 4 else None),
                trocar_senha=True,
            )
            usuario.definir_senha(str(linha[1] if len(linha) > 1 and linha[1] is not None else "123"))
            db.session.add(usuario)
            resumo["usuarios"] += 1

    wb.close()
    db.session.add(
        Historico(
            usuario="sistema",
            acao="Importação",
            detalhe=f"{caminho.name}: {resumo['funcionarios']} funcionários, "
            f"{resumo['ferias']} férias, {resumo['usuarios']} usuários",
        )
    )
    db.session.commit()
    return resumo


def _planilhas_na_pasta(pasta: Path) -> list[Path]:
    arquivos = sorted(pasta.glob("*.xlsm")) + sorted(pasta.glob("*.xlsx"))
    return [p for p in arquivos if not p.name.startswith("~$")]


def _escolher_planilha(planilhas: list[Path], *palavras: str) -> Path | None:
    for planilha in planilhas:
        nome = planilha.name.lower()
        if any(palavra in nome for palavra in palavras):
            return planilha
    return None


def _chave(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    normalizado = normalizado.lower().replace("-", " ")
    normalizado = re.sub(r"[^a-z0-9 ]+", " ", normalizado)
    return re.sub(r"\s+", " ", normalizado).strip()


TIPOS_LINHA_CRONOGRAMA = {
    "diagramacao df": ("Diagramação", "DF"),
    "diagramacao sp": ("Diagramação", "SP"),
    "producao": ("Produção", None),
    "manuseio fgv": ("Manuseio FGV", None),
    "aplicacao": ("Aplicação", None),
}


def classificar_etapa(nome: str) -> tuple[str, str | None] | None:
    return TIPOS_LINHA_CRONOGRAMA.get(_chave(nome))


def _chave_projeto(nome: str) -> str:
    chave = re.sub(r"\bdiagramacao\b", "", _chave(nome))
    return re.sub(r"\s+", " ", chave).strip()


def _obter_projeto(nome: str, ordem: int) -> tuple[ProjetoProva, bool]:
    chave = _chave_projeto(nome)
    for existente in db.session.scalars(db.select(ProjetoProva)):
        if _chave_projeto(existente.nome) == chave:
            return existente, False
    projeto = ProjetoProva(nome=nome.strip(), ordem=ordem)
    db.session.add(projeto)
    db.session.flush()
    return projeto, True


def _obter_etapa(projeto: ProjetoProva, tipo: str, local: str | None, ordem: int) -> tuple[EtapaProva, bool]:
    consulta = db.select(EtapaProva).filter_by(projeto_id=projeto.id, tipo=tipo)
    if local:
        consulta = consulta.filter_by(local=local)
    else:
        consulta = consulta.filter(db.or_(EtapaProva.local.is_(None), EtapaProva.local == ""))
    existente = db.session.scalar(consulta)
    if existente:
        return existente, False
    etapa = EtapaProva(projeto=projeto, tipo=tipo, local=local, ordem=ordem)
    db.session.add(etapa)
    db.session.flush()
    return etapa, True


def importar_provas(caminho: Path) -> dict:
    """Importa o cronograma de provas (abas Cronograma, Projetos e Controle_Diario).

    Projetos e etapas já existentes (mesmo nome/tipo/local) são atualizados só se
    ainda não tiverem datas, para não sobrescrever o que já foi ajustado no sistema.
    """
    from openpyxl import load_workbook

    wb = load_workbook(caminho, data_only=True)
    resumo = {"projetos": 0, "etapas": 0, "lancamentos": 0}

    if "Cronograma" in wb.sheetnames:
        projeto = None
        ordem_projeto = db.session.scalar(db.select(db.func.max(ProjetoProva.ordem))) or 0
        ordem_etapa = 0
        for linha in wb["Cronograma"].iter_rows(min_row=9, values_only=True):
            nome = str(linha[1]).strip() if len(linha) > 1 and linha[1] else ""
            if not nome or nome.lower().startswith("insira novos"):
                continue
            classificada = classificar_etapa(nome)
            if classificada is None:
                ordem_projeto += 1
                projeto, novo = _obter_projeto(nome, ordem_projeto)
                if novo:
                    resumo["projetos"] += 1
                ordem_etapa = 0
                continue
            if projeto is None:
                continue
            tipo, local = classificada
            ordem_etapa += 1
            etapa, nova = _obter_etapa(projeto, tipo, local, ordem_etapa)
            if nova:
                resumo["etapas"] += 1
            inicio = _para_data(linha[4] if len(linha) > 4 else None)
            fim = _para_data(linha[5] if len(linha) > 5 else None)
            if inicio and not etapa.inicio:
                etapa.inicio = inicio
            if fim and not etapa.fim:
                etapa.fim = fim

    indice_projetos: dict[str, ProjetoProva] = {
        _chave_projeto(p.nome): p for p in db.session.scalars(db.select(ProjetoProva))
    }

    if "Projetos" in wb.sheetnames:
        for linha in wb["Projetos"].iter_rows(min_row=2, values_only=True):
            if not linha or not linha[0]:
                continue
            nome_bruto = str(linha[0]).strip()
            local = str(linha[1]).strip().upper() if len(linha) > 1 and linha[1] else None
            if local not in ("DF", "SP"):
                local = None
            chave = _chave_projeto(nome_bruto)
            projeto = indice_projetos.get(chave)
            if not projeto:
                ordem = (db.session.scalar(db.select(db.func.max(ProjetoProva.ordem))) or 0) + 1
                projeto, novo = _obter_projeto(nome_bruto.replace(" Diagramação", "").replace(" Diagramacao", ""), ordem)
                if novo:
                    resumo["projetos"] += 1
                indice_projetos[chave] = projeto
            etapa, nova = _obter_etapa(projeto, "Diagramação", local, len(projeto.etapas) + 1)
            if nova:
                resumo["etapas"] += 1
            inicio = _para_data(linha[2] if len(linha) > 2 else None)
            fim = _para_data(linha[3] if len(linha) > 3 else None)
            if inicio and not etapa.inicio:
                etapa.inicio = inicio
            if fim and not etapa.fim:
                etapa.fim = fim
            if len(linha) > 15 and isinstance(linha[15], (int, float)) and not etapa.pcs_adicionais:
                etapa.pcs_adicionais = int(linha[15])
            nomes_equipe = [str(c).strip() for c in linha[4:15] if c]
            if nomes_equipe and not etapa.equipe:
                for nome in nomes_equipe:
                    funcionario = db.session.scalar(db.select(Funcionario).filter_by(nome=nome))
                    if funcionario and funcionario not in etapa.equipe:
                        etapa.equipe.append(funcionario)

    if "Controle_Diario" in wb.sheetnames:
        for linha in wb["Controle_Diario"].iter_rows(min_row=2, values_only=True):
            if not linha or not linha[0]:
                continue
            nome_projeto = str(linha[0]).strip()
            chave = _chave_projeto(nome_projeto)
            projeto = indice_projetos.get(chave)
            if not projeto:
                continue
            local = str(linha[4]).strip().upper() if len(linha) > 4 and linha[4] else None
            etapa = next(
                (e for e in projeto.etapas if e.tipo == "Diagramação" and (not local or (e.local or "").upper() == local)),
                projeto.etapas[0] if projeto.etapas else None,
            )
            if not etapa:
                continue
            data_lanc = _para_data(linha[3] if len(linha) > 3 else None)
            nome_func = str(linha[5]).strip() if len(linha) > 5 and linha[5] else ""
            funcionario = db.session.scalar(db.select(Funcionario).filter_by(nome=nome_func)) if nome_func else None
            if not data_lanc or not funcionario:
                continue
            ja_existe = db.session.scalar(
                db.select(LancamentoDiario).filter_by(
                    etapa_id=etapa.id, funcionario_id=funcionario.id, data=data_lanc
                )
            )
            if ja_existe:
                continue
            tipo = "TRABALHO"
            if len(linha) > 6 and linha[6]:
                tipo = "FOLGA"
            if len(linha) > 7 and linha[7]:
                tipo = "FOLGA_DF"
            if len(linha) > 8 and linha[8]:
                tipo = "FERIADO_TRABALHADO"
            realizada = True
            if len(linha) > 2 and linha[2] is not None:
                realizada = _sim(linha[2]) or str(linha[2]).strip().upper() in ("X", "SIM")
            verba = linha[11] if len(linha) > 11 and isinstance(linha[11], (int, float)) else None
            db.session.add(
                LancamentoDiario(
                    etapa=etapa,
                    funcionario=funcionario,
                    data=data_lanc,
                    tipo=tipo,
                    tarefa=str(linha[1]).strip() if len(linha) > 1 and linha[1] else None,
                    realizada=realizada,
                    verba=verba,
                    observacao=str(linha[12]).strip() if len(linha) > 12 and linha[12] else None,
                    criado_por="importação",
                )
            )
            resumo["lancamentos"] += 1

    wb.close()
    db.session.add(
        Historico(
            usuario="sistema",
            acao="Importação",
            detalhe=f"{caminho.name}: {resumo['projetos']} projetos, "
            f"{resumo['etapas']} etapas, {resumo['lancamentos']} lançamentos",
        )
    )
    db.session.commit()
    return resumo


def nomes_abas(caminho: Path) -> list[str]:
    from openpyxl import load_workbook

    wb = load_workbook(caminho, read_only=True)
    nomes = list(wb.sheetnames)
    wb.close()
    return nomes


def inicializar_banco(pasta_planilha: Path | None) -> None:
    tem_usuario = db.session.scalar(db.select(db.func.count(Usuario.id)))
    if not tem_usuario:
        if pasta_planilha:
            planilhas = _planilhas_na_pasta(pasta_planilha)
            ferias = _escolher_planilha(planilhas, "feria") or (planilhas[0] if planilhas else None)
            if ferias:
                importar_planilha(ferias)

        if not db.session.scalar(db.select(db.func.count(Usuario.id))):
            admin = Usuario(login="admin", admin=True, trocar_senha=True)
            admin.definir_senha(os.environ.get("ADMIN_SENHA_INICIAL") or "admin")
            db.session.add(admin)
            db.session.commit()

    if pasta_planilha and not db.session.scalar(db.select(db.func.count(ProjetoProva.id))):
        provas = _escolher_planilha(_planilhas_na_pasta(pasta_planilha), "prova")
        if provas:
            importar_provas(provas)
