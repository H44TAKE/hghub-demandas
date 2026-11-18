# -*- coding: utf-8 -*-
"""1.2.0: aviso diario, tema por conta, 5a roda sem cabecalho de placa."""

import importlib
import sys
from datetime import datetime, timedelta

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
    return c, {"Authorization": "Bearer " + r.get_json()["token"]}, r.get_json()["usuario"]["id"]


def test_tema_lembrado_por_conta_e_por_pc(ambiente):
    c, h, uid = ambiente
    assert c.get("/api/tema-local").get_json()["tema"] is None
    assert c.post("/api/tema-local", json={"tema": "azul"}).status_code == 400
    assert c.post("/api/preferencias/tema", json={"tema": "light"}, headers=h).status_code == 200
    assert c.get("/api/tema-local").get_json()["tema"] == "light"                 # vale ate na tela de login
    me = c.post("/api/sessao/validar", headers=h).get_json()["usuario"]
    assert me["tema"] == "light"                                                  # e em qualquer PC em que a conta entrar
    c.post("/api/preferencias/tema", json={"tema": "dark"}, headers=h)
    assert c.post("/api/sessao/validar", headers=h).get_json()["usuario"]["tema"] == "dark"
    assert c.post("/api/preferencias/tema", json={"tema": "dark"}).status_code == 401


def test_aviso_semanal_um_email_por_pessoa_e_um_pc_por_semana(ambiente):
    c, h, uid = ambiente
    from backend import avisos
    from backend.models import sistema
    c.post("/api/pasta", json={"nome": "R.H", "setor": "R.H"}, headers=h)
    pasta = c.get("/api/dados", headers=h).get_json()["pastas"][0]
    c.post("/api/projeto", json={"nome": "Exames", "id_pasta": pasta["id"]}, headers=h)
    proj = c.get("/api/dados", headers=h).get_json()["pastas"][0]["projetos"][0]["id"]
    segunda = datetime(2026, 11, 16, 9, 0)
    for titulo, dias, status in [("Atrasada", -3, "Pendente"), ("Hoje", 0, "Pendente"), ("Em5dias", 5, "Pendente"),
                                 ("Longe", 12, "Pendente"), ("Feita", 0, "Concluída")]:
        r = c.post("/api/tarefa", json={"id_projeto": proj, "titulo": titulo, "status": status, "responsavel_id": uid,
                                        "data_vencimento": (segunda + timedelta(days=dias)).strftime("%Y-%m-%d")}, headers=h)
        assert r.status_code == 200
    enviados = []
    falso = lambda dest, assunto, corpo: enviados.append((dest, corpo)) or (True, "ok")
    assert avisos.enviar_avisos_da_semana(sistema, segunda.replace(hour=6), falso) is None     # antes das 8h de segunda
    assert avisos.enviar_avisos_da_semana(sistema, segunda, falso) == 1
    corpo = enviados[0][1]
    assert enviados[0][0] == "admin@redehg.com" and "Atrasada" in corpo and "Hoje" in corpo and "Em5dias" in corpo
    assert "Longe" not in corpo and "Feita" not in corpo
    assert avisos.enviar_avisos_da_semana(sistema, segunda + timedelta(days=2), falso) is None   # mesma semana: nao repete
    assert avisos.enviar_avisos_da_semana(sistema, segunda + timedelta(days=5), falso) is None   # sabado: nao envia
    assert avisos.enviar_avisos_da_semana(sistema, segunda + timedelta(days=7), falso) == 1      # proxima segunda: envia


def test_quinta_roda_sem_cabecalho_de_placa():
    import pandas as pd
    from backend import manutencao as m
    sem_cabecalho = pd.DataFrame({"Unnamed: 0": ["ABC-1D23", "XYZ-9K99"], "STATUS": ["OK", "ATRASADO"]})
    com_cabecalho = pd.DataFrame({"PLACA": ["ABC-1D23"], "STATUS": ["OK"]})
    assert m._coluna_da_placa(sem_cabecalho) == 0 and m._coluna_da_placa(com_cabecalho) == "PLACA"
    mapa = m._mapa_por_placa(sem_cabecalho, m._coluna_da_placa(sem_cabecalho), status="STATUS")
    assert set(mapa) == {"ABC-1D23", "XYZ-9K99"}                    # antes: {} (a 5a roda ficava vazia)


def test_exportar_removido_e_switch_travado():
    import os
    css = open(os.path.join(os.path.dirname(__file__), "..", "static", "css", "style.css"), encoding="utf-8").read()
    assert "html, body, .app, .main { overflow: clip; }" in css and ".switch { position: relative; }" in css
    html = open(os.path.join(os.path.dirname(__file__), "..", "templates", "index.html"), encoding="utf-8").read()
    assert "Exportar Excel" not in html and "export-setor" not in html
