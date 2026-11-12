# -*- coding: utf-8 -*-
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



# Gravidade de cada status, do melhor para o pior - usada para calcular o pior
# status entre os 5 itens controlados por veiculo (status_geral).
ORDEM_STATUS = {"EM DIA": 0, "PROGRAMAR PARADA": 1, "PARAR URGENTE": 2}


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
