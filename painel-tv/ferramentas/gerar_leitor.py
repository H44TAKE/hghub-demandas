# -*- coding: utf-8 -*-
"""Regenera servidor/leitor.py: cópia SOMENTE-LEITURA da lógica de backend/manutencao.py (app principal).

Uso (de dentro de painel-tv/, no PC de desenvolvimento onde está o app principal):
    python ferramentas/gerar_leitor.py
Depois rode os testes:
    python -m unittest discover -s testes

O leitor é GERADO (não editado à mão) para que a regra de leitura/status do painel seja sempre
a mesma do app principal. Este script só funciona onde a pasta `backend/` do app principal existe;
no mini PC da TV ele não é necessário.
"""
import ast
import os
import sys

PASTA_PAINEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ = os.path.dirname(PASTA_PAINEL)
ORIGEM = os.path.join(RAIZ, "backend", "manutencao.py")

# Tudo o que é escrita no Drive/planilha, cadastro, ocultos e telas do app principal.
DESCARTAR = set("""
log ABA_PADRAO _ESCOPOS MIME_XLSX SETOR_MANUTENCAO ABA_ULTIMO_KM CAMPOS_EDITAVEIS ConflitoEdicao
usuario_pode_ver_manutencao
_NS_MAIN _NS_REL_DOC _NS_REL_PKG _RE_REF_CELULA _EPOCA_EXCEL _achar_linha_planilha _coluna_para_letra
_letra_para_coluna _data_para_serial_excel _formatar_numero_xml _localizar_xml_da_aba _achar_ou_criar_celula
_definir_valor_celula_xml _patch_celula_xml _patch_linha_xml _achar_primeira_linha_vazia
_marcar_recalculo_workbook_xml _reconstruir_zip_com_arquivos_trocados _converter_para_escrita
_valor_planilha_para_json _valores_conflitam atualizar_campo_veiculo ABAS_ESSENCIAIS validar_estrutura_planilha
substituir_planilha_no_drive _atualizar_cache_planilha buscar_veiculos buscar_veiculos_e_metadata
CAMPOS_CADASTRO_FROTA CAMPOS_CADASTRO_CARRETAS TIPOS_CADASTRO listar_cadastro cadastrar_veiculo
ARQUIVO_OCULTOS _ocultos_cache _aviso_ocultos_em carregar_ocultos definir_visibilidade
resumo_analitico_matriz linhas_categorias linhas_categoria CATEGORIAS_TABELA
""".split())

CABECALHO = '''# -*- coding: utf-8 -*-
"""Leitor SOMENTE-LEITURA da planilha de manutenção (Google Drive) para o Painel de TV.

Cópia enxuta de `backend/manutencao.py` do app principal (App matheus): a MESMA lógica
de leitura, cruzamento das abas e cálculo de status, sem nenhuma função de escrita
(editar célula, importar planilha, cadastro, ocultos). O painel nunca grava nada no Drive:
as credenciais são pedidas com escopo `drive.readonly`.

GERADO por ferramentas/gerar_leitor.py - se a regra de leitura mudar no app principal,
regenere este arquivo em vez de editar à mão (ver LEIA-ME.md, "Manutenção do painel").
"""

import io
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta

from .configuracao import ARQUIVO_CREDENCIAIS, carregar_config

log = logging.getLogger("painel")

ABA_PADRAO = "Revisões e óleo"

# Só leitura: o painel nunca altera a planilha.
_ESCOPOS = ["https://www.googleapis.com/auth/drive.readonly"]
ARQUIVO_CREDENCIAIS_SHEETS = ARQUIVO_CREDENCIAIS


def carregar_ocultos():
    """O painel mostra TODOS os veículos (decisão do setor): não há lista de ocultos."""
    return {}

'''


def nomes(no):
    if isinstance(no, (ast.FunctionDef, ast.ClassDef)):
        return {no.name}
    if isinstance(no, ast.Assign):
        return {t.id for t in no.targets if isinstance(t, ast.Name)}
    if isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name):
        return {no.target.id}
    return set()


def gerar(destino):
    origem = open(ORIGEM, encoding="utf-8").read()
    linhas = origem.split("\n")
    corpo = []
    for no in ast.parse(origem).body:
        if isinstance(no, (ast.Import, ast.ImportFrom)):
            continue  # os imports são reescritos no cabeçalho
        if isinstance(no, ast.Expr) and isinstance(no.value, ast.Constant) and isinstance(no.value.value, str):
            continue  # docstring do módulo
        if nomes(no) & DESCARTAR:
            continue
        inicio = no.lineno - 1
        while inicio > 0 and linhas[inicio - 1].lstrip().startswith("#"):
            inicio -= 1  # comentários colados acima do nó fazem parte dele
        corpo.append("\n".join(linhas[inicio:no.end_lineno]))
    texto = CABECALHO + "\n\n" + "\n\n\n".join(corpo) + "\n"
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)
    print("gerado", destino, "-", len(texto.split("\n")), "linhas")


if __name__ == "__main__":
    gerar(sys.argv[1] if len(sys.argv) > 1 else os.path.join(PASTA_PAINEL, "servidor", "leitor.py"))
