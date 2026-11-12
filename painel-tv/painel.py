# -*- coding: utf-8 -*-
"""Painel de TV da Manutenção - REDE HG.

Sobe o servidor local e abre a página em tela cheia (Edge em modo quiosque). Pensado para
rodar sozinho num mini PC ligado à TV: sem mouse, sem teclado, sem ninguém abrir nada.

    python painel.py            -> servidor + tela cheia
    python painel.py --sem-tela -> só o servidor (abra http://127.0.0.1:5001 num navegador)
    python painel.py --demo     -> dados fictícios (testar a TV sem credencial do Google)
"""

import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

from servidor.app import criar_app
from servidor.configuracao import PASTA_BASE, carregar_config

log = logging.getLogger("painel")


def configurar_log():
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    arquivo = logging.handlers.RotatingFileHandler(
        os.path.join(PASTA_BASE, "painel.log"), maxBytes=512_000, backupCount=2, encoding="utf-8")
    arquivo.setFormatter(formato)
    log.setLevel(logging.INFO)
    log.addHandler(arquivo)
    if sys.stderr:
        console = logging.StreamHandler()
        console.setFormatter(formato)
        log.addHandler(console)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def achar_edge():
    candidatos = [shutil.which("msedge")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            candidatos.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    return next((c for c in candidatos if c and os.path.exists(c)), None)


def abrir_tela(url):
    """Edge em quiosque (tela cheia, sem barra, sem avisos). Sem Edge, abre o navegador padrão."""
    edge = achar_edge()
    if edge:
        # Perfil próprio: não herda abas/restauração de sessão e não briga com o Edge de uso normal.
        perfil = os.path.join(PASTA_BASE, ".perfil-edge")
        subprocess.Popen([
            edge, f"--user-data-dir={perfil}", "--kiosk", url, "--edge-kiosk-type=fullscreen",
            "--no-first-run", "--disable-session-crashed-bubble", "--disable-infobars",
            "--autoplay-policy=no-user-gesture-required",
        ])
    else:
        log.warning("Microsoft Edge não encontrado; abrindo no navegador padrão.")
        webbrowser.open(url)


def main():
    configurar_log()
    config = carregar_config()
    if "--demo" in sys.argv:
        config["modo_demo"] = True
    porta = int(config["porta"])
    url = f"http://127.0.0.1:{porta}"

    app = criar_app(config=config)
    app.atualizador.iniciar()
    log.info("Painel de TV iniciando em %s (%s).", url, "DEMO" if config["modo_demo"] else "planilha do Drive")

    if "--sem-tela" not in sys.argv:
        threading.Timer(1.5, abrir_tela, args=(url,)).start()

    app.run(host="127.0.0.1", port=porta, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
