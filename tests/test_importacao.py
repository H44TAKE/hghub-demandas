# -*- coding: utf-8 -*-
"""Importar planilha de exames: previa, importacao, sem duplicar, permissoes. Bases descartaveis."""

import base64
import importlib
import io
import sys
from datetime import datetime

import openpyxl
import pytest


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    dados = tmp_path / "dados"
    dados.mkdir()
    monkeypatch.setenv("HGHUB_ARQUIVO_DADOS", str(dados / "dados_sistema_hg.json"))
    monkeypatch.setenv("HGHUB_ADMIN_SENHA", "SenhaDeTeste#1")
    monkeypatch.setenv("APPDATA", str(tmp_path / "ap"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    for nome in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[nome]
    app = importlib.import_module("backend").create_app()
    c = app.test_client()
    r = c.post("/api/login", json={"email": "admin@redehg.com", "senha": "SenhaDeTeste#1"})
    return c, {"Authorization": "Bearer " + r.get_json()["token"]}


def planilha(abas=None):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    cab = ["REGIÃO", "NOME", "EXAME", "DATA EXAME", "VENCIMENTO", "SITUAÇÃO", "COBRAR RESULTADO", "EXAME LIBERADO", "CLINICA"]
    linhas = [
        ["MG", "ALVARO ROGERIO LOUZADA DE SOUZA", "SEMESTRAIS", None, datetime(2026, 11, 21), "NÃO AGENDADO", None, None, None],
        ["MG", "LUIS ALBERTO NASCIMENTO", "ANUAIS", None, datetime(2026, 11, 30), "NÃO AGENDADO", None, None, None],
        ["BA", "MARCOS ROGERIO ANDRADE LEAL", "IPIRANGA + SEMESTRAIS", datetime(2026, 11, 10), datetime(2026, 11, 30), "REALIZADO", None, None, "CLINICA X"],
        ["MG", "JOEL FELICIO BARBARA", "SEMESTRAIS", None, datetime(2026, 11, 15), "NÃO AGENDADO", None, None, None],
        ["MG", "DIEGO RICARDO DE OLIVEIRA", "IPIRANGA", None, datetime(2026, 11, 30), "NÃO AGENDADO", None, None, None],
    ]
    for nome in (abas or ["NOVEMBRO2026", "DEZEMBRO2026"]):
        ws = wb.create_sheet(nome)
        ws.append([None, "VENCIMENTO EXAMES " + nome])
        ws.append(cab)
        for l in (linhas if nome == "NOVEMBRO2026" else linhas[:1]):
            ws.append(l)
        ws.append([None, "*revisado em 26/10/26"])
    buf = io.BytesIO()
    wb.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


def montar_rh(c, h):
    """Pasta R.H com o projeto de Minas Gerais e as demandas que ja existem hoje (titulos so com 2 nomes)."""
    c.post("/api/pasta", json={"nome": "R.H", "setor": "R.H"}, headers=h)
    pasta = c.get("/api/dados", headers=h).get_json()["pastas"][0]
    c.post("/api/projeto", json={"nome": "EXAMES PERIODICOS MINAS GERAIS", "id_pasta": pasta["id"]}, headers=h)
    pasta = c.get("/api/dados", headers=h).get_json()["pastas"][0]
    proj = pasta["projetos"][0]["id"]

    def tarefa(titulo, venc, exame, status="Pendente"):
        r = c.post("/api/tarefa", json={"id_projeto": proj, "titulo": titulo, "descricao": "", "status": status,
                                        "data_vencimento": venc, "etiqueta_nome": exame, "etiqueta_cor": "#ff0000"}, headers=h)
        assert r.status_code == 200, r.get_json()
    tarefa("LUIS ALBERTO", "2026-10-24", "SEMESTRAIS", "Concluída")        # ciclo antigo, concluido: NAO bloqueia
    tarefa("JOEL FELICIO", "2026-11-01", "SEMESTRAIS")                      # aberta, mesmo exame: duplicada
    tarefa("DIEGO RICARDO", "2026-11-30", "IPIRANGA", "Concluída")          # mesmo vencimento: duplicada
    return pasta["id"], proj


def test_aba_sugerida_e_leitura():
    from backend import importacao
    nomes = ["DEZEMBRO2026", "NOVEMBRO2026", "OUTUBRO2026"]
    assert importacao.aba_sugerida(nomes, datetime(2026, 11, 18)) == "NOVEMBRO2026"
    assert importacao.aba_sugerida(nomes, datetime(2027, 3, 1)) == "DEZEMBRO2026"     # sem a do mes: a mais recente
    linhas = importacao.ler_exames(importacao.decodificar_arquivo(planilha()), "NOVEMBRO2026")
    assert len(linhas) == 5 and linhas[0]["vencimento"] == datetime(2026, 11, 21)      # rodape "*revisado" ignorado
    assert importacao.titulo_da_demanda("ALVARO ROGERIO LOUZADA DE SOUZA") == "ALVARO ROGERIO"
    assert importacao.titulo_da_demanda("JUVANILSON DE BRITO QUEIROZ") == "JUVANILSON DE BRITO"
    assert importacao.mesma_pessoa("JUVANILSON DE BRITO", "JUVANILSON DE BRITO QUEIROZ")
    assert importacao.mesma_pessoa("ALVARO ROGÉRIO", "ALVARO ROGERIO LOUZADA DE SOUZA")
    assert not importacao.mesma_pessoa("DANIEL SOARES", "DANIEL PEREIRA DA SILVA")


def test_arquivo_invalido_e_recusado(ambiente):
    c, h = ambiente
    for lixo in ("", "naoebase64!!", base64.b64encode(b"texto qualquer").decode()):
        r = c.post("/api/importar/planilha", json={"arquivo": lixo}, headers=h)
        assert r.status_code == 400 and r.get_json()["success"] is False


def test_previa_nao_grava_e_importacao_nao_duplica(ambiente):
    c, h = ambiente
    pasta_id, proj_mg = montar_rh(c, h)
    antes = c.get("/api/dados", headers=h).get_json()
    arquivo = planilha()

    previa = c.post("/api/importar/planilha", json={"arquivo": arquivo, "aba": "NOVEMBRO2026", "pasta_id": pasta_id}, headers=h).get_json()
    assert previa["resumo"] == {"lidas": 5, "novas": 3, "existentes": 2}
    estados = {l["nome"].split()[0]: l["estado"] for l in previa["linhas"]}
    assert estados == {"ALVARO": "nova", "LUIS": "nova", "MARCOS": "nova", "JOEL": "existente", "DIEGO": "existente"}
    reg = {r["regiao"]: r for r in previa["regioes"]}
    assert reg["MG"]["projeto_id"] == proj_mg and reg["BA"]["criar"] is True
    assert c.get("/api/dados", headers=h).get_json() == antes                       # a previa NAO gravou nada

    r = c.post("/api/importar/planilha", json={"arquivo": arquivo, "aba": "NOVEMBRO2026", "pasta_id": pasta_id,
                                               "confirmar": True, "responsavel_id": ""}, headers=h).get_json()
    assert r["criadas"] == 3 and r["existentes"] == 2 and r["projetos_criados"] == ["EXAMES PERIODICOS BAHIA"]

    pasta = c.get("/api/dados", headers=h).get_json()["pastas"][0]
    projetos = {p["nome"]: p for p in pasta["projetos"]}
    mg = {t["titulo"]: t for t in projetos["EXAMES PERIODICOS MINAS GERAIS"]["tarefas"]}
    assert mg["ALVARO ROGERIO"]["status"]["nome"] == "Pendente" and mg["ALVARO ROGERIO"]["data_vencimento"] == "2026-11-21"
    assert mg["ALVARO ROGERIO"]["etiqueta"]["nome"] == "SEMESTRAIS" and mg["ALVARO ROGERIO"]["prioridade"] in ("Alta", "Média", "Baixa")
    assert "ALVARO ROGERIO LOUZADA DE SOUZA" in mg["ALVARO ROGERIO"]["descricao"]
    ba = projetos["EXAMES PERIODICOS BAHIA"]["tarefas"][0]
    assert ba["status"]["nome"] == "Concluída" and ba["data_conclusao"].startswith("2026-11-10")       # REALIZADO
    assert sum(1 for t in mg.values() if t["titulo"].startswith("JOEL")) == 1                     # nao duplicou
    assert sum(1 for t in mg.values() if t["titulo"].startswith("DIEGO")) == 1

    # importar de novo: nada novo (idempotente)
    r2 = c.post("/api/importar/planilha", json={"arquivo": arquivo, "aba": "NOVEMBRO2026", "pasta_id": pasta_id, "confirmar": True}, headers=h).get_json()
    assert r2["criadas"] == 0 and r2["existentes"] == 5


def test_importar_exige_pasta_e_respeita_acesso(ambiente):
    c, h = ambiente
    pasta_id, _ = montar_rh(c, h)
    arquivo = planilha()
    assert c.post("/api/importar/planilha", json={"arquivo": arquivo, "confirmar": True}, headers=h).status_code == 400
    c.post("/api/cadastro", json={"nome": "Ana", "email": "ana@x.com", "senha": "abcd1234", "setor": "Frota"})
    t = c.post("/api/login", json={"email": "ana@x.com", "senha": "abcd1234"}).get_json()["token"]
    ana = {"Authorization": "Bearer " + t}
    r = c.post("/api/importar/planilha", json={"arquivo": arquivo, "pasta_id": pasta_id, "confirmar": True}, headers=ana)
    assert r.status_code == 403                                                    # setor Frota nao mexe na pasta R.H
    assert c.post("/api/importar/planilha", json={"arquivo": arquivo}).status_code == 401
