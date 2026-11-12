# -*- coding: utf-8 -*-
"""Rotas /api/atualizacao/* e /api/versao. Exigem login (a API inteira exige; ver routes.py)."""

import logging
import os
import threading

from flask import jsonify

from . import atualizador as A
from . import versao

log = logging.getLogger("hghub")
_atualizador = None
_trava = threading.Lock()


def _encerrar_app_em_breve():
    """Da tempo da resposta HTTP chegar ao navegador e fecha o processo para liberar os arquivos."""
    threading.Timer(1.5, lambda: os._exit(0)).start()


def obter_atualizador():
    """Atualizador unico do processo (config de producao; desligado se o repositorio nao esta definido)."""
    global _atualizador
    with _trava:
        if _atualizador is None:
            try:
                cfg = A.config_padrao()
            except A.ErroAtualizacao as erro:
                log.error("Atualizador desligado: %s", erro)
                cfg = None
            _atualizador = A.Atualizador(cfg, encerrar_app=_encerrar_app_em_breve)
        return _atualizador


def registrar(api, erro):
    @api.get("/versao")
    def api_versao():
        return jsonify({"success": True, "versao": versao.__version__, "nome": versao.NOME_APP})

    @api.get("/atualizacao/estado")
    def api_atualizacao_estado():
        return jsonify({"success": True, **obter_atualizador().estado()})

    @api.get("/atualizacao/novidade")
    def api_atualizacao_novidade():
        """Depois de atualizar: {'de','para'} uma unica vez (para 'atualizacao concluida')."""
        return jsonify({"success": True, "novidade": obter_atualizador().novidade_pos_atualizacao()})

    @api.post("/atualizacao/verificar")
    def api_atualizacao_verificar():
        at = obter_atualizador()
        if at.estado()["fase"] not in ("verificando", "baixando", "aplicando"):
            threading.Thread(target=lambda: at.verificar(manual=True), name="hghub-verificar", daemon=True).start()
        return jsonify({"success": True, **at.estado()})

    @api.post("/atualizacao/baixar")
    def api_atualizacao_baixar():
        at = obter_atualizador()
        if not at.iniciar_download():
            return erro("Não há atualização para baixar agora.", 409)
        return jsonify({"success": True, **at.estado()})

    @api.post("/atualizacao/aplicar")
    def api_atualizacao_aplicar():
        try:
            obter_atualizador().aplicar()
        except A.ErroAtualizacao as falha:
            return erro(str(falha), 409)
        return jsonify({"success": True, "message": "Instalando a atualização. O sistema vai fechar e abrir de novo sozinho."})

    @api.post("/atualizacao/lembrar")
    def api_atualizacao_lembrar():
        obter_atualizador().lembrar_mais_tarde(24)
        return jsonify({"success": True, **obter_atualizador().estado()})

    @api.post("/atualizacao/restaurar")
    def api_atualizacao_restaurar():
        try:
            obter_atualizador().restaurar_versao_anterior()
        except A.ErroAtualizacao as falha:
            return erro(str(falha), 409)
        return jsonify({"success": True, "message": "Voltando para a versão anterior. O sistema vai reabrir sozinho."})
