# -*- coding: utf-8 -*-
"""Testes do sistema de atualizacao: assinatura, manifesto, download, aplicacao, reversao.

Usa um servidor HTTP LOCAL de verdade (127.0.0.1) no lugar do GitHub. Nenhum teste fala com a
internet nem com a pasta de rede."""

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend import atualizador as A
from tools import atualizacao as T

REPO = "empresa/hghub-releases"
TAMANHO = 1_200_000


# ------------------------------------------------------------------------------- servidor falso
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        rota = self.server.rotas.get(self.path)
        if rota is None:
            self.send_response(404)
            self.end_headers()
            return
        if callable(rota):
            return rota(self)
        status, corpo = rota
        self.send_response(status)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


class Servidor:
    def __init__(self):
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.http.rotas = {}
        self.porta = self.http.server_address[1]
        self.base = f"http://127.0.0.1:{self.porta}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def parar(self):
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture()
def servidor():
    s = Servidor()
    yield s
    s.parar()


@pytest.fixture(scope="module")
def chave():
    return Ed25519PrivateKey.generate()


@pytest.fixture()
def pasta(tmp_path):
    return str(tmp_path / "local")


def cfg_de(servidor, chave, **extra):
    return A.ConfigAtualizacao(
        repo=REPO, chaves_publicas=(T.chave_publica_b64(chave),), base_url=servidor.base,
        hosts_permitidos=frozenset({"127.0.0.1"}), permitir_http_local=True, timeout=5.0, **extra)


def publicar(servidor, chave, versao="1.0.1", conteudo=None, adulterar_hash=False, tamanho_manifesto=None,
             assinar_com=None, nome=None):
    """Publica no servidor falso um release completo (manifesto + assinatura + instalador)."""
    conteudo = conteudo if conteudo is not None else os.urandom(TAMANHO)
    nome = nome or f"HGhub-Setup-{versao}.exe"
    sha = hashlib.sha256(conteudo).hexdigest()
    if adulterar_hash:
        sha = "0" * 64
    manifesto = {
        "esquema": 1, "produto": "hghub", "versao": versao, "publicado_em": "2026-11-10T12:00:00Z",
        "obrigatoria": False, "versao_minima_suportada": "1.0.0",
        "instalador": {"nome": nome, "url": f"{servidor.base}/{REPO}/releases/download/v{versao}/{nome}",
                       "tamanho": tamanho_manifesto or len(conteudo), "sha256": sha},
        "notas": ["Correcoes de estabilidade.", "Melhorias visuais."], "url_novidades": f"{servidor.base}/{REPO}/releases/tag/v{versao}",
    }
    bruto = T.serializar_manifesto(manifesto)
    assinatura = T.assinar_bytes(assinar_com or chave, bruto).encode()
    base = f"/{REPO}/releases/latest/download"
    servidor.http.rotas[f"{base}/update-manifest.json"] = (200, bruto)
    servidor.http.rotas[f"{base}/update-manifest.json.sig"] = (200, assinatura)
    servidor.http.rotas[f"/{REPO}/releases/download/v{versao}/{nome}"] = (200, conteudo)
    return conteudo, manifesto


def novo(servidor, chave, pasta, instalada="1.0.0", **kw):
    chamadas = []
    encerrou = []
    at = A.Atualizador(cfg_de(servidor, chave), versao_instalada=instalada, pasta=pasta,
                       executar=lambda caminho: chamadas.append(caminho), encerrar_app=lambda: encerrou.append(1), **kw)
    return at, chamadas, encerrou


# ------------------------------------------------------------------------------- versoes
def test_ordem_semantica():
    ordem = ["0.9.9", "1.0.0-rc.1", "1.0.0-rc.2", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]
    for menor, maior in zip(ordem, ordem[1:]):
        assert A.versao_mais_nova(maior, menor), (maior, menor)
        assert not A.versao_mais_nova(menor, maior), (menor, maior)
    assert not A.versao_mais_nova("1.0.0", "1.0.0")
    assert A.versao_mais_nova("v1.2.0", "1.1.9")
    assert A.versao_mais_nova("1.10.0", "1.9.0")          # compara numeros, nao texto


@pytest.mark.parametrize("ruim", ["", "1", "1.0", "a.b.c", "1.0.0.0", "1.0.x", None])
def test_versao_invalida(ruim):
    with pytest.raises(A.ErroAtualizacao):
        A.interpretar_versao(ruim)


# ------------------------------------------------------------------------------- assinatura
def test_assinatura_valida_e_adulterada(chave):
    dados = b'{"versao": "1.0.1"}'
    assinatura = T.assinar_bytes(chave, dados)
    pub = [T.chave_publica_b64(chave)]
    assert A.verificar_assinatura(dados, assinatura, pub)
    assert not A.verificar_assinatura(dados + b" ", assinatura, pub)                 # conteudo alterado
    assert not A.verificar_assinatura(dados, T.assinar_bytes(Ed25519PrivateKey.generate(), dados), pub)   # outra chave
    for lixo in ("", "nao-e-base64!!", "AAAA", None):
        assert not A.verificar_assinatura(dados, lixo, pub)


def test_rotacao_de_chaves(chave):
    nova = Ed25519PrivateKey.generate()
    dados = b"x"
    ambas = [T.chave_publica_b64(chave), T.chave_publica_b64(nova)]
    assert A.verificar_assinatura(dados, T.assinar_bytes(chave, dados), ambas)
    assert A.verificar_assinatura(dados, T.assinar_bytes(nova, dados), ambas)


# ------------------------------------------------------------------------------- verificar
def test_detecta_atualizacao_disponivel(servidor, chave, pasta):
    publicar(servidor, chave)
    at, _, _ = novo(servidor, chave, pasta)
    e = at.verificar(manual=True)
    assert e["fase"] == "disponivel" and e["versao_nova"] == "1.0.1" and e["versao_instalada"] == "1.0.0"
    assert e["notas"] == ["Correcoes de estabilidade.", "Melhorias visuais."] and e["erro"] is None


def test_sem_atualizacao_quando_igual_ou_mais_nova_instalada(servidor, chave, pasta):
    publicar(servidor, chave, versao="1.0.1")
    for instalada in ("1.0.1", "1.2.0"):                    # igual e DOWNGRADE: nunca oferece
        at, _, _ = novo(servidor, chave, pasta, instalada=instalada)
        assert at.verificar(manual=True)["fase"] == "atualizado"


def test_sem_internet(chave, pasta):
    s = Servidor()
    s.parar()                                               # porta fechada = sem rede
    at = A.Atualizador(cfg_de(s, chave), versao_instalada="1.0.0", pasta=pasta)
    e = at.verificar(manual=True)
    assert e["fase"] == "erro" and "conexao" in e["erro"].lower()
    # a verificacao AUTOMATICA falha em silencio: nao incomoda o usuario nem trava
    assert at.verificar(manual=False)["fase"] == "ocioso"


def test_release_inexistente_404(servidor, chave, pasta):
    at, _, _ = novo(servidor, chave, pasta)
    e = at.verificar(manual=True)
    assert e["fase"] == "erro" and "404" in e["erro"]


def test_manifesto_com_assinatura_invalida_e_recusado(servidor, chave, pasta):
    publicar(servidor, chave, assinar_com=Ed25519PrivateKey.generate())
    at, _, _ = novo(servidor, chave, pasta)
    e = at.verificar(manual=True)
    assert e["fase"] == "erro" and "assinatura" in e["erro"].lower() and e["versao_nova"] is None


def test_manifesto_adulterado_depois_de_assinado(servidor, chave, pasta):
    publicar(servidor, chave)
    rota = f"/{REPO}/releases/latest/download/update-manifest.json"
    status, bruto = servidor.http.rotas[rota]
    servidor.http.rotas[rota] = (status, bruto.replace(b"1.0.1", b"9.9.9"))
    at, _, _ = novo(servidor, chave, pasta)
    assert at.verificar(manual=True)["fase"] == "erro"


@pytest.mark.parametrize("mexer", [
    lambda m: m.update(produto="outro"),
    lambda m: m.update(esquema=2),
    lambda m: m["instalador"].update(nome="Malware.exe"),
    lambda m: m["instalador"].update(nome="HGhub-Setup-9.9.9.exe"),          # nome nao bate com a versao
    lambda m: m["instalador"].update(sha256="xyz"),
    lambda m: m["instalador"].update(tamanho=10),
    lambda m: m["instalador"].update(tamanho=10 ** 12),
    lambda m: m["instalador"].update(url="https://evil.example/HGhub-Setup-1.0.1.exe"),
    lambda m: m["instalador"].update(url="http://127.0.0.1:1/outro/repo/releases/download/v1.0.1/HGhub-Setup-1.0.1.exe"),
    lambda m: m.update(versao="1.0"),
    lambda m: m.pop("instalador"),
])
def test_manifesto_validado_mesmo_assinado(servidor, chave, pasta, mexer):
    """Mesmo com assinatura VALIDA, um manifesto incoerente nunca vira atualizacao."""
    _, manifesto = publicar(servidor, chave)
    mexer(manifesto)
    bruto = T.serializar_manifesto(manifesto)
    base = f"/{REPO}/releases/latest/download"
    servidor.http.rotas[f"{base}/update-manifest.json"] = (200, bruto)
    servidor.http.rotas[f"{base}/update-manifest.json.sig"] = (200, T.assinar_bytes(chave, bruto).encode())
    at, _, _ = novo(servidor, chave, pasta)
    e = at.verificar(manual=True)
    assert e["fase"] == "erro" and e["versao_nova"] is None


def test_resposta_gigante_e_ignorada(servidor, chave, pasta):
    servidor.http.rotas[f"/{REPO}/releases/latest/download/update-manifest.json"] = (200, b"x" * (A.LIMITE_MANIFESTO + 10))
    servidor.http.rotas[f"/{REPO}/releases/latest/download/update-manifest.json.sig"] = (200, b"x")
    at, _, _ = novo(servidor, chave, pasta)
    assert at.verificar(manual=True)["fase"] == "erro"


def test_redirecionamento_para_host_nao_permitido_e_bloqueado(servidor, chave, pasta):
    def redireciona(h):
        h.send_response(302)
        h.send_header("Location", "http://evil.invalid/update-manifest.json")
        h.end_headers()
    servidor.http.rotas[f"/{REPO}/releases/latest/download/update-manifest.json"] = redireciona
    at, _, _ = novo(servidor, chave, pasta)
    e = at.verificar(manual=True)
    assert e["fase"] == "erro" and "origem" in e["erro"].lower()


def test_config_recusa_http_e_hosts_estranhos():
    cfg = A.ConfigAtualizacao(repo=REPO, chaves_publicas=("x",))
    cfg.validar_url("https://github.com/a/b/releases/latest/download/update-manifest.json")
    cfg.validar_url("https://objects.githubusercontent.com/abc")
    for url in ("http://github.com/a", "https://github.com.evil.com/a", "https://evil.com/github.com",
                "https://user:senha@github.com/a", "https://github.com:8443/a", "ftp://github.com/a", "file:///c:/x"):
        with pytest.raises(A.ErroAtualizacao):
            cfg.validar_url(url)


# ------------------------------------------------------------------------------- download
def baixar_ate_o_fim(at):
    assert at.iniciar_download()
    at.aguardar_download(30)
    return at.estado()


def test_download_completo_com_progresso(servidor, chave, pasta):
    conteudo, manifesto = publicar(servidor, chave)
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    e = baixar_ate_o_fim(at)
    assert e["fase"] == "pronta" and e["progresso"] == 100 and e["bytes"] == len(conteudo)
    caminho = os.path.join(pasta, "updates", "1.0.1", "HGhub-Setup-1.0.1.exe")
    assert hashlib.sha256(open(caminho, "rb").read()).hexdigest() == manifesto["instalador"]["sha256"]
    assert not os.path.exists(caminho + ".part")
    # verificar de novo reconhece o arquivo ja baixado e valido (nao baixa outra vez)
    at2, _, _ = novo(servidor, chave, pasta)
    assert at2.verificar(manual=True)["fase"] == "pronta"


def test_download_corrompido_e_descartado(servidor, chave, pasta):
    conteudo, _ = publicar(servidor, chave)
    servidor.http.rotas[f"/{REPO}/releases/download/v1.0.1/HGhub-Setup-1.0.1.exe"] = (200, b"\x00" + conteudo[1:])
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    e = baixar_ate_o_fim(at)
    assert e["fase"] == "erro" and "corrompido" in e["erro"].lower()
    pasta_versao = os.path.join(pasta, "updates", "1.0.1")
    assert not any(os.path.exists(os.path.join(pasta_versao, n)) for n in os.listdir(pasta_versao) if "Setup" in n)


def test_download_truncado(servidor, chave, pasta):
    conteudo, _ = publicar(servidor, chave)

    def trunca(h):
        h.send_response(200)
        h.send_header("Content-Length", str(len(conteudo)))
        h.end_headers()
        h.wfile.write(conteudo[: len(conteudo) // 2])      # promete tudo, entrega metade e fecha
        h.wfile.flush()
        h.close_connection = True
    servidor.http.rotas[f"/{REPO}/releases/download/v1.0.1/HGhub-Setup-1.0.1.exe"] = trunca
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    e = baixar_ate_o_fim(at)
    assert e["fase"] == "erro" and e["progresso"] < 100
    assert not os.path.exists(os.path.join(pasta, "updates", "1.0.1", "HGhub-Setup-1.0.1.exe"))


def test_download_incompleto_sem_content_length(servidor, chave, pasta):
    """Servidor que nao declara o tamanho e fecha no meio: so a conferencia de TAMANHO pega (a rede nao acusa erro)."""
    conteudo, _ = publicar(servidor, chave)

    def metade_sem_tamanho(h):
        h.send_response(200)
        h.end_headers()                                       # sem Content-Length
        h.wfile.write(conteudo[: len(conteudo) // 2])
        h.wfile.flush()
        h.close_connection = True
    servidor.http.rotas[f"/{REPO}/releases/download/v1.0.1/HGhub-Setup-1.0.1.exe"] = metade_sem_tamanho
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    e = baixar_ate_o_fim(at)
    assert e["fase"] == "erro" and "incompleto" in e["erro"].lower()
    assert not os.path.exists(os.path.join(pasta, "updates", "1.0.1", "HGhub-Setup-1.0.1.exe"))


def test_nome_do_instalador_precisa_ser_o_da_versao(servidor, chave, pasta):
    """Manifesto assinado e coerente em tudo, exceto que o instalador e de OUTRA versao: recusado."""
    _, manifesto = publicar(servidor, chave)
    manifesto["instalador"]["nome"] = "HGhub-Setup-9.9.9.exe"
    manifesto["instalador"]["url"] = f"{servidor.base}/{REPO}/releases/download/v1.0.1/HGhub-Setup-9.9.9.exe"
    bruto = T.serializar_manifesto(manifesto)
    with pytest.raises(A.ErroAtualizacao, match="nao corresponde a versao"):
        A.interpretar_manifesto(bruto, T.assinar_bytes(chave, bruto), cfg_de(servidor, chave))


def test_download_maior_que_o_declarado(servidor, chave, pasta):
    conteudo, _ = publicar(servidor, chave)
    servidor.http.rotas[f"/{REPO}/releases/download/v1.0.1/HGhub-Setup-1.0.1.exe"] = (200, conteudo + b"EXTRA" * 1000)
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    assert baixar_ate_o_fim(at)["fase"] == "erro"


def test_tentar_de_novo_apos_falha(servidor, chave, pasta):
    conteudo, _ = publicar(servidor, chave)
    rota = f"/{REPO}/releases/download/v1.0.1/HGhub-Setup-1.0.1.exe"
    servidor.http.rotas[rota] = (200, b"lixo" * 400_000)
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    assert baixar_ate_o_fim(at)["fase"] == "erro"
    servidor.http.rotas[rota] = (200, conteudo)
    assert baixar_ate_o_fim(at)["fase"] == "pronta"


# ------------------------------------------------------------------------------- aplicar
def test_aplicar_dispara_instalador_e_encerra_o_app(servidor, chave, pasta):
    publicar(servidor, chave)
    at, chamadas, encerrou = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    assert at.aplicar() is True
    assert chamadas == [os.path.join(pasta, "updates", "1.0.1", "HGhub-Setup-1.0.1.exe")] and encerrou == [1]
    estado = json.load(open(os.path.join(pasta, "update_state.json")))
    assert estado["status"] == "aplicando" and estado["versao_anterior"] == "1.0.0" and estado["versao_nova"] == "1.0.1"


def test_aplicar_sem_download_e_recusado(servidor, chave, pasta):
    publicar(servidor, chave)
    at, chamadas, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    with pytest.raises(A.ErroAtualizacao):
        at.aplicar()
    assert chamadas == []


def test_arquivo_trocado_depois_do_download_nao_e_executado(servidor, chave, pasta):
    publicar(servidor, chave)
    at, chamadas, encerrou = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    caminho = os.path.join(pasta, "updates", "1.0.1", "HGhub-Setup-1.0.1.exe")
    with open(caminho, "r+b") as f:                           # alguem troca o arquivo na pasta do usuario
        f.write(b"MALWARE")
    with pytest.raises(A.ErroAtualizacao):
        at.aplicar()
    assert chamadas == [] and encerrou == [] and at.estado()["fase"] == "disponivel"


def test_falha_ao_iniciar_instalador_mantem_o_app_aberto(servidor, chave, pasta):
    publicar(servidor, chave)

    def quebra(_):
        raise OSError("bloqueado pelo antivirus")
    encerrou = []
    at = A.Atualizador(cfg_de(servidor, chave), versao_instalada="1.0.0", pasta=pasta, executar=quebra,
                       encerrar_app=lambda: encerrou.append(1))
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    with pytest.raises(A.ErroAtualizacao):
        at.aplicar()
    assert encerrou == [] and at.estado()["fase"] == "pronta"          # continua aberto e pode tentar de novo


# ------------------------------------------------------------------------------- reversao
def _instalar_copia_atual(pasta, versao, conteudo):
    """Estado de um PC que ja tem a `versao` instalada e o instalador dela guardado como 'atual'."""
    os.makedirs(os.path.join(pasta, "installers"), exist_ok=True)
    with open(os.path.join(pasta, "installers", "HGhub-Setup-atual.exe"), "wb") as f:
        f.write(conteudo)
    A._escrever_json_atomico(os.path.join(pasta, "installers", "estado.json"), {
        "atual": {"versao": versao, "arquivo": "HGhub-Setup-atual.exe", "sha256": hashlib.sha256(conteudo).hexdigest()}})


def _simular_instalacao(pasta, conteudo_instalador):
    """O que o instalador real faz: deixa uma copia de si mesmo em installers/HGhub-Setup-novo.exe."""
    os.makedirs(os.path.join(pasta, "installers"), exist_ok=True)
    with open(os.path.join(pasta, "installers", "HGhub-Setup-novo.exe"), "wb") as f:
        f.write(conteudo_instalador)


def test_atualizar_guarda_o_instalador_anterior_e_permite_restaurar(servidor, chave, pasta):
    antigo = os.urandom(TAMANHO)
    _instalar_copia_atual(pasta, "1.0.0", antigo)
    conteudo_novo, _ = publicar(servidor, chave)
    at, chamadas, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    at.aplicar()
    # o instalador roda (fora do teste) e deposita a copia de si; o app novo abre e faz a rotacao
    _simular_instalacao(pasta, conteudo_novo)
    nova = A.Atualizador(cfg_de(servidor, chave), versao_instalada="1.0.1", pasta=pasta,
                         executar=lambda caminho: chamadas.append(caminho), encerrar_app=lambda: None)
    assert nova.registrar_inicio_do_app() == "ok"
    est = json.load(open(os.path.join(pasta, "installers", "estado.json")))
    assert est["anterior"]["versao"] == "1.0.0" and est["atual"]["versao"] == "1.0.1"
    assert not os.path.exists(os.path.join(pasta, "installers", "HGhub-Setup-novo.exe"))
    assert nova.pode_restaurar_versao_anterior()
    # o usuario pede para voltar: roda uma COPIA do instalador antigo, fora da pasta de instaladores
    chamadas.clear()
    assert nova.restaurar_versao_anterior() is True
    assert chamadas == [os.path.join(pasta, "updates", "reversao", "HGhub-Setup-1.0.0.exe")]
    assert open(chamadas[0], "rb").read() == antigo
    assert json.load(open(os.path.join(pasta, "update_state.json")))["status"] == "revertendo"


def test_reinstalar_o_mesmo_instalador_nao_perde_a_versao_anterior(servidor, chave, pasta):
    antigo, novo_inst = os.urandom(TAMANHO), os.urandom(TAMANHO)
    _instalar_copia_atual(pasta, "1.0.0", antigo)
    _simular_instalacao(pasta, novo_inst)
    a = A.Atualizador(cfg_de(servidor, chave), versao_instalada="1.0.1", pasta=pasta)
    assert a.registrar_instalador_recem_instalado() is True
    _simular_instalacao(pasta, novo_inst)                               # o usuario roda o MESMO instalador de novo
    assert a.registrar_instalador_recem_instalado() is False
    est = json.load(open(os.path.join(pasta, "installers", "estado.json")))
    assert est["anterior"]["versao"] == "1.0.0" and est["atual"]["versao"] == "1.0.1"


def test_primeira_instalacao_registra_o_instalador_como_atual(servidor, chave, pasta):
    conteudo = os.urandom(TAMANHO)
    _simular_instalacao(pasta, conteudo)
    a = A.Atualizador(cfg_de(servidor, chave), versao_instalada="1.0.0", pasta=pasta)
    assert a.registrar_inicio_do_app() == "ok"
    est = json.load(open(os.path.join(pasta, "installers", "estado.json")))
    assert est["atual"]["sha256"] == hashlib.sha256(conteudo).hexdigest() and "anterior" not in est
    assert not a.pode_restaurar_versao_anterior()


def test_restaurar_recusa_copia_corrompida(servidor, chave, pasta):
    _instalar_copia_atual(pasta, "1.0.0", os.urandom(TAMANHO))
    conteudo_novo, _ = publicar(servidor, chave)
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    at.aplicar()
    _simular_instalacao(pasta, conteudo_novo)
    at2, chamadas, _ = novo(servidor, chave, pasta, instalada="1.0.1")
    at2.registrar_inicio_do_app()
    with open(os.path.join(pasta, "installers", "HGhub-Setup-anterior.exe"), "r+b") as f:
        f.write(b"CORROMPIDO")
    with pytest.raises(A.ErroAtualizacao):
        at2.restaurar_versao_anterior()
    assert chamadas == []


def test_restaurar_sem_versao_anterior(servidor, chave, pasta):
    at, chamadas, _ = novo(servidor, chave, pasta)
    assert not at.pode_restaurar_versao_anterior()
    with pytest.raises(A.ErroAtualizacao):
        at.restaurar_versao_anterior()


def test_reversao_automatica_quando_a_versao_nova_nao_sobe(servidor, chave, pasta):
    _instalar_copia_atual(pasta, "1.0.0", os.urandom(TAMANHO))
    publicar(servidor, chave)
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    at.aplicar()
    _simular_instalacao(pasta, os.urandom(TAMANHO))
    # a versao 1.0.1 abre e "cai" varias vezes sem nunca se confirmar
    nova = lambda: A.Atualizador(cfg_de(servidor, chave), versao_instalada="1.0.1", pasta=pasta)  # noqa: E731
    resultados = [nova().registrar_inicio_do_app() for _ in range(A.FALHAS_ATE_REVERTER + 1)]
    assert resultados[:-1] == ["ok"] * A.FALHAS_ATE_REVERTER and resultados[-1] == "reverter"


def test_versao_nova_que_se_confirma_nao_reverte(servidor, chave, pasta):
    _instalar_copia_atual(pasta, "1.0.0", os.urandom(TAMANHO))
    publicar(servidor, chave)
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    at.aplicar()
    nova = A.Atualizador(cfg_de(servidor, chave), versao_instalada="1.0.1", pasta=pasta)
    assert nova.registrar_inicio_do_app() == "ok"
    assert nova.novidade_pos_atualizacao() == {"de": "1.0.0", "para": "1.0.1"}
    assert nova.novidade_pos_atualizacao() is None                     # so mostra uma vez
    assert nova.confirmar_saude() is True
    for _ in range(10):
        assert nova.registrar_inicio_do_app() == "ok"                  # confirmada: nunca mais reverte


def test_instalador_que_falhou_mantem_a_versao_antiga(servidor, chave, pasta):
    publicar(servidor, chave)
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    at.aplicar()
    # o instalador nao concluiu: o app abre de novo na 1.0.0
    antiga = A.Atualizador(cfg_de(servidor, chave), versao_instalada="1.0.0", pasta=pasta)
    assert antiga.registrar_inicio_do_app() == "ok"
    assert json.load(open(os.path.join(pasta, "update_state.json")))["status"] == "falhou_instalacao"


# ------------------------------------------------------------------------------- preferencias
def test_intervalo_entre_verificacoes_e_lembrar_mais_tarde(servidor, chave, pasta):
    publicar(servidor, chave)
    at, _, _ = novo(servidor, chave, pasta)
    assert at.deve_verificar_agora()                                   # nunca verificou
    at.verificar()
    assert not at.deve_verificar_agora()                               # acabou de verificar
    at._salvar_config_local(ultima_verificacao="2020-01-01T00:00:00+00:00")
    assert at.deve_verificar_agora()                                   # passou do intervalo
    at.lembrar_mais_tarde(24)
    assert not at.deve_verificar_agora()                               # adiado
    at._salvar_config_local(adiado_ate="2020-01-01T00:00:00+00:00")
    assert at.deve_verificar_agora()


def test_atualizador_desligado_sem_repositorio_configurado(pasta, monkeypatch):
    monkeypatch.setattr(A.versao, "REPO_RELEASES", "")
    assert A.config_padrao() is None
    at = A.Atualizador(None, pasta=pasta)
    e = at.verificar(manual=True)
    assert e["fase"] == "desligado" and e["configurado"] is False and not at.deve_verificar_agora()
    monkeypatch.setattr(A.versao, "REPO_RELEASES", "dono/repo")
    assert A.config_padrao().repo == "dono/repo"
    monkeypatch.setattr(A.versao, "REPO_RELEASES", "../../etc")
    with pytest.raises(A.ErroAtualizacao):
        A.config_padrao()


def test_nada_e_gravado_fora_da_pasta_local(servidor, chave, pasta, tmp_path):
    """Dados do usuario nunca sao tocados: o atualizador so escreve dentro da sua pasta local."""
    dados = tmp_path / "dados"
    dados.mkdir()
    (dados / "dados_sistema_hg.json").write_text('{"intacto": true}', encoding="utf-8")
    antes = {p.name: p.read_bytes() for p in dados.iterdir()}
    publicar(servidor, chave)
    at, _, _ = novo(servidor, chave, pasta)
    at.verificar(manual=True)
    baixar_ate_o_fim(at)
    at.aplicar()
    assert {p.name: p.read_bytes() for p in dados.iterdir()} == antes
    assert os.path.isdir(pasta)


def test_ferramentas_de_publicacao_geram_manifesto_aceito_pelo_app(servidor, chave, pasta, tmp_path):
    """A mesma ferramenta usada no CI produz um manifesto que o app aceita (e que so a chave certa assina)."""
    instalador = tmp_path / "HGhub-Setup-1.0.1.exe"
    instalador.write_bytes(os.urandom(TAMANHO))
    manifesto = T.montar_manifesto("1.0.1", instalador, REPO, ["Novidade A"])
    manifesto["instalador"]["url"] = f"{servidor.base}/{REPO}/releases/download/v1.0.1/HGhub-Setup-1.0.1.exe"
    bruto = T.serializar_manifesto(manifesto)
    cfg = cfg_de(servidor, chave)
    m = A.interpretar_manifesto(bruto, T.assinar_bytes(chave, bruto), cfg)
    assert m.versao == "1.0.1" and m.sha256 == manifesto["instalador"]["sha256"] and m.notas == ["Novidade A"]
    with pytest.raises(SystemExit):
        T.montar_manifesto("1.0.1", tmp_path / "Outro.exe", REPO, [])
