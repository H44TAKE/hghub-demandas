# -*- coding: utf-8 -*-
"""Infraestrutura transversal: caminhos de dados, configuracao, seguranca e e-mail."""

import contextlib
import hashlib
import json
import logging
import os
import secrets
import smtplib
import ssl
import sys
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("hghub")

# ==============================================================
# CAMINHOS
# ==============================================================
# Diretorio da aplicacao (funciona tambem empacotado com PyInstaller).
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NOME_ARQUIVO_DADOS = "dados_sistema_hg.json"

# Pasta LOCAL do app no PC do usuario (fora da pasta de instalacao: sobrevive a atualizacoes e a
# desinstalacao): logs, estado do atualizador, credenciais, base local quando nao ha rede.
PASTA_LOCAL_APP = os.environ.get("HGHUB_DIR_LOCAL") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "HGhub")
EMPACOTADO = bool(getattr(sys, "frozen", False))

# Pasta de rede com os dados de producao, compartilhada entre os usuarios. A MESMA
# pasta do servidor aparece com letras de unidade diferentes conforme o PC (na maioria
# e Z:, em alguns e Y:), entao procura em todas as letras desta lista. Para aceitar outra
# letra, basta acrescentar aqui.
#
# A pasta ja mudou de lugar no SGQ (em 20/10/2026 saiu de "02 - RD - QUALIDADE" para
# "01 - QUALIDADE"; sem achar nenhuma, o app cairia numa base local VAZIA e todo mundo veria
# o sistema sem dados). Por isso a lista de locais: o primeiro e o oficial; os seguintes sao
# reserva, caso a pasta volte ou apareca em outro PC ainda no lugar antigo.
PASTAS_REDE_DADOS = (
    r"SGQ\01 - QUALIDADE\SISTEMA DEMANDAS - Copia",
    r"SGQ\02 - RD - QUALIDADE\SISTEMA DEMANDAS - Copia",
)
LETRAS_UNIDADE_REDE = ("Z", "Y")
ARQUIVO_DADOS_LOCAL = NOME_ARQUIVO_DADOS


def _arquivo_dados_na_rede():
    """Caminho do JSON de producao na rede: percorre os locais de `PASTAS_REDE_DADOS`, em ordem,
    e em cada um as unidades de `LETRAS_UNIDADE_REDE` (Z:, depois Y:); vale a primeira pasta que
    existir. None se nenhuma estiver acessivel neste PC."""
    for pasta_rede in PASTAS_REDE_DADOS:
        for letra in LETRAS_UNIDADE_REDE:
            pasta = f"{letra}:\\{pasta_rede}"
            if os.path.isdir(pasta):
                return os.path.join(pasta, NOME_ARQUIVO_DADOS)
    return None


def _resolver_arquivo_dados():
    """Escolhe onde os dados vivem, sem nunca abandonar um arquivo existente.

    A unidade de rede (Z: ou Y:) tem prioridade (dados de producao). Sem ela, reaproveita
    um JSON local ja existente antes de assumir um caminho novo.
    """
    # Escape hatch para desenvolvimento e testes: aponta o sistema para uma base
    # de mentira, sem risco de encostar nos dados reais da rede.
    forcado = os.environ.get("HGHUB_ARQUIVO_DADOS")
    if forcado:
        return os.path.abspath(forcado)

    na_rede = _arquivo_dados_na_rede()
    if na_rede:
        return na_rede

    candidatos = [
        os.path.join(os.getcwd(), NOME_ARQUIVO_DADOS),
        os.path.join(BASE_DIR, NOME_ARQUIVO_DADOS),
    ]
    for caminho in candidatos:
        if os.path.exists(caminho):
            return caminho
    if EMPACOTADO:
        # Instalado e sem a pasta de rede: a base local NOVA nasce na pasta do usuario, nunca dentro da
        # instalacao (que uma atualizacao ou a desinstalacao poderiam mexer).
        return os.path.join(PASTA_LOCAL_APP, "dados", NOME_ARQUIVO_DADOS)
    return candidatos[0]


ARQUIVO_DADOS = _resolver_arquivo_dados()


def obter_diretorio_dados():
    """Diretorio onde todos os arquivos de dados do sistema estao armazenados."""
    return os.path.dirname(os.path.abspath(ARQUIVO_DADOS))


def caminho_arquivo_dados(nome_arquivo):
    """Caminho completo de um arquivo dentro do diretorio de dados."""
    return os.path.join(obter_diretorio_dados(), nome_arquivo)


def caminho_sessao_local():
    """Arquivo de sessao gravado no PC do usuario.

    Fica fora da pasta de dados de proposito: com o JSON em rede, duas pessoas
    compartilhariam a mesma sessao se o token morasse junto dos dados.
    """
    pasta_appdata = os.environ.get("APPDATA")
    if pasta_appdata:
        pasta_local = os.path.join(pasta_appdata, "HGHub")
    else:
        pasta_local = os.path.join(os.path.expanduser("~"), ".hghub")
    os.makedirs(pasta_local, exist_ok=True)
    return os.path.join(pasta_local, "sessao_hg.json")


ARQUIVO_CONFIG = caminho_arquivo_dados("config.json")
ARQUIVO_USUARIOS = caminho_arquivo_dados("usuarios.json")
# Quem e Conta Mestre e de quais setores cada conta e - fica na MESMA pasta dos
# demais arquivos do sistema (Z:), para valer em todos os computadores. Fonte da
# verdade dessas duas informacoes: ver `SistemaGestaoHG._aplicar_permissoes`.
ARQUIVO_PERMISSOES = caminho_arquivo_dados("permissoes_contas.json")
PASTA_FOTOS = caminho_arquivo_dados("fotos_usuarios")
PASTA_ANEXOS = caminho_arquivo_dados("anexos_tarefas")
# Credencial de Service Account do Google, para o Modulo de Manutencao
# Preventiva (backend/manutencao.py). Nunca versionar este arquivo.
# Fica na raiz do projeto (BASE_DIR), nao na pasta de dados: e um segredo do
# deployment da aplicacao, nao um dado compartilhado pela rede (Z:).
def _resolver_credenciais():
    """Onde esta o credenciais.json do Google: variavel de ambiente, pasta local do usuario (recomendado:
    nao e tocada por atualizacoes) ou, por compatibilidade, ao lado do programa."""
    for caminho in (os.environ.get("HGHUB_CREDENCIAIS"),
                    os.path.join(PASTA_LOCAL_APP, "credenciais.json"),
                    os.path.join(BASE_DIR, "credenciais.json")):
        if caminho and os.path.exists(caminho):
            return caminho
    return os.path.join(PASTA_LOCAL_APP, "credenciais.json")


ARQUIVO_CREDENCIAIS_SHEETS = _resolver_credenciais()


# ==============================================================
# CONFIGURACAO
# ==============================================================
# Só é usado quando config.json ainda não existe; um config.json presente nunca é
# sobrescrito. Nenhum segredo (nem o e-mail remetente) mora no código-fonte: o e-mail e a
# senha de app vêm do `config.json` da pasta de dados ou das variáveis de ambiente
# HGHUB_SMTP_EMAIL / HGHUB_SMTP_SENHA. Sem eles, a recuperação de senha avisa que o SMTP
# não está configurado, em vez de falhar em silêncio.
CONFIG_PADRAO = {
    "smtp": {
        "email": os.environ.get("HGHUB_SMTP_EMAIL", ""),
        "senha_app": os.environ.get("HGHUB_SMTP_SENHA", ""),
        "servidor": os.environ.get("HGHUB_SMTP_SERVIDOR", "smtp.gmail.com"),
        "porta": int(os.environ.get("HGHUB_SMTP_PORTA", "587")),
    },
    # Modulo de Manutencao Preventiva (ver backend/manutencao.py): vazio por
    # padrao de proposito - so preenche sozinho quando alguem configurar.
    "google_sheets": {
        "planilha_id": os.environ.get("HGHUB_SHEETS_ID", ""),
        "aba": os.environ.get("HGHUB_SHEETS_ABA", "Revisões e óleo"),
    },
}


def criar_arquivos_automaticamente():
    """Garante a existencia de config.json e usuarios.json sem sobrescrever nada."""
    os.makedirs(os.path.dirname(os.path.abspath(ARQUIVO_CONFIG)), exist_ok=True)   # instalacao nova: pasta ainda nao existe
    if not os.path.exists(ARQUIVO_CONFIG):
        with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
            json.dump(CONFIG_PADRAO, f, indent=2, ensure_ascii=False)

    if not os.path.exists(ARQUIVO_USUARIOS):
        with open(ARQUIVO_USUARIOS, "w", encoding="utf-8") as f:
            json.dump({"usuarios": []}, f, indent=2, ensure_ascii=False)


def carregar_config():
    """Le config.json, criando-o com valores padrao caso ainda nao exista."""
    criar_arquivos_automaticamente()
    try:
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as erro:
        log.warning("Falha ao ler config.json (%s). Usando configuracao padrao.", erro)
        return CONFIG_PADRAO


# ==============================================================
# CONCORRENCIA ENTRE PROCESSOS (BLOQUEIO DE ARQUIVO)
# ==============================================================
# Cada PC roda seu proprio processo Flask local, todos apontando para o MESMO
# dados_sistema_hg.json na rede. Sem uma trava real entre processos, dois PCs
# salvando ao mesmo tempo fariam um sobrescrever silenciosamente o outro (o
# classico "lost update"). A trava usa um arquivo-marcador (`<arquivo>.lock`)
# criado com O_EXCL, que so tem sucesso se nenhum outro processo o possui -
# essa criacao e atomica no sistema de arquivos, inclusive em unidades de rede
# SMB, sem exigir nenhuma dependencia externa (nem migrar para outro banco).
TEMPO_LIMITE_TRAVA_PADRAO = 20
IDADE_MAXIMA_TRAVA_ORFA = 30  # segundos; acima disso, assume processo morto e destrava


@contextlib.contextmanager
def bloqueio_arquivo(caminho, tempo_limite=TEMPO_LIMITE_TRAVA_PADRAO):
    """Trava exclusiva entre processos para o ciclo ler-modificar-gravar de `caminho`.

    Uso: `with bloqueio_arquivo(ARQUIVO_DADOS): ...` em volta de RELER o estado
    mais recente do disco e GRAVAR de volta - nunca em volta de uma escrita que
    parte de uma copia em memoria potencialmente desatualizada.
    """
    caminho_trava = f"{caminho}.lock"
    inicio = time.time()

    while True:
        try:
            descritor = os.open(caminho_trava, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descritor)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(caminho_trava) > IDADE_MAXIMA_TRAVA_ORFA:
                    os.remove(caminho_trava)  # trava orfa de um processo que morreu com ela presa
                    continue
            except OSError:
                pass  # outro processo removeu/recriou a trava entre as duas chamadas; tenta de novo

            if time.time() - inicio > tempo_limite:
                raise TimeoutError(f"Não foi possível obter o bloqueio de '{caminho}' a tempo.")
            time.sleep(0.05)

    try:
        yield
    finally:
        try:
            os.remove(caminho_trava)
        except OSError:
            pass


# ==============================================================
# SEGURANCA
# ==============================================================
ITERACOES_PBKDF2 = 200_000


def hash_senha(senha, salt=None):
    """Gera o hash PBKDF2-HMAC-SHA256 no formato `salt$hash`."""
    if salt is None:
        salt = secrets.token_hex(16)
    chave = hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), salt.encode("utf-8"), ITERACOES_PBKDF2
    )
    return f"{salt}${chave.hex()}"


def verificar_senha(senha, hash_armazenado):
    """Compara senha e hash. Aceita senhas antigas em texto puro para migracao."""
    if not hash_armazenado:
        return False
    if "$" in hash_armazenado:
        salt, _ = hash_armazenado.split("$", 1)
        return secrets.compare_digest(hash_senha(senha, salt), hash_armazenado)
    return secrets.compare_digest(senha, hash_armazenado)


def senha_e_texto_puro(hash_armazenado):
    """Indica se a senha ainda esta sem hash e precisa ser migrada."""
    return bool(hash_armazenado) and "$" not in hash_armazenado


def gerar_token():
    """Token de sessao opaco."""
    return secrets.token_urlsafe(48)


def gerar_codigo_recuperacao():
    """Codigo numerico de 6 digitos para recuperacao de senha."""
    # `secrets` (nao `random`): o codigo e uma credencial de curta duracao e nao pode ser previsivel.
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


class LimitadorTentativas:
    """Freio contra forca bruta: no maximo `maximo` ocorrencias por chave dentro de `janela` segundos.
    Em memoria (cada PC roda o proprio processo); suficiente porque a API so escuta em 127.0.0.1."""

    def __init__(self, maximo, janela_segundos):
        self.maximo = maximo
        self.janela = janela_segundos
        self._eventos = {}
        self._trava = threading.Lock()

    def _podar(self, chave, agora):
        eventos = [t for t in self._eventos.get(chave, []) if agora - t < self.janela]
        if eventos:
            self._eventos[chave] = eventos
        else:
            self._eventos.pop(chave, None)
        return eventos

    def segundos_restantes(self, chave):
        """0 se pode tentar; senao, quantos segundos faltam para liberar."""
        with self._trava:
            agora = time.time()
            eventos = self._podar(chave, agora)
            if len(eventos) < self.maximo:
                return 0
            return max(1, int(self.janela - (agora - eventos[0])) + 1)

    def registrar(self, chave):
        with self._trava:
            agora = time.time()
            self._podar(chave, agora)
            self._eventos.setdefault(chave, []).append(agora)

    def limpar(self, chave):
        with self._trava:
            self._eventos.pop(chave, None)


def extrair_token(request):
    """Le o token do corpo JSON ou do header Authorization."""
    dados = request.get_json(silent=True) or {}
    token = dados.get("token")
    if not token:
        token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    return token or None


# ==============================================================
# SESSAO LOCAL (arquivo no PC do usuario)
# ==============================================================
def salvar_sessao_local(token, usuario_id):
    try:
        with open(caminho_sessao_local(), "w", encoding="utf-8") as f:
            json.dump({"token": token, "usuario_id": usuario_id}, f)
    except Exception as erro:
        log.warning("Nao foi possivel salvar a sessao local: %s", erro)


def ler_sessao_local():
    try:
        caminho = caminho_sessao_local()
        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as erro:
        log.warning("Nao foi possivel ler a sessao local: %s", erro)
    return None


def remover_sessao_local():
    try:
        caminho = caminho_sessao_local()
        if os.path.exists(caminho):
            os.remove(caminho)
    except Exception as erro:
        log.warning("Nao foi possivel remover a sessao local: %s", erro)


# ==============================================================
# E-MAIL
# ==============================================================
def _template_email_recuperacao(codigo):
    return f"""
    <html>
    <body style="margin:0;padding:32px 16px;background:#F1F4F9;font-family:Segoe UI,Arial,sans-serif;">
      <div style="max-width:480px;margin:0 auto;background:#FFFFFF;border-radius:16px;overflow:hidden;border:1px solid #E4E8F0;">
        <div style="background:linear-gradient(135deg,#0D3B8C,#2563EB);padding:28px 24px;text-align:center;">
          <h1 style="color:#F5B800;margin:0;font-size:22px;letter-spacing:3px;">REDE HG</h1>
          <p style="color:rgba(255,255,255,0.75);margin:6px 0 0;font-size:11px;letter-spacing:2px;">TRANSPORTADORA</p>
        </div>
        <div style="padding:32px 28px;">
          <h2 style="color:#0D3B8C;margin:0 0 16px;font-size:19px;">Recuperacao de senha</h2>
          <p style="color:#475569;line-height:1.6;margin:0 0 24px;">Recebemos uma solicitacao para redefinir a senha da sua conta. Use o codigo abaixo para continuar.</p>
          <div style="background:#FFF8E1;border:1px solid #F5D67F;padding:20px;border-radius:12px;text-align:center;margin:0 0 24px;">
            <span style="font-size:34px;font-weight:700;color:#0D3B8C;letter-spacing:10px;font-family:Consolas,monospace;">{codigo}</span>
          </div>
          <p style="color:#64748B;font-size:13px;margin:0 0 8px;">O codigo e valido por <strong>10 minutos</strong>.</p>
          <p style="color:#64748B;font-size:13px;margin:0;">Se voce nao fez essa solicitacao, ignore este e-mail.</p>
          <hr style="border:none;border-top:1px solid #E4E8F0;margin:24px 0;">
          <p style="color:#94A3B8;font-size:12px;margin:0;">Equipe REDE HG</p>
        </div>
      </div>
    </body>
    </html>
    """


def enviar_email_recuperacao(destinatario, codigo):
    """Envia o codigo de recuperacao por SMTP. Retorna (sucesso, mensagem)."""
    try:
        config = carregar_config().get("smtp", {})
        remetente = config.get("email")
        senha_app = (config.get("senha_app") or "").replace(" ", "")
        if not remetente or not senha_app:
            return False, "Credenciais SMTP nao configuradas em config.json"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Recuperacao de Senha - REDE HG"
        msg["From"] = remetente
        msg["To"] = destinatario
        msg.attach(MIMEText(_template_email_recuperacao(codigo), "html"))

        with smtplib.SMTP(config.get("servidor", "smtp.gmail.com"), config.get("porta", 587), timeout=20) as server:
            server.starttls(context=ssl.create_default_context())   # valida o certificado do servidor
            server.ehlo()
            _autenticar_smtp(server, remetente, senha_app)
            server.send_message(msg)
        return True, "E-mail enviado com sucesso"
    except smtplib.SMTPAuthenticationError as erro:
        return False, (
            f"O servidor de e-mail recusou o login de {remetente} (erro {erro.smtp_code}: usuário ou senha de app "
            "não aceitos). A senha de app foi revogada ou trocada: gere uma nova senha de app para essa conta "
            "e atualize 'smtp.senha_app' no config.json (pasta de dados, ao lado do dados_sistema_hg.json)."
        )
    except Exception as erro:
        return False, f"Erro ao enviar e-mail: {erro}"


def _autenticar_smtp(server, remetente, senha_app):
    """Login no servidor SMTP mostrando o motivo REAL de uma recusa.

    `SMTP.login()` tenta os mecanismos em sequência (PLAIN, depois LOGIN): quando o Gmail recusa a senha
    (535), ele derruba a conexão na segunda tentativa e o erro que chegava ao log era um enganoso
    "Connection unexpectedly closed". Com PLAIN (que todo servidor moderno aceita) a recusa sobe como
    `SMTPAuthenticationError`; servidores que só falam LOGIN seguem pelo caminho antigo."""
    mecanismos = server.esmtp_features.get("auth", "").upper().split()
    if "PLAIN" in mecanismos:
        server.user, server.password = remetente, senha_app
        server.auth("PLAIN", server.auth_plain, initial_response_ok=True)
    else:
        server.login(remetente, senha_app)
