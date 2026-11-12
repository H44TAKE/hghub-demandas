# -*- coding: utf-8 -*-
"""Integracao com o Google Drive para o Modulo de Manutencao Preventiva.

Aposenta a planilha `FORM-MANUT 001 - Controle de preventiva.xlsx` do setor de
Manutencao: em vez de abrir o Excel local, o backend baixa o MESMO arquivo
.xlsx direto do Google Drive (Drive API v3, `files().get_media`) e le seus
bytes em memoria com pandas/openpyxl. A equipe de Manutencao continua editando
o arquivo Office normalmente (Excel ou Google Drive) - nada aqui converte ou
duplica o arquivo, entao nao ha risco de duas copias dessincronizadas. O
layout de colunas replica exatamente a aba "Revisões e óleo" da planilha
original - ver [[Módulo de Manutenção]] no Segundo Cérebro do projeto para o
mapeamento completo e o porque de cada indice.

Sincronizacao BIDIRECIONAL: alem de ler, este modulo tambem escreve de volta
no MESMO arquivo (`atualizar_campo_veiculo`, `substituir_planilha_no_drive`).
Toda escrita segue duas regras inegociaveis, documentadas em detalhe em
[[Módulo de Manutenção]]:
  1. So celulas de ENTRADA BRUTA sao editaveis (ex.: "última km", "última
     data") - nunca uma celula com formula (ex.: STATUS, PRÓXIMA, FALTA).
     Escrever numa formula a apagaria permanentemente. `CAMPOS_EDITAVEIS`
     e a unica porta de entrada permitida.
  2. Concorrencia otimista: outro app (`app-patio@...`) e pessoas de verdade
     tambem escrevem neste arquivo pelo Excel/Drive ao mesmo tempo. Toda
     escrita compara o `modifiedTime` atual do arquivo com o que o cliente
     tinha na tela; se mudou, recusa a escrita (`ConflitoEdicao`) em vez de
     arriscar sobrescrever a mudanca de outra pessoa.
A rota que consome isto fica em `backend/routes.py` (`/api/manutencao*`).
"""

import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

from .utils import ARQUIVO_CREDENCIAIS_SHEETS, bloqueio_arquivo, caminho_arquivo_dados, carregar_config

log = logging.getLogger("hghub")

ABA_PADRAO = "Revisões e óleo"
SETOR_MANUTENCAO = "Manutenção"

# Escopo completo (nao só leitura): o write-back precisa gravar no arquivo.
_ESCOPOS = ["https://www.googleapis.com/auth/drive"]

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Gravidade de cada status, do melhor para o pior - usada para calcular o pior
# status entre os 5 itens controlados por veiculo (status_geral).
ORDEM_STATUS = {"EM DIA": 0, "PROGRAMAR PARADA": 1, "PARAR URGENTE": 2}

# Campos de "Revisões e óleo" que podem ser editados pelo app (write-back).
# Cada valor é (aba_alvo, coluna_placa, coluna_valor, tipo): `aba_alvo=None`
# significa "a mesma aba configurada em `_config_sheets()`" (normalmente
# ABA_PADRAO), com a placa na coluna B (2) - o caso comum. `km_atual` é a
# ÚNICA exceção: confirmado lendo o arquivo real, a célula "KM ATUAL" na aba
# principal é uma FÓRMULA (`=VLOOKUP(placa, 'Último km'!A:B, 2, 0)`), não uma
# entrada bruta - a fonte de verdade é a aba utilitária "Último km"
# (coluna A = placa, B = km). As demais colunas foram auditadas uma a uma
# contra o arquivo real (nenhuma tem fórmula em nenhuma linha de veículo de
# verdade) antes de entrarem nesta lista - ver [[Módulo de Manutenção]].
ABA_ULTIMO_KM = "Último km"

CAMPOS_EDITAVEIS = {
    "km_atual": (ABA_ULTIMO_KM, 1, 2, "numero"),
    "observacoes": (None, 2, 55, "texto"),
    "revisao.ultima_data": (None, 2, 7, "data"),
    "revisao.ultima_km": (None, 2, 8, "numero"),
    "aditivo.ultima_data": (None, 2, 12, "data"),
    "aditivo.ultima_km": (None, 2, 13, "numero"),
    "oleo_cambio.ultima_data": (None, 2, 17, "data"),
    "oleo_cambio.ultima_km": (None, 2, 18, "numero"),
    "oleo_diferencial.ultima_data": (None, 2, 21, "data"),
    "oleo_diferencial.ultima_km": (None, 2, 22, "numero"),
    "oleo_motor.ultima_data": (None, 2, 25, "data"),
    "oleo_motor.ultima_km": (None, 2, 26, "numero"),
}


_PADRAO_PLACA = re.compile(r"^[A-Z]{3}-?\d[A-Z0-9]\d{2}$")


def _placa_valida(placa):
    """Formato de placa brasileira (antiga LLL-NNNN ou Mercosul LLLNLNN), com
    ou sem hífen. Existe porque a aba "Frota" real tem 2 linhas (auditadas
    manualmente) que usam a própria coluna Placa como anotação livre da
    equipe - "Veículos novos" e "Plano SCANIA", com o resto da linha quase
    vazio - em vez de uma placa de verdade. Sem este filtro, o Cadastro de
    Frota mostraria essas 2 anotações como se fossem veículos cadastrados,
    com todo o resto dos campos em branco. Usado tanto para filtrar essas
    linhas fantasma na leitura quanto para validar o formulário de cadastro
    novo antes de gravar."""
    return bool(_PADRAO_PLACA.match(placa))


class PlanilhaNaoConfigurada(Exception):
    """Falta alguma peça (biblioteca, credencial ou ID do arquivo) para ler o
    Drive - nunca deixa a rota derrubar o app, so avisa o que falta."""


class ConflitoEdicao(Exception):
    """Alguem mais (outra pessoa no Excel/Drive, ou o outro app que também
    escreve neste arquivo) alterou a MESMA CÉLULA entre a tela abrir e o
    usuário salvar. Recusa a escrita em vez de arriscar apagar a mudança
    alheia. `valor_atual`/`modificado_em` (quando presentes) dão ao frontend
    o suficiente para oferecer um botão "Atualizar" que já mostra o valor
    novo, em vez de só um erro sem saída - ver `atualizar_campo_veiculo`."""

    def __init__(self, mensagem, valor_atual=None, modificado_em=None):
        super().__init__(mensagem)
        self.valor_atual = valor_atual
        self.modificado_em = modificado_em


def usuario_pode_ver_manutencao(usuario):
    """Regra de acesso do módulo: setor Manutenção ou Conta Mestre."""
    if not usuario:
        return False
    if usuario.conta_mestre:
        return True
    return SETOR_MANUTENCAO in (usuario.setores or [])


def _config_sheets():
    config = carregar_config().get("google_sheets", {}) or {}
    planilha_id = os.environ.get("HGHUB_SHEETS_ID") or config.get("planilha_id") or ""
    aba = os.environ.get("HGHUB_SHEETS_ABA") or config.get("aba") or ABA_PADRAO
    return planilha_id.strip(), aba


_servico_drive_cache = None


def _obter_servico_drive():
    """Constrói o client do Drive UMA vez por processo e reaproveita depois -
    criar `Credentials`/`build()` do zero custa ~1s de handshake OAuth2 a
    cada chamada (medido contra a API real), e o objeto de credenciais do
    google-auth já sabe renovar o próprio token sozinho quando expira, então
    reaproveitar é seguro para a vida inteira do processo (é o padrão
    recomendado pela própria Google, não uma otimização arriscada)."""
    global _servico_drive_cache
    if _servico_drive_cache is not None:
        return _servico_drive_cache

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError as falha:
        raise PlanilhaNaoConfigurada(
            "Bibliotecas do Google Drive não instaladas. "
            "Rode: pip install google-api-python-client google-auth pandas openpyxl"
        ) from falha

    if not os.path.exists(ARQUIVO_CREDENCIAIS_SHEETS):
        raise PlanilhaNaoConfigurada(
            f"Arquivo de credenciais não encontrado em '{ARQUIVO_CREDENCIAIS_SHEETS}'. "
            "Gere uma chave de Service Account no Google Cloud e salve-a com esse nome."
        )

    try:
        credenciais = Credentials.from_service_account_file(ARQUIVO_CREDENCIAIS_SHEETS, scopes=_ESCOPOS)
        _servico_drive_cache = build("drive", "v3", credentials=credenciais, cache_discovery=False)
        return _servico_drive_cache
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Credencial do Google inválida: {falha}") from falha


def _baixar_planilha_excel(servico, arquivo_id):
    """Baixa o .xlsx original do Drive inteiro para a memoria (nunca para disco
    e nunca converte o arquivo - a equipe de Manutencao continua editando o
    mesmo arquivo Office, sem risco de cópias dessincronizadas)."""
    from googleapiclient.http import MediaIoBaseDownload

    buffer = io.BytesIO()
    requisicao = servico.files().get_media(fileId=arquivo_id)
    downloader = MediaIoBaseDownload(buffer, requisicao)
    concluido = False
    while not concluido:
        _, concluido = downloader.next_chunk()
    buffer.seek(0)
    return buffer


_cache_planilha = {"arquivo_id": None, "modificado_em": None, "bytes": None, "verificado_em": 0.0}
_trava_download = threading.Lock()

# Por quanto tempo a planilha baixada vale SEM perguntar nada ao Drive. Navegar entre
# as abas não deve baixar/consultar a planilha de novo: uma vez baixada, vale para
# todas. Passado esse tempo, a próxima leitura pergunta ao Drive só o `modifiedTime`
# (barato) e baixa de novo apenas se alguém mudou o arquivo. Atualização manual
# (`forcar_atualizacao`) e TODA escrita (`fresco=True`) ignoram esta janela.
IDADE_MAXIMA_CACHE_SEGUNDOS = 300


def forcar_atualizacao():
    """Faz a PRÓXIMA leitura conferir o arquivo no Drive agora (botão de atualizar)."""
    _cache_planilha["verificado_em"] = 0.0


def _obter_bytes_planilha(servico, arquivo_id, fresco=False):
    """Baixa o .xlsx, reaproveitando o cache em memória.

    - Dentro de `IDADE_MAXIMA_CACHE_SEGUNDOS` da última conferência, devolve o cache
      sem falar com o Drive (é isso que faz trocar de aba ser instantâneo).
    - Depois disso (ou com `fresco=True`), confere o `modifiedTime` no Drive
      (`files().get`, ~0.3-0.7s, sem baixar conteúdo) e só baixa de novo (~3-10s)
      se o arquivo mudou.

    Toda ESCRITA usa `fresco=True`: a checagem de conflito por célula precisa do
    conteúdo mais recente, nunca de um cache dentro da janela.

    `_trava_download` evita que várias requisições simultâneas (o frontend dispara
    várias ao abrir a tela) baixem o mesmo arquivo em paralelo: as outras esperam e
    reaproveitam. Devolve (bytes, modificado_em)."""
    with _trava_download:
        c = _cache_planilha
        mesmo_arquivo = c["arquivo_id"] == arquivo_id and c["bytes"] is not None
        if mesmo_arquivo and not fresco and (time.monotonic() - c["verificado_em"]) < IDADE_MAXIMA_CACHE_SEGUNDOS:
            return c["bytes"], c["modificado_em"]

        metadata = servico.files().get(fileId=arquivo_id, fields="modifiedTime").execute()
        modificado_em = metadata["modifiedTime"]
        if mesmo_arquivo and c["modificado_em"] == modificado_em:
            c["verificado_em"] = time.monotonic()
            return c["bytes"], modificado_em

        conteudo = _baixar_planilha_excel(servico, arquivo_id).read()
        c.update(arquivo_id=arquivo_id, modificado_em=modificado_em, bytes=conteudo, verificado_em=time.monotonic())
        return conteudo, modificado_em


def _atualizar_cache_planilha(arquivo_id, modificado_em, conteudo):
    """Depois de um write-back bem-sucedido, o app já tem em mãos os bytes
    novos e o `modifiedTime` novo - grava os dois direto no cache em vez de
    invalidar e forçar um download extra na próxima leitura."""
    _cache_planilha.update(arquivo_id=arquivo_id, modificado_em=modificado_em, bytes=conteudo,
                           verificado_em=time.monotonic())


# Resultado já PROCESSADO de cada leitura (as ~11 abas do Excel cruzadas por placa levam
# vários segundos no pandas): calculado uma vez por versão da planilha e reaproveitado
# por TODAS as abas/rotas. A chave inclui o `modifiedTime`, então uma planilha que mudou
# (ou uma escrita nossa, que troca o `modifiedTime` no cache) invalida sozinha. RLock porque
# o prontuário reaproveita a leitura de veículos de dentro da própria construção.
_memos = {}
_trava_memos = threading.RLock()


def _memoizado(chave, modificado_em, construir):
    with _trava_memos:
        guardado = _memos.get(chave)
        if guardado and guardado["modificado_em"] == modificado_em:
            return guardado["valor"]
        valor = construir()
        _memos[chave] = {"modificado_em": modificado_em, "valor": valor}
        return valor


def _valor_celula_pandas(valor):
    """Normaliza um valor cru do pandas: NaN/NaT -> None, escalares numpy ->
    tipo nativo do Python, Timestamp mantido (já é um `datetime`)."""
    import pandas as pd

    if pd.isna(valor):
        return None
    if isinstance(valor, pd.Timestamp):
        return valor.to_pydatetime()
    if hasattr(valor, "item"):
        return valor.item()
    return valor


# ==============================================================
# PARSING (agnostico da fonte: funciona com uma lista de valores por linha,
# venha do pandas.read_excel (producao) ou de uma leitura local para depuracao)
# ==============================================================
def _celula(linha, indice_1_based):
    posicao = indice_1_based - 1
    return linha[posicao] if 0 <= posicao < len(linha) else None


def _vazio(valor):
    """None, NaN ou infinito (a celula vazia que o pandas devolve como float NaN)."""
    return valor is None or (isinstance(valor, float) and (valor != valor or valor in (float("inf"), float("-inf"))))


def _texto(valor):
    # Celula vazia vira None, nunca o texto "nan": o pandas devolve float NaN para ela, e a
    # leitura do cadastro (Frota/info-carretas) passa os valores crus para ca.
    if _vazio(valor):
        return None
    # Campos textuais (chamado, OC/OS, tipo) as vezes chegam como float do
    # pandas quando a celula parece um numero - sem isso, "6200105.0" em vez
    # de "6200105".
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = str(valor).strip()
    return texto or None


def _numero(valor):
    """'145.824' (separador de milhar BR) -> 145824. Aceita numero puro tambem. Celula vazia
    (None/NaN/infinito) -> None: um NaN que chegue ao JSON e invalido e o navegador descarta a
    resposta inteira (a tela fica vazia)."""
    if _vazio(valor):
        return None
    if isinstance(valor, (int, float)):
        return valor
    texto = str(valor).strip()
    if not texto:
        return None
    limpo = texto.replace(".", "").replace(",", ".")
    try:
        numero = float(limpo)
        return int(numero) if numero.is_integer() else numero
    except ValueError:
        return None


def _data(valor):
    # pd.NaT passa em `isinstance(valor, datetime)` (herda de datetime.datetime)
    # mas não aceita `.strftime()` - a checagem de NaN/NaT já deveria ter
    # acontecido antes (`_valor_celula_pandas`), mas blindar aqui também custa
    # nada e evita esse mesmo susto em qualquer chamada futura direta.
    if isinstance(valor, datetime) and str(valor) == "NaT":
        return None
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d")
    texto = _texto(valor)
    if not texto:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _status_geral(*status_itens):
    validos = [s for s in status_itens if s in ORDEM_STATUS]
    if not validos:
        return None
    return max(validos, key=lambda s: ORDEM_STATUS[s])


def _item_manutencao(linha, ultima_data_col, ultima_km_col, proxima_km_col, km_falta_col,
                      status_col, prog_col=None, viscosidade_col=None, tipo_col=None):
    item = {
        "ultima_data": _data(_celula(linha, ultima_data_col)),
        "ultima_km": _numero(_celula(linha, ultima_km_col)),
        "proxima_km": _numero(_celula(linha, proxima_km_col)),
        "km_falta": _numero(_celula(linha, km_falta_col)),
        "status": _texto(_celula(linha, status_col)),
    }
    if prog_col:
        item["prog_troca"] = _texto(_celula(linha, prog_col))
    if viscosidade_col:
        item["viscosidade"] = _texto(_celula(linha, viscosidade_col))
    if tipo_col:
        item["tipo"] = _texto(_celula(linha, tipo_col))
    return item


def _parse_linha_veiculo(linha):
    """Converte uma linha crua da aba "Revisões e óleo" (lista de valores, 1
    posição por coluna) no registro de um veículo, ou None se a linha não for
    uma linha de veículo de verdade (títulos, rodapés, tabelas auxiliares que
    a planilha original espalha nas mesmas linhas em colunas mais à direita).

    Mapeamento de colunas (1-based, igual ao número da coluna na planilha):
    1 COD · 2 Placa · 3 Marca · 4 Modelo · 5 Ano fab · 6 Ano mod
    7-10   última revisão (data, km, chamado, OC/OS)
    12-15  última troca aditivo (data, km, tipo, OC/OS)
    17-19  última troca óleo câmbio (data, km, OC/OS)
    21-23  última troca óleo diferencial (data, km, OC/OS)
    25-27  última troca óleo motor (data, km, OC/OS)
    28 KM atual · 30 média KM/mês · 55 observações · 56 centro de custo
    31-34  próxima revisão (km, km falta, status, prog. troca)
    35-38  próxima troca aditivo (km, km falta, status, prog. troca)
    39-42,44  próxima troca óleo câmbio (km, km falta, viscosidade, status, prog. troca)
    45-49  próxima troca óleo diferencial (km, km falta, viscosidade, status, prog.)
    50-54  próxima troca óleo motor (km, km falta, viscosidade, status, prog. troca)
    """
    cod = _texto(_celula(linha, 1))
    placa = _texto(_celula(linha, 2))
    if not cod or not placa:
        return None
    # A leitura crua (header=None) inclui a própria linha de cabeçalho da aba
    # ("COD" / "Placa" nas colunas 1/2) - descarta antes de virar um "veículo".
    if cod.strip().upper() == "COD" or placa.strip().upper() == "PLACA":
        return None

    revisao = _item_manutencao(linha, 7, 8, 31, 32, 33, prog_col=34)
    aditivo = _item_manutencao(linha, 12, 13, 35, 36, 37, prog_col=38, tipo_col=14)
    oleo_cambio = _item_manutencao(linha, 17, 18, 39, 40, 42, prog_col=44, viscosidade_col=41)
    oleo_diferencial = _item_manutencao(linha, 21, 22, 45, 46, 48, prog_col=49, viscosidade_col=47)
    oleo_motor = _item_manutencao(linha, 25, 26, 50, 51, 53, prog_col=54, viscosidade_col=52)

    return {
        "cod": cod,
        "placa": placa,
        "marca": _texto(_celula(linha, 3)),
        "modelo": _texto(_celula(linha, 4)),
        "ano_fab": _numero(_celula(linha, 5)),
        "ano_mod": _numero(_celula(linha, 6)),
        "km_atual": _numero(_celula(linha, 28)),
        "media_km_mes": _numero(_celula(linha, 30)),
        "centro_custo": _texto(_celula(linha, 56)),
        "observacoes": _texto(_celula(linha, 55)),
        "revisao": revisao,
        "aditivo": aditivo,
        "oleo_cambio": oleo_cambio,
        "oleo_diferencial": oleo_diferencial,
        "oleo_motor": oleo_motor,
        "status_geral": _status_geral(
            revisao["status"], aditivo["status"], oleo_cambio["status"],
            oleo_diferencial["status"], oleo_motor["status"],
        ),
    }


def _ler_veiculos_da_aba(buffer, aba):
    """Transforma o buffer .xlsx (já baixado) na lista de veículos. Função pura
    - não fala com o Drive - para ser reaproveitada tanto por `buscar_veiculos`
    (só leitura) quanto por `buscar_veiculos_e_metadata` (leitura + modifiedTime
    para controle de concorrência do write-back)."""
    try:
        import pandas as pd
        tabela = pd.read_excel(buffer, sheet_name=aba, header=None, engine="openpyxl")
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível ler a aba '{aba}' do arquivo: {falha}") from falha

    # Descarta de cara linhas sem Placa (coluna 2 = índice 1): o Excel real tem
    # centenas de linhas em branco abaixo da última frota cadastrada (a
    # formatação de célula "usa" a linha inteira sem ela ter dado nenhum), e
    # não faz sentido nem montar o dicionário de um veículo que não existe.
    if 1 in tabela.columns:
        coluna_placa = tabela[1].astype(str).str.strip()
        tabela = tabela[tabela[1].notna() & (coluna_placa != "") & (coluna_placa.str.lower() != "nan")]

    veiculos = []
    for tupla in tabela.itertuples(index=False, name=None):
        linha = [_valor_celula_pandas(valor) for valor in tupla]
        registro = _parse_linha_veiculo(linha)
        if registro:
            veiculos.append(registro)
    return veiculos


def _veiculos_memoizados(conteudo, arquivo_id, modificado_em, aba):
    return _memoizado(("veiculos", arquivo_id, aba), modificado_em,
                      lambda: _ler_veiculos_da_aba(io.BytesIO(conteudo), aba))


def buscar_veiculos():
    """Baixa o .xlsx original do Drive e converte a aba de controle em uma
    lista de veículos.

    Lança `PlanilhaNaoConfigurada` se qualquer pré-requisito faltar (biblioteca,
    credencial, ID do arquivo) - a rota Flask decide como reagir a isso, sem
    derrubar o app. Nunca deixe essa função ser chamada fora de um try/except.
    """
    arquivos, _ = buscar_veiculos_e_metadata()
    return arquivos


def buscar_veiculos_e_metadata():
    """Igual a `buscar_veiculos()`, mas também devolve o `modifiedTime` atual
    do arquivo no Drive - o app guarda esse valor e o devolve numa eventual
    edição, para o backend detectar se alguém mexeu na planilha nesse meio
    tempo (ver `ConflitoEdicao`)."""
    arquivo_id, aba = _config_sheets()
    if not arquivo_id:
        raise PlanilhaNaoConfigurada(
            "ID do arquivo no Google Drive não configurado (config.json → "
            "google_sheets.planilha_id, ou variável de ambiente HGHUB_SHEETS_ID)."
        )

    servico = _obter_servico_drive()
    try:
        conteudo, modificado_em = _obter_bytes_planilha(servico, arquivo_id)
    except PlanilhaNaoConfigurada:
        raise
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível baixar o arquivo do Drive: {falha}") from falha

    veiculos = _veiculos_memoizados(conteudo, arquivo_id, modificado_em, aba)
    ocultos = carregar_ocultos()
    return [v for v in veiculos if (v.get("placa") or "").strip().upper() not in ocultos], modificado_em


# ==============================================================
# WRITE-BACK (sincronização bidirecional com o Drive)
#
# Duas operações, dois riscos diferentes, duas defesas diferentes:
#   - `atualizar_campo_veiculo`: edita UMA célula de entrada bruta de UMA
#     placa. Risco = colidir com outra edição concorrente -> defesa =
#     concorrência otimista via `modifiedTime`.
#   - `substituir_planilha_no_drive`: troca o arquivo INTEIRO (import de
#     emergência). Risco = subir um arquivo errado/corrompido por cima da
#     base real -> defesa = validar a estrutura (abas essenciais) antes de
#     upar, e o Drive mantém histórico de versões do arquivo anterior mesmo
#     assim (Versões > Gerenciar versões, recuperável se algo passar batido).
#
# PATCH CIRÚRGICO NO XML (não `openpyxl.load_workbook().save()`): testado
# contra o conteúdo real da planilha, `load_workbook().save()` apaga o valor
# calculado de TODAS as fórmulas do arquivo inteiro, não só da célula editada
# (achado documentado em [[Módulo de Manutenção]]) - inaceitável para um
# arquivo que a equipe consulta o dia inteiro. A alternativa aqui edita só o
# XML da célula-alvo, dentro do .xlsx (que é um .zip), e reconstrói o arquivo
# copiando todo o resto byte a byte - fórmulas, cache de fórmulas de outras
# células, tabelas dinâmicas, estilos, tudo o que não foi tocado sai
# idêntico ao original.
# ==============================================================
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_RE_REF_CELULA = re.compile(r"^([A-Z]+)(\d+)$")
_EPOCA_EXCEL = date(1899, 12, 30)  # serial 0 do Excel (com o bug do ano bissexto 1900 incluído de propósito)


def _achar_linha_planilha(planilha, placa_alvo, coluna_placa=2):
    """Varre a coluna da Placa (1-based, padrão B) da aba já aberta no
    openpyxl (modo leitura - nunca salva esse objeto de volta, só usa para
    achar a linha) e devolve o número da linha (1-based) da placa procurada,
    ou None. Resolve a linha NA HORA da escrita (não reaproveita um índice de
    uma leitura anterior), porque a planilha pode ter ganhado/perdido linhas
    entre a tela abrir e o usuário salvar.

    O número da linha vem do próprio contador de `enumerate`, NUNCA de
    `linha[0].row` - achado ao escrever o teste de concorrência por célula:
    em modo `read_only=True` (o modo real usado aqui), uma linha cuja
    primeira coluna nunca recebeu valor nenhum vira uma sequência de
    `EmptyCell`, que não tem atributo `.row` e derrubava a busca com
    `AttributeError` assim que a linha da placa procurada calhava de ter a
    coluna A em branco - por exemplo, o COD em branco numa linha real da
    aba Frota."""
    indice = coluna_placa - 1
    for numero_linha, linha in enumerate(planilha.iter_rows(min_row=1, max_col=coluna_placa), start=1):
        celula_placa = linha[indice].value
        if celula_placa is not None and str(celula_placa).strip().upper() == placa_alvo:
            return numero_linha
    return None


def _coluna_para_letra(indice_1_based):
    letras = ""
    n = indice_1_based
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def _letra_para_coluna(referencia_celula):
    letras = _RE_REF_CELULA.match(referencia_celula).group(1)
    indice = 0
    for caractere in letras:
        indice = indice * 26 + (ord(caractere) - 64)
    return indice


def _data_para_serial_excel(valor_data):
    if isinstance(valor_data, datetime):
        valor_data = valor_data.date()
    return (valor_data - _EPOCA_EXCEL).days


def _formatar_numero_xml(numero):
    if isinstance(numero, float) and numero.is_integer():
        numero = int(numero)
    return str(numero)


def _localizar_xml_da_aba(zip_leitura, nome_aba):
    """Descobre o caminho do XML interno (ex.: 'xl/worksheets/sheet3.xml') que
    corresponde ao nome de aba visível - a ordem física de sheet1.xml,
    sheet2.xml... não é garantida bater com a ordem das abas visíveis, então
    a única forma confiável é seguir workbook.xml -> seu .rels."""
    workbook_xml = ET.fromstring(zip_leitura.read("xl/workbook.xml"))
    r_id = None
    for sheet in workbook_xml.findall(f"{{{_NS_MAIN}}}sheets/{{{_NS_MAIN}}}sheet"):
        if sheet.get("name") == nome_aba:
            r_id = sheet.get(f"{{{_NS_REL_DOC}}}id")
            break
    if not r_id:
        return None

    rels_xml = ET.fromstring(zip_leitura.read("xl/_rels/workbook.xml.rels"))
    for rel in rels_xml.findall(f"{{{_NS_REL_PKG}}}Relationship"):
        if rel.get("Id") == r_id:
            alvo = rel.get("Target")
            # O Target de um .rels pode ser absoluto dentro do pacote
            # ("/xl/worksheets/sheet1.xml") ou relativo à pasta do arquivo que
            # o referencia ("worksheets/sheet1.xml", relativo a "xl/") - os
            # dois formatos são válidos no OOXML e apareceram em arquivos
            # diferentes durante os testes.
            if alvo.startswith("/"):
                return alvo.lstrip("/")
            return alvo if alvo.startswith("xl/") else f"xl/{alvo}"
    return None


def _achar_ou_criar_celula(linha_el, coluna):
    """Acha a `<c>` da coluna dentro de um `<row>` já carregado, criando-a
    (vazia, na posição ordenada correta) se ainda não existir. Fatorado de
    `_patch_celula_xml` para ser reaproveitado também por `_patch_linha_xml`
    (cadastro de veículo/carreta novo, que escreve várias células de uma vez
    na mesma linha)."""
    celulas = linha_el.findall(f"{{{_NS_MAIN}}}c")
    for indice, c in enumerate(celulas):
        coluna_atual = _letra_para_coluna(c.get("r"))
        if coluna_atual == coluna:
            return c
        if coluna_atual > coluna:
            novo = ET.Element(f"{{{_NS_MAIN}}}c")
            novo.set("r", f"{_coluna_para_letra(coluna)}{linha_el.get('r')}")
            linha_el.insert(indice, novo)
            return novo
    novo = ET.Element(f"{{{_NS_MAIN}}}c")
    novo.set("r", f"{_coluna_para_letra(coluna)}{linha_el.get('r')}")
    linha_el.append(novo)
    return novo


def _definir_valor_celula_xml(celula_el, valor_convertido, tipo):
    """Aplica um valor (já convertido pelo tipo) numa `<c>` existente,
    preservando o atributo `s` (estilo/formatação) e escolhendo o tipo de
    valor certo: texto vira `inlineStr` (nunca precisa tocar em
    sharedStrings.xml), número/data viram `<v>` cru. Fatorado de
    `_patch_celula_xml` para ser reaproveitado por `_patch_linha_xml`."""
    for filho in list(celula_el):
        celula_el.remove(filho)

    if valor_convertido is None:
        celula_el.attrib.pop("t", None)
    elif tipo == "texto":
        celula_el.set("t", "inlineStr")
        is_el = ET.SubElement(celula_el, f"{{{_NS_MAIN}}}is")
        t_el = ET.SubElement(is_el, f"{{{_NS_MAIN}}}t")
        t_el.text = str(valor_convertido)
    else:
        celula_el.attrib.pop("t", None)
        v_el = ET.SubElement(celula_el, f"{{{_NS_MAIN}}}v")
        v_el.text = (
            str(_data_para_serial_excel(valor_convertido)) if tipo == "data" else _formatar_numero_xml(valor_convertido)
        )


def _patch_celula_xml(xml_bytes, linha, coluna, valor_convertido, tipo):
    """Edita só a célula (linha, coluna) dentro do XML cru de UMA aba,
    preservando a formatação existente (atributo `s`, estilo) e recusando se
    a célula já tiver uma fórmula (`<f>`) - trava de segurança, não deveria
    acontecer dado que `CAMPOS_EDITAVEIS` só aponta para colunas de entrada
    bruta, mas mais vale recusar alto e claro do que apagar uma fórmula."""
    ET.register_namespace("", _NS_MAIN)
    raiz = ET.fromstring(xml_bytes)
    ref_alvo = f"{_coluna_para_letra(coluna)}{linha}"

    sheet_data = raiz.find(f"{{{_NS_MAIN}}}sheetData")
    linha_el = next((l for l in sheet_data.findall(f"{{{_NS_MAIN}}}row") if l.get("r") == str(linha)), None)
    if linha_el is None:
        raise ValueError(f"Linha {linha} não encontrada na planilha (referência {ref_alvo}).")

    celula_el = _achar_ou_criar_celula(linha_el, coluna)
    if celula_el.find(f"{{{_NS_MAIN}}}f") is not None:
        raise ValueError(f"A célula {ref_alvo} contém uma fórmula - recusado por segurança.")

    _definir_valor_celula_xml(celula_el, valor_convertido, tipo)
    return ET.tostring(raiz, encoding="UTF-8", xml_declaration=True)


def _patch_linha_xml(xml_bytes, linha, valores_por_coluna):
    """Como `_patch_celula_xml`, mas escreve VÁRIAS células de UMA MESMA linha
    de uma vez - usado pelo cadastro de veículo/carreta novo (`linha` é a
    primeira linha livre da aba, achada por `_achar_primeira_linha_vazia`).
    `valores_por_coluna` é `{coluna_1based: (valor_convertido, tipo)}`.

    As abas de cadastro (`Frota`, `info-carretas`) têm ~1000 linhas
    pré-formatadas bem além da última linha com dado real (herança de
    formatação de célula em massa que o Excel aplica à linha inteira) - a
    "linha vazia" já existe fisicamente no XML, então gravar um cadastro novo
    NUNCA precisa deslocar nenhuma linha existente para baixo. Isso importa
    porque toda e qualquer outra aba do ERP faz VLOOKUP contra a Placa destas
    duas abas (ver [[Módulo de Manutenção]]) - deslocar uma linha quebraria
    silenciosamente cada uma dessas fórmulas.

    Confere TODAS as células-alvo antes de gravar qualquer uma (mesma trava
    contra célula-com-fórmula de `_patch_celula_xml`) - uma linha gravada pela
    metade seria pior do que recusar o cadastro inteiro."""
    ET.register_namespace("", _NS_MAIN)
    raiz = ET.fromstring(xml_bytes)
    sheet_data = raiz.find(f"{{{_NS_MAIN}}}sheetData")

    linha_el = next((l for l in sheet_data.findall(f"{{{_NS_MAIN}}}row") if l.get("r") == str(linha)), None)
    if linha_el is None:
        # So acontece se o cadastro ja tiver esgotado as ~1000 linhas
        # pre-formatadas da planilha - caso raro, mas cria a linha do zero
        # (sem herdar estilo de nenhuma linha vizinha) em vez de falhar.
        linha_el = ET.SubElement(sheet_data, f"{{{_NS_MAIN}}}row")
        linha_el.set("r", str(linha))

    celulas_alvo = {}
    for coluna, (_valor, _tipo) in valores_por_coluna.items():
        celula_el = _achar_ou_criar_celula(linha_el, coluna)
        if celula_el.find(f"{{{_NS_MAIN}}}f") is not None:
            ref = f"{_coluna_para_letra(coluna)}{linha}"
            raise ValueError(f"A célula {ref} contém uma fórmula - recusado por segurança.")
        celulas_alvo[coluna] = celula_el

    for coluna, (valor_convertido, tipo) in valores_por_coluna.items():
        _definir_valor_celula_xml(celulas_alvo[coluna], valor_convertido, tipo)

    return ET.tostring(raiz, encoding="UTF-8", xml_declaration=True)


def _achar_primeira_linha_vazia(planilha, coluna_placa, linha_inicial):
    """Acha a próxima linha livre (1-based) na coluna da Placa, a partir de
    `linha_inicial`: a linha logo após a última com algum valor. Usada pelo
    cadastro de veículo/carreta novo para saber onde gravar - ver o porquê de
    nunca precisar deslocar nada em `_patch_linha_xml`.

    Mesmo cuidado de `_achar_linha_planilha`: o número da linha vem do
    contador de `enumerate`, nunca de `linha[0].row` - as 2 linhas fantasma
    reais da aba Frota ("Veículos novos", "Plano SCANIA") têm o resto da
    linha quase vazio, então é bem provável que a coluna A (COD) esteja em
    branco nelas, o que em modo `read_only=True` vira `EmptyCell` sem
    `.row` e derrubaria exatamente a varredura que decide onde cadastrar
    o próximo veículo."""
    indice = coluna_placa - 1
    ultima_ocupada = linha_inicial - 1
    for numero_linha, linha in enumerate(planilha.iter_rows(min_row=linha_inicial, max_col=coluna_placa), start=linha_inicial):
        valor = linha[indice].value
        if valor is not None and str(valor).strip():
            ultima_ocupada = numero_linha
    return ultima_ocupada + 1


def _marcar_recalculo_workbook_xml(xml_bytes):
    """Liga `fullCalcOnLoad` no <calcPr> do workbook.xml, se existir - pede
    pro Excel/Sheets recalcular tudo no próximo open de verdade, já que a
    célula que acabamos de editar pode ser usada por alguma fórmula (ex.: o
    STATUS deste item específico fica desatualizado até isso acontecer -
    only esse item, não o arquivo inteiro como no `openpyxl`). Se por algum
    motivo o workbook não tiver <calcPr> (incomum), não mexe - não é uma
    correção essencial, só uma melhoria de UX para quem abrir depois."""
    ET.register_namespace("", _NS_MAIN)
    raiz = ET.fromstring(xml_bytes)
    calc_pr = raiz.find(f"{{{_NS_MAIN}}}calcPr")
    if calc_pr is None:
        return xml_bytes
    calc_pr.set("fullCalcOnLoad", "1")
    return ET.tostring(raiz, encoding="UTF-8", xml_declaration=True)


def _reconstruir_zip_com_arquivos_trocados(zip_bytes_original, substituicoes):
    """Copia um .xlsx (zip) inteiro para um novo buffer, trocando só os
    arquivos internos listados em `substituicoes` ({caminho: novos_bytes}).
    Todo o resto (tabelas dinâmicas, estilos, sharedStrings, outras abas,
    docProps...) sai byte a byte idêntico ao original."""
    saida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_bytes_original), "r") as origem, \
         zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as destino:
        for item in origem.infolist():
            conteudo = substituicoes.get(item.filename, origem.read(item.filename))
            destino.writestr(item, conteudo)
    saida.seek(0)
    return saida


def _converter_para_escrita(valor, tipo):
    if valor is None or valor == "":
        return None
    if tipo == "numero":
        numero = _numero(valor) if isinstance(valor, str) else valor
        if numero is None:
            raise ValueError(f"'{valor}' não é um número válido.")
        return numero
    if tipo == "data":
        if isinstance(valor, (date, datetime)):
            return valor
        try:
            return datetime.strptime(str(valor).strip(), "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"'{valor}' não é uma data válida (use AAAA-MM-DD).") from None
    return str(valor).strip()


def _valor_planilha_para_json(valor, tipo):
    """Mesmo formato que o resto da API já usa para esse tipo de campo (ISO
    `AAAA-MM-DD` para data, número puro, texto puro) - para o valor "atual"
    de um conflito poder ser mostrado ou reaproveitado direto pelo frontend,
    sem um segundo formato só para esse caso."""
    if valor is None:
        return None
    if tipo == "data":
        if isinstance(valor, (date, datetime)):
            return valor.strftime("%Y-%m-%d")
        return str(valor).strip() or None
    if tipo == "numero":
        try:
            return float(valor)
        except (TypeError, ValueError):
            return None
    return str(valor).strip() or None


def _valores_conflitam(valor_planilha_bruto, valor_cliente_bruto, tipo):
    """True só quando a célula de verdade mudou de valor desde que o cliente
    carregou a tela - não quando o arquivo inteiro ganhou um `modifiedTime`
    novo por causa de uma edição em outra placa, outra aba, ou outro sistema
    (`app-patio@...`) mexendo em algo sem relação nenhuma com este campo.
    Os dois lados passam pelo MESMO normalizador de tipo (`_converter_para_escrita`)
    antes de comparar, porque o openpyxl devolve datas como `datetime` e o
    cliente manda uma string ISO - sem essa normalização, o mesmo dado em
    representações diferentes pareceria uma mudança real."""
    try:
        normalizado_planilha = _converter_para_escrita(valor_planilha_bruto, tipo)
    except ValueError:
        normalizado_planilha = valor_planilha_bruto
    try:
        normalizado_cliente = _converter_para_escrita(valor_cliente_bruto, tipo)
    except ValueError:
        normalizado_cliente = valor_cliente_bruto

    if isinstance(normalizado_planilha, datetime):
        normalizado_planilha = normalizado_planilha.date()
    if isinstance(normalizado_cliente, datetime):
        normalizado_cliente = normalizado_cliente.date()
    if tipo == "numero":
        normalizado_planilha = float(normalizado_planilha) if normalizado_planilha is not None else None
        normalizado_cliente = float(normalizado_cliente) if normalizado_cliente is not None else None

    return normalizado_planilha != normalizado_cliente


def atualizar_campo_veiculo(placa, campo, novo_valor, modificado_em_esperado=None, valor_anterior=None):
    """Escreve UM campo de UMA placa de volta no .xlsx no Drive, editando só a
    célula bruta correspondente com openpyxl (as fórmulas e a formatação do
    resto do arquivo saem intactas) e reenviando a nova versão.

    Concorrência otimista POR CÉLULA, não por arquivo: `valor_anterior` é o
    valor que o cliente tinha em tela antes de editar. Só levanta
    `ConflitoEdicao` quando a célula-alvo de verdade mudou para outro valor
    nesse meio-tempo (comparação normalizada por tipo, ver `_valores_conflitam`)
    - um `modifiedTime` de arquivo mais novo, sozinho, não trava mais o
    salvamento, porque quase sempre é ruído de outra placa/aba sendo editada
    ao mesmo tempo, não um conflito de verdade nesta célula. Chamadas antigas
    que não mandam `valor_anterior` caem de volta na trava conservadora
    anterior (todo o arquivo), para não afrouxar a segurança de quem ainda
    não manda esse dado."""
    if campo not in CAMPOS_EDITAVEIS:
        raise ValueError(f"O campo '{campo}' não pode ser editado por aqui.")
    aba_override, coluna_placa, coluna, tipo = CAMPOS_EDITAVEIS[campo]
    valor_convertido = _converter_para_escrita(novo_valor, tipo)

    placa_normalizada = _placa_normalizada(placa)
    if not placa_normalizada:
        raise ValueError("Informe uma placa válida.")

    arquivo_id, aba_padrao = _config_sheets()
    aba = aba_override or aba_padrao
    if not arquivo_id:
        raise PlanilhaNaoConfigurada(
            "ID do arquivo no Google Drive não configurado (config.json → "
            "google_sheets.planilha_id, ou variável de ambiente HGHUB_SHEETS_ID)."
        )
    servico = _obter_servico_drive()

    try:
        bytes_originais, modificado_em_atual = _obter_bytes_planilha(servico, arquivo_id, fresco=True)
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível baixar o arquivo do Drive: {falha}") from falha

    arquivo_mudou = bool(modificado_em_esperado) and modificado_em_esperado != modificado_em_atual

    try:
        from openpyxl import load_workbook
        # Só para achar a linha da placa (e, se preciso, o valor atual da
        # célula-alvo) - este objeto NUNCA é salvo de volta. A escrita de
        # verdade é um patch cirúrgico no XML (abaixo), porque salvar com o
        # openpyxl apaga o cache calculado de TODAS as fórmulas do arquivo
        # inteiro, não só da célula editada (achado documentado em
        # [[Módulo de Manutenção]]).
        livro_leitura = load_workbook(io.BytesIO(bytes_originais), read_only=True, data_only=False)
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível abrir o arquivo para edição: {falha}") from falha

    if aba not in livro_leitura.sheetnames:
        livro_leitura.close()
        raise PlanilhaNaoConfigurada(f"A aba '{aba}' não existe mais neste arquivo.")
    linha_excel = _achar_linha_planilha(livro_leitura[aba], placa_normalizada, coluna_placa)

    valor_planilha_atual = None
    if arquivo_mudou and linha_excel is not None:
        # O arquivo (inteiro) mudou desde que a tela abriu - antes de recusar
        # o salvamento, confere se a mudança teve QUALQUER relação com esta
        # célula específica. Iteração de 1 linha/1 coluna, segura em modo
        # read_only (mesmo padrão de acesso de `_achar_linha_planilha`).
        linha_alvo = next(livro_leitura[aba].iter_rows(
            min_row=linha_excel, max_row=linha_excel, min_col=coluna, max_col=coluna,
        ))
        valor_planilha_atual = linha_alvo[0].value
    livro_leitura.close()
    if linha_excel is None:
        raise ValueError(f"Placa '{placa_normalizada}' não encontrada na aba '{aba}'.")

    if arquivo_mudou:
        if valor_anterior is not None:
            if _valores_conflitam(valor_planilha_atual, valor_anterior, tipo):
                raise ConflitoEdicao(
                    "Outra pessoa (ou outro sistema) já alterou este mesmo campo enquanto você "
                    "editava. Atualize para ver o valor mais recente antes de salvar por cima.",
                    valor_atual=_valor_planilha_para_json(valor_planilha_atual, tipo),
                    modificado_em=modificado_em_atual,
                )
            # A célula-alvo continua com o mesmo valor que o cliente via na
            # tela - a mudança de `modifiedTime` foi em outra placa/aba, sem
            # relação com este campo. Seguro prosseguir com a escrita.
        else:
            # Chamador não mandou o valor anterior para comparar por célula -
            # mantém a trava conservadora de arquivo inteiro (compatibilidade).
            raise ConflitoEdicao(
                "Esta planilha foi alterada por outra pessoa (ou outro sistema) desde que você "
                "abriu esta tela. Recarregue os dados antes de salvar, para não sobrescrever a "
                "mudança de ninguém.",
                modificado_em=modificado_em_atual,
            )

    try:
        with zipfile.ZipFile(io.BytesIO(bytes_originais), "r") as zip_leitura:
            caminho_xml_aba = _localizar_xml_da_aba(zip_leitura, aba)
            if not caminho_xml_aba:
                raise PlanilhaNaoConfigurada(f"Não encontrei o arquivo interno correspondente à aba '{aba}'.")
            xml_aba_original = zip_leitura.read(caminho_xml_aba)
            xml_workbook_original = zip_leitura.read("xl/workbook.xml")

        xml_aba_novo = _patch_celula_xml(xml_aba_original, linha_excel, coluna, valor_convertido, tipo)
        xml_workbook_novo = _marcar_recalculo_workbook_xml(xml_workbook_original)

        saida = _reconstruir_zip_com_arquivos_trocados(bytes_originais, {
            caminho_xml_aba: xml_aba_novo,
            "xl/workbook.xml": xml_workbook_novo,
        })
    except (PlanilhaNaoConfigurada, ValueError):
        raise
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível editar o arquivo: {falha}") from falha

    try:
        from googleapiclient.http import MediaIoBaseUpload
        upload = MediaIoBaseUpload(saida, mimetype=MIME_XLSX, resumable=True)
        resultado = servico.files().update(fileId=arquivo_id, media_body=upload, fields="modifiedTime").execute()
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível enviar a alteração ao Drive: {falha}") from falha

    # A próxima leitura (Dashboard, categoria, Prontuário) já sai instantânea
    # e correta: os bytes pós-edição já estão em mãos, sem precisar baixar de
    # novo só para descobrir o que acabamos de escrever.
    _atualizar_cache_planilha(arquivo_id, resultado["modifiedTime"], saida.getvalue())

    log.info("Manutenção: placa %s, campo '%s' atualizado para %r (patch cirúrgico de XML).",
              placa_normalizada, campo, valor_convertido)
    return {"modificado_em": resultado["modifiedTime"]}


ABAS_ESSENCIAIS = {ABA_PADRAO, "Frota", "info-carretas"}


def validar_estrutura_planilha(buffer):
    """Barreira de segurança para o import de emergência (drag & drop): confere
    que o .xlsx enviado tem pelo menos as abas essenciais antes de aceitar
    substituir a base real por ele. Não valida o CONTEÚDO de cada aba (só a
    estrutura) - a intenção é pegar "mandei o arquivo errado", não garantir
    qualidade de dado."""
    try:
        import pandas as pd
        nomes_abas = set(pd.ExcelFile(buffer, engine="openpyxl").sheet_names)
    except Exception as falha:
        raise ValueError(f"Arquivo não é um .xlsx válido: {falha}") from falha

    faltando = ABAS_ESSENCIAIS - nomes_abas
    if faltando:
        raise ValueError(
            "Este arquivo não parece ser a planilha de manutenção correta - "
            f"faltam as abas: {', '.join(sorted(faltando))}."
        )
    buffer.seek(0)


def substituir_planilha_no_drive(bytes_arquivo):
    """Import de emergência: sobe um .xlsx novo por cima do arquivo configurado
    no Drive, depois de validar a estrutura mínima. O Google Drive guarda uma
    revisão do conteúdo anterior automaticamente - se o arquivo enviado
    estiver errado mesmo passando na validação, dá para restaurar a versão
    anterior por "Gerenciar versões" no próprio Drive."""
    buffer = io.BytesIO(bytes_arquivo)
    validar_estrutura_planilha(buffer)

    arquivo_id, _ = _config_sheets()
    if not arquivo_id:
        raise PlanilhaNaoConfigurada(
            "ID do arquivo no Google Drive não configurado (config.json → "
            "google_sheets.planilha_id, ou variável de ambiente HGHUB_SHEETS_ID)."
        )
    servico = _obter_servico_drive()

    try:
        from googleapiclient.http import MediaIoBaseUpload
        upload = MediaIoBaseUpload(buffer, mimetype=MIME_XLSX, resumable=True)
        resultado = servico.files().update(fileId=arquivo_id, media_body=upload, fields="modifiedTime").execute()
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível enviar o arquivo ao Drive: {falha}") from falha

    _atualizar_cache_planilha(arquivo_id, resultado["modifiedTime"], bytes_arquivo)

    log.info("Manutenção: planilha inteira substituída via import de emergência.")
    return {"modificado_em": resultado["modifiedTime"]}


# ==============================================================
# PRONTUÁRIO DO VEÍCULO (todas as 20 abas do ERP, unificadas por Placa)
#
# Mapeamento completo, o porquê de cada aba e a regra "tempo OU km, o que
# vier primeiro" estão documentados em detalhe no Segundo Cérebro:
# [[🧠 Dicionario de Dados - ERP Manutencao]]. Resumo essencial:
#   - Cavalos (cadastro mestre: aba "Frota") e Carretas (cadastro mestre:
#     aba "info-carretas") são dois universos de placas que NUNCA se cruzam.
#   - Cada categoria de manutenção de carreta é rastreada em DUAS abas
#     paralelas - uma por TEMPO (dias) e uma por KM - porque a regra real é
#     "vence no que vier primeiro". O status geral da categoria é o PIOR
#     entre as duas, o que a planilha original (aba RESUMO ANALITICO) não
#     faz hoje (ela só olha a aba de tempo) - corrigido aqui de propósito.
#   - 3 das 20 abas têm prefixo "Cópia de" (backups) e são ignoradas.
# ==============================================================
ABA_FROTA = "Frota"
ABA_CADASTRO_CARRETAS = "info-carretas"
ABA_ALINHAMENTO = "Alinhamento"
ABA_QUINTA_RODA_CARRETA = "Inspeção 5ª roda e pino rei"
ABA_INTEGRIDADE_TEMPO = "Preventiva Integridade Chassi"
ABA_INTEGRIDADE_KM = "Integridade chassi 10.000KM"
ABA_SR_TANQUES_TEMPO = "Preventiva SR - TANQUES"
ABA_PREVENTIVA_CARRETAS_TEMPO = "Preventiva Semestral CARRETAS"
ABA_RESUMO_ANALITICO = "RESUMO ANALITICO"


def _normalizar_cabecalho(nome):
    texto = str(nome).replace("\n", " ")
    return " ".join(texto.split()).strip().upper()


def _serie(df, *alternativas):
    """Acha uma coluna pelo nome, tolerando \\n embutido, espaços extras e
    variação de maiúsculas (a mesma informação vem com nomes ligeiramente
    diferentes em abas diferentes - ex.: 'ANO FAB' vs 'ANO FAB.'). Se nada
    bater, devolve uma série vazia em vez de estourar - graceful por design,
    igual ao resto deste módulo."""
    import pandas as pd

    normalizados = {_normalizar_cabecalho(c): c for c in df.columns}
    for alternativa in alternativas:
        chave = _normalizar_cabecalho(alternativa)
        if chave in normalizados:
            return df[normalizados[chave]]
    return pd.Series([None] * len(df), index=df.index)


def _placa_normalizada(valor):
    texto = _texto(valor)
    return texto.strip().upper() if texto else None


def _mapa_por_placa(df, coluna_placa, **campos):
    """Converte um DataFrame (cabeçalho já resolvido) num dict {placa:
    {campo: valor, ...}}. `coluna_placa` é um índice inteiro (para cabeçalhos
    corrompidos, ex. a aba de 5ª roda por km) ou um nome de coluna (com
    correspondência tolerante via `_serie`). Cada campo em `campos` recebe a
    mesma flexibilidade."""
    placas = df.iloc[:, coluna_placa] if isinstance(coluna_placa, int) else _serie(df, coluna_placa)
    series = {
        campo: (df.iloc[:, ref] if isinstance(ref, int) else _serie(df, ref))
        for campo, ref in campos.items()
    }
    resultado = {}
    for posicao in range(len(df)):
        placa = _placa_normalizada(placas.iloc[posicao])
        if not placa:
            continue
        registro = {campo: _valor_celula_pandas(serie.iloc[posicao]) for campo, serie in series.items()}
        # Linha "fantasma": tinha algo parecido com placa na coluna A, mas
        # nenhum dado real nas colunas que importam (sobra de formatação de
        # célula em linhas em branco, ex. a placa solta "46" sem mais nada).
        if all(valor is None for valor in registro.values()):
            continue
        resultado[placa] = registro
    return resultado


def _item_por_data(bruto):
    """Formata um registro cru (data) no formato comum do Prontuário."""
    return {
        "unidade": "dias",
        "status": _texto(bruto.get("status")),
        "ultima_data": _data(bruto.get("ultima")),
        "proxima": _data(bruto.get("proxima")),
        "falta": _numero(bruto.get("falta")),
        "estabelecimento": _texto(bruto.get("estabelecimento")),
        "chamado": _texto(bruto.get("chamado")),
        "observacoes": _texto(bruto.get("observacoes")),
    }


def _item_por_km(bruto):
    """Formata um registro cru (km) no formato comum do Prontuário."""
    return {
        "unidade": "km",
        "status": _texto(bruto.get("status")),
        "km_atual": _numero(bruto.get("km_atual")),
        "periodicidade": _numero(bruto.get("periodicidade")),
        "proxima": _numero(bruto.get("proxima")),
        "falta": _numero(bruto.get("falta")),
        "chamado": _texto(bruto.get("chamado")),
        "observacoes": _texto(bruto.get("observacoes")),
    }


def _categoria_dupla(mapa_tempo, mapa_km, placa):
    """Uma categoria "dupla" (tempo + km) para uma placa: o pior status entre
    as duas condições - a correção documentada no Dicionário de Dados para o
    gap da aba RESUMO ANALITICO original, que só olhava a aba de tempo."""
    tempo = _item_por_data(mapa_tempo[placa]) if placa in mapa_tempo else None
    km = _item_por_km(mapa_km[placa]) if placa in mapa_km else None
    if not tempo and not km:
        return None
    return {
        "tempo": tempo,
        "km": km,
        "status_geral": _status_geral(tempo["status"] if tempo else None, km["status"] if km else None),
    }


# SR-Tanques NÃO é uma categoria "dupla" (tempo + km) como integridade de
# chassi ou preventiva de carretas - é puramente por TEMPO, sem barra de
# progresso, com uma regra matemática fixa confirmada pelo setor: a próxima
# revisão é sempre a última + 180 dias corridos.
SR_TANQUES_DIAS_VALIDADE = 180
# "Próxima já passou" -> PARAR URGENTE; faltando <= 15 dias -> PROGRAMAR
# PARADA (dar tempo de agendar a parada); senão, EM DIA.
SR_TANQUES_DIAS_ALERTA = 15


def _calcular_sr_tanques(mapa_sr_tempo, placa):
    """SR-Tanques calculado 100% por nós, nunca pela fórmula da planilha.

    Achado que motivou isto: a aba "Preventiva SR - TANQUES" tem colunas de
    Status/Próxima revisão/Razão que são FÓRMULAS do Excel referenciando a
    célula de "Data Revisão". Quando essa célula está vazia, o Excel calcula
    a diferença de datas a partir do serial 0 (30/12/1899) e produz números
    sem sentido - ex. "atrasado há 46.000 dias" - que a UI antiga repassava
    direto para a tela. Por isso `_mapa_por_placa` para esta aba (ver
    `montar_prontuario_frota`) só lê a data bruta da última revisão (e os
    campos de texto livre estabelecimento/chamado) - nunca as 3 colunas
    calculadas.

    Sem "última revisão" registrada, não há cálculo possível: devolve status
    `None` (mesma convenção de "sem dado" usada no resto do módulo), nunca um
    status forçado ou uma data inventada."""
    bruto = mapa_sr_tempo.get(placa)
    if not bruto:
        return None

    ultima_data = _data(bruto.get("ultima"))
    estabelecimento = _texto(bruto.get("estabelecimento"))
    chamado = _texto(bruto.get("chamado"))
    if not ultima_data:
        return {
            "status": None, "ultima_data": None, "proxima": None, "falta": None,
            "estabelecimento": estabelecimento, "chamado": chamado,
        }

    ultima = datetime.strptime(ultima_data, "%Y-%m-%d").date()
    proxima = ultima + timedelta(days=SR_TANQUES_DIAS_VALIDADE)
    falta = (proxima - date.today()).days

    if falta < 0:
        status = "PARAR URGENTE"
    elif falta <= SR_TANQUES_DIAS_ALERTA:
        status = "PROGRAMAR PARADA"
    else:
        status = "EM DIA"

    return {
        "status": status,
        "ultima_data": ultima_data,
        "proxima": proxima.strftime("%Y-%m-%d"),
        "falta": falta,
        "estabelecimento": estabelecimento,
        "chamado": chamado,
    }


def _ler_cadastro_frota(xls):
    import pandas as pd

    df = pd.read_excel(xls, sheet_name=ABA_FROTA, header=0, usecols="A:G")
    cadastro = {}
    for _, linha in df.iterrows():
        placa = _placa_normalizada(linha.iloc[1])
        if not placa or not _placa_valida(placa):
            continue
        cadastro[placa] = {
            "tipo": "Cavalo",
            "cod": _texto(linha.iloc[0]),
            "marca": _texto(linha.iloc[2]),
            "modelo": _texto(linha.iloc[3]),
            "ano_fab": _numero(linha.iloc[4]),
            "ano_mod": _numero(linha.iloc[5]),
            "centro_custo": _texto(linha.iloc[6]) if len(linha) > 6 else None,
        }
    return cadastro


def _ler_cadastro_carretas(xls):
    import pandas as pd

    df = pd.read_excel(xls, sheet_name=ABA_CADASTRO_CARRETAS, header=None)
    cadastro = {}
    for _, linha in df.iterrows():
        placa = _placa_normalizada(linha.iloc[0])
        if not placa or not _placa_valida(placa):
            continue
        cadastro[placa] = {
            "tipo": "Carreta",
            "uf": _texto(linha.iloc[1]) if len(linha) > 1 else None,
            "renavam": _texto(linha.iloc[2]) if len(linha) > 2 else None,
            "chassi": _texto(linha.iloc[3]) if len(linha) > 3 else None,
            "marca": _texto(linha.iloc[4]) if len(linha) > 4 else None,
            "modelo": _texto(linha.iloc[5]) if len(linha) > 5 else None,
            "ano_fab": _numero(linha.iloc[6]) if len(linha) > 6 else None,
            "ano_mod": _numero(linha.iloc[7]) if len(linha) > 7 else None,
            "eixos": _numero(linha.iloc[8]) if len(linha) > 8 else None,
            "capacidade": _numero(linha.iloc[9]) if len(linha) > 9 else None,
            "implemento": _texto(linha.iloc[10]) if len(linha) > 10 else None,
        }
    return cadastro


# ==============================================================
# CADASTRO DE FROTA (CRUD real: leitura + inserção de veículo/carreta novo)
#
# Cada campo é (coluna_1based, tipo, obrigatorio) - mesma linguagem de tipo de
# `CAMPOS_EDITAVEIS` (`_converter_para_escrita`). `placa` não entra nesses
# dicts porque é tratada à parte (normalização + validação de formato +
# checagem de duplicidade valem para os dois tipos igual).
# ==============================================================
CAMPOS_CADASTRO_FROTA = {
    "cod": (1, "numero", False),
    "marca": (3, "texto", False),
    "modelo": (4, "texto", False),
    "ano_fab": (5, "numero", False),
    "ano_mod": (6, "numero", False),
    "centro_custo": (7, "texto", False),
}

CAMPOS_CADASTRO_CARRETAS = {
    "uf": (2, "texto", False),
    "renavam": (3, "texto", False),
    "chassi": (4, "texto", False),
    "marca": (5, "texto", False),
    "modelo": (6, "texto", False),
    "ano_fab": (7, "numero", False),
    "ano_mod": (8, "numero", False),
    "eixos": (9, "numero", False),
    "capacidade": (10, "numero", False),
    "implemento": (11, "texto", False),
}

# `linha_inicial` é onde a varredura de "próxima linha livre" começa: logo
# depois do cabeçalho em Frota (linha 1), ou desde o topo em info-carretas
# (que não tem cabeçalho nenhum - `header=None` em `_ler_cadastro_carretas`).
TIPOS_CADASTRO = {
    "cavalo": {
        "aba": ABA_FROTA,
        "coluna_placa": 2,
        "linha_inicial": 2,
        "campos": CAMPOS_CADASTRO_FROTA,
    },
    "carreta": {
        "aba": ABA_CADASTRO_CARRETAS,
        "coluna_placa": 1,
        "linha_inicial": 1,
        "campos": CAMPOS_CADASTRO_CARRETAS,
    },
}


def listar_cadastro():
    """Cadastro de Frota: lista os dois universos (Cavalos + Carretas) juntos,
    1 dict por veículo com a `placa` embutida - alimenta a tabela da nova
    sub-aba "Cadastro de Frota". Devolve (lista, modificado_em)."""
    arquivo_id, _ = _config_sheets()
    if not arquivo_id:
        raise PlanilhaNaoConfigurada(
            "ID do arquivo no Google Drive não configurado (config.json → "
            "google_sheets.planilha_id, ou variável de ambiente HGHUB_SHEETS_ID)."
        )
    servico = _obter_servico_drive()
    try:
        conteudo, modificado_em = _obter_bytes_planilha(servico, arquivo_id)
    except PlanilhaNaoConfigurada:
        raise
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível baixar o arquivo do Drive: {falha}") from falha

    def construir():
        import pandas as pd
        xls = pd.ExcelFile(io.BytesIO(conteudo), engine="openpyxl")
        cadastro = {**_ler_cadastro_frota(xls), **_ler_cadastro_carretas(xls)}
        lista = [{"placa": placa, **dados} for placa, dados in cadastro.items()]
        lista.sort(key=lambda v: v["placa"])
        return lista

    lista = _memoizado(("cadastro", arquivo_id), modificado_em, construir)
    ocultos = carregar_ocultos()
    return [{**v, "oculto": v["placa"] in ocultos} for v in lista], modificado_em


def cadastrar_veiculo(tipo, dados, modificado_em_esperado=None):
    """Cadastra um Cavalo (aba `Frota`) ou uma Carreta (aba `info-carretas`)
    novo, escrevendo na primeira linha livre da aba (`_achar_primeira_linha_vazia`)
    - NUNCA desloca nenhuma linha existente, e por isso nenhuma fórmula de
    NENHUMA outra aba do ERP (todas fazem VLOOKUP contra a Placa destas duas
    abas) corre risco de passar a apontar para a linha errada depois.

    Mesmo patch cirúrgico de XML de `atualizar_campo_veiculo` (nunca
    `openpyxl.load_workbook().save()`, ver o cabeçalho da seção WRITE-BACK
    acima) - o motivo é idêntico: salvar com openpyxl apagaria o cache
    calculado de toda fórmula do arquivo inteiro, inclusive nas outras 19
    abas, não só nesta. Mas SEM a trava de concorrência por `modifiedTime` de
    arquivo: aqui não existe uma "célula anterior" alheia para proteger -
    ver o comentário antes da checagem de duplicidade, abaixo."""
    if tipo not in TIPOS_CADASTRO:
        raise ValueError(f"Tipo de cadastro inválido: '{tipo}' (use 'cavalo' ou 'carreta').")
    config = TIPOS_CADASTRO[tipo]

    placa = _placa_normalizada(dados.get("placa"))
    if not placa:
        raise ValueError("Informe a placa.")
    if not _placa_valida(placa):
        raise ValueError(f"'{placa}' não parece ser uma placa válida.")

    valores_convertidos = {}
    for nome_campo, (coluna, tipo_campo, obrigatorio) in config["campos"].items():
        bruto = dados.get(nome_campo)
        if obrigatorio and (bruto is None or str(bruto).strip() == ""):
            raise ValueError(f"O campo '{nome_campo}' é obrigatório.")
        try:
            valores_convertidos[coluna] = (_converter_para_escrita(bruto, tipo_campo), tipo_campo)
        except ValueError as motivo:
            raise ValueError(f"Campo '{nome_campo}': {motivo}") from None

    arquivo_id, _ = _config_sheets()
    if not arquivo_id:
        raise PlanilhaNaoConfigurada(
            "ID do arquivo no Google Drive não configurado (config.json → "
            "google_sheets.planilha_id, ou variável de ambiente HGHUB_SHEETS_ID)."
        )
    servico = _obter_servico_drive()

    try:
        bytes_originais, modificado_em_atual = _obter_bytes_planilha(servico, arquivo_id, fresco=True)
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível baixar o arquivo do Drive: {falha}") from falha

    # Sem trava por `modifiedTime` de arquivo aqui - ela travava sozinha
    # cadastros de verdade sem proteger nada a mais: a duplicidade de placa
    # (abaixo) e a próxima linha livre (`_achar_primeira_linha_vazia`, mais
    # adiante) já são recalculadas contra ESTES bytes recém-baixados a cada
    # chamada, nunca contra uma lista em cache - ao contrário da edição de
    # UMA célula existente (`atualizar_campo_veiculo`), aqui não existe uma
    # "célula anterior" que outra pessoa possa ter sobrescrito por baixo.
    # `modificado_em_esperado` continua aceito só por compatibilidade com o
    # frontend, que manda o de sempre.

    # Duplicidade checada nos DOIS universos (Cavalo/Carreta nunca se cruzam
    # de verdade, mas isso também pega o erro de escolher o `tipo` errado).
    try:
        import pandas as pd
        xls = pd.ExcelFile(io.BytesIO(bytes_originais), engine="openpyxl")
        cadastro_existente = {**_ler_cadastro_frota(xls), **_ler_cadastro_carretas(xls)}
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível conferir os cadastros existentes: {falha}") from falha
    if placa in cadastro_existente:
        raise ValueError(f"A placa '{placa}' já está cadastrada.")

    aba = config["aba"]
    try:
        from openpyxl import load_workbook
        # Só para achar a próxima linha livre - este objeto NUNCA é salvo de
        # volta (ver docstring acima sobre o motivo de nunca usar
        # `openpyxl...save()` neste módulo).
        livro_leitura = load_workbook(io.BytesIO(bytes_originais), read_only=True, data_only=False)
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível abrir o arquivo para edição: {falha}") from falha

    if aba not in livro_leitura.sheetnames:
        livro_leitura.close()
        raise PlanilhaNaoConfigurada(f"A aba '{aba}' não existe mais neste arquivo.")
    linha_nova = _achar_primeira_linha_vazia(livro_leitura[aba], config["coluna_placa"], config["linha_inicial"])
    livro_leitura.close()

    valores_convertidos[config["coluna_placa"]] = (placa, "texto")

    try:
        with zipfile.ZipFile(io.BytesIO(bytes_originais), "r") as zip_leitura:
            caminho_xml_aba = _localizar_xml_da_aba(zip_leitura, aba)
            if not caminho_xml_aba:
                raise PlanilhaNaoConfigurada(f"Não encontrei o arquivo interno correspondente à aba '{aba}'.")
            xml_aba_original = zip_leitura.read(caminho_xml_aba)
            xml_workbook_original = zip_leitura.read("xl/workbook.xml")

        xml_aba_novo = _patch_linha_xml(xml_aba_original, linha_nova, valores_convertidos)
        xml_workbook_novo = _marcar_recalculo_workbook_xml(xml_workbook_original)

        saida = _reconstruir_zip_com_arquivos_trocados(bytes_originais, {
            caminho_xml_aba: xml_aba_novo,
            "xl/workbook.xml": xml_workbook_novo,
        })
    except (PlanilhaNaoConfigurada, ValueError):
        raise
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível editar o arquivo: {falha}") from falha

    try:
        from googleapiclient.http import MediaIoBaseUpload
        upload = MediaIoBaseUpload(saida, mimetype=MIME_XLSX, resumable=True)
        resultado = servico.files().update(fileId=arquivo_id, media_body=upload, fields="modifiedTime").execute()
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível enviar o cadastro ao Drive: {falha}") from falha

    _atualizar_cache_planilha(arquivo_id, resultado["modifiedTime"], saida.getvalue())
    log.info("Manutenção: cadastrada nova %s (placa %s) na linha %s da aba '%s'.",
              tipo, placa, linha_nova, aba)
    return {"modificado_em": resultado["modifiedTime"], "placa": placa}


# ==============================================================
# GERENCIAR VEÍCULOS (aparecer ou não nas telas)
#
# Em vez de APAGAR um veículo da planilha, cada veículo do cadastro pode ser marcado como
# "não aparece": ele some do Dashboard, das páginas de análise, das abas por categoria,
# da lista de Revisões e do Resumo, e não conta na saúde. A planilha NÃO é tocada - a
# escolha fica num arquivo próprio na pasta compartilhada (`veiculos_ocultos.json`, ao
# lado dos demais arquivos do sistema), que versões antigas do programa não conhecem e
# portanto não apagam. Reativar é só ligar de novo; nada se perde.
# ==============================================================
ARQUIVO_OCULTOS = caminho_arquivo_dados("veiculos_ocultos.json")
_ocultos_cache = {}
_aviso_ocultos_em = 0.0


def carregar_ocultos():
    """{placa: {...}} dos veículos marcados como "não aparece". Arquivo ausente = ninguém
    oculto; arquivo ilegível = o último conteúdo bom (nunca "mostra tudo" por um tropeço)."""
    global _ocultos_cache, _aviso_ocultos_em
    try:
        if not os.path.exists(ARQUIVO_OCULTOS):
            return {}
        with open(ARQUIVO_OCULTOS, "r", encoding="utf-8") as f:
            ocultos = json.load(f).get("ocultos")
        if not isinstance(ocultos, dict):
            raise ValueError("a chave 'ocultos' está ausente ou inválida")
        _ocultos_cache = {str(placa).strip().upper(): dados for placa, dados in ocultos.items()}
        return _ocultos_cache
    except Exception as falha:
        agora = time.time()
        if agora - _aviso_ocultos_em > 60:  # roda a cada leitura: não inunda o log
            _aviso_ocultos_em = agora
            log.error("Não foi possível ler veiculos_ocultos.json (%s). Mantendo o último estado conhecido.", falha)
        return _ocultos_cache


def definir_visibilidade(placa, visivel, autor=None):
    """Marca/desmarca um veículo como "não aparece". Atômico e sob trava de arquivo (vários
    PCs usam a mesma pasta); só acrescenta/remove a placa pedida, e um arquivo existente que
    não consiga ser lido NUNCA é sobrescrito. Devolve {placa, oculto}."""
    global _ocultos_cache
    placa = _placa_normalizada(placa)
    if not placa or not _placa_valida(placa):
        raise ValueError("Informe uma placa válida.")

    os.makedirs(os.path.dirname(os.path.abspath(ARQUIVO_OCULTOS)), exist_ok=True)
    with bloqueio_arquivo(ARQUIVO_OCULTOS, tempo_limite=10):
        atuais = {}
        if os.path.exists(ARQUIVO_OCULTOS):
            try:
                with open(ARQUIVO_OCULTOS, "r", encoding="utf-8") as f:
                    atuais = json.load(f).get("ocultos")
                if not isinstance(atuais, dict):
                    raise ValueError("a chave 'ocultos' está ausente ou inválida")
            except Exception as falha:
                raise ValueError("O arquivo de veículos ocultos está ilegível; por segurança não vou "
                                 f"sobrescrevê-lo ({falha}).") from falha
        atuais = {str(k).strip().upper(): v for k, v in atuais.items()}
        if visivel:
            atuais.pop(placa, None)
        else:
            atuais[placa] = {"por": autor or "sistema", "em": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        destino = os.path.dirname(os.path.abspath(ARQUIVO_OCULTOS))
        os.makedirs(destino, exist_ok=True)
        descritor, temporario = tempfile.mkstemp(dir=destino, suffix=".tmp")
        try:
            with os.fdopen(descritor, "w", encoding="utf-8") as f:
                json.dump({
                    "_leia_me": ("Veiculos marcados como 'nao aparece' (menu Frota & Manutencao > Cadastro de "
                                 "Frota > Gerenciar). A planilha nao e alterada; para voltar a aparecer, desligue "
                                 "la na tela ou apague a placa daqui."),
                    "ocultos": atuais,
                }, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporario, ARQUIVO_OCULTOS)
        except Exception:
            if os.path.exists(temporario):
                try:
                    os.remove(temporario)
                except OSError:
                    pass
            raise
        _ocultos_cache = atuais
    log.info("Manutenção: %s %s a placa %s nas telas.", autor or "sistema", "voltou a mostrar" if visivel else "ocultou", placa)
    return {"placa": placa, "oculto": not visivel}


def _data_valida(texto_iso):
    """Filtra datas-fantasma: quando a célula de origem estava em branco, uma
    fórmula do tipo `=ultima_data + periodicidade` na planilha original vira
    uma data ali por volta de 1900 (serial do Excel próximo de 0) em vez de
    ficar vazia. Uma data real desta frota nunca é anterior a 2000."""
    if not texto_iso:
        return None
    return texto_iso if int(texto_iso[:4]) >= 2000 else None


def _ler_alinhamento(xls):
    """A aba 'Alinhamento' mistura Cavalos (rastreados por KM, período
    30.000km) e Carretas (rastreados por dias, período 180) na MESMA aba e nas
    MESMAS colunas - só a coluna `Tipo` de cada linha diz qual regra vale.
    Por isso não usa `_mapa_por_placa` genérico: cada linha é formatada de um
    jeito diferente dependendo do próprio `Tipo`."""
    import pandas as pd

    df = pd.read_excel(xls, sheet_name=ABA_ALINHAMENTO, header=0)
    tipo_serie = _serie(df, "Tipo")
    status_serie = _serie(df, "STATUS")
    km_atual_serie = _serie(df, "ÚLTIMO KM SISTEMA")
    periodicidade_serie = _serie(df, "PERIODICIDADE")
    proxima_serie = _serie(df, "PRÓXIMO ALINHAMENTO")
    falta_serie = _serie(df, "KM/TEMPO\nFALTA/EXCED.")
    ultima_data_serie = _serie(df, "DATA ÚLTIMO ALINHAMENTO")
    chamado_serie = _serie(df, "CHAMADO")
    obs_serie = _serie(df, "Observações")
    placa_serie = _serie(df, "PLACA")

    resultado = {}
    for posicao in range(len(df)):
        placa = _placa_normalizada(placa_serie.iloc[posicao])
        if not placa:
            continue
        tipo = _texto(_valor_celula_pandas(tipo_serie.iloc[posicao]))
        status = _texto(_valor_celula_pandas(status_serie.iloc[posicao]))
        periodicidade = _numero(_valor_celula_pandas(periodicidade_serie.iloc[posicao]))
        chamado = _texto(_valor_celula_pandas(chamado_serie.iloc[posicao]))
        observacoes = _texto(_valor_celula_pandas(obs_serie.iloc[posicao]))

        if tipo == "Carreta":
            resultado[placa] = {
                "unidade": "dias",
                "status": status,
                "ultima_data": _data_valida(_data(_valor_celula_pandas(ultima_data_serie.iloc[posicao]))),
                "proxima": _data_valida(_data(_valor_celula_pandas(proxima_serie.iloc[posicao]))),
                "falta": _numero(_valor_celula_pandas(falta_serie.iloc[posicao])),
                "periodicidade": periodicidade,
                "chamado": chamado,
                "observacoes": observacoes,
            }
        else:
            resultado[placa] = {
                "unidade": "km",
                "status": status,
                "km_atual": _numero(_valor_celula_pandas(km_atual_serie.iloc[posicao])),
                "periodicidade": periodicidade,
                "proxima": _numero(_valor_celula_pandas(proxima_serie.iloc[posicao])),
                "falta": _numero(_valor_celula_pandas(falta_serie.iloc[posicao])),
                "chamado": chamado,
                "observacoes": observacoes,
            }
    return resultado


def _ler_logistica_resumo(xls):
    """Lê só a coluna de "RESUMO ANALITICO" que NÃO é derivável de nenhuma
    outra aba: "NO PATIO" é valor manual/direto (não fórmula) nessa aba. As
    outras colunas informativas dessa mesma aba (REVISAO, 5ª RODA,
    INTEGRIDADE, SR - TANQUES) são só um `VLOOKUP` contra abas que este
    módulo já lê e já cruza melhor (pior status entre tempo E km, não só
    tempo como a fórmula original) - o Resumo Analítico do app usa esse
    valor corrigido (`_categoria_dupla`) em vez de reler a fórmula antiga.

    "IPIRANGA" foi removida por pedido explícito - a coluna não tinha
    contrapartida de status (era só texto livre, sem regra de vencimento) e
    poluía o painel sem agregar uma decisão operacional clara. Ver
    [[Módulo de Manutenção]]."""
    import pandas as pd

    df = pd.read_excel(xls, sheet_name=ABA_RESUMO_ANALITICO, header=0)
    placa_serie = _serie(df, "PLACA")
    no_patio_serie = _serie(df, "NO PATIO")

    resultado = {}
    for posicao in range(len(df)):
        placa = _placa_normalizada(placa_serie.iloc[posicao])
        if not placa or not _placa_valida(placa):
            continue
        resultado[placa] = {
            "no_patio": bool(_valor_celula_pandas(no_patio_serie.iloc[posicao])),
        }
    return resultado


CATEGORIAS_TABELA = {"alinhamento", "quinta_roda", "integridade_chassi", "sr_tanques", "preventiva_carretas"}


def linhas_categorias(chaves):
    """Como uma "linhas_categoria" no plural: materializa VÁRIAS categorias do
    Prontuário de uma vez, montando `montar_prontuario_frota()` (a parte cara
    - baixar e cruzar as ~11 abas) só UMA vez e fatiando o resultado por
    categoria. Existe porque uma aba de sub-navegação que precise de mais de
    uma categoria ao mesmo tempo chamava esta função uma vez por categoria -
    downloads/parses completos e concorrentes só para abrir uma aba.
    Devolve {chave: [linhas...]}."""
    invalidas = sorted(set(chaves) - CATEGORIAS_TABELA)
    if invalidas:
        raise ValueError(f"Categoria(s) desconhecida(s): {', '.join(invalidas)}.")

    prontuario = montar_prontuario_frota()
    ocultos = carregar_ocultos()
    resultado = {chave: [] for chave in chaves}
    for placa, registro in prontuario.items():
        if placa in ocultos:
            continue
        cadastro = registro.get("cadastro") or {}
        categorias = registro.get("categorias") or {}
        for chave in chaves:
            item = categorias.get(chave)
            if not item:
                continue
            resultado[chave].append({
                "placa": placa,
                "tipo": cadastro.get("tipo"),
                "marca": cadastro.get("marca"),
                "modelo": cadastro.get("modelo"),
                "item": item,
            })
    for linhas in resultado.values():
        linhas.sort(key=lambda linha: linha["placa"])
    return resultado


def linhas_categoria(chave):
    """Materializa UMA categoria do Prontuário como tabela - atalho de
    `linhas_categorias` para quem só precisa de uma."""
    return linhas_categorias([chave])[chave]


def montar_prontuario_frota():
    """Baixa o .xlsx (uma vez) e monta o Prontuário completo: um dicionário
    {placa: {...}} cruzando as 20 abas do ERP de manutenção pela Placa.
    Levanta `PlanilhaNaoConfigurada` nas mesmas condições de `buscar_veiculos`.

    O resultado é MEMOIZADO por versão da planilha (ver `_memoizado`) e compartilhado
    entre as rotas: quem o consome só pode LER, nunca alterar o dicionário devolvido.
    """
    arquivo_id, aba_oleo = _config_sheets()
    if not arquivo_id:
        raise PlanilhaNaoConfigurada(
            "ID do arquivo no Google Drive não configurado (config.json → "
            "google_sheets.planilha_id, ou variável de ambiente HGHUB_SHEETS_ID)."
        )

    servico = _obter_servico_drive()
    try:
        conteudo, modificado_em = _obter_bytes_planilha(servico, arquivo_id)
    except PlanilhaNaoConfigurada:
        raise
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível baixar o arquivo do Drive: {falha}") from falha

    return _memoizado(("prontuario", arquivo_id, aba_oleo), modificado_em,
                      lambda: _construir_prontuario(conteudo, arquivo_id, aba_oleo, modificado_em))


def _construir_prontuario(conteudo, arquivo_id, aba_oleo, modificado_em):
    """O trabalho pesado de `montar_prontuario_frota`: lê e cruza as abas do .xlsx."""
    import pandas as pd

    try:
        # Mesmos bytes já baixados acima (cache) para tudo: o ExcelFile das
        # demais abas E a lista de veículos de "Revisões e óleo" - antes,
        # `buscar_veiculos()` baixava o arquivo de novo, do zero, sozinho.
        xls = pd.ExcelFile(io.BytesIO(conteudo), engine="openpyxl")

        cadastro = {**_ler_cadastro_frota(xls), **_ler_cadastro_carretas(xls)}

        veiculos_oleo = {
            v["placa"].strip().upper(): v for v in _veiculos_memoizados(conteudo, arquivo_id, modificado_em, aba_oleo)
        }

        mapa_alinhamento = _ler_alinhamento(xls)

        aba_5r_carreta = pd.read_excel(xls, sheet_name=ABA_QUINTA_RODA_CARRETA, header=0)
        mapa_5r_carreta = _mapa_por_placa(
            aba_5r_carreta, "PLACA",
            status="STATUS", ultima="DATA ÚLTIMA INSPEÇÃO", proxima="PRÓXIMA INSPEÇÃO",
            falta="TEMPO FALTA/EXCED.", estabelecimento="Estabelecimento", chamado="CHAMADO TKL",
            observacoes="Observações",
        )
        aba_integridade_tempo = pd.read_excel(xls, sheet_name=ABA_INTEGRIDADE_TEMPO, header=0)
        mapa_integridade_tempo = _mapa_por_placa(
            aba_integridade_tempo, "PLACA",
            status="Status", ultima="Data \nRevisão", proxima="Próxima revisão", falta="Razão",
            estabelecimento="Estabelecimento", chamado="Chamado TKL", observacoes="Observações",
        )
        aba_integridade_km = pd.read_excel(xls, sheet_name=ABA_INTEGRIDADE_KM, header=0)
        mapa_integridade_km = _mapa_por_placa(
            aba_integridade_km, "PLACA",
            status="STATUS", km_atual="KM ATUAL", periodicidade="PERIODICIDADE",
            proxima="PRÓXIMA INSPEÇÃO", falta="TEMPO FALTA/EXCED.", chamado="CHAMADO TKL",
            observacoes="Observações",
        )

        # Só a data bruta da última revisão (+ texto livre) - "Status",
        # "Próxima revisão" e "Razão" são fórmulas do Excel que enlouquecem
        # quando a célula de origem está vazia (ver `_calcular_sr_tanques`),
        # então nem chegam a ser lidas: a regra dos 180 dias é computada por
        # nós, sempre. SR-Tanques também não tem aba de KM (categoria só por
        # tempo, sem barra de progresso - diferente de integridade de chassi,
        # a única categoria "dupla" que sobrou).
        aba_sr_tempo = pd.read_excel(xls, sheet_name=ABA_SR_TANQUES_TEMPO, header=0)
        mapa_sr_tempo = _mapa_por_placa(
            aba_sr_tempo, "PLACA",
            ultima="Data \nRevisão", estabelecimento="Estabelecimento", chamado="Chamado TKL",
        )

        # "Preventiva Semestral CARRETAS" tem um título mesclado na linha 0 -
        # o cabeçalho de verdade só começa na linha 1. Categoria só por TEMPO
        # (decisão de negócio: o controle por KM foi removido - ver
        # [[Módulo de Manutenção]]).
        aba_prev_carretas_tempo = pd.read_excel(xls, sheet_name=ABA_PREVENTIVA_CARRETAS_TEMPO, header=1)
        mapa_prev_carretas_tempo = _mapa_por_placa(
            aba_prev_carretas_tempo, "PLACA",
            status="StatusPK", ultima="Data \nRevisão", proxima="Próxima revisão", falta="Razão",
            estabelecimento="Estabelecimento", chamado="Chamado TKL", observacoes="Observações",
        )

        mapa_logistica = _ler_logistica_resumo(xls)
    except PlanilhaNaoConfigurada:
        raise
    except Exception as falha:
        raise PlanilhaNaoConfigurada(f"Não foi possível ler as abas do Prontuário: {falha}") from falha

    todas_as_placas = set(cadastro) | set(veiculos_oleo) | set(mapa_alinhamento) \
        | set(mapa_5r_carreta) | set(mapa_integridade_tempo) | set(mapa_integridade_km) \
        | set(mapa_sr_tempo) | set(mapa_prev_carretas_tempo) \
        | set(mapa_logistica)

    prontuario = {}
    for placa in todas_as_placas:
        categorias = {}

        if placa in veiculos_oleo:
            categorias["revisao_oleo"] = veiculos_oleo[placa]
        if placa in mapa_alinhamento:
            categorias["alinhamento"] = mapa_alinhamento[placa]

        # Só por TEMPO (Carreta) - o controle por KM do Cavalo (aba "Inspeção
        # 5ª roda 10.000KM") foi removido por decisão de negócio. Cavalo sem
        # mais nenhuma forma de rastrear 5ª roda: essa categoria agora só
        # existe pra Carreta.
        if placa in mapa_5r_carreta:
            categorias["quinta_roda"] = _item_por_data(mapa_5r_carreta[placa])

        integridade = _categoria_dupla(mapa_integridade_tempo, mapa_integridade_km, placa)
        if integridade:
            categorias["integridade_chassi"] = integridade

        sr_tanques = _calcular_sr_tanques(mapa_sr_tempo, placa)
        if sr_tanques:
            categorias["sr_tanques"] = sr_tanques

        # Só por TEMPO - o controle por KM ("PREVENTIVA CARRETAS 40.000KM")
        # foi removido por decisão de negócio (mesma erradicação do KM do
        # Cavalo na 5ª roda, acima). Não é mais "dupla": passou a confiar
        # 100% na aba "Preventiva Semestral CARRETAS", igual a alinhamento.
        if placa in mapa_prev_carretas_tempo:
            categorias["preventiva_carretas"] = _item_por_data(mapa_prev_carretas_tempo[placa])

        if not categorias and placa not in cadastro:
            continue

        status_por_categoria = []
        for chave, valor in categorias.items():
            if "status_geral" in valor:
                status_por_categoria.append(valor["status_geral"])
            else:
                status_por_categoria.append(valor.get("status"))

        prontuario[placa] = {
            "placa": placa,
            "cadastro": cadastro.get(placa),
            "categorias": categorias,
            "logistica": mapa_logistica.get(placa),
            "status_geral": _status_geral(*status_por_categoria),
        }

    return prontuario


def resumo_analitico_matriz():
    """Painel de Guerra: recria a visão da aba "RESUMO ANALITICO" da planilha
    original (colunas PLACA, REVISAO, 5ª RODA, INTEGRIDADE, SR-TANQUES, NO
    PATIO - "IPIRANGA" foi removida por pedido explícito, não tinha regra de
    status associada), mas cruzando TEMPO e KM (o pior dos dois) em cada
    categoria "dupla", em vez de só TEMPO como a fórmula original faz - ver
    [[Módulo de Manutenção]]. Cada linha é 1 placa; nenhuma leitura extra do
    Drive além do que `montar_prontuario_frota()` já baixa e cruza - a coluna
    SR-TANQUES vem exatamente da mesma categoria (`sr_tanques.status`, regra
    dos 180 dias - `_calcular_sr_tanques`) que alimenta a aba dedicada
    "SR-Tanques" da interface: as duas são vistas diferentes da MESMA fonte,
    sempre em sincronia por construção."""
    prontuario = montar_prontuario_frota()
    ocultos = carregar_ocultos()
    linhas = []
    for placa, registro in prontuario.items():
        if placa in ocultos:
            continue
        categorias = registro.get("categorias") or {}
        cadastro = registro.get("cadastro") or {}
        logistica = registro.get("logistica") or {}
        preventiva = categorias.get("preventiva_carretas")
        integridade = categorias.get("integridade_chassi")
        sr_tanques = categorias.get("sr_tanques")
        quinta_roda = categorias.get("quinta_roda")

        linha = {
            "placa": placa,
            "tipo": cadastro.get("tipo"),
            "marca": cadastro.get("marca"),
            "modelo": cadastro.get("modelo"),
            "revisao": preventiva.get("status") if preventiva else None,
            "quinta_roda": quinta_roda.get("status") if quinta_roda else None,
            "integridade": integridade.get("status_geral") if integridade else None,
            "sr_tanques": sr_tanques.get("status") if sr_tanques else None,
            "no_patio": bool(logistica.get("no_patio")) if logistica else False,
            "status_geral": registro.get("status_geral"),
        }
        # Só entra na matriz quem tem pelo menos 1 das 4 categorias OU
        # aparecia na aba original - uma linha 100% vazia não ajuda ninguém.
        if any([linha["revisao"], linha["quinta_roda"], linha["integridade"], linha["sr_tanques"], logistica]):
            linhas.append(linha)

    linhas.sort(key=lambda l: l["placa"])
    return linhas


# Toda frente de manutenção que entra no Health Score do Dashboard Geral -
# rótulo em português só para depuração/logs; o frontend tem sua própria
# tradução (`ROTULOS_FRENTE` em manutencao.js) para não acoplar o texto da
# tela ao backend.
CATEGORIAS_FRENTE = (
    "revisao_oleo", "alinhamento", "quinta_roda", "integridade_chassi",
    "sr_tanques", "preventiva_carretas",
)


def _proxima_data_categoria(valor):
    """Extrai a "próxima data" (calendário) de uma categoria, quando ela tiver
    uma - só itens rastreados por TEMPO têm isso; um item por KM não tem
    nenhuma data de calendário para prever. Cobre os 3 formatos que o
    Prontuário já produz, sem precisar saber qual categoria é qual:
    - "dupla" (integridade de chassi, preventiva de carretas): usa só o lado
      `tempo` (o lado `km` não tem data).
    - simples por dias (alinhamento de carreta, 5ª roda de carreta): tem
      `unidade == "dias"` e `proxima` já é uma data ISO.
    - SR-Tanques: não tem `unidade` nem `tempo`, mas `proxima` é sempre uma
      data (ou None) - a regra dos 180 dias.
    Usada pela "Previsão de Paradas" (`calcular_saude_frota`), que soma
    quantas dessas datas caem em cada um dos próximos 6 meses."""
    if not valor:
        return None
    if "tempo" in valor:
        sub = valor.get("tempo")
        data = sub.get("proxima") if sub else None
    elif valor.get("unidade") == "dias":
        data = valor.get("proxima")
    elif "unidade" not in valor and "proxima" in valor:
        data = valor.get("proxima")
    else:
        data = None

    # Blindagem contra o MESMO bug de época do Excel que motivou a regra dos
    # 180 dias do SR-Tanques (`_calcular_sr_tanques`): confirmado ao rodar
    # contra o arquivo real, outras abas "por tempo" (alinhamento de carreta,
    # preventiva de carretas) têm a mesma coluna de fórmula
    # "Próxima revisão" - com a última data em branco, o Excel calcula a
    # partir do serial 0 (1899/1900) e essas abas NÃO tiveram o mesmo
    # tratamento que o SR-Tanques (a regra exata de cada uma não foi
    # confirmada pelo setor). Uma data de calendário antes do ano 2000 nunca
    # é uma previsão real, então é descartada aqui - comparação lexicográfica
    # de string ISO funciona porque "AAAA-MM-DD" ordena igual a uma data.
    if data and data < "2000-01-01":
        return None
    return data


# Um "falta" (dias ou km até/além da próxima manutenção) acima destes limites é
# quase certamente lixo de fórmula do Excel - o mesmo bug de época documentado em
# `_calcular_sr_tanques` (data de origem em branco => serial 0 => "atrasado há
# 46.000 dias"). Melhor mostrar "sem número" do que um número absurdo na tela.
LIMITE_FALTA_DIAS = 3650
LIMITE_FALTA_KM = 1_000_000


def _detalhe_frente(valor):
    """{unidade, falta, proxima} do item que DITA o status de uma frente, para as
    páginas de análise do Dashboard (atraso, previsão de paradas).

    `falta` segue o sinal da planilha: positivo = ainda falta, negativo = já passou
    (dias para a unidade "dias", km para "km"). `proxima` é a data ISO da próxima
    manutenção - só existe para itens por TEMPO. Cobre os formatos que o Prontuário
    produz, sem precisar saber qual frente é qual:

    - "dupla" (integridade de chassi, tem `tempo` e `km`): o lado de pior status
      (empate: o de tempo);
    - revisão & óleo (tem `revisao`, `oleo_motor`...): entre os 5 itens, o de pior
      status e, no empate, o que mais passou do km;
    - simples (alinhamento, 5ª roda, preventiva) e SR-Tanques (sem `unidade`, sempre
      por dias).

    Devolve None quando não há nenhum número confiável."""
    if not valor:
        return None

    def severidade(status):
        return ORDEM_STATUS.get(status, -1)

    proxima = None
    if "tempo" in valor:
        candidatos = [(item, un) for item, un in ((valor.get("tempo"), "dias"), (valor.get("km"), "km")) if item]
        if not candidatos:
            return None
        item, unidade = max(candidatos, key=lambda c: severidade(c[0].get("status")))
        falta = item.get("falta")
        proxima = item.get("proxima") if unidade == "dias" else None
    elif "revisao" in valor:
        itens = [valor.get(chave) for chave in ("revisao", "aditivo", "oleo_cambio", "oleo_diferencial", "oleo_motor")]
        itens = [i for i in itens if i and i.get("status")]
        if not itens:
            return None
        pior = max(severidade(i["status"]) for i in itens)
        piores = [i for i in itens if severidade(i["status"]) == pior]
        com_km = [i for i in piores if i.get("km_falta") is not None]
        item = min(com_km, key=lambda i: i["km_falta"]) if com_km else piores[0]
        unidade, falta = "km", item.get("km_falta")
    else:
        unidade = valor.get("unidade") or "dias"
        falta = valor.get("falta")
        proxima = valor.get("proxima") if unidade == "dias" else None

    limite = LIMITE_FALTA_DIAS if unidade == "dias" else LIMITE_FALTA_KM
    if falta is None or abs(falta) > limite:
        falta = None
    if not isinstance(proxima, str) or proxima < "2000-01-01":
        proxima = None
    if falta is None and proxima is None:
        return None
    return {"unidade": unidade, "falta": falta, "proxima": proxima}


def _ano_do_veiculo(cadastro):
    """Ano do veículo para a análise por idade: o do modelo, senão o de fabricação;
    só aceita anos plausíveis (a planilha tem digitação solta)."""
    for chave in ("ano_mod", "ano_fab"):
        ano = cadastro.get(chave)
        if isinstance(ano, (int, float)) and 1980 <= ano <= date.today().year + 1:
            return int(ano)
    return None


def calcular_saude_frota(prontuario):
    """Base do Dashboard Geral "PRO MAX": Health Score + filtro "Apenas
    Cavalos / Apenas Carretas / Ambos", cruzando TODAS as frentes de
    manutenção (óleo, alinhamento, chassi, 5ª roda, SR-tanques, preventiva de
    carretas) - não só a aba "Revisões e óleo" como o antigo
    `calcular_metricas()` fazia (removido: só cobria Cavalos com motor).

    Devolve uma lista achatada, 1 item por veículo/implemento (Cavalo OU
    Carreta, os dois universos juntos), com o status JÁ resumido por frente -
    o detalhe completo de cada categoria continua vindo das abas dedicadas e
    do Prontuário. O frontend filtra essa lista por `tipo` e recalcula os
    agregados (KPIs, Health Score, gráficos) no cliente a cada clique no
    filtro, sem precisar voltar ao Drive.

    Só entram veículos com `cadastro` conhecido (Frota ou info-carretas) -
    sem isso não dá para classificar Cavalo/Carreta, o próprio propósito
    desta função; uma placa "órfã" (apareceu numa aba de categoria mas nunca
    foi cadastrada) continua aparecendo normalmente no Prontuário e no
    Resumo Analítico, só não entra neste painel específico.

    `proximas` é a lista de datas (ISO, uma por categoria que tiver uma -
    ver `_proxima_data_categoria`) usada pela "Previsão de Paradas": o
    frontend agrupa por mês essas datas de TODOS os veículos filtrados,
    sem round-trip novo ao Drive.

    Campos para as páginas de análise de cada card (Saúde da Frota e cada
    frente): `detalhes` ({frente: {unidade, falta, proxima}}, ver
    `_detalhe_frente`) dá o atraso e a próxima data POR FRENTE; `ano` e
    `centro_custo` vêm do cadastro e permitem cortar a saúde por idade e por
    centro de custo."""
    veiculos = []
    ocultos = carregar_ocultos()
    for placa, registro in prontuario.items():
        if placa in ocultos:
            continue
        cadastro = registro.get("cadastro")
        if not cadastro or not cadastro.get("tipo"):
            continue

        categorias = registro.get("categorias") or {}
        status_por_frente = {}
        proximas_datas = []
        detalhes = {}
        for chave in CATEGORIAS_FRENTE:
            valor = categorias.get(chave)
            if not valor:
                continue
            status_por_frente[chave] = valor["status_geral"] if "status_geral" in valor else valor.get("status")
            data = _proxima_data_categoria(valor)
            if data:
                proximas_datas.append(data)
            detalhe = _detalhe_frente(valor)
            if detalhe:
                detalhes[chave] = detalhe

        veiculos.append({
            "placa": placa,
            "tipo": cadastro.get("tipo"),
            "marca": cadastro.get("marca"),
            "modelo": cadastro.get("modelo"),
            "status_geral": registro.get("status_geral"),
            "categorias": status_por_frente,
            "proximas": proximas_datas,
            "detalhes": detalhes,
            "ano": _ano_do_veiculo(cadastro),
            "centro_custo": cadastro.get("centro_custo"),
        })

    veiculos.sort(key=lambda v: v["placa"])
    return veiculos

