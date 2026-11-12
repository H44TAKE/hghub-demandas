# -*- coding: utf-8 -*-
"""Fabrica da aplicacao Flask do Sistema de Demandas - REDE HG."""

import logging
import math
import os

from flask import Flask, jsonify
from flask.json.provider import DefaultJSONProvider

from .utils import BASE_DIR, ARQUIVO_DADOS, criar_arquivos_automaticamente

__all__ = ["create_app", "ARQUIVO_DADOS"]

TAMANHO_MAXIMO_UPLOAD = 32 * 1024 * 1024  # 32 MB por requisição (anexos em base64)


def _sem_nan(valor):
    """Troca NaN/infinito por None (-> null) em qualquer estrutura aninhada."""
    if isinstance(valor, float):
        return None if (math.isnan(valor) or math.isinf(valor)) else valor
    if isinstance(valor, dict):
        return {k: _sem_nan(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_sem_nan(v) for v in valor]
    return valor


class ProvedorJsonSeguro(DefaultJSONProvider):
    """JSON das respostas da API sempre VALIDO. O `json` do Python escreve NaN/Infinity, que nao sao
    JSON de verdade: o navegador (`response.json()`) rejeita a resposta INTEIRA, e uma unica celula
    vazia da planilha deixava a tela toda vazia (Cadastro de Frota, busca de placa...). Aqui qualquer
    NaN que escape do tratamento de origem sai como null."""

    def dumps(self, obj, **kwargs):
        return super().dumps(_sem_nan(obj), **kwargs)


def configurar_logs():
    """Log em arquivo, ao lado da aplicacao. Empacotado com --windowed nao ha console."""
    if logging.getLogger("hghub").handlers:
        return
    logger = logging.getLogger("hghub")
    logger.setLevel(logging.INFO)
    formato = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    try:
        from logging.handlers import RotatingFileHandler
        from .utils import EMPACOTADO, PASTA_LOCAL_APP
        pasta_logs = os.path.join(PASTA_LOCAL_APP, "logs") if EMPACOTADO else BASE_DIR
        os.makedirs(pasta_logs, exist_ok=True)
        arquivo = RotatingFileHandler(os.path.join(pasta_logs, "hghub.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        arquivo.setFormatter(formato)
        logger.addHandler(arquivo)
    except OSError:
        pass

    try:
        import sys
        if sys.stderr is not None and sys.stderr.isatty():
            console = logging.StreamHandler()
            console.setFormatter(formato)
            logger.addHandler(console)
    except Exception:
        pass


def create_app():
    configurar_logs()
    criar_arquivos_automaticamente()

    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
        static_url_path="/static",
    )
    app.json = ProvedorJsonSeguro(app)
    app.config["MAX_CONTENT_LENGTH"] = TAMANHO_MAXIMO_UPLOAD
    app.config["JSON_SORT_KEYS"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    from .routes import api, arquivos, paginas

    app.register_blueprint(paginas)
    app.register_blueprint(arquivos)
    app.register_blueprint(api)

    @app.errorhandler(404)
    def nao_encontrado(_erro):
        return jsonify({"success": False, "message": "Recurso não encontrado."}), 404

    @app.errorhandler(413)
    def arquivo_grande(_erro):
        return jsonify({"success": False, "message": "Arquivo muito grande (limite de 32 MB)."}), 413

    from .models import BaseIlegivelError

    @app.errorhandler(BaseIlegivelError)
    def base_ilegivel(_erro):
        return jsonify({"success": False, "message": "A base de dados não pôde ser lida agora (rede instável ou arquivo "
                        "danificado). Nada foi alterado. Tente novamente; se persistir, avise o administrador."}), 503

    @app.errorhandler(500)
    def erro_interno(erro):
        logging.getLogger("hghub").exception("Erro interno: %s", erro)
        return jsonify({"success": False, "message": "Erro interno no servidor."}), 500

    @app.after_request
    def sem_cache(resposta):
        # A janela do pywebview guarda respostas de forma agressiva; sem isso a
        # interface continua servindo CSS/JS antigos depois de uma atualização.
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "DENY")
        resposta.headers.setdefault("Referrer-Policy", "no-referrer")
        if resposta.mimetype in ("text/html", "text/css", "application/javascript", "text/javascript"):
            resposta.headers["Cache-Control"] = "no-store, max-age=0"
        return resposta

    return app
