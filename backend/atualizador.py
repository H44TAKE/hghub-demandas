# -*- coding: utf-8 -*-
"""Atualizacao automatica do HGhub pelo GitHub Releases.

Fluxo (nada disto trava a interface: tudo roda em thread propria):
  1. verificar     - baixa `update-manifest.json` e `update-manifest.json.sig` do ULTIMO release,
                     confere a ASSINATURA Ed25519, valida o conteudo e compara a versao.
  2. baixar        - baixa o instalador em streaming, com progresso, e confere TAMANHO e SHA-256
                     contra o manifesto assinado.
  3. aplicar       - reconfere o hash, guarda o instalador atual para o caso de reversao e
                     dispara o instalador em modo silencioso; o app se encerra para liberar os
                     arquivos e o instalador o reabre.
  4. confirmar     - na proxima abertura, a versao nova se declara saudavel; se ela nao conseguir
                     subir, o app volta sozinho a versao anterior (ver `registrar_inicio_do_app`).

SEGURANCA: HTTPS apenas, hosts em lista fechada (inclusive em cada redirecionamento), o JSON
da API nunca e confiado (so o manifesto ASSINADO vale), a URL do instalador tem de estar sob
o repositorio configurado e a tag da versao, nome e hash sao verificados, sem downgrade. Uma
resposta invalida, truncada ou de origem estranha nunca vira "atualizacao disponivel".

DADOS: nada aqui toca na pasta de dados do sistema. Estado, downloads, copias de instalador e
configuracao local ficam em %LOCALAPPDATA%/HGhub (fora da pasta de instalacao), por isso
sobrevivem a atualizacoes.
"""

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import versao

log = logging.getLogger("hghub")

HOSTS_GITHUB = frozenset({"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"})
PADRAO_VERSAO = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
PADRAO_INSTALADOR = re.compile(r"^HGhub-Setup-(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\.exe$")
PADRAO_SHA256 = re.compile(r"^[0-9a-f]{64}$")
LIMITE_MANIFESTO = 256 * 1024
TAMANHO_MIN_INSTALADOR = 1_000_000
TAMANHO_MAX_INSTALADOR = 1_500_000_000
FALHAS_ATE_REVERTER = 3
SEGUNDOS_PARA_CONFIRMAR = 90


class ErroAtualizacao(Exception):
    """Falha esperada do processo de atualizacao (a mensagem e mostrada ao usuario)."""


# --------------------------------------------------------------------------- versoes (semver)
def interpretar_versao(texto):
    """'1.2.3' ou 'v1.2.3-rc.1' -> (1, 2, 3, ('rc', 1)) comparavel; levanta ErroAtualizacao se invalida."""
    m = PADRAO_VERSAO.match((texto or "").strip().lstrip("vV"))
    if not m:
        raise ErroAtualizacao(f"Versao invalida: {texto!r}")
    prerelease = ()
    if m.group(4):
        prerelease = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(4).split("."))
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), prerelease


def _chave_ordem(v):
    maior, menor, patch, pre = v
    # sem pre-release e MAIOR que com pre-release (1.0.0 > 1.0.0-rc.1)
    return (maior, menor, patch, 1 if not pre else 0, pre)


def versao_mais_nova(candidata, instalada):
    """True se `candidata` e estritamente mais nova que `instalada` (nunca permite downgrade)."""
    return _chave_ordem(interpretar_versao(candidata)) > _chave_ordem(interpretar_versao(instalada))


# --------------------------------------------------------------------------- pastas locais
def pasta_local():
    """%LOCALAPPDATA%/HGhub: estado, downloads e copias do instalador (NUNCA dentro da instalacao)."""
    base = os.environ.get("HGHUB_DIR_LOCAL") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "HGhub")
    os.makedirs(base, exist_ok=True)
    return base


def _escrever_json_atomico(caminho, dados):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporario, caminho)


def _ler_json(caminho, padrao):
    try:
        with open(caminho, "r", encoding="utf-8-sig") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else padrao
    except (OSError, ValueError):
        return padrao


def sha256_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


# --------------------------------------------------------------------------- configuracao
@dataclass
class ConfigAtualizacao:
    repo: str
    chaves_publicas: tuple = field(default_factory=lambda: tuple(versao.CHAVES_PUBLICAS_ATUALIZACAO))
    base_url: str = "https://github.com"
    hosts_permitidos: frozenset = HOSTS_GITHUB
    permitir_http_local: bool = False   # SO para testes automatizados (127.0.0.1)
    timeout: float = 20.0
    intervalo_horas: float = 6.0

    @property
    def url_manifesto(self):
        return f"{self.base_url}/{self.repo}/releases/latest/download/update-manifest.json"

    @property
    def url_assinatura(self):
        return self.url_manifesto + ".sig"

    def validar_url(self, url):
        """Aceita so HTTPS (ou http://127.0.0.1 em teste) para hosts da lista fechada."""
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").lower()
        if p.username or p.password:
            raise ErroAtualizacao("URL de atualizacao com credenciais embutidas foi recusada.")
        if p.scheme == "https" and host in self.hosts_permitidos and p.port in (None, 443):
            return
        if self.permitir_http_local and p.scheme in ("http", "https") and host in self.hosts_permitidos:
            return
        raise ErroAtualizacao(f"Origem nao permitida para atualizacao: {host or url!r}")

    def prefixo_instalador(self, versao_nova):
        return f"{self.base_url}/{self.repo}/releases/download/v{versao_nova}/"


def config_padrao():
    """Config de producao a partir de backend/versao.py; None se o atualizador nao foi configurado."""
    repo = (versao.REPO_RELEASES or "").strip()
    if not repo:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ErroAtualizacao(f"REPO_RELEASES invalido: {repo!r}")
    cfg = ConfigAtualizacao(repo=repo)
    # Modo de teste (E2E com dois builds reais): aponta para um servidor local. So vale com a
    # variavel de ambiente explicita e MESMO ASSIM a assinatura Ed25519 continua obrigatoria.
    if os.environ.get("HGHUB_ATUALIZACAO_TESTE") == "1":
        arq = os.path.join(pasta_local(), "atualizacao_teste.json")
        teste = _ler_json(arq, {})
        if teste.get("base_url"):
            cfg.base_url = teste["base_url"].rstrip("/")
            cfg.hosts_permitidos = frozenset(teste.get("hosts", ["127.0.0.1"]))
            cfg.permitir_http_local = True
            if teste.get("chaves_publicas"):
                cfg.chaves_publicas = tuple(teste["chaves_publicas"])
            if teste.get("repo"):
                cfg.repo = teste["repo"]
    return cfg


# --------------------------------------------------------------------------- rede
class _RedirecionamentoSeguro(urllib.request.HTTPRedirectHandler):
    """Cada salto de redirecionamento tambem precisa passar na lista fechada de hosts."""

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.cfg.validar_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def abrir_url(cfg, url):
    cfg.validar_url(url)
    opener = urllib.request.build_opener(_RedirecionamentoSeguro(cfg))
    req = urllib.request.Request(url, headers={"User-Agent": f"HGhub/{versao.__version__}", "Accept": "*/*"})
    try:
        return opener.open(req, timeout=cfg.timeout)
    except urllib.error.HTTPError as erro:
        raise ErroAtualizacao(f"O servidor de atualizacao respondeu {erro.code}.") from erro
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroAtualizacao("Sem conexao com o servidor de atualizacao.") from erro


def _baixar_bytes(cfg, url, limite):
    with abrir_url(cfg, url) as resp:
        dados = resp.read(limite + 1)
    if len(dados) > limite:
        raise ErroAtualizacao("Resposta de atualizacao grande demais; ignorada.")
    return dados


# --------------------------------------------------------------------------- manifesto
def verificar_assinatura(dados, assinatura_b64, chaves_b64):
    try:
        assinatura = base64.b64decode((assinatura_b64 or "").strip(), validate=True)
    except Exception:
        return False
    for chave in chaves_b64:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(chave)).verify(assinatura, dados)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


@dataclass
class Manifesto:
    versao: str
    publicado_em: str
    nome: str
    url: str
    tamanho: int
    sha256: str
    notas: list
    url_novidades: str
    obrigatoria: bool
    versao_minima: str


def interpretar_manifesto(bruto, assinatura, cfg):
    """Confere assinatura e conteudo. So devolve um Manifesto se TUDO estiver correto."""
    if not verificar_assinatura(bruto, assinatura, cfg.chaves_publicas):
        raise ErroAtualizacao("A assinatura do manifesto de atualizacao nao confere. A atualizacao foi recusada.")
    try:
        m = json.loads(bruto.decode("utf-8"))
        inst = m["instalador"]
        v = str(m["versao"]).lstrip("vV")
        interpretar_versao(v)
        if m.get("produto") != "hghub" or m.get("esquema") != 1:
            raise ErroAtualizacao("Manifesto de outro produto ou de formato desconhecido.")
        nome, url, tamanho, sha = str(inst["nome"]), str(inst["url"]), int(inst["tamanho"]), str(inst["sha256"]).lower()
    except ErroAtualizacao:
        raise
    except Exception as erro:
        raise ErroAtualizacao("Manifesto de atualizacao malformado.") from erro

    achado = PADRAO_INSTALADOR.match(nome)
    if not achado or achado.group(1) != v:
        raise ErroAtualizacao("Nome do instalador no manifesto nao corresponde a versao.")
    if not PADRAO_SHA256.match(sha):
        raise ErroAtualizacao("Hash SHA-256 invalido no manifesto.")
    if not (TAMANHO_MIN_INSTALADOR <= tamanho <= TAMANHO_MAX_INSTALADOR):
        raise ErroAtualizacao("Tamanho do instalador fora do esperado.")
    cfg.validar_url(url)
    if url != cfg.prefixo_instalador(v) + nome:
        raise ErroAtualizacao("O instalador nao esta no repositorio/tag esperados.")
    notas = [str(n)[:300] for n in (m.get("notas") or [])][:30]
    return Manifesto(v, str(m.get("publicado_em", "")), nome, url, tamanho, sha, notas,
                     str(m.get("url_novidades", "")), bool(m.get("obrigatoria", False)),
                     str(m.get("versao_minima_suportada", "0.0.0")))


# --------------------------------------------------------------------------- orquestrador
def _agora():
    return datetime.now(timezone.utc)


class Atualizador:
    """Estado e operacoes do atualizador. Seguro para varias threads."""

    def __init__(self, cfg=None, versao_instalada=None, pasta=None, executar=None, encerrar_app=None):
        self.cfg = cfg
        self.versao_instalada = versao_instalada or versao.__version__
        self.pasta = pasta or pasta_local()
        self._executar = executar or self._executar_instalador
        self._encerrar_app = encerrar_app
        self._trava = threading.RLock()
        self._thread_download = None
        self._manifesto = None
        self._estado = {
            "fase": "desligado" if cfg is None else "ocioso",
            "versao_instalada": self.versao_instalada,
            "versao_nova": None, "notas": [], "url_novidades": None, "obrigatoria": False,
            "progresso": 0, "bytes": 0, "total": 0, "erro": None,
            "ultima_verificacao": None, "configurado": cfg is not None,
            "pode_restaurar": self.pode_restaurar_versao_anterior(),
        }

    # ------------------------------------------------------------- caminhos
    @property
    def dir_downloads(self):
        return os.path.join(self.pasta, "updates")

    @property
    def dir_instaladores(self):
        return os.path.join(self.pasta, "installers")

    @property
    def arq_config_local(self):
        return os.path.join(self.pasta, "config_local.json")

    @property
    def arq_estado_update(self):
        return os.path.join(self.pasta, "update_state.json")

    @property
    def arq_instaladores(self):
        return os.path.join(self.dir_instaladores, "estado.json")

    # ------------------------------------------------------------- estado publico
    def estado(self):
        with self._trava:
            e = dict(self._estado)
            e["notas"] = list(e["notas"])
            e["pode_restaurar"] = self.pode_restaurar_versao_anterior()
            return e

    def _definir(self, **campos):
        with self._trava:
            self._estado.update(campos)

    def _falhar(self, mensagem, fase="erro"):
        log.warning("Atualizador: %s", mensagem)
        self._definir(fase=fase, erro=mensagem)

    # ------------------------------------------------------------- preferencias locais
    def _config_local(self):
        return _ler_json(self.arq_config_local, {})

    def _salvar_config_local(self, **campos):
        dados = self._config_local()
        dados.update(campos)
        _escrever_json_atomico(self.arq_config_local, dados)

    def deve_verificar_agora(self):
        """Respeita o intervalo entre verificacoes automaticas e o 'lembrar mais tarde'."""
        if self.cfg is None:
            return False
        c = self._config_local()
        agora = _agora()
        for chave in ("adiado_ate",):
            try:
                if c.get(chave) and datetime.fromisoformat(c[chave]) > agora:
                    return False
            except ValueError:
                pass
        try:
            ultima = datetime.fromisoformat(c["ultima_verificacao"]) if c.get("ultima_verificacao") else None
        except ValueError:
            ultima = None
        return ultima is None or agora - ultima >= timedelta(hours=self.cfg.intervalo_horas)

    def lembrar_mais_tarde(self, horas=24):
        self._salvar_config_local(adiado_ate=(_agora() + timedelta(hours=horas)).isoformat())
        self._definir(fase="ocioso" if self._manifesto is None else "adiada")

    # ------------------------------------------------------------- 1. verificar
    def verificar(self, manual=False):
        """Consulta o GitHub. Nunca levanta: o resultado fica em `estado()`."""
        if self.cfg is None:
            self._definir(fase="desligado", erro="O atualizador ainda nao foi configurado para este aplicativo.")
            return self.estado()
        with self._trava:
            if self._estado["fase"] in ("baixando", "aplicando"):
                return self.estado()
            self._definir(fase="verificando", erro=None)
        try:
            bruto = _baixar_bytes(self.cfg, self.cfg.url_manifesto, LIMITE_MANIFESTO)
            assinatura = _baixar_bytes(self.cfg, self.cfg.url_assinatura, 4096).decode("ascii", "ignore")
            m = interpretar_manifesto(bruto, assinatura, self.cfg)
        except ErroAtualizacao as erro:
            self._salvar_config_local(ultima_verificacao=_agora().isoformat())
            self._definir(ultima_verificacao=_agora().isoformat())
            self._falhar(str(erro), fase="erro" if manual else "ocioso")
            return self.estado()
        except Exception as erro:  # noqa: BLE001 - nunca derruba o app por causa do atualizador
            log.exception("Atualizador: falha inesperada ao verificar")
            self._falhar(f"Falha inesperada ao verificar atualizacoes: {erro}", fase="erro" if manual else "ocioso")
            return self.estado()

        agora = _agora().isoformat()
        self._salvar_config_local(ultima_verificacao=agora)
        if versao_mais_nova(m.versao, self.versao_instalada):
            self._manifesto = m
            self._definir(fase="disponivel", versao_nova=m.versao, notas=m.notas, url_novidades=m.url_novidades,
                          obrigatoria=m.obrigatoria, total=m.tamanho, progresso=0, bytes=0, erro=None,
                          ultima_verificacao=agora)
            if self._arquivo_baixado_valido(m):
                self._definir(fase="pronta", progresso=100, bytes=m.tamanho)
        else:
            self._manifesto = None
            self._definir(fase="atualizado", versao_nova=None, notas=[], erro=None, ultima_verificacao=agora)
        return self.estado()

    # ------------------------------------------------------------- 2. baixar
    def _caminho_instalador(self, m):
        return os.path.join(self.dir_downloads, m.versao, m.nome)

    def _arquivo_baixado_valido(self, m):
        caminho = self._caminho_instalador(m)
        try:
            return os.path.getsize(caminho) == m.tamanho and sha256_arquivo(caminho) == m.sha256
        except OSError:
            return False

    def iniciar_download(self):
        """Dispara o download em thread propria. Devolve False se nao ha o que baixar."""
        with self._trava:
            m = self._manifesto
            if m is None or self._estado["fase"] not in ("disponivel", "erro", "adiada"):
                return False
            if self._thread_download and self._thread_download.is_alive():
                return False
            self._definir(fase="baixando", progresso=0, bytes=0, total=m.tamanho, erro=None)
            self._thread_download = threading.Thread(target=self._baixar, args=(m,), name="hghub-download", daemon=True)
            self._thread_download.start()
            return True

    def _baixar(self, m):
        destino = self._caminho_instalador(m)
        parcial = destino + ".part"
        try:
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            if self._arquivo_baixado_valido(m):
                self._definir(fase="pronta", progresso=100, bytes=m.tamanho)
                return
            h = hashlib.sha256()
            recebido = 0
            with abrir_url(self.cfg, m.url) as resp, open(parcial, "wb") as f:
                declarado = resp.headers.get("Content-Length")
                if declarado and int(declarado) != m.tamanho:
                    raise ErroAtualizacao("O tamanho do download nao confere com o manifesto.")
                while True:
                    bloco = resp.read(256 * 1024)
                    if not bloco:
                        break
                    recebido += len(bloco)
                    if recebido > m.tamanho:
                        raise ErroAtualizacao("O download passou do tamanho esperado; foi descartado.")
                    f.write(bloco)
                    h.update(bloco)
                    self._definir(bytes=recebido, progresso=min(99, int(recebido * 100 / m.tamanho)))
                f.flush()
                os.fsync(f.fileno())
            if recebido != m.tamanho:
                raise ErroAtualizacao("Download incompleto. Tente novamente.")
            if h.hexdigest() != m.sha256:
                raise ErroAtualizacao("O arquivo baixado esta corrompido (SHA-256 diferente do manifesto) e foi descartado.")
            os.replace(parcial, destino)
            self._definir(fase="pronta", progresso=100, bytes=m.tamanho)
        except ErroAtualizacao as erro:
            self._limpar(parcial)
            self._falhar(str(erro))
        except Exception as erro:  # noqa: BLE001
            log.exception("Atualizador: falha inesperada no download")
            self._limpar(parcial)
            self._falhar(f"Falha ao baixar a atualizacao: {erro}")

    @staticmethod
    def _limpar(caminho):
        try:
            os.remove(caminho)
        except OSError:
            pass

    def aguardar_download(self, limite=120):
        t = self._thread_download
        if t:
            t.join(limite)

    # ------------------------------------------------------------- 3. aplicar
    def aplicar(self):
        """Reconfere o instalador, guarda o atual para reversao e o executa. True se disparou."""
        with self._trava:
            m = self._manifesto
            if m is None or self._estado["fase"] != "pronta":
                raise ErroAtualizacao("Nao ha atualizacao pronta para instalar.")
            caminho = self._caminho_instalador(m)
            if not self._arquivo_baixado_valido(m):
                self._falhar("O instalador baixado nao passou na verificacao de integridade. Baixe novamente.", fase="disponivel")
                raise ErroAtualizacao(self._estado["erro"])
            _escrever_json_atomico(self.arq_estado_update, {
                "status": "aplicando", "versao_anterior": self.versao_instalada, "versao_nova": m.versao,
                "falhas_consecutivas": 0, "desde": _agora().isoformat()})
            self._definir(fase="aplicando")
        try:
            self._executar(caminho)
        except Exception as erro:  # noqa: BLE001 - nao conseguiu nem iniciar o instalador
            _escrever_json_atomico(self.arq_estado_update, {"status": "falhou_instalacao", "versao_anterior": self.versao_instalada,
                                                            "versao_nova": m.versao, "falhas_consecutivas": 0})
            self._falhar(f"Nao foi possivel iniciar o instalador: {erro}", fase="pronta")
            raise ErroAtualizacao(self._estado["erro"]) from erro
        if self._encerrar_app:
            self._encerrar_app()
        return True

    def _executar_instalador(self, caminho):
        """Instalador do Inno Setup em modo silencioso; ele fecha o app, copia e REABRE o programa."""
        log_instalacao = os.path.join(self.pasta, "logs", "instalador-atualizacao.log")
        os.makedirs(os.path.dirname(log_instalacao), exist_ok=True)
        args = [caminho, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                "/RELAUNCH=1", f"/LOG={log_instalacao}"]
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        subprocess.Popen(args, close_fds=True, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------- copias do instalador (reversao)
    def _estado_instaladores(self):
        return _ler_json(self.arq_instaladores, {})

    def registrar_instalador_recem_instalado(self):
        """O instalador deposita uma copia de si mesmo em installers/HGhub-Setup-novo.exe. Na
        primeira abertura depois de instalar (por atualizacao automatica OU manual), o app faz a
        rotacao: o instalador que estava 'atual' vira 'anterior' (so se ainda estiver integro) e o
        recem-instalado vira o 'atual'. E o que permite voltar a versao anterior."""
        novo = os.path.join(self.dir_instaladores, "HGhub-Setup-novo.exe")
        if not os.path.exists(novo):
            return False
        try:
            sha = sha256_arquivo(novo)
            est = self._estado_instaladores()
            atual = est.get("atual")
            caminho_atual = os.path.join(self.dir_instaladores, "HGhub-Setup-atual.exe")
            if atual and atual.get("sha256") == sha:
                os.remove(novo)                      # o mesmo instalador de novo: nada a rotacionar
                return False
            if atual and os.path.exists(caminho_atual) and sha256_arquivo(caminho_atual) == atual.get("sha256"):
                shutil.copy2(caminho_atual, os.path.join(self.dir_instaladores, "HGhub-Setup-anterior.exe"))
                est["anterior"] = {"versao": atual["versao"], "arquivo": "HGhub-Setup-anterior.exe", "sha256": atual["sha256"]}
            os.replace(novo, caminho_atual)
            est["atual"] = {"versao": self.versao_instalada, "arquivo": "HGhub-Setup-atual.exe", "sha256": sha}
            _escrever_json_atomico(self.arq_instaladores, est)
            return True
        except OSError:
            log.warning("Atualizador: nao foi possivel guardar o instalador recem-instalado.", exc_info=True)
            return False

    def pode_restaurar_versao_anterior(self):
        a = self._estado_instaladores().get("anterior")
        return bool(a and os.path.exists(os.path.join(self.dir_instaladores, a["arquivo"])))

    def restaurar_versao_anterior(self):
        """Reinstala a versao anterior (so se o arquivo guardado conferir com o hash registrado).
        Roda uma COPIA fora da pasta de instaladores: ao terminar, o proprio instalador antigo
        deposita 'novo' e a rotacao na proxima abertura deixa a versao mais nova como 'anterior'."""
        a = self._estado_instaladores().get("anterior")
        if not a:
            raise ErroAtualizacao("Nao ha versao anterior guardada neste computador.")
        origem = os.path.join(self.dir_instaladores, a["arquivo"])
        try:
            integro = sha256_arquivo(origem) == a["sha256"]
        except OSError:
            integro = False
        if not integro:
            raise ErroAtualizacao("A copia da versao anterior esta ausente ou corrompida.")
        pasta_reversao = os.path.join(self.dir_downloads, "reversao")
        os.makedirs(pasta_reversao, exist_ok=True)
        caminho = os.path.join(pasta_reversao, f"HGhub-Setup-{a['versao']}.exe")
        shutil.copy2(origem, caminho)
        _escrever_json_atomico(self.arq_estado_update, {
            "status": "revertendo", "versao_anterior": a["versao"], "versao_nova": self.versao_instalada,
            "falhas_consecutivas": 0, "desde": _agora().isoformat()})
        self._definir(fase="aplicando")
        try:
            self._executar(caminho)
        except Exception as erro:  # noqa: BLE001
            self._falhar(f"Nao foi possivel iniciar a reversao: {erro}", fase="erro")
            raise ErroAtualizacao(self._estado["erro"]) from erro
        if self._encerrar_app:
            self._encerrar_app()
        return True

    # ------------------------------------------------------------- 4. confirmar / reversao automatica
    def registrar_inicio_do_app(self):
        """Chamar no INICIO de cada abertura. Devolve 'reverter' se a versao nova nunca ficou de pe.

        Cada abertura da versao recem-instalada que NAO chegou a se confirmar (ficar de pe por
        SEGUNDOS_PARA_CONFIRMAR) conta como uma falha; passando de FALHAS_ATE_REVERTER aberturas
        sem confirmacao, e havendo a versao anterior guardada, pede-se a reversao."""
        self.registrar_instalador_recem_instalado()
        est = _ler_json(self.arq_estado_update, {})
        status = est.get("status")
        novo, antigo = est.get("versao_nova"), est.get("versao_anterior")

        if status == "revertendo" and antigo == self.versao_instalada:
            est["status"] = "revertida"
            _escrever_json_atomico(self.arq_estado_update, est)
            return "ok"
        if status == "aplicando" and novo != self.versao_instalada and antigo == self.versao_instalada:
            # o instalador nao concluiu: continua a versao antiga
            est["status"] = "falhou_instalacao"
            _escrever_json_atomico(self.arq_estado_update, est)
            return "ok"
        if status == "aplicando" and novo == self.versao_instalada:
            est["status"] = status = "aguardando_confirmacao"
            est["falhas_consecutivas"] = 0
        if status == "aguardando_confirmacao" and novo == self.versao_instalada:
            est["falhas_consecutivas"] = int(est.get("falhas_consecutivas", 0)) + 1
            _escrever_json_atomico(self.arq_estado_update, est)
            if est["falhas_consecutivas"] > FALHAS_ATE_REVERTER and self.pode_restaurar_versao_anterior():
                return "reverter"
        return "ok"

    def confirmar_saude(self):
        """A versao nova ficou de pe tempo suficiente: zera o contador e confirma a atualizacao."""
        est = _ler_json(self.arq_estado_update, {})
        if est.get("status") == "aguardando_confirmacao" and est.get("versao_nova") == self.versao_instalada:
            est.update(status="confirmada", falhas_consecutivas=0)
            _escrever_json_atomico(self.arq_estado_update, est)
            log.info("Atualizacao para %s confirmada.", self.versao_instalada)
            return True
        return False

    def agendar_confirmacao(self, segundos=SEGUNDOS_PARA_CONFIRMAR):
        t = threading.Timer(segundos, self.confirmar_saude)
        t.daemon = True
        t.start()
        return t

    def novidade_pos_atualizacao(self):
        """Depois de atualizar, devolve {'de','para'} uma unica vez para mostrar 'atualizacao concluida'."""
        est = _ler_json(self.arq_estado_update, {})
        if est.get("status") in ("aguardando_confirmacao", "confirmada") and est.get("versao_nova") == self.versao_instalada \
                and not est.get("mensagem_exibida"):
            est["mensagem_exibida"] = True
            _escrever_json_atomico(self.arq_estado_update, est)
            return {"de": est.get("versao_anterior"), "para": est.get("versao_nova")}
        return None

    # ------------------------------------------------------------- segundo plano
    def iniciar_verificacao_periodica(self, atraso=8.0, intervalo_minutos=30):
        """Verifica 'atraso' s depois de abrir e depois reavalia a cada 'intervalo_minutos'.
        So consulta a rede quando `deve_verificar_agora()` permite (6 h entre verificacoes)."""
        def laco():
            time.sleep(atraso)
            while True:
                try:
                    if self.deve_verificar_agora():
                        self.verificar()
                except Exception:  # noqa: BLE001
                    log.exception("Atualizador: erro no laco de verificacao")
                time.sleep(intervalo_minutos * 60)
        t = threading.Thread(target=laco, name="hghub-atualizador", daemon=True)
        t.start()
        return t
