# -*- coding: utf-8 -*-
"""Servidor local do Painel de TV: serve a página e `/api/painel`.

Uma thread de fundo busca a planilha de tempos em tempos e guarda o ÚLTIMO resultado bom.
A TV só lê esse resultado guardado (instantâneo): uma queda de internet ou do Drive nunca
deixa a tela em branco - ela segue mostrando os últimos dados, com um aviso de "desatualizado".
"""

import logging
import threading
import time

from flask import Flask, jsonify, send_from_directory

from . import dados
from .configuracao import PASTA_WEB, carregar_config

log = logging.getLogger("painel")

# Depois de tantas tentativas seguidas sem sucesso a tela passa a avisar que o dado está velho.
AVISO_APOS_FALHAS = 2


class Atualizador:
    def __init__(self, config, buscar_veiculos):
        self.config = config
        self.buscar_veiculos = buscar_veiculos  # () -> (veiculos, modificado_em)
        self.trava = threading.Lock()
        self.ultimo = None          # envelope do último sucesso
        self.mensagem = None        # por que não está configurado / último erro
        self.falhas = 0
        self.nao_configurado = False

    def atualizar_uma_vez(self):
        try:
            veiculos, modificado_em = self.buscar_veiculos()
            envelope = dados.envelope(dados.montar_painel(veiculos, self.config.get("frentes_ocultas")), "demo" if self.config["modo_demo"] else "real", modificado_em)
            with self.trava:
                self.ultimo, self.falhas, self.mensagem, self.nao_configurado = envelope, 0, None, False
        except Exception as falha:  # noqa: BLE001 - qualquer falha mantém o último dado bom
            from .leitor import PlanilhaNaoConfigurada
            with self.trava:
                self.mensagem = str(falha)
                if isinstance(falha, PlanilhaNaoConfigurada) and self.ultimo is None:
                    self.nao_configurado = True
                else:
                    self.falhas += 1
            log.error("Falha ao atualizar o painel (%s). Mantendo o último dado.", falha)

    def laco(self):
        while True:
            self.atualizar_uma_vez()
            time.sleep(max(15, int(self.config["atualizar_a_cada_segundos"])))

    def iniciar(self):
        threading.Thread(target=self.laco, name="atualizador", daemon=True).start()

    def resposta(self):
        with self.trava:
            if self.ultimo is None:
                return {"modo": "demo" if self.config["modo_demo"] else "real", "carregando": not self.nao_configurado,
                        "nao_configurado": self.nao_configurado, "mensagem": self.mensagem}
            r = dict(self.ultimo)
            r["desatualizado"] = self.falhas >= AVISO_APOS_FALHAS
            r["intervalo_segundos"] = int(self.config["atualizar_a_cada_segundos"])
            return r


def _buscar_do_drive():
    from . import leitor
    if not leitor._config_sheets()[0]:
        raise leitor.PlanilhaNaoConfigurada(
            "Falta o ID da planilha: preencha \"planilha_id\" no arquivo config.json da pasta do painel "
            "(o trecho do link do Drive entre /d/ e /edit) e reinicie o painel."
        )
    leitor.forcar_atualizacao()  # a cada ciclo confere o `modifiedTime` (barato) e só baixa se mudou
    prontuario = leitor.montar_prontuario_frota()
    modificado_em = leitor._cache_planilha.get("modificado_em")
    return leitor.calcular_saude_frota(prontuario), modificado_em


def criar_app(buscar_veiculos=None, config=None):
    config = config or carregar_config()
    if buscar_veiculos is None:
        buscar_veiculos = (lambda: (dados.veiculos_demo(), None)) if config["modo_demo"] else _buscar_do_drive

    atualizador = Atualizador(config, buscar_veiculos)
    app = Flask(__name__, static_folder=None)
    app.atualizador = atualizador

    @app.get("/")
    def pagina():
        return send_from_directory(PASTA_WEB, "index.html")

    @app.get("/web/<path:arquivo>")
    def estatico(arquivo):
        resposta = send_from_directory(PASTA_WEB, arquivo)
        resposta.headers["Cache-Control"] = "no-cache"
        return resposta

    @app.get("/api/painel")
    def api_painel():
        resposta = jsonify(atualizador.resposta())
        resposta.headers["Cache-Control"] = "no-store"
        return resposta

    return app
