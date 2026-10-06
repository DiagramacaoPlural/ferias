"""Carga inicial do banco a partir da planilha original (Férias_Final.xlsm)."""

from datetime import date, datetime
from pathlib import Path

from .models import STATUS_APROVADA, STATUS_PROGRAMADA, Ferias, Funcionario, Historico, Usuario, db


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


def inicializar_banco(pasta_planilha: Path | None) -> None:
    if db.session.scalar(db.select(db.func.count(Usuario.id))):
        return

    if pasta_planilha:
        planilhas = sorted(pasta_planilha.glob("*.xlsm")) + sorted(pasta_planilha.glob("*.xlsx"))
        planilhas = [p for p in planilhas if not p.name.startswith("~$")]
        if planilhas:
            importar_planilha(planilhas[0])

    if not db.session.scalar(db.select(db.func.count(Usuario.id))):
        admin = Usuario(login="admin", admin=True, trocar_senha=True)
        admin.definir_senha("admin")
        db.session.add(admin)
        db.session.commit()
