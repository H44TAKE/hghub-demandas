# -*- coding: utf-8 -*-
"""Importar planilha de vencimento de exames (aba do mes) como demandas, sem duplicar as que ja existem.

A planilha tem uma aba por mes (ex.: "NOVEMBRO2026") com as colunas REGIAO, NOME, EXAME, DATA EXAME,
VENCIMENTO, SITUACAO, COBRAR RESULTADO, EXAME LIBERADO, CLINICA, OBS. Cada linha vira uma demanda no
mesmo padrao das que o RH ja tem (titulo = primeiro e segundo nome, etiqueta = tipo do exame,
vencimento = VENCIMENTO). O arquivo SO e lido; nada nele e alterado.
"""

import base64
import io
import re
import unicodedata
from collections import Counter
from datetime import datetime

MESES = ("JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO",
         "OUTUBRO", "NOVEMBRO", "DEZEMBRO")
ESTADOS = {"MG": "MINAS GERAIS", "BA": "BAHIA", "AL": "ALAGOAS", "SE": "SERGIPE", "ES": "ESPIRITO SANTO",
           "GO": "GOIAS", "SP": "SAO PAULO", "RJ": "RIO DE JANEIRO", "PE": "PERNAMBUCO"}
TAMANHO_MAXIMO = 16 * 1024 * 1024


class ImportacaoError(ValueError):
    """Erro com mensagem pronta para mostrar ao usuario."""


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.upper().split())


def decodificar_arquivo(conteudo_base64):
    if not conteudo_base64 or not isinstance(conteudo_base64, str):
        raise ImportacaoError("Nenhum arquivo foi enviado.")
    if "," in conteudo_base64[:100]:
        conteudo_base64 = conteudo_base64.split(",", 1)[1]
    try:
        binario = base64.b64decode(conteudo_base64, validate=False)
    except Exception as erro:
        raise ImportacaoError("O arquivo enviado está corrompido.") from erro
    if len(binario) > TAMANHO_MAXIMO:
        raise ImportacaoError("Arquivo muito grande (limite de 16 MB).")
    if binario[:2] != b"PK":
        raise ImportacaoError("O arquivo precisa ser uma planilha do Excel (.xlsx).")
    return binario


def _abrir(binario):
    import warnings

    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return openpyxl.load_workbook(io.BytesIO(binario), read_only=True, data_only=True)
        except Exception as erro:
            raise ImportacaoError(f"Não consegui abrir a planilha: {erro}") from erro


def aba_sugerida(nomes, hoje=None):
    """A aba do mes corrente (ex.: NOVEMBRO2026); se nao houver, a mais recente que existir; senao a primeira."""
    hoje = hoje or datetime.now()
    alvo = f"{MESES[hoje.month - 1]}{hoje.year}"
    chaves = {n: normalizar(n).replace(" ", "") for n in nomes}
    for nome, chave in chaves.items():
        if chave == alvo:
            return nome
    candidatas = []
    for nome, chave in chaves.items():
        for i, mes in enumerate(MESES):
            if chave.startswith(mes):
                ano = re.search(r"(\d{4})", chave)
                if ano:
                    candidatas.append((int(ano.group(1)), i, nome))
    return max(candidatas)[2] if candidatas else (nomes[0] if nomes else None)


def listar_abas(binario):
    wb = _abrir(binario)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _data(valor):
    if isinstance(valor, datetime):
        return valor
    if hasattr(valor, "year") and hasattr(valor, "month"):
        return datetime(valor.year, valor.month, valor.day)
    texto = str(valor or "").strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(texto[:10], formato)
        except ValueError:
            continue
    return None


def ler_exames(binario, aba):
    """Linhas da aba: [{regiao, nome, exame, data_exame, vencimento, situacao, cobrar, liberado, clinica, obs}]."""
    wb = _abrir(binario)
    try:
        if aba not in wb.sheetnames:
            raise ImportacaoError(f"A aba '{aba}' não existe na planilha.")
        linhas = list(wb[aba].iter_rows(values_only=True))
    finally:
        wb.close()

    cabecalho, mapa = None, {}
    for indice, linha in enumerate(linhas[:30]):
        nomes = [normalizar(c) for c in linha]
        if "NOME" in nomes and "VENCIMENTO" in nomes:
            cabecalho = indice
            for posicao, nome in enumerate(nomes):
                chave = {"REGIAO": "regiao", "NOME": "nome", "EXAME": "exame", "DATA EXAME": "data_exame",
                         "VENCIMENTO": "vencimento", "SITUACAO": "situacao", "COBRAR RESULTADO": "cobrar",
                         "EXAME LIBERADO": "liberado", "CLINICA": "clinica", "OBS": "obs"}.get(nome)
                if chave and chave not in mapa:
                    mapa[chave] = posicao
            break
    if cabecalho is None:
        raise ImportacaoError(f"Não achei as colunas NOME e VENCIMENTO na aba '{aba}'. "
                              "Confira se é a aba de vencimento de exames.")

    saida = []
    for linha in linhas[cabecalho + 1:]:
        def campo(chave):
            posicao = mapa.get(chave)
            return linha[posicao] if posicao is not None and posicao < len(linha) else None
        nome = " ".join(str(campo("nome") or "").split())
        if not nome or nome.startswith("*"):
            continue
        vencimento = _data(campo("vencimento"))
        if not vencimento:
            continue
        saida.append({
            "regiao": normalizar(campo("regiao")),
            "nome": nome,
            "exame": " ".join(str(campo("exame") or "").split()),
            "data_exame": _data(campo("data_exame")),
            "vencimento": vencimento,
            "situacao": " ".join(str(campo("situacao") or "").split()),
            "cobrar": _data(campo("cobrar")),
            "liberado": _data(campo("liberado")),
            "clinica": " ".join(str(campo("clinica") or "").split()),
            "obs": " ".join(str(campo("obs") or "").split()),
        })
    return saida


# ---------------------------------------------------------------- planejamento (sem gravar nada)
def titulo_da_demanda(nome):
    """Mesmo padrao do RH: so o primeiro e o segundo nome (o nome completo vai na descricao)."""
    partes = nome.split()
    titulo = partes[:1]
    for parte in partes[1:]:
        titulo.append(parte)
        if normalizar(parte) not in ("DE", "DA", "DO", "DAS", "DOS", "E"):      # "JUVANILSON DE BRITO"
            break
    return " ".join(titulo).upper()


def _tokens(texto):
    return normalizar(texto).split()


def mesma_pessoa(titulo_existente, nome_completo):
    """'ALVARO ROGERIO' (titulo existente) e 'ALVARO ROGERIO LOUZADA DE SOUZA' (planilha) sao a mesma pessoa
    quando os nomes do titulo sao o comeco do nome completo. Titulos de um nome so exigem igualdade."""
    a, b = _tokens(titulo_existente), _tokens(nome_completo)
    if not a or not b:
        return False
    if len(a) == 1:
        return a == b
    return b[:len(a)] == a


def prioridade_por_prazo(vencimento, hoje=None):
    dias = (vencimento.date() - (hoje or datetime.now()).date()).days
    return "Alta" if dias <= 10 else ("Média" if dias <= 20 else "Baixa")


def status_da_linha(linha):
    situacao = normalizar(linha["situacao"])
    if "REALIZADO" in situacao or linha["liberado"]:
        return "Concluída"
    if "AGENDADO" in situacao and "NAO" not in situacao or linha["data_exame"]:
        return "Em Andamento"
    return "Pendente"


def projeto_padrao_da_regiao(regiao):
    return f"EXAMES PERIODICOS {ESTADOS.get(regiao, regiao or 'GERAL')}"


def pasta_sugerida(pastas):
    """A pasta do RH (R.H, RH, Recursos Humanos...), se existir."""
    for pasta in pastas:
        chave = normalizar(pasta.nome).replace(".", "").replace(" ", "")
        if chave in ("RH", "RECURSOSHUMANOS", "DEPARTAMENTOPESSOAL", "DP"):
            return pasta
    return None


def projeto_sugerido(pasta, regiao):
    alvo = normalizar(projeto_padrao_da_regiao(regiao))
    for projeto in pasta.projetos:
        if normalizar(projeto.nome) == alvo:
            return projeto
    return None


def responsavel_mais_comum(projeto):
    if projeto is None:
        return None
    ids = Counter(t.responsavel.id for t in projeto.tarefas if t.responsavel)
    return ids.most_common(1)[0][0] if ids else None


def classificar(pasta, linhas):
    """Para cada linha: ('nova' | 'existente', motivo). Duplicada = mesma pessoa com o mesmo vencimento na
    pasta, OU mesma pessoa com demanda ainda aberta do mesmo tipo de exame."""
    todas = [t for pr in pasta.projetos for t in pr.tarefas]
    resultado = []
    for linha in linhas:
        motivo = None
        for tarefa in todas:
            if not mesma_pessoa(tarefa.titulo, linha["nome"]):
                continue
            venc = tarefa.data_vencimento or tarefa.data_limite
            if venc and venc.date() == linha["vencimento"].date():
                motivo = "já existe (mesma pessoa e mesmo vencimento)"
                break
            aberta = not tarefa.concluida and tarefa.status.nome != "Cancelada"
            if aberta and normalizar(tarefa.etiqueta.nome) == normalizar(linha["exame"]):
                motivo = "já existe uma demanda aberta desta pessoa para este exame"
                break
        resultado.append(("existente", motivo) if motivo else ("nova", None))
    return resultado


def descricao_da_linha(linha, aba):
    partes = [f"Importada da planilha (aba {aba}).", f"Nome completo: {linha['nome']}."]
    if linha["regiao"]:
        partes.append(f"Região: {linha['regiao']}.")
    if linha["clinica"]:
        partes.append(f"Clínica: {linha['clinica']}.")
    if linha["situacao"]:
        partes.append(f"Situação na planilha: {linha['situacao']}.")
    if linha["data_exame"]:
        partes.append(f"Data do exame: {linha['data_exame'].strftime('%d/%m/%Y')}.")
    if linha["obs"]:
        partes.append(f"Obs.: {linha['obs']}")
    return " ".join(partes)
