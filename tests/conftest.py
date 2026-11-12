# -*- coding: utf-8 -*-
"""Configuracao global dos testes (pytest).

REGRA DE OURO: nenhum teste pode tocar a base de producao (Z:/Y:). Por isso, ANTES de qualquer
`import backend`, a variavel HGHUB_ARQUIVO_DADOS aponta para uma pasta temporaria exclusiva da
sessao de testes, e a pasta local do app (estado do atualizador, logs...) tambem."""

import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE_TESTES = tempfile.mkdtemp(prefix="hghub_pytest_")
os.environ["HGHUB_ARQUIVO_DADOS"] = os.path.join(_BASE_TESTES, "dados", "dados_sistema_hg.json")
os.environ["HGHUB_DIR_LOCAL"] = os.path.join(_BASE_TESTES, "local")
os.environ.pop("HGHUB_SHEETS_ID", None)
os.makedirs(os.path.dirname(os.environ["HGHUB_ARQUIVO_DADOS"]), exist_ok=True)
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

assert not os.environ["HGHUB_ARQUIVO_DADOS"].upper().startswith(("Z:", "Y:"))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_BASE_TESTES, ignore_errors=True)
