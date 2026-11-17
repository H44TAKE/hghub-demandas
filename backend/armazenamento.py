# -*- coding: utf-8 -*-
"""Armazenamento em disco do HGhub: onde cada coisa e salva, como e lida e como o legado e importado.

Layout da pasta de dados atual (`HGhub-dados`, ao lado dos arquivos antigos):

    usuarios.json               contas: nome, e-mail, senha (HASH), permissoes. Editavel a mao (veja LEIA-ME.txt)
    preferencias_usuarios.json  notificacoes e favoritos de cada conta
    demandas.json               pastas > projetos > tarefas
    catalogos.json              setores, cidades, etiquetas salvas, anotacoes
    sessoes.json                sessoes abertas e codigos de recuperacao - SO hashes
    configuracao.json           e-mail (SMTP) e planilha do Google
    manutencao_veiculos_ocultos.json
    fotos/  anexos/             arquivos enviados pelos usuarios
    copias/                     copias de seguranca feitas antes de reimportar
    _migracao.json  _trava.lock  LEIA-ME.txt

Os arquivos ANTIGOS (dados_sistema_hg.json, usuarios.json, permissoes_contas.json, config.json,
veiculos_ocultos.json, fotos_usuarios/, anexos_tarefas/) NUNCA sao gravados, apagados ou movidos por este
modulo: sao lidos uma vez (migracao) e continuam la como copia de seguranca visual. Fotos e anexos antigos
continuam sendo servidos direto da pasta antiga.

Este modulo trabalha com dicionarios simples (o mesmo formato do JSON antigo, "estado"); a conversao para
objetos (Usuario, Pasta...) fica em `models.py`.
"""

import contextlib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime

from . import utils
from .utils import bloqueio_arquivo, hash_token

log = logging.getLogger("hghub")

FORMATO = 1                      # versao do formato dos arquivos novos
FORMATO_DATA_HORA = "%Y-%m-%d %H:%M:%S"


class ArmazenamentoError(RuntimeError):
    """Um arquivo existe mas nao pode ser lido/entendido: nada deve ser gravado por cima dele."""


# arquivo -> assinatura (sha256 do que foi lido/gravado por ultimo). So reescreve o que mudou.
_assinaturas = {}

_LEIA_ME_GERAL = """\
HGhub - pasta de dados
======================
Esta pasta e a base REAL do sistema de demandas. O programa le e grava aqui; os arquivos antigos que ficam na
pasta acima (dados_sistema_hg.json, usuarios.json, config.json ...) sao apenas uma COPIA da epoca da migracao:
o programa NAO grava mais neles, e mudar qualquer coisa neles nao tem efeito.

  usuarios.json               contas dos funcionarios (nome, e-mail, setores, conta mestre, senha em HASH)
  preferencias_usuarios.json  notificacoes e favoritos de cada conta
  demandas.json               pastas > projetos > tarefas
  catalogos.json              setores, cidades, etiquetas salvas, anotacoes
  sessoes.json                sessoes abertas / codigos de recuperacao (somente hashes - nao serve para entrar)
  configuracao.json           e-mail (SMTP) e planilha do Google
  fotos\\  anexos\\            arquivos enviados pelos usuarios (os antigos continuam na pasta antiga)
  copias\\                     copias de seguranca feitas antes de reimportar do legado

COMO CONSULTAR / MUDAR UMA CONTA (usuarios.json)
  - Para ver e-mails e dados de todo mundo, basta abrir o usuarios.json.
  - Para TROCAR A SENHA de alguem: escreva a senha nova (minimo 6 caracteres) no campo "nova_senha" dessa
    conta e salve. Na proxima abertura do programa ela e convertida em hash, o campo volta a ficar vazio e a
    pessoa e deslogada. (A senha atual nunca aparece: o arquivo guarda so o hash.)
  - Para mudar um e-mail, nome ou os setores: edite o campo e salve. Mantenha o JSON valido (aspas, virgulas);
    se ficar invalido o programa se recusa a gravar qualquer coisa (nada e perdido) ate voce corrigir.
  - Prefira alterar permissoes (conta mestre / setores) pela tela "Gerenciar usuarios" do programa.
  - Faca uma copia do arquivo antes de editar a mao.
"""


# ---------------------------------------------------------------- leitura / escrita basicas
def ler_json(caminho):
    """Le um JSON existente. Qualquer falha vira ArmazenamentoError (nunca devolve 'vazio' por engano)."""
    try:
        with open(caminho, "r", encoding="utf-8-sig") as f:
            bruto = f.read()
        dados = json.loads(bruto)
    except Exception as erro:
        raise ArmazenamentoError(f"Nao foi possivel ler {os.path.basename(caminho)}: {erro}") from erro
    if not isinstance(dados, dict):
        raise ArmazenamentoError(f"{os.path.basename(caminho)} nao tem o formato esperado.")
    formato = dados.get("_formato", 0)
    if isinstance(formato, int) and formato > FORMATO:
        raise ArmazenamentoError(f"{os.path.basename(caminho)} foi criado por uma versao mais nova do programa; "
                                 "atualize o HGhub neste computador.")
    _assinaturas[caminho] = hashlib.sha256(bruto.encode("utf-8")).hexdigest()
    return dados


def _serializar(dados):
    return json.dumps(dados, indent=2, ensure_ascii=False)


def escrever_json(caminho, dados, forcar=False):
    """Grava de forma atomica (temporario + os.replace). So grava se o conteudo mudou desde a ultima
    leitura/gravacao. Devolve True se gravou."""
    texto = _serializar(dados)
    assinatura = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    if not forcar and _assinaturas.get(caminho) == assinatura and os.path.exists(caminho):
        return False
    destino = os.path.dirname(os.path.abspath(caminho)) or "."
    os.makedirs(destino, exist_ok=True)
    descritor, temporario = tempfile.mkstemp(dir=destino, suffix=".tmp")
    try:
        with os.fdopen(descritor, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        for tentativa in range(4):          # rede/antivirus seguram o arquivo por instantes (WinError 5)
            try:
                os.replace(temporario, caminho)
                break
            except PermissionError:
                if tentativa == 3:
                    raise
                time.sleep(0.25 * (tentativa + 1))
    except Exception:
        if os.path.exists(temporario):
            try:
                os.remove(temporario)
            except OSError:
                pass
        raise
    _assinaturas[caminho] = assinatura
    return True


def _agora():
    return datetime.now().strftime(FORMATO_DATA_HORA)


def _impressao(caminho):
    """tamanho/mtime/sha256 de um arquivo legado (para saber depois se mudou)."""
    if not os.path.isfile(caminho):
        return None
    info = os.stat(caminho)
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return {"tamanho": info.st_size, "mtime": info.st_mtime, "sha256": h.hexdigest()}


# ---------------------------------------------------------------- regras de conversao (dicionarios)
_CHAVES_PARA_PREFERENCIAS = ("notificacoes", "favoritos")
_CHAVES_PARA_SESSOES = ("codigo_recuperacao", "expiracao_codigo")


def _permissoes_legadas(usuarios, contas):
    """Aplica a regra do arquivo antigo permissoes_contas.json (fonte da verdade das permissoes nele)."""
    if not isinstance(contas, dict):
        return
    por_id = {c.get("id"): c for c in contas.values() if isinstance(c, dict) and c.get("id")}
    for u in usuarios:
        email = (u.get("email") or "").strip().lower()
        entrada = contas.get(email) or por_id.get(u.get("id"))
        if entrada is not None:
            u["conta_mestre"] = bool(entrada.get("conta_mestre"))
            u["setores"] = [s.strip() for s in (entrada.get("setores") or []) if isinstance(s, str) and s.strip()]
            u["permissao_atualizada_em"] = entrada.get("atualizado_em")
            u["permissao_atualizada_por"] = entrada.get("atualizado_por")
        elif "conta_mestre" not in u.get("_original_chaves", ()):
            # conta gravada por versao antiga que nao conhece permissoes: falha FECHADA, nunca "mestre por omissao"
            u["conta_mestre"] = False
            u["setores"] = []


def _usuario_do_legado(bruto):
    u = dict(bruto)
    original = set(bruto.keys())
    setores = u.get("setores")
    if setores is None:
        legado = u.pop("setor", None)
        setores = [legado] if legado else []
    u.pop("setor", None)
    u["setores"] = setores
    u["conta_mestre"] = u.get("conta_mestre", True)
    u["_original_chaves"] = original
    return u


def _ler_opcional(caminho):
    return ler_json(caminho) if os.path.isfile(caminho) else None


def _estado_do_legado():
    """Monta o estado (dicionario) a partir dos arquivos ANTIGOS, so lendo. None se nao ha dados antigos."""
    if not os.path.isfile(utils.ARQUIVO_DADOS):
        return None
    dados = ler_json(utils.ARQUIVO_DADOS)
    usuarios_json = _ler_opcional(utils.LEGADO_USUARIOS)
    lista = (usuarios_json or {}).get("usuarios") or dados.get("usuarios") or []
    usuarios = [_usuario_do_legado(u) for u in lista if isinstance(u, dict) and u.get("id")]
    permissoes = _ler_opcional(utils.LEGADO_PERMISSOES)
    _permissoes_legadas(usuarios, (permissoes or {}).get("contas"))
    for u in usuarios:
        u.pop("_original_chaves", None)
    return {
        "usuarios": usuarios,
        "pastas": dados.get("pastas", []),
        "anotacoes": dados.get("anotacoes", {}),
        "sessoes": {hash_token(t): s for t, s in (dados.get("sessoes") or {}).items() if isinstance(s, dict)},
        "cidades": dados.get("cidades", []),
        "setores": dados.get("setores", []),
        "etiquetas_salvas": dados.get("etiquetas_salvas", []),
    }


# ---------------------------------------------------------------- estado novo <-> arquivos
def gravar_estado(estado, forcar=False):
    """Divide o estado nos arquivos novos e grava so os que mudaram. Devolve a lista dos que gravou."""
    usuarios, preferencias, recuperacoes = [], {}, {}
    for u in estado["usuarios"]:
        item = {k: v for k, v in u.items()
                if k not in _CHAVES_PARA_PREFERENCIAS + _CHAVES_PARA_SESSOES + ("senha", "foto_perfil_arquivo")}
        item["senha_hash"] = u.get("senha")
        item.setdefault("nova_senha", "")
        usuarios.append(item)
        preferencias[u["id"]] = {"notificacoes": u.get("notificacoes") or [], "favoritos": u.get("favoritos") or []}
        if u.get("codigo_recuperacao"):
            recuperacoes[u["id"]] = {"codigo_hash": u["codigo_recuperacao"], "expira_em": u.get("expiracao_codigo")}
    gravados = []
    arquivos = (
        (utils.ARQUIVO_USUARIOS, {"_formato": FORMATO, "usuarios": usuarios}),
        (utils.ARQUIVO_PREFERENCIAS, {"_formato": FORMATO, "usuarios": preferencias}),
        (utils.ARQUIVO_DEMANDAS, {"_formato": FORMATO, "pastas": estado["pastas"]}),
        (utils.ARQUIVO_CATALOGOS, {"_formato": FORMATO, "setores": estado["setores"], "cidades": estado["cidades"],
                                   "etiquetas_salvas": estado["etiquetas_salvas"], "anotacoes": estado["anotacoes"]}),
        (utils.ARQUIVO_SESSOES, {"_formato": FORMATO, "sessoes": estado["sessoes"], "recuperacoes": recuperacoes}),
    )
    for caminho, conteudo in arquivos:
        if escrever_json(caminho, conteudo, forcar=forcar):
            gravados.append(os.path.basename(caminho))
    return gravados


def ler_estado():
    """Le os arquivos novos e devolve o estado (mesmo formato do JSON antigo). None se a base ainda nao existe
    (nem usuarios.json nem demandas.json). Se so um dos dois existir, ou algo estiver ilegivel: erro."""
    tem_usuarios = os.path.isfile(utils.ARQUIVO_USUARIOS)
    tem_demandas = os.path.isfile(utils.ARQUIVO_DEMANDAS)
    if not tem_usuarios and not tem_demandas:
        return None
    if tem_usuarios != tem_demandas:
        faltando = "demandas.json" if tem_usuarios else "usuarios.json"
        raise ArmazenamentoError(f"{faltando} nao foi encontrado na pasta de dados; nada sera recriado em cima.")
    usuarios_json = ler_json(utils.ARQUIVO_USUARIOS)
    demandas = ler_json(utils.ARQUIVO_DEMANDAS)
    prefs = (_ler_opcional(utils.ARQUIVO_PREFERENCIAS) or {}).get("usuarios") or {}
    catalogos = _ler_opcional(utils.ARQUIVO_CATALOGOS) or {}
    sessoes_json = _ler_opcional(utils.ARQUIVO_SESSOES) or {}
    recuperacoes = sessoes_json.get("recuperacoes") or {}

    usuarios = []
    for bruto in usuarios_json.get("usuarios") or []:
        u = dict(bruto)
        u["senha"] = u.pop("senha_hash", None) or u.get("senha") or ""
        p = prefs.get(u.get("id"), {})
        u["notificacoes"] = p.get("notificacoes", [])
        u["favoritos"] = p.get("favoritos", [])
        r = recuperacoes.get(u.get("id"))
        if r:
            u["codigo_recuperacao"] = r.get("codigo_hash")
            u["expiracao_codigo"] = r.get("expira_em")
        usuarios.append(u)
    return {
        "usuarios": usuarios,
        "pastas": demandas.get("pastas", []),
        "anotacoes": catalogos.get("anotacoes", {}),
        "sessoes": sessoes_json.get("sessoes", {}),
        "cidades": catalogos.get("cidades", []),
        "setores": catalogos.get("setores", []),
        "etiquetas_salvas": catalogos.get("etiquetas_salvas", []),
    }


# ---------------------------------------------------------------- migracao do legado
def migrado():
    return os.path.isfile(utils.ARQUIVO_MIGRACAO)


def _copiar_json_se_valido(origem, destino):
    if not os.path.isfile(origem) or os.path.exists(destino):
        return
    try:
        ler_json_qualquer = json.load(open(origem, "r", encoding="utf-8-sig"))
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(ler_json_qualquer, f, indent=2, ensure_ascii=False)
    except Exception as erro:
        log.warning("Nao consegui importar %s: %s", os.path.basename(origem), erro)


def garantir_migrado(travado=False):
    """Idempotente. Devolve 'existente', 'migrado' ou 'novo'.

    - Ja tem `_migracao.json`: nada a fazer.
    - Ha dados antigos: importa TUDO para a pasta nova (so lendo os antigos) e marca como migrado.
    - Nao ha nada: marca como base nova (o chamador cria o admin inicial).
    Se os dados antigos existem mas nao sao lidos, levanta ArmazenamentoError e NADA e criado.
    """
    if migrado():
        return "existente"
    os.makedirs(utils.PASTA_DADOS, exist_ok=True)
    with (contextlib.nullcontext() if travado else bloqueio_arquivo(utils.ARQUIVO_TRAVA, tempo_limite=60)):
        if migrado():                         # outro PC terminou enquanto esperavamos a trava
            return "existente"
        estado = _estado_do_legado()
        marca = {"formato": FORMATO, "criado_em": _agora(), "maquina": os.environ.get("COMPUTERNAME", "?")}
        if estado is None:
            marca["origem"] = None
            marca["situacao"] = "base nova (nao havia dados antigos)"
            resultado = "novo"
        else:
            gravar_estado(estado, forcar=True)
            _copiar_json_se_valido(utils.LEGADO_CONFIG, utils.ARQUIVO_CONFIG)
            _copiar_json_se_valido(utils.LEGADO_OCULTOS, utils.ARQUIVO_OCULTOS)
            marca["origem"] = {nome: _impressao(caminho) for nome, caminho in _ARQUIVOS_LEGADOS.items()}
            marca["contagem"] = {
                "usuarios": len(estado["usuarios"]),
                "pastas": len(estado["pastas"]),
                "projetos": sum(len(p.get("projetos", [])) for p in estado["pastas"]),
                "tarefas": sum(len(pr.get("tarefas", [])) for p in estado["pastas"] for pr in p.get("projetos", [])),
            }
            marca["situacao"] = "importado dos arquivos antigos (somente leitura)"
            resultado = "migrado"
            log.warning("Migracao do legado concluida: %s", marca["contagem"])
        with open(os.path.join(utils.PASTA_DADOS, "LEIA-ME.txt"), "w", encoding="utf-8") as f:
            f.write(_LEIA_ME_GERAL)
        escrever_json(utils.ARQUIVO_MIGRACAO, marca, forcar=True)     # por ULTIMO: marca que terminou
        return resultado


_ARQUIVOS_LEGADOS = {
    "dados_sistema_hg.json": None,      # preenchido abaixo (caminhos dependem de utils)
    "usuarios.json": None,
    "permissoes_contas.json": None,
}


def _atualizar_mapa_legado():
    _ARQUIVOS_LEGADOS["dados_sistema_hg.json"] = utils.ARQUIVO_DADOS
    _ARQUIVOS_LEGADOS["usuarios.json"] = utils.LEGADO_USUARIOS
    _ARQUIVOS_LEGADOS["permissoes_contas.json"] = utils.LEGADO_PERMISSOES


_atualizar_mapa_legado()


# ---------------------------------------------------------------- legado alterado depois da migracao
_cache_legado = {"em": 0.0, "valor": None}


def legado_alterado(max_idade=60):
    """Lista os arquivos antigos que mudaram desde a migracao (ex.: um PC com versao antiga continua gravando
    neles). Cache curto porque e chamado com frequencia. [] = tudo igual."""
    agora = time.time()
    if agora - _cache_legado["em"] < max_idade and _cache_legado["valor"] is not None:
        return _cache_legado["valor"]
    alterados = []
    try:
        marca = ler_json(utils.ARQUIVO_MIGRACAO)
        origem = marca.get("origem") or {}
        for nome, caminho in _ARQUIVOS_LEGADOS.items():
            antes = origem.get(nome)
            if not os.path.isfile(caminho):
                continue
            info = os.stat(caminho)
            if antes is None or (info.st_size, info.st_mtime) != (antes.get("tamanho"), antes.get("mtime")):
                alterados.append(nome)
    except Exception as erro:
        log.warning("Nao foi possivel conferir os arquivos antigos: %s", erro)
        alterados = []
    _cache_legado.update(em=agora, valor=alterados)
    return alterados


def informacoes():
    """Resumo para a tela de administracao."""
    try:
        marca = ler_json(utils.ARQUIVO_MIGRACAO)
    except ArmazenamentoError:
        marca = {}
    return {
        "pasta": utils.PASTA_DADOS,
        "migracao": {k: marca.get(k) for k in ("criado_em", "maquina", "situacao", "contagem")},
        "legado_alterado": legado_alterado(),
    }


# ---------------------------------------------------------------- reimportar (somar o que falta)
def _chave_item(item):
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def mesclar_legado(atual, legado):
    """Acrescenta ao estado ATUAL o que existe no legado e ainda nao existe aqui (por id / e-mail). Nunca
    remove nem sobrescreve nada do que ja esta na base nova. Devolve (novo_estado, relatorio)."""
    novo = json.loads(json.dumps(atual))
    rel = {"usuarios": 0, "pastas": 0, "projetos": 0, "tarefas": 0, "catalogos": 0}
    emails = {(u.get("email") or "").strip().lower() for u in novo["usuarios"]}
    ids_usuarios = {u.get("id") for u in novo["usuarios"]}
    for u in legado["usuarios"]:
        email = (u.get("email") or "").strip().lower()
        if email and email not in emails and u.get("id") not in ids_usuarios:
            novo["usuarios"].append(u)
            rel["usuarios"] += 1
    pastas = {p.get("id"): p for p in novo["pastas"]}
    for p in legado["pastas"]:
        existente = pastas.get(p.get("id"))
        if existente is None:
            novo["pastas"].append(p)
            rel["pastas"] += 1
            rel["projetos"] += len(p.get("projetos", []))
            rel["tarefas"] += sum(len(pr.get("tarefas", [])) for pr in p.get("projetos", []))
            continue
        projetos = {pr.get("id"): pr for pr in existente.get("projetos", [])}
        for pr in p.get("projetos", []):
            ex_pr = projetos.get(pr.get("id"))
            if ex_pr is None:
                existente.setdefault("projetos", []).append(pr)
                rel["projetos"] += 1
                rel["tarefas"] += len(pr.get("tarefas", []))
                continue
            ids = {t.get("id") for t in ex_pr.get("tarefas", [])}
            for t in pr.get("tarefas", []):
                if t.get("id") not in ids:
                    ex_pr.setdefault("tarefas", []).append(t)
                    rel["tarefas"] += 1
    for chave in ("setores", "cidades", "etiquetas_salvas"):
        presentes = {_chave_item(x) for x in novo[chave]}
        for x in legado[chave]:
            if _chave_item(x) not in presentes:
                novo[chave].append(x)
                rel["catalogos"] += 1
    for chave, texto in legado["anotacoes"].items():
        if chave not in novo["anotacoes"]:
            novo["anotacoes"][chave] = texto
            rel["catalogos"] += 1
    return novo, rel


def reimportar_legado(aplicar=False):
    """Compara o legado com a base nova. `aplicar=False` so conta o que seria acrescentado; `True` faz uma
    copia de seguranca dos arquivos novos e grava a soma. Sempre sob a trava."""
    with bloqueio_arquivo(utils.ARQUIVO_TRAVA, tempo_limite=60):
        atual = ler_estado()
        legado = _estado_do_legado()
        if atual is None or legado is None:
            raise ArmazenamentoError("Nao ha base nova ou dados antigos para comparar.")
        novo, rel = mesclar_legado(atual, legado)
        rel["aplicado"] = False
        if aplicar and any(rel[k] for k in ("usuarios", "pastas", "projetos", "tarefas", "catalogos")):
            destino = os.path.join(utils.PASTA_COPIAS, datetime.now().strftime("%Y%m%d-%H%M%S") + "-antes-de-reimportar")
            os.makedirs(destino, exist_ok=True)
            for caminho in (utils.ARQUIVO_USUARIOS, utils.ARQUIVO_PREFERENCIAS, utils.ARQUIVO_DEMANDAS,
                            utils.ARQUIVO_CATALOGOS, utils.ARQUIVO_SESSOES):
                if os.path.isfile(caminho):
                    shutil.copy2(caminho, destino)
            gravar_estado(novo)
            marca = ler_json(utils.ARQUIVO_MIGRACAO)
            marca["origem"] = {nome: _impressao(caminho) for nome, caminho in _ARQUIVOS_LEGADOS.items()}
            marca["reimportado_em"] = _agora()
            escrever_json(utils.ARQUIVO_MIGRACAO, marca, forcar=True)
            _cache_legado.update(em=0.0, valor=None)
            rel["aplicado"] = True
            rel["copia"] = destino
        elif aplicar:
            marca = ler_json(utils.ARQUIVO_MIGRACAO)
            marca["origem"] = {nome: _impressao(caminho) for nome, caminho in _ARQUIVOS_LEGADOS.items()}
            escrever_json(utils.ARQUIVO_MIGRACAO, marca, forcar=True)
            _cache_legado.update(em=0.0, valor=None)
        return rel


def caminho_para_servir(subpasta_nova, subpasta_legada, nome):
    """Arquivo de foto/anexo: procura na pasta nova e, se nao achar, na antiga (so leitura)."""
    nome = os.path.basename(nome)
    for pasta in (subpasta_nova, subpasta_legada):
        candidato = os.path.join(pasta, nome)
        if os.path.isfile(candidato):
            return pasta
    return None
