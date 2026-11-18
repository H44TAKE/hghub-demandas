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


ARQUIVO_DADOS = _resolver_arquivo_dados()    # LEGADO: so leitura (ver backend/armazenamento.py)


def _sem_rede_bloqueado():
    """Instalado, sem a pasta de rede e sem base local explicitamente permitida: o app NAO deve abrir uma
    base local vazia em silencio (todo mundo veria o sistema "sem dados" e novas demandas ficariam isoladas)."""
    if not EMPACOTADO or os.environ.get("HGHUB_ARQUIVO_DADOS") or _arquivo_dados_na_rede():
        return False
    try:
        with open(os.path.join(PASTA_LOCAL_APP, "config_local.json"), "r", encoding="utf-8-sig") as f:
            if json.load(f).get("permitir_base_local") is True:
                return False
    except Exception:
        pass
    return True


SEM_REDE_BLOQUEADO = _sem_rede_bloqueado()
MENSAGEM_SEM_REDE = ("A pasta de dados da rede (unidade Z: ou Y:) não foi encontrada neste computador. "
                     "Conecte-se à rede, confira se a unidade está mapeada e reabra o programa. "
                     "Nada foi alterado.")


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


# ---- ARQUIVOS LEGADOS: o app so LE (importa uma vez e serve de copia de seguranca visual). Nunca grava. ----
LEGADO_CONFIG = caminho_arquivo_dados("config.json")
LEGADO_USUARIOS = caminho_arquivo_dados("usuarios.json")
LEGADO_PERMISSOES = caminho_arquivo_dados("permissoes_contas.json")
LEGADO_OCULTOS = caminho_arquivo_dados("veiculos_ocultos.json")
LEGADO_PASTA_FOTOS = caminho_arquivo_dados("fotos_usuarios")
LEGADO_PASTA_ANEXOS = caminho_arquivo_dados("anexos_tarefas")

# ---- ARMAZENAMENTO ATUAL: tudo o que o app grava mora numa pasta propria, uma coisa em cada arquivo ----
NOME_PASTA_DADOS = "HGhub-dados"
PASTA_DADOS = os.path.join(obter_diretorio_dados(), NOME_PASTA_DADOS)


def caminho_dado(*partes):
    """Caminho dentro da pasta de dados atual (`HGhub-dados`)."""
    return os.path.join(PASTA_DADOS, *partes)


ARQUIVO_USUARIOS = caminho_dado("usuarios.json")                  # contas, e-mails, senhas (hash), permissoes
ARQUIVO_PREFERENCIAS = caminho_dado("preferencias_usuarios.json") # notificacoes e favoritos de cada conta
ARQUIVO_DEMANDAS = caminho_dado("demandas.json")                  # pastas > projetos > tarefas
ARQUIVO_CATALOGOS = caminho_dado("catalogos.json")                # setores, cidades, etiquetas, anotacoes
ARQUIVO_SESSOES = caminho_dado("sessoes.json")                    # sessoes abertas e codigos de recuperacao (hash)
ARQUIVO_CONFIG = caminho_dado("configuracao.json")                # e-mail (SMTP) e planilha do Google
ARQUIVO_OCULTOS = caminho_dado("manutencao_veiculos_ocultos.json")
ARQUIVO_MIGRACAO = caminho_dado("_migracao.json")                 # marca que o legado ja foi importado
ARQUIVO_TRAVA = caminho_dado("_trava")                            # trava entre PCs (gera _trava.lock)
PASTA_FOTOS = caminho_dado("fotos")
PASTA_ANEXOS = caminho_dado("anexos")
PASTA_COPIAS = caminho_dado("copias")                             # copias de seguranca feitas antes de reimportar
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
    """Garante a pasta de dados e o configuracao.json (nunca sobrescreve; nunca toca nos arquivos legados)."""
    if SEM_REDE_BLOQUEADO:
        return
    os.makedirs(PASTA_DADOS, exist_ok=True)
    if not os.path.exists(ARQUIVO_CONFIG) and not os.path.exists(ARQUIVO_MIGRACAO) and not os.path.exists(LEGADO_CONFIG):
        with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
            json.dump(CONFIG_PADRAO, f, indent=2, ensure_ascii=False)


def carregar_config():
    """Le o configuracao.json (o config.json legado e importado na migracao; depois disso so vale o novo)."""
    criar_arquivos_automaticamente()
    for caminho in (ARQUIVO_CONFIG, LEGADO_CONFIG):
        if os.path.exists(caminho):
            try:
                with open(caminho, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
            except Exception as erro:
                log.warning("Falha ao ler %s (%s).", caminho, erro)
                break
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
IDADE_MAXIMA_TRAVA_ORFA = 90  # segundos; acima disso, assume processo morto e destrava


@contextlib.contextmanager
def bloqueio_arquivo(caminho, tempo_limite=TEMPO_LIMITE_TRAVA_PADRAO):
    """Trava exclusiva entre processos para o ciclo ler-modificar-gravar de `caminho`.

    Uso: `with bloqueio_arquivo(ARQUIVO_TRAVA): ...` em volta de RELER o estado mais recente do disco e
    GRAVAR de volta - nunca em volta de uma escrita que parte de uma copia em memoria desatualizada.

    A trava tem DONO (maquina/processo/instante): so quem a criou a remove. Uma trava velha demais
    (processo morto) e quebrada, mas o dono antigo nao apaga por engano a trava nova de outro PC.
    """
    caminho_trava = f"{caminho}.lock"
    dono = f"{os.environ.get('COMPUTERNAME', '?')}|{os.getpid()}|{secrets.token_hex(6)}"
    inicio = time.time()
    os.makedirs(os.path.dirname(os.path.abspath(caminho_trava)), exist_ok=True)

    while True:
        try:
            descritor = os.open(caminho_trava, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(descritor, dono.encode("utf-8"))
            finally:
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
            with open(caminho_trava, "r", encoding="utf-8") as f:
                meu = f.read() == dono
        except OSError:
            meu = False
        if meu:
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


def hash_token(token):
    """Sessoes e codigos de recuperacao ficam no arquivo COMPARTILHADO so como hash: quem le o JSON nao
    consegue usar o token. (O token em si so existe no PC de quem fez login.)"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


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
def caminho_preferencias_locais():
    return os.path.join(os.path.dirname(caminho_sessao_local()), "preferencias_locais.json")


def ler_preferencias_locais():
    try:
        with open(caminho_preferencias_locais(), "r", encoding="utf-8") as f:
            dados = json.load(f)
            return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def salvar_preferencia_local(chave, valor):
    """Preferencias deste PC (ex.: tema), lembradas ate antes do login."""
    try:
        dados = ler_preferencias_locais()
        dados[chave] = valor
        with open(caminho_preferencias_locais(), "w", encoding="utf-8") as f:
            json.dump(dados, f)
    except Exception as erro:
        log.warning("Nao foi possivel salvar a preferencia local: %s", erro)


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


def enviar_email(destinatario, assunto, corpo_html):
    """Envia um e-mail HTML por SMTP. Retorna (sucesso, mensagem)."""
    remetente = None
    try:
        config = carregar_config().get("smtp", {})
        remetente = config.get("email")
        senha_app = (config.get("senha_app") or "").replace(" ", "")
        if not remetente or not senha_app:
            return False, "Credenciais SMTP nao configuradas em configuracao.json"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = remetente
        msg["To"] = destinatario
        msg.attach(MIMEText(corpo_html, "html"))

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
            "e atualize 'smtp.senha_app' no configuracao.json (pasta HGhub-dados)."
        )
    except Exception as erro:
        return False, f"Erro ao enviar e-mail: {erro}"


def enviar_email_recuperacao(destinatario, codigo):
    """Envia o codigo de recuperacao por SMTP. Retorna (sucesso, mensagem)."""
    return enviar_email(destinatario, "Recuperacao de Senha - REDE HG", _template_email_recuperacao(codigo))


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
