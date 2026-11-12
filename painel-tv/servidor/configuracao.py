# -*- coding: utf-8 -*-
"""Caminhos e configuração do Painel de TV.

Tudo mora na pasta do próprio painel (ao lado de `painel.py`, ou do .exe se for
empacotado): não depende da unidade Z: nem de nenhum outro programa.

- `config.json`      : ID da planilha no Drive, nome da aba, intervalo de atualização.
- `credenciais.json` : chave da Service Account do Google. É um SEGREDO: copie à mão para
                       o mini PC, nunca coloque em e-mail, zip ou pasta compartilhada.
                       Pode ficar em outro lugar apontando com a variável `PAINEL_CREDENCIAIS`.
"""

import json
import logging
import os
import sys

log = logging.getLogger("painel")

if getattr(sys, "frozen", False):
    PASTA_BASE = os.path.dirname(sys.executable)
else:
    PASTA_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASTA_WEB = os.path.join(PASTA_BASE, "web")
ARQUIVO_CONFIG = os.environ.get("PAINEL_CONFIG") or os.path.join(PASTA_BASE, "config.json")
ARQUIVO_CREDENCIAIS = os.environ.get("PAINEL_CREDENCIAIS") or os.path.join(PASTA_BASE, "credenciais.json")

PADROES = {
    "planilha_id": "",
    "aba": "Revisões e óleo",
    "porta": 5001,
    "atualizar_a_cada_segundos": 300,
    "modo_demo": False,
    # Frentes que o painel NÃO mostra (nem o cartão, nem os chips, nem entram na conta de quem
    # precisa de atenção). Chaves: revisao_oleo, alinhamento, quinta_roda, integridade_chassi,
    # sr_tanques, preventiva_carretas. O Alinhamento fica de fora porque a aba da planilha está
    # desatualizada; quando for atualizada, tire "alinhamento" desta lista e reinicie o painel.
    "frentes_ocultas": ["alinhamento"],
}


def carregar_config():
    """Devolve a configuração com os padrões preenchidos. Arquivo ausente ou quebrado
    não derruba o painel: ele sobe e mostra o aviso de "não configurado"."""
    config = dict(PADROES)
    try:
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8-sig") as f:
            lida = json.load(f)
        if isinstance(lida, dict):
            config.update({k: v for k, v in lida.items() if k in PADROES})
    except FileNotFoundError:
        pass
    except Exception as falha:
        log.error("config.json ilegível (%s). Usando os padrões.", falha)

    if not isinstance(config["frentes_ocultas"], list):
        log.error("config.json: 'frentes_ocultas' precisa ser uma lista. Usando o padrão.")
        config["frentes_ocultas"] = list(PADROES["frentes_ocultas"])
    config["frentes_ocultas"] = [str(f).strip() for f in config["frentes_ocultas"] if str(f).strip()]

    # Variáveis de ambiente vencem o arquivo (úteis para teste sem mexer no config).
    if os.environ.get("PAINEL_PLANILHA_ID"):
        config["planilha_id"] = os.environ["PAINEL_PLANILHA_ID"]
    if os.environ.get("PAINEL_DEMO"):
        config["modo_demo"] = os.environ["PAINEL_DEMO"] not in ("0", "false", "False", "")

    # O leitor (copiado do app principal) lê o ID e a aba daqui, sob a chave "google_sheets".
    config["google_sheets"] = {"planilha_id": str(config["planilha_id"] or "").strip(), "aba": config["aba"]}
    return config
