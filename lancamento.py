# -*- coding: utf-8 -*-
"""Abre o programa a partir do DISCO LOCAL quando o .exe foi aberto de uma pasta de rede.

Aberto de dentro de uma pasta de REDE (Z:, Y:, \\\\servidor\\...) o programa fechava em silencio:
o motor da janela (pywebview, via .NET) nao carrega DLLs de unidade de rede. Entao, empacotado e
vindo da rede, ele copia a propria pasta para o disco local (%LOCALAPPDATA%\\HGhub) e se reabre de la.
Assim a pasta do .exe pode ficar na rede, para todo mundo abrir com dois cliques; os DADOS do
sistema continuam na pasta de rede, como sempre.

Nas aberturas seguintes so copia o que mudou (versao nova). O HGhub.exe e copiado por ULTIMO: se a
copia for interrompida no meio, o .exe local continua "desatualizado" e a proxima abertura refaz.

Este modulo e importado ANTES do sistema (nao toca em dados) e nunca deixa uma falha passar em
silencio: qualquer erro vira uma caixa de mensagem e um arquivo `erro_inicio.txt`.
"""

import os
import shutil
import subprocess
import sys
import threading
import traceback

TITULO_AVISO = "HGhub - preparando"
TITULO_ERRO = "REDE HG - Sistema de Demandas"
ARQUIVOS_IGNORADOS = {"hghub.log"}


def esta_em_unidade_de_rede(caminho):
    caminho = os.path.abspath(caminho)
    if caminho.startswith("\\\\"):
        return True
    unidade = os.path.splitdrive(caminho)[0]
    if not unidade:
        return False
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(unidade + "\\") == 4  # DRIVE_REMOTE
    except Exception:
        return False


def mesmo_arquivo(a, b):
    """Mesmo tamanho e mesma data (copy2 preserva a data): a copia local esta em dia."""
    try:
        sa, sb = os.stat(a), os.stat(b)
        return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 2
    except OSError:
        return False


def _caixa(titulo, texto, icone):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, texto, titulo, icone)
    except Exception:
        pass


def mostrar_erro(texto):
    _caixa(TITULO_ERRO, texto, 0x10)


def _abrir_aviso_preparando():
    """Caixa de mensagem (sem botoes de acao relevantes) numa thread, enquanto a copia acontece;
    sem nada na tela parecia que o programa 'nao abriu'. Fechada por `_fechar_aviso`."""
    texto = ("Preparando o programa neste computador...\n\n"
             "Na primeira vez e nas atualizacoes isso leva alguns minutos.\n"
             "A janela do sistema abre sozinha quando terminar.")
    threading.Thread(target=_caixa, args=(TITULO_AVISO, texto, 0x40), daemon=True).start()


def _fechar_aviso():
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, TITULO_AVISO)
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    except Exception:
        pass


def copiar_pasta(origem, destino, nome_exe):
    """Copia o que e novo/diferente de `origem` para `destino` (o .exe por ultimo).
    Devolve (copiados, falhas); `falhas` = arquivos que nao puderam ser copiados (ex.: em uso)."""
    pendentes, exe = [], None
    for raiz, _pastas, arquivos in os.walk(origem):
        relativo = os.path.relpath(raiz, origem)
        alvo = destino if relativo == "." else os.path.join(destino, relativo)
        os.makedirs(alvo, exist_ok=True)
        for nome in arquivos:
            if nome.lower() in ARQUIVOS_IGNORADOS:
                continue
            item = (os.path.join(raiz, nome), os.path.join(alvo, nome))
            if relativo == "." and nome == nome_exe:
                exe = item
            elif not mesmo_arquivo(*item):
                pendentes.append(item)
    if exe and not mesmo_arquivo(*exe):
        pendentes.append(exe)

    copiados, falhas = 0, []
    for src, dst in pendentes:
        try:
            shutil.copy2(src, dst)
            copiados += 1
        except OSError as falha:
            falhas.append(f"{dst}: {falha}")
    return copiados, falhas


def _registrar(texto):
    """Melhor esforco: deixa o motivo de uma falha de inicio num arquivo que da para achar."""
    for pasta in (os.environ.get("LOCALAPPDATA"), os.environ.get("TEMP"), os.path.expanduser("~")):
        if not pasta:
            continue
        try:
            os.makedirs(os.path.join(pasta, "HGhub"), exist_ok=True)
            with open(os.path.join(pasta, "HGhub", "erro_inicio.txt"), "a", encoding="utf-8") as f:
                f.write(texto + "\n")
            return
        except OSError:
            continue


def reabrir_do_disco_local(executavel=None, argv=None, congelado=None, lancar=None):
    """True se o programa foi (ou deveria ter sido) reaberto a partir de uma copia local: o processo
    atual deve encerrar. False = esta tudo certo, siga abrindo normalmente.

    Os parametros existem para teste; no uso real vem tudo de `sys`."""
    if congelado is None:
        congelado = getattr(sys, "frozen", False)
    if not congelado or os.environ.get("HGHUB_JA_LOCAL"):
        return False
    executavel = executavel or sys.executable
    origem = os.path.dirname(executavel)
    if not esta_em_unidade_de_rede(origem):
        return False

    argv = sys.argv[1:] if argv is None else argv
    destino = os.environ.get("HGHUB_DESTINO_LOCAL") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "HGhub")
    nome_exe = os.path.basename(executavel)
    exe_local = os.path.join(destino, nome_exe)

    try:
        if not mesmo_arquivo(executavel, exe_local):
            _abrir_aviso_preparando()
            try:
                copiados, falhas = copiar_pasta(origem, destino, nome_exe)
            finally:
                _fechar_aviso()
            if falhas:
                _registrar(f"Falhas ao copiar {origem} -> {destino}:\n  " + "\n  ".join(falhas[:20]))
            if not os.path.exists(exe_local):
                mostrar_erro("Nao foi possivel copiar o programa para este computador.\n\n"
                             f"Origem: {origem}\nDestino: {destino}\n\n"
                             "Verifique o acesso a pasta de rede e o espaco em disco.\n"
                             f"Detalhes em: {destino}\\erro_inicio.txt")
                return True
            # Se so alguns arquivos falharam mas ja existe uma copia local (ex.: o sistema aberto em
            # outra janela mantem o .exe em uso), abre a que existe.

        ambiente = {**os.environ, "HGHUB_JA_LOCAL": "1"}
        (lancar or _lancar)(exe_local, argv, destino, ambiente)
        return True
    except Exception:
        _registrar("Erro ao abrir a partir do disco local:\n" + traceback.format_exc())
        mostrar_erro("Nao foi possivel abrir o programa a partir da pasta de rede.\n\n"
                     "Copie a pasta do programa para o disco deste computador (por exemplo, C:\\HGhub) "
                     "e abra o HGhub.exe de la.")
        return True


def _lancar(exe_local, argv, destino, ambiente):
    subprocess.Popen(
        [exe_local, *argv], cwd=destino, env=ambiente, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
