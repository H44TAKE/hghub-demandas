# -*- coding: utf-8 -*-
"""Teste de PONTA A PONTA com os instaladores REAIS (nao e coletado pelo pytest).

    python tests/e2e_instalado.py <HGhub-Setup-1.0.0.exe> <HGhub-Setup-1.0.1.exe> [chave_privada.pem]

Instala a versao antiga numa pasta temporaria, abre o app instalado, atualiza para a nova por um
"GitHub" local (127.0.0.1, assinado com a chave de producao), confere que os dados continuam
intactos, volta a versao anterior, simula falta de internet, assinatura falsa e pacote adulterado e
por fim desinstala. Os DADOS do sistema ficam numa pasta temporaria (nunca a base de producao).

Efeitos na maquina de teste: grava/limpa arquivos de estado em %LOCALAPPDATA%/HGhub (installers,
updates, update_state.json, config_local.json, atualizacao_teste.json) e a entrada de desinstalacao
do usuario (removida pelo desinstalador no final). A porta 5000 precisa estar livre."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from tools import atualizacao as T  # noqa: E402

REPO = "H44TAKE/hghub-demandas"
PORTA_MOCK = 8765
URL_APP = "http://127.0.0.1:5000"
SENHA = "SenhaE2E#1"

setup_antigo, setup_novo = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
chave_pem = Path(sys.argv[3]) if len(sys.argv) > 3 else Path.home() / ".hghub-signing" / "update_ed25519_private.pem"
chave = T.carregar_chave_privada(str(chave_pem))

TMP = Path(tempfile.mkdtemp(prefix="hghub_e2e_"))
APP_DIR = TMP / "app"
DADOS = TMP / "dados" / "dados_sistema_hg.json"
LOCAL = Path(os.environ["LOCALAPPDATA"]) / "HGhub"
ok = total = 0
processo = None


def confere(cond, desc):
    global ok, total
    total += 1
    ok += bool(cond)
    print(f"  {'ok  ' if cond else 'FALHOU'} {desc}", flush=True)
    return bool(cond)


# --------------------------------------------------------------------------- "GitHub" local
class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        corpo = self.server.rotas.get(self.path)
        if corpo is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


class Mock:
    def __init__(self):
        self.http = None
        self.rotas = {}

    def subir(self):
        self.http = ThreadingHTTPServer(("127.0.0.1", PORTA_MOCK), _H)
        self.http.rotas = self.rotas
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def parar(self):
        if self.http:
            self.http.shutdown()
            self.http.server_close()
            self.http = None

    def publicar(self, versao, instalador_bytes, assinar_com=None, adulterar_instalador=False):
        nome = f"HGhub-Setup-{versao}.exe"
        m = {
            "esquema": 1, "produto": "hghub", "versao": versao, "publicado_em": "2026-11-12T12:00:00Z",
            "obrigatoria": False, "versao_minima_suportada": "1.0.0",
            "instalador": {"nome": nome, "url": f"http://127.0.0.1:{PORTA_MOCK}/{REPO}/releases/download/v{versao}/{nome}",
                           "tamanho": len(instalador_bytes), "sha256": hashlib.sha256(instalador_bytes).hexdigest()},
            "notas": ["Correcoes de estabilidade.", "Melhorias visuais."],
            "url_novidades": f"http://127.0.0.1:{PORTA_MOCK}/{REPO}/releases/tag/v{versao}",
        }
        bruto = T.serializar_manifesto(m)
        base = f"/{REPO}/releases/latest/download"
        self.rotas[f"{base}/update-manifest.json"] = bruto
        self.rotas[f"{base}/update-manifest.json.sig"] = T.assinar_bytes(assinar_com or chave, bruto).encode()
        servido = (b"\x00" + instalador_bytes[1:]) if adulterar_instalador else instalador_bytes
        self.rotas[f"/{REPO}/releases/download/v{versao}/{nome}"] = servido


# --------------------------------------------------------------------------- app instalado
def api(metodo, caminho, corpo=None, token=None, timeout=30):
    req = urllib.request.Request(URL_APP + caminho, method=metodo,
                                 data=json.dumps(corpo).encode() if corpo is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ConnectionError):
        return 0, {}


def esperar(cond, limite=120, passo=1.0):
    fim = time.time() + limite
    while time.time() < fim:
        try:
            r = cond()
            if r:
                return r
        except Exception:
            pass
        time.sleep(passo)
    return None


def porta_livre(porta):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", porta)) != 0


def env_app():
    return {**os.environ, "HGHUB_ARQUIVO_DADOS": str(DADOS), "HGHUB_ADMIN_SENHA": SENHA, "HGHUB_ATUALIZACAO_TESTE": "1",
            "PYTHONUTF8": "1"}


def instalar(setup, extra=()):
    r = subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={APP_DIR}", "/NOICONS",
                        "/TASKS=", f"/LOG={TMP / 'instalacao.log'}", *extra], env=env_app(), timeout=600)
    return r.returncode == 0


def abrir_app():
    global processo
    processo = subprocess.Popen([str(APP_DIR / "HGhub.exe")], env=env_app(), cwd=str(APP_DIR))
    return esperar(lambda: api("POST", "/api/sessao/validar")[0] in (200, 401), 90)


def login():
    st, d = api("POST", "/api/login", {"email": "admin@redehg.com", "senha": SENHA})
    return d.get("token") if st == 200 else None


def versao_rodando(token):
    st, d = api("GET", "/api/versao", token=token)
    return d.get("versao") if st == 200 else None


def encerrar_app():
    ps = ("Get-Process HGhub -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '" + str(APP_DIR) + "*' } | Stop-Process -Force")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    esperar(lambda: porta_livre(5000), 20, 0.5)


def estado(token):
    return api("GET", "/api/atualizacao/estado", token=token)[1]


def esperar_fase(token, fases, limite=90):
    return esperar(lambda: (e := estado(token)) and e.get("fase") in fases and e, limite, 0.5)


def limpar_local():
    for nome in ("installers", "updates"):
        shutil.rmtree(LOCAL / nome, ignore_errors=True)
    for nome in ("update_state.json", "config_local.json", "atualizacao_teste.json"):
        try:
            (LOCAL / nome).unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- roteiro
def main():
    if not porta_livre(5000):
        sys.exit("A porta 5000 esta ocupada (HGhub aberto?). Feche e rode de novo.")
    limpar_local()
    LOCAL.mkdir(parents=True, exist_ok=True)
    DADOS.parent.mkdir(parents=True)
    mock = Mock()
    novo_bytes = setup_novo.read_bytes()
    mock.publicar(setup_novo.stem.split("-")[-1], novo_bytes)
    mock.subir()
    (LOCAL / "atualizacao_teste.json").write_text(json.dumps(
        {"base_url": f"http://127.0.0.1:{PORTA_MOCK}", "hosts": ["127.0.0.1"], "repo": REPO}), encoding="utf-8")
    v_antiga = setup_antigo.stem.split("-")[-1]
    v_nova = setup_novo.stem.split("-")[-1]
    try:
        print(f"[1] instalar a {v_antiga} em pasta temporaria e abrir")
        confere(instalar(setup_antigo), "instalador silencioso concluiu (sem pedir administrador)")
        confere((APP_DIR / "HGhub.exe").exists() and (APP_DIR / "unins000.exe").exists(), "HGhub.exe e desinstalador instalados")
        confere(not any((APP_DIR / n).exists() for n in ("credenciais.json", "config.json", "dados_sistema_hg.json", "usuarios.json")),
                "nenhum segredo/dado dentro da instalacao")
        confere(abrir_app(), "o app instalado abre e o servidor responde")
        token = login()
        confere(token, "login funciona (admin criado na base temporaria)")
        confere(versao_rodando(token) == v_antiga, f"versao em execucao = {v_antiga}")
        confere(api("POST", "/api/etiquetas", {"nome": "E2E-Etiqueta", "cor": "#123456"}, token)[0] == 200, "dado do usuario criado (etiqueta)")
        confere(api("POST", "/api/cadastro", {"nome": "Ana E2E", "email": "ana.e2e@x.com", "senha": "abcd1234", "setor": "Frota"})[0] == 200, "conta de usuario criada")
        e = esperar_fase(token, ("disponivel", "pronta"), 60)
        confere(e and e["versao_nova"] == v_nova, f"o app percebe sozinho a versao {v_nova} (verificacao em segundo plano)")
        antes = DADOS.read_bytes()

        print(f"[2] atualizar {v_antiga} -> {v_nova} pelo proprio app")
        st, _ = api("POST", "/api/atualizacao/baixar", token=token)
        confere(st == 200, "baixar iniciado")
        e = esperar_fase(token, ("pronta", "erro"), 120)
        confere(e and e["fase"] == "pronta" and e["progresso"] == 100, "download concluido com SHA-256 conferido")
        st, _ = api("POST", "/api/atualizacao/aplicar", token=token)
        confere(st == 200, "aplicar aceito")
        confere(esperar(lambda: porta_livre(5000), 30, 0.5), "o app fechou para liberar os arquivos")
        confere(esperar(lambda: api("POST", "/api/sessao/validar")[0] in (200, 401), 180), "o instalador reabriu o app sozinho")
        token = login()
        confere(versao_rodando(token) == v_nova, f"versao em execucao = {v_nova}")
        confere(api("GET", "/api/etiquetas", token=token)[1].get("etiquetas") and any(
            x["nome"] == "E2E-Etiqueta" for x in api("GET", "/api/etiquetas", token=token)[1]["etiquetas"]), "etiqueta criada ANTES continua la")
        confere(api("POST", "/api/login", {"email": "ana.e2e@x.com", "senha": "abcd1234"})[0] == 200, "conta criada ANTES continua funcionando")
        confere(b"ana.e2e@x.com" in DADOS.read_bytes() and len(DADOS.read_bytes()) >= len(antes) - 2000, "arquivo de dados preservado")
        est = json.loads((LOCAL / "installers" / "estado.json").read_text(encoding="utf-8")) if (LOCAL / "installers" / "estado.json").exists() else {}
        confere(est.get("atual", {}).get("versao") == v_nova and est.get("anterior", {}).get("versao") == v_antiga,
                f"instalador anterior ({v_antiga}) guardado para reversao")
        e = estado(token)
        confere(e.get("pode_restaurar") is True, "tela oferece 'voltar a versao anterior'")

        print("[3] voltar a versao anterior")
        st, _ = api("POST", "/api/atualizacao/restaurar", token=token)
        confere(st == 200, "restaurar aceito")
        confere(esperar(lambda: porta_livre(5000), 30, 0.5), "o app fechou")
        confere(esperar(lambda: api("POST", "/api/sessao/validar")[0] in (200, 401), 180), "reabriu sozinho")
        token = login()
        confere(versao_rodando(token) == v_antiga, f"voltou para a {v_antiga}")
        confere(api("POST", "/api/login", {"email": "ana.e2e@x.com", "senha": "abcd1234"})[0] == 200, "dados intactos depois da reversao")

        print("[4] sem internet")
        mock.parar()
        api("POST", "/api/atualizacao/verificar", token=token)
        e = esperar_fase(token, ("erro", "atualizado", "disponivel"), 40)
        confere(e and e["fase"] == "erro" and "conex" in (e["erro"] or "").lower(), f"falha de rede vira aviso claro ({(e or {}).get('erro')})")
        confere(versao_rodando(token) == v_antiga and api("GET", "/api/dados", token=token)[0] == 200, "o app continua funcionando normalmente")

        print("[5] manifesto com assinatura FALSA")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        mock.publicar(v_nova, novo_bytes, assinar_com=Ed25519PrivateKey.generate())
        mock.subir()
        api("POST", "/api/atualizacao/verificar", token=token)
        e = esperar_fase(token, ("erro", "disponivel", "atualizado"), 40)
        confere(e and e["fase"] == "erro" and "assinatura" in (e["erro"] or "").lower() and not e["versao_nova"],
                "assinatura invalida: atualizacao recusada")

        print("[6] instalador ADULTERADO no servidor (manifesto legitimo)")
        mock.publicar(v_nova, novo_bytes, adulterar_instalador=True)
        shutil.rmtree(LOCAL / "updates", ignore_errors=True)
        api("POST", "/api/atualizacao/verificar", token=token)
        e = esperar_fase(token, ("disponivel", "pronta", "erro"), 40)
        confere(e and e["fase"] == "disponivel", "manifesto legitimo reconhecido")
        api("POST", "/api/atualizacao/baixar", token=token)
        e = esperar_fase(token, ("erro", "pronta"), 90)
        confere(e and e["fase"] == "erro" and "corromp" in (e["erro"] or "").lower(), "pacote adulterado e descartado (SHA-256)")
        confere(api("POST", "/api/atualizacao/aplicar", token=token)[0] == 409 and versao_rodando(token) == v_antiga, "nada e instalado")
        mock.parar()

        print("[7] desinstalar")
        encerrar_app()
        r = subprocess.run([str(APP_DIR / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=300)
        time.sleep(3)
        confere(r.returncode == 0 and not (APP_DIR / "HGhub.exe").exists(), "desinstalador removeu o programa")
        confere(DADOS.exists() and b"ana.e2e@x.com" in DADOS.read_bytes(), "os DADOS do usuario continuam depois de desinstalar")
    finally:
        mock.parar()
        encerrar_app()
        limpar_local()
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{ok}/{total} verificacoes ok")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
