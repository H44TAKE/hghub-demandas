# -*- coding: utf-8 -*-
"""Regressoes das falhas de seguranca e de perda de dados achadas na auditoria.
Cada teste usa uma base DESCARTAVEL exclusiva (nunca Z:/Y:)."""

import importlib
import json
import sys
import time

import pytest


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    """App Flask novo sobre uma base vazia e isolada (recarrega os modulos para reler os caminhos)."""
    dados = tmp_path / "dados"
    dados.mkdir()
    monkeypatch.setenv("HGHUB_ARQUIVO_DADOS", str(dados / "dados_sistema_hg.json"))
    monkeypatch.setenv("HGHUB_ADMIN_SENHA", "SenhaDeTeste#1")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    for nome in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[nome]
    backend = importlib.import_module("backend")
    app = backend.create_app()
    from backend import routes
    return app, app.test_client(), dados, routes


def login(cliente, email="admin@redehg.com", senha="SenhaDeTeste#1"):
    r = cliente.post("/api/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": "Bearer " + r.get_json()["token"]}


def criar_conta(cliente, nome, email, setor):
    r = cliente.post("/api/cadastro", json={"nome": nome, "email": email, "senha": "abcd1234", "setor": setor})
    assert r.status_code == 200, r.get_json()
    return login(cliente, email, "abcd1234")


def test_admin_inicial_nao_usa_a_senha_fixa_123456(tmp_path, monkeypatch):
    monkeypatch.delenv("HGHUB_ADMIN_SENHA", raising=False)
    monkeypatch.setenv("HGHUB_ARQUIVO_DADOS", str(tmp_path / "d" / "dados_sistema_hg.json"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "ap"))
    for nome in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[nome]
    app = importlib.import_module("backend").create_app()
    c = app.test_client()
    assert c.post("/api/login", json={"email": "admin@redehg.com", "senha": "123456"}).status_code == 401
    arq = tmp_path / "d" / "SENHA_INICIAL_ADMIN.txt"
    assert arq.exists()
    senha = [l for l in arq.read_text(encoding="utf-8").splitlines() if l.startswith("Senha: ")][0][7:]
    assert len(senha) >= 12 and c.post("/api/login", json={"email": "admin@redehg.com", "senha": senha}).status_code == 200


@pytest.mark.parametrize("metodo,rota", [
    ("get", "/api/dados"), ("get", "/api/dashboard"), ("get", "/api/exportar"), ("get", "/api/cidades"),
    ("get", "/api/etiquetas"), ("post", "/api/setores/adicionar"), ("post", "/api/etiquetas"),
    ("post", "/api/cidades/adicionar"), ("post", "/api/anotacao"), ("post", "/api/perfil/atualizar"),
    ("get", "/api/usuario/x/notificacoes"), ("post", "/api/usuario/x/notificacoes/lidas"),
    ("post", "/api/usuario/x/favoritos"), ("post", "/api/pasta"), ("post", "/api/tarefa"),
])
def test_rotas_privadas_exigem_login(ambiente, metodo, rota):
    _, c, _, _ = ambiente
    r = getattr(c, metodo)(rota, json={})
    assert r.status_code == 401, (rota, r.status_code)


def test_rotas_publicas_continuam_abertas(ambiente):
    _, c, _, _ = ambiente
    assert c.get("/api/setores").status_code == 200
    assert c.post("/api/recuperacao/solicitar", json={"email": "x@y.com"}).status_code == 200
    assert c.post("/api/sessao/validar").status_code == 401        # responde (negando), nao e bloqueada
    assert c.get("/").status_code == 200


def test_exportar_com_token_respeita_o_setor(ambiente):
    _, c, _, _ = ambiente
    admin = login(c)
    c.post("/api/pasta", json={"nome": "Pasta RH", "setor": "RH"}, headers=admin)
    frota = criar_conta(c, "Ana", "ana@x.com", "Frota")
    r = c.get("/api/exportar", headers=frota)
    assert r.status_code == 200 and r.data[:2] == b"PK"            # xlsx de verdade
    assert c.get("/api/exportar").status_code == 401


def test_perfil_e_notificacoes_so_do_proprio_dono(ambiente):
    _, c, _, _ = ambiente
    ana = criar_conta(c, "Ana", "ana@x.com", "Frota")
    bia = criar_conta(c, "Bia", "bia@x.com", "Frota")
    id_ana = c.post("/api/sessao/validar", headers=ana).get_json()["usuario"]["id"]
    assert c.get(f"/api/usuario/{id_ana}/notificacoes", headers=ana).status_code == 200
    assert c.get(f"/api/usuario/{id_ana}/notificacoes", headers=bia).status_code == 403
    assert c.post("/api/perfil/atualizar", json={"usuario_id": id_ana, "nome_exibicao": "Hack"}, headers=bia).status_code == 403
    assert c.post(f"/api/usuario/{id_ana}/favoritos", json={"item_id": "1"}, headers=bia).status_code == 403
    assert c.post("/api/perfil/atualizar", json={"usuario_id": id_ana, "nome_exibicao": "Ana S."}, headers=ana).status_code == 200
    admin = login(c)                                                # Conta Mestre pode
    assert c.get(f"/api/usuario/{id_ana}/notificacoes", headers=admin).status_code == 200


def test_dados_nao_vazam_para_anonimo(ambiente):
    _, c, _, _ = ambiente
    r = c.get("/api/dados")
    assert r.status_code == 401 and b"admin@redehg.com" not in r.data


def test_anexo_html_e_baixado_nunca_executado(ambiente):
    app, c, dados, routes = ambiente
    pasta = dados / "anexos_tarefas"
    pasta.mkdir(exist_ok=True)
    (pasta / "mal.html").write_text("<script>roubar(localStorage)</script>", encoding="utf-8")
    (pasta / "mal.svg").write_text("<svg onload='x()'/>", encoding="utf-8")
    (pasta / "foto.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    for nome in ("mal.html", "mal.svg"):
        r = c.get("/anexos_tarefas/" + nome)
        assert "attachment" in r.headers.get("Content-Disposition", ""), nome
        assert "sandbox" in r.headers["Content-Security-Policy"]
    r = c.get("/anexos_tarefas/foto.png")
    assert "attachment" not in r.headers.get("Content-Disposition", "")       # imagem segue abrindo na tela
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert c.get("/anexos_tarefas/../dados_sistema_hg.json").status_code == 404


def test_login_bloqueia_forca_bruta(ambiente):
    _, c, _, _ = ambiente
    for _ in range(5):
        assert c.post("/api/login", json={"email": "admin@redehg.com", "senha": "errada"}).status_code == 401
    r = c.post("/api/login", json={"email": "admin@redehg.com", "senha": "SenhaDeTeste#1"})
    assert r.status_code == 429 and "Muitas tentativas" in r.get_json()["message"]
    assert c.post("/api/login", json={"email": "outra@pessoa.com", "senha": "x"}).status_code == 401   # so a conta alvo trava


def test_codigo_de_recuperacao_bloqueia_forca_bruta(ambiente):
    _, c, _, routes = ambiente
    c.post("/api/recuperacao/solicitar", json={"email": "admin@redehg.com"})
    time.sleep(0.3)
    from backend.models import sistema
    sistema.recarregar()
    certo = sistema.buscar_usuario_por_email("admin@redehg.com").codigo_recuperacao
    errado = "000000" if certo != "000000" else "000001"
    for _ in range(5):
        assert c.post("/api/recuperacao/validar", json={"email": "admin@redehg.com", "codigo": errado}).status_code == 400
    r = c.post("/api/recuperacao/validar", json={"email": "admin@redehg.com", "codigo": certo})
    assert r.status_code == 429                                     # mesmo o codigo CERTO fica bloqueado


def test_codigo_de_recuperacao_usa_secrets():
    import inspect
    from backend import utils
    fonte = inspect.getsource(utils.gerar_codigo_recuperacao)
    assert "secrets.randbelow" in fonte and "random.randint" not in fonte
    assert all(len(utils.gerar_codigo_recuperacao()) == 6 for _ in range(50))


def test_sessao_expira_depois_de_30_dias(ambiente):
    _, c, dados, _ = ambiente
    h = login(c)
    assert c.post("/api/sessao/validar", headers=h).status_code == 200
    arq = dados / "dados_sistema_hg.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    token = h["Authorization"].split()[1]
    d["sessoes"][token]["criada_em"] = "2020-01-01T00:00:00"
    arq.write_text(json.dumps(d), encoding="utf-8")
    assert c.post("/api/sessao/validar", headers=h).status_code == 401
    assert c.get("/api/dados", headers=h).status_code == 401


def test_base_ilegivel_nunca_e_sobrescrita(ambiente):
    app, c, dados, _ = ambiente
    login(c)
    arq = dados / "dados_sistema_hg.json"
    original = arq.read_text(encoding="utf-8")
    arq.write_text(original[: len(original) // 2], encoding="utf-8")       # arquivo truncado
    truncado = arq.read_bytes()
    r = c.post("/api/login", json={"email": "admin@redehg.com", "senha": "SenhaDeTeste#1"})
    assert r.status_code in (500, 503)
    assert arq.read_bytes() == truncado                                    # NADA foi gravado por cima
    arq.write_text(original, encoding="utf-8")                              # reparado: volta a funcionar
    assert c.post("/api/login", json={"email": "admin@redehg.com", "senha": "SenhaDeTeste#1"}).status_code == 200


def test_base_com_bom_e_lida(ambiente):
    app, c, dados, _ = ambiente
    login(c)
    arq = dados / "dados_sistema_hg.json"
    arq.write_bytes(b"\xef\xbb\xbf" + arq.read_bytes())
    assert c.post("/api/login", json={"email": "admin@redehg.com", "senha": "SenhaDeTeste#1"}).status_code == 200


def test_falha_de_gravacao_nao_responde_sucesso(ambiente, monkeypatch):
    app, c, dados, _ = ambiente
    h = login(c)
    antes = (dados / "dados_sistema_hg.json").read_bytes()

    def quebra(*a, **k):
        raise PermissionError("WinError 5")
    monkeypatch.setattr("backend.models.os.replace", quebra)
    monkeypatch.setattr("backend.models.time.sleep", lambda s: None)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    r = c.post("/api/etiquetas", json={"nome": "Urgente"}, headers=h)
    assert r.status_code == 500 and r.get_json()["success"] is False       # antes: "sucesso" e a alteracao sumia
    assert (dados / "dados_sistema_hg.json").read_bytes() == antes
    assert not list(dados.glob("*.tmp"))


def test_smtp_valida_certificado():
    import inspect
    from backend import utils
    assert "create_default_context" in inspect.getsource(utils.enviar_email_recuperacao)
