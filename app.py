# -*- coding: utf-8 -*-
"""REDE HG - Sistema de Demandas.

Ponto de entrada: sobe o Flask em uma thread e abre a janela nativa do pywebview.
Toda a logica vive em `backend/`; a interface, em `templates/` e `static/`.

    python app.py            abre o aplicativo desktop
    python app.py --web      apenas o servidor, para abrir no navegador
"""

import os
import sys

# ============================================================
# Empacotado com PyInstaller --windowed nao existe console: sem estes
# substitutos, qualquer escrita em stdout/stderr derruba o processo com
# "lost sys.stderr". Fora desse cenario o terminal continua funcionando.
# ============================================================
class _SaidaNula:
    def write(self, *args, **kwargs): pass
    def flush(self, *args, **kwargs): pass
    def close(self, *args, **kwargs): pass
    def isatty(self): return False
    def readable(self): return False
    def writable(self): return True
    def seekable(self): return False
    @property
    def buffer(self): return self
    @property
    def encoding(self): return "utf-8"
    @property
    def errors(self): return "replace"


if sys.stdout is None or sys.stderr is None:
    sys.stdout = sys.__stdout__ = _SaidaNula()
    sys.stderr = sys.__stderr__ = _SaidaNula()

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# EdgeChromium (WebView2) e o unico motor que renderiza corretamente o
# JavaScript moderno e o Chart.js desta interface. MSHTML fica como ultimo
# recurso, e a pagina aparece sem estilo quando ele entra em acao.
os.environ.setdefault("PYWEBVIEW_GUI", "edgechromium")


# Aberto de uma pasta de REDE (Z:, Y:...) o motor da janela nao carrega e o programa fecharia em
# silencio: nesse caso ele se copia para o disco local e se reabre de la (ver lancamento.py).
# Precisa vir ANTES de carregar o sistema.
import lancamento  # noqa: E402

if lancamento.reabrir_do_disco_local():
    sys.exit(0)

import logging
import threading
import time
import webbrowser

from backend import ARQUIVO_DADOS, create_app
from backend import versao
from backend.rotas_atualizacao import obter_atualizador

log = logging.getLogger("hghub")

HOST = "127.0.0.1"
PORTA = 5000
URL = f"http://{HOST}:{PORTA}"

app = create_app()


def instancia_unica():
    """Uma so janela por PC: duas instancias brigariam pela porta 5000 e pelos mesmos arquivos. O nome do
    mutex e o mesmo que o instalador usa (AppMutex) para saber se o programa esta aberto."""
    try:
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "HGhubAppMutex")
        if ctypes.windll.kernel32.GetLastError() == 183:           # ERROR_ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(0, "O sistema já está aberto neste computador.\n"
                                             "Procure a janela na barra de tarefas.", versao.NOME_APP, 0x40)
            return False
        globals()["_mutex"] = mutex                                # mantem o handle vivo ate o processo acabar
    except Exception:
        pass
    return True


def iniciar_atualizador():
    """Reversao automatica se a versao recem-instalada nao sobe, confirmacao de saude e verificacao periodica."""
    at = obter_atualizador()
    try:
        if at.registrar_inicio_do_app() == "reverter":
            log.error("A versao %s nao conseguiu se firmar; voltando para a anterior.", versao.__version__)
            at.restaurar_versao_anterior()
            sys.exit(0)
        at.agendar_confirmacao()
        at.iniciar_verificacao_periodica()
    except SystemExit:
        raise
    except Exception:
        log.exception("Falha ao iniciar o atualizador (o sistema segue normalmente).")


def iniciar_servidor():
    app.run(host=HOST, port=PORTA, debug=False, use_reloader=False, threaded=True)


def abrir_janela():
    try:
        import webview
    except ImportError:
        log.error("pywebview não encontrado. Instale com: pip install pywebview")
        print("\nERRO: biblioteca 'pywebview' não encontrada.")
        print("Instale com: pip install pywebview\n")
        sys.exit(1)

    servidor = threading.Thread(target=iniciar_servidor, daemon=True)
    servidor.start()
    time.sleep(1)

    webview.create_window(
        title="REDE HG - Sistema de Demandas",
        url=URL,
        width=1360,
        height=860,
        min_size=(1024, 680),
        background_color="#0B1220",
        resizable=True,
    )

    for motor in ("edgechromium", "mshtml", None):
        try:
            webview.start(gui=motor) if motor else webview.start()
            return
        except Exception as falha:
            log.warning("Motor de renderização '%s' indisponível: %s", motor, falha)


if __name__ == "__main__":
    if not instancia_unica():
        sys.exit(0)
    log.info("Iniciando Sistema de Demandas %s. Dados em: %s", versao.__version__, ARQUIVO_DADOS)
    iniciar_atualizador()
    print("=" * 60)
    print("  REDE HG - SISTEMA DE DEMANDAS")
    print(f"  Dados: {ARQUIVO_DADOS}")
    print(f"  Servidor: {URL}")
    print("=" * 60)

    if "--web" in sys.argv:
        # Sem pywebview aqui de propósito (--web é o modo "só servidor" já
        # existente) - abre o navegador padrão sozinho, 1.2s depois do
        # servidor subir, pro usuário nunca precisar digitar a URL na mão.
        threading.Timer(1.2, lambda: webbrowser.open(URL)).start()
        iniciar_servidor()
    else:
        abrir_janela()
