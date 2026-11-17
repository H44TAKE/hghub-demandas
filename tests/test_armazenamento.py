# -*- coding: utf-8 -*-
"""Armazenamento novo (HGhub-dados): migracao do legado, legado SOMENTE LEITURA, usuarios.json editavel,
tokens so em hash, reimportacao e bloqueio sem rede. Tudo em bases descartaveis (nunca Z:/Y:)."""

import hashlib
import importlib
import json
import sys

import pytest

SENHA_LEGADA = "SenhaAntiga#7"


def _hash_arquivos(pasta):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(pasta.iterdir()) if p.is_file()}


def _tarefa(tid, titulo):
    return {"id": tid, "titulo": titulo, "descricao": "", "status": {"nome": "Pendente", "cor": "#F5B800"},
            "responsavel_id": None, "prioridade": "Média", "comentarios": [], "anexos": [], "historico": []}


def montar_legado(dados_dir, utils):
    """Cria arquivos no formato ANTIGO (como a producao), com 2 contas, 1 pasta/projeto/tarefa e permissoes."""
    h = utils.hash_senha(SENHA_LEGADA)
    usuarios = [
        {"id": "u1", "nome": "Ana Mestre", "email": "ana@redehg.com", "senha": h, "setores": [], "conta_mestre": True,
         "campo_futuro": "preservar", "notificacoes": [{"id": "n1", "texto": "oi", "lida": False}], "favoritos": ["t1"]},
        {"id": "u2", "nome": "Beto Frota", "email": "beto@redehg.com", "senha": h, "setores": ["Frota"],
         "conta_mestre": False, "codigo_recuperacao": "123456"},
    ]
    dados = {
        "usuarios": usuarios,
        "pastas": [{"id": "p1", "nome": "Frota", "cor_fundo": "#0D3B8C", "setor": "Frota",
                    "projetos": [{"id": "pr1", "nome": "Revisoes", "tarefas": [_tarefa("t1", "Trocar oleo")]}]}],
        "anotacoes": {"k": "texto"}, "sessoes": {"TOKEN_ANTIGO_EM_TEXTO": {"usuario_id": "u1",
                                                                          "criada_em": "2099-01-01T00:00:00"}},
        "cidades": ["Goiânia"], "setores": ["Geral", "Frota"], "etiquetas_salvas": [],
    }
    (dados_dir / "dados_sistema_hg.json").write_text(json.dumps(dados), encoding="utf-8")
    (dados_dir / "usuarios.json").write_text(json.dumps({"usuarios": usuarios}), encoding="utf-8")
    # permissoes_contas.json manda: Beto passou a ter 2 setores
    (dados_dir / "permissoes_contas.json").write_text(json.dumps({"contas": {
        "ana@redehg.com": {"id": "u1", "conta_mestre": True, "setores": []},
        "beto@redehg.com": {"id": "u2", "conta_mestre": False, "setores": ["Frota", "Manutenção"]}}}), encoding="utf-8")
    (dados_dir / "config.json").write_text(json.dumps({"smtp": {"email": "x@y", "senha_app": "S"}}), encoding="utf-8")


@pytest.fixture()
def iniciar(tmp_path, monkeypatch):
    dados_dir = tmp_path / "dados"
    dados_dir.mkdir()

    def _iniciar(legado=True):
        monkeypatch.setenv("HGHUB_ARQUIVO_DADOS", str(dados_dir / "dados_sistema_hg.json"))
        monkeypatch.setenv("HGHUB_ADMIN_SENHA", "SenhaDeTeste#1")
        monkeypatch.setenv("APPDATA", str(tmp_path / "ap"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
        for nome in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
            del sys.modules[nome]
        backend = importlib.import_module("backend")      # so carrega utils; o sistema nasce no create_app
        if legado:
            montar_legado(dados_dir, importlib.import_module("backend.utils"))
        _iniciar.antes = _hash_arquivos(dados_dir)             # foto dos arquivos antigos ANTES do app abrir
        app = backend.create_app()
        return app, app.test_client(), dados_dir, dados_dir / "HGhub-dados"
    return _iniciar


def entrar(c, email="ana@redehg.com", senha=SENHA_LEGADA):
    r = c.post("/api/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": "Bearer " + r.get_json()["token"]}


def test_migra_o_legado_sem_tocar_nos_arquivos_antigos(iniciar):
    app, c, dados_dir, novo = iniciar()
    antes = iniciar.antes
    h = entrar(c)
    # mesmas contas/senhas, permissoes do permissoes_contas.json valendo (Beto com 2 setores)
    dados = c.get("/api/dados", headers=h).get_json()
    contas = {u["email"]: u for u in dados["usuarios"]}
    assert set(contas) == {"ana@redehg.com", "beto@redehg.com"}
    assert contas["beto@redehg.com"]["setores"] == ["Frota", "Manutenção"] and not contas["beto@redehg.com"]["conta_mestre"]
    assert any(t["titulo"] == "Trocar oleo" for p in dados["pastas"] for pr in p["projetos"] for t in pr["tarefas"])
    # arquivos novos, um por assunto
    assert {"usuarios.json", "demandas.json", "catalogos.json", "preferencias_usuarios.json", "sessoes.json",
            "configuracao.json", "_migracao.json", "LEIA-ME.txt"} <= {p.name for p in novo.iterdir()}
    # grava varias coisas no sistema novo...
    assert c.post("/api/etiquetas", json={"nome": "Urgente"}, headers=h).status_code == 200
    assert c.post("/api/cidades/adicionar", json={"cidade": "Anápolis"}, headers=h).status_code == 200
    # ...e os arquivos ANTIGOS continuam byte a byte iguais (somente leitura)
    assert _hash_arquivos(dados_dir) == antes


def test_usuarios_json_tem_hash_e_preserva_campos_desconhecidos(iniciar):
    _, c, _, novo = iniciar()
    h = entrar(c)
    c.post("/api/etiquetas", json={"nome": "X"}, headers=h)
    usuarios = json.loads((novo / "usuarios.json").read_text(encoding="utf-8"))["usuarios"]
    ana = next(u for u in usuarios if u["email"] == "ana@redehg.com")
    assert "senha" not in ana and ana["senha_hash"].count("$") == 1 and SENHA_LEGADA not in json.dumps(usuarios)
    assert ana["campo_futuro"] == "preservar"                         # campo desconhecido sobrevive a regravacoes
    assert "notificacoes" not in ana and "codigo_recuperacao" not in ana   # isso mora em outros arquivos
    prefs = json.loads((novo / "preferencias_usuarios.json").read_text(encoding="utf-8"))["usuarios"]
    assert prefs["u1"]["favoritos"] == ["t1"]


def test_sessao_do_legado_segue_valida_e_so_o_hash_fica_no_arquivo(iniciar):
    _, c, _, novo = iniciar()
    sess = json.loads((novo / "sessoes.json").read_text(encoding="utf-8"))["sessoes"]
    assert "TOKEN_ANTIGO_EM_TEXTO" not in sess and len(next(iter(sess))) == 64
    assert c.post("/api/sessao/validar", headers={"Authorization": "Bearer TOKEN_ANTIGO_EM_TEXTO"}).status_code == 200
    h = entrar(c)
    token = h["Authorization"].split()[1]
    assert token not in (novo / "sessoes.json").read_text(encoding="utf-8")


def test_codigo_de_recuperacao_so_em_hash(iniciar, monkeypatch):
    _, c, _, novo = iniciar()
    enviados = []
    monkeypatch.setattr("backend.routes.enviar_email_recuperacao", lambda e, cod: enviados.append(cod) or (True, "ok"))
    r = c.post("/api/recuperacao/solicitar", json={"email": "beto@redehg.com"})
    assert r.status_code == 200
    import time
    time.sleep(0.3)
    texto = (novo / "sessoes.json").read_text(encoding="utf-8")
    assert enviados and enviados[0] not in texto and "codigo_hash" in texto
    r = c.post("/api/recuperacao/redefinir", json={"email": "beto@redehg.com", "codigo": enviados[0], "nova_senha": "OutraSenha#9", "confirmar_senha": "OutraSenha#9"})
    assert r.status_code == 200, r.get_json()
    entrar(c, "beto@redehg.com", "OutraSenha#9")


def test_trocar_senha_pelo_campo_nova_senha(iniciar):
    _, c, _, novo = iniciar()
    h_beto = entrar(c, "beto@redehg.com")
    arq = novo / "usuarios.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    beto = next(u for u in d["usuarios"] if u["email"] == "beto@redehg.com")
    beto["nova_senha"] = "SenhaNova#55"
    arq.write_text(json.dumps(d, indent=2), encoding="utf-8")
    # proxima requisicao aplica: a senha antiga morre, a nova vale, a sessao antiga cai, o campo some
    assert c.post("/api/login", json={"email": "beto@redehg.com", "senha": SENHA_LEGADA}).status_code == 401
    entrar(c, "beto@redehg.com", "SenhaNova#55")
    assert c.post("/api/sessao/validar", headers=h_beto).status_code == 401
    d = json.loads(arq.read_text(encoding="utf-8"))
    assert next(u for u in d["usuarios"] if u["email"] == "beto@redehg.com")["nova_senha"] == ""
    assert "SenhaNova#55" not in arq.read_text(encoding="utf-8")


def test_nova_senha_curta_e_ignorada(iniciar):
    _, c, _, novo = iniciar()
    arq = novo / "usuarios.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    next(u for u in d["usuarios"] if u["email"] == "beto@redehg.com")["nova_senha"] = "123"
    arq.write_text(json.dumps(d), encoding="utf-8")
    entrar(c, "beto@redehg.com", SENHA_LEGADA)                         # senha antiga continua valendo


def test_email_editado_a_mao_vale(iniciar):
    _, c, _, novo = iniciar()
    arq = novo / "usuarios.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    next(u for u in d["usuarios"] if u["email"] == "beto@redehg.com")["email"] = "beto.novo@redehg.com"
    arq.write_text(json.dumps(d), encoding="utf-8")
    entrar(c, "beto.novo@redehg.com", SENHA_LEGADA)


def test_json_de_usuarios_quebrado_bloqueia_sem_perder_nada(iniciar):
    _, c, _, novo = iniciar()
    arq = novo / "usuarios.json"
    bom = arq.read_bytes()
    arq.write_text(bom.decode("utf-8")[:50], encoding="utf-8")        # alguem editou e deixou invalido
    quebrado = arq.read_bytes()
    r = c.post("/api/login", json={"email": "ana@redehg.com", "senha": SENHA_LEGADA})
    assert r.status_code == 503 and r.get_json()["tipo"] == "base_indisponivel"
    assert c.get("/api/cidades").status_code in (401, 503)
    assert arq.read_bytes() == quebrado                               # nada foi sobrescrito
    arq.write_bytes(bom)
    entrar(c)


def test_formato_mais_novo_e_recusado(iniciar):
    _, c, _, novo = iniciar()
    arq = novo / "demandas.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    d["_formato"] = 99
    arq.write_text(json.dumps(d), encoding="utf-8")
    assert c.post("/api/login", json={"email": "ana@redehg.com", "senha": SENHA_LEGADA}).status_code == 503


def test_arquivo_faltando_nao_recria_base_vazia(iniciar):
    _, c, _, novo = iniciar()
    (novo / "demandas.json").unlink()
    assert c.post("/api/login", json={"email": "ana@redehg.com", "senha": SENHA_LEGADA}).status_code == 503
    assert not (novo / "demandas.json").exists()


def test_reimportar_soma_o_que_um_pc_antigo_criou(iniciar):
    _, c, dados_dir, novo = iniciar()
    h = entrar(c)
    assert c.get("/api/armazenamento", headers=h).get_json()["legado_alterado"] == []
    # um PC com versao antiga continua gravando no arquivo antigo: nova tarefa + nova pasta
    arq = dados_dir / "dados_sistema_hg.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    d["pastas"][0]["projetos"][0]["tarefas"].append(_tarefa("t9", "Criada no PC antigo"))
    d["pastas"].append({"id": "p2", "nome": "RH", "cor_fundo": "#000", "setor": "RH", "projetos": []})
    arq.write_text(json.dumps(d), encoding="utf-8")
    import backend.armazenamento as arm
    arm._cache_legado.update(em=0.0, valor=None)
    assert c.get("/api/armazenamento", headers=h).get_json()["legado_alterado"] == ["dados_sistema_hg.json"]
    seco = c.post("/api/armazenamento/reimportar", json={"aplicar": False}, headers=h).get_json()["relatorio"]
    assert seco["tarefas"] == 1 and seco["pastas"] == 1 and seco["aplicado"] is False
    antes_legado = arq.read_bytes()
    r = c.post("/api/armazenamento/reimportar", json={"aplicar": True}, headers=h).get_json()["relatorio"]
    assert r["aplicado"] is True and list((novo / "copias").iterdir())      # copia de seguranca feita antes
    dados = c.get("/api/dados", headers=h).get_json()
    titulos = [t["titulo"] for p in dados["pastas"] for pr in p["projetos"] for t in pr["tarefas"]]
    assert titulos.count("Criada no PC antigo") == 1 and any(p["nome"] == "RH" for p in dados["pastas"])
    again = c.post("/api/armazenamento/reimportar", json={"aplicar": True}, headers=h).get_json()["relatorio"]
    assert again["tarefas"] == 0 and again["pastas"] == 0                  # idempotente: nao duplica
    assert arq.read_bytes() == antes_legado                                 # o legado segue intocado
    assert c.get("/api/armazenamento", headers=h).get_json()["legado_alterado"] == []


def test_so_conta_mestre_ve_armazenamento(iniciar):
    _, c, _, _ = iniciar()
    h = entrar(c, "beto@redehg.com")
    assert c.get("/api/armazenamento", headers=h).status_code == 403
    assert c.post("/api/armazenamento/reimportar", json={"aplicar": True}, headers=h).status_code == 403


def test_foto_e_anexo_antigos_continuam_servidos(iniciar):
    _, c, dados_dir, novo = iniciar(legado=False)
    entrar(c, "admin@redehg.com", "SenhaDeTeste#1")
    (dados_dir / "anexos_tarefas").mkdir()
    (dados_dir / "anexos_tarefas" / "velho.txt").write_text("antigo", encoding="utf-8")
    r = c.get("/anexos_tarefas/velho.txt")
    assert r.status_code == 200 and r.data == b"antigo"
    assert list((dados_dir / "anexos_tarefas").iterdir())[0].read_text(encoding="utf-8") == "antigo"
    assert c.get("/anexos_tarefas/nao_existe.txt").status_code == 404


def test_base_nova_cria_so_a_pasta_nova(iniciar):
    _, c, dados_dir, novo = iniciar(legado=False)
    entrar(c, "admin@redehg.com", "SenhaDeTeste#1")
    nomes = {p.name for p in dados_dir.iterdir()}
    assert nomes == {"HGhub-dados"}                                    # nada de arquivo solto no nivel antigo


def test_sem_rede_nao_cria_base_local_vazia(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("HGHUB_ARQUIVO_DADOS", str(tmp_path / "d" / "dados_sistema_hg.json"))
    utils = importlib.import_module("backend.utils")
    monkeypatch.delenv("HGHUB_ARQUIVO_DADOS")
    monkeypatch.setattr(utils, "EMPACOTADO", True)
    monkeypatch.setattr(utils, "_arquivo_dados_na_rede", lambda: None)       # nunca toca a rede real
    monkeypatch.setattr(utils, "PASTA_LOCAL_APP", str(tmp_path / "local"))
    assert utils._sem_rede_bloqueado() is True
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "config_local.json").write_text('{"permitir_base_local": true}', encoding="utf-8")
    assert utils._sem_rede_bloqueado() is False                               # opt-in explicito
    monkeypatch.setattr(utils, "EMPACOTADO", False)
    (tmp_path / "local" / "config_local.json").unlink()
    assert utils._sem_rede_bloqueado() is False                               # desenvolvimento nao e bloqueado


def test_trava_so_e_removida_pelo_dono(tmp_path):
    from backend.utils import bloqueio_arquivo
    base = str(tmp_path / "x")
    with bloqueio_arquivo(base):
        (tmp_path / "x.lock").write_text("OUTRO-PC|1|abc", encoding="utf-8")   # alguem "roubou" a trava
    assert (tmp_path / "x.lock").exists()                                      # nao apagamos a trava dos outros
    (tmp_path / "x.lock").unlink()
    with bloqueio_arquivo(base):
        assert (tmp_path / "x.lock").exists()
    assert not (tmp_path / "x.lock").exists()


def test_cadastro_em_setor_existente_fica_pendente_ate_a_conta_mestre_liberar(iniciar):
    _, c, _, novo = iniciar()
    r = c.post("/api/cadastro", json={"nome": "Intruso", "email": "x@x.com", "senha": "abcd1234", "setor": "Frota"})
    assert r.status_code == 200 and r.get_json()["pendente"] is True
    h = entrar(c, "x@x.com", "abcd1234")
    dados = c.get("/api/dados", headers=h).get_json()
    assert dados["pastas"] == []                                       # nao ve nada do setor Frota
    admin = entrar(c)
    contas = {u["email"]: u for u in c.get("/api/dados", headers=admin).get_json()["usuarios"]}
    assert contas["x@x.com"]["setor_solicitado"] == "Frota" and contas["x@x.com"]["setores"] == []
    # setor ainda sem pasta: liberado na hora
    r = c.post("/api/cadastro", json={"nome": "Novo", "email": "y@x.com", "senha": "abcd1234", "setor": "Juridico"})
    assert r.get_json().get("pendente") is None
