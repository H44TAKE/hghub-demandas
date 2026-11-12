# -*- coding: utf-8 -*-
"""Modelo de dominio e persistencia do Sistema de Demandas.

Hierarquia: Pasta -> Projeto -> Tarefa. Tudo e serializado em JSON; o formato
gravado em disco e compativel com o das versoes anteriores do sistema (campos
novos sempre vem com `.get(..., padrao)`), entao bases existentes continuam
abrindo sem migracao manual.

Concorrencia: cada PC roda seu proprio processo Flask local, todos apontando
para o mesmo arquivo de dados na rede. Por isso o estado NUNCA e tratado como
"carregado uma vez, mutado em memoria" - toda escrita passa por `transacao()`,
que trava o arquivo, recarrega o que ha de mais recente no disco, deixa o
chamador aplicar a mudanca sobre esse estado fresco, e so entao grava e libera
a trava. Ver `SistemaGestaoHG.transacao` e `backend/utils.bloqueio_arquivo`.
"""

import contextlib
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import uuid
from collections import Counter
from datetime import datetime

from .utils import (
    ARQUIVO_DADOS,
    ARQUIVO_PERMISSOES,
    ARQUIVO_USUARIOS,
    PASTA_ANEXOS,
    PASTA_FOTOS,
    bloqueio_arquivo,
    criar_arquivos_automaticamente,
    caminho_arquivo_dados,
    gerar_token,
    hash_senha,
    senha_e_texto_puro,
    verificar_senha,
)

log = logging.getLogger("hghub")

SESSAO_DIAS = 30   # uma sessao sem uso por mais que isso deixa de valer (e e removida)


class BaseIlegivelError(RuntimeError):
    """O arquivo de dados existe mas nao pode ser lido: NADA pode ser gravado por cima dele."""

FORMATO_DATA_HORA = "%Y-%m-%d %H:%M:%S"
FORMATO_DATA = "%Y-%m-%d"

PRIORIDADES = ("Alta", "Média", "Baixa")
CORES_PRIORIDADE = {"Alta": "#DC2626", "Média": "#EA580C", "Baixa": "#94A3B8"}

SETOR_PADRAO = "Geral"
INTERVALO_MINIMO_ENTRE_GRAVACOES_AUTOMATICAS = 60  # segundos


def _para_data_hora(valor):
    return datetime.strptime(valor, FORMATO_DATA_HORA) if valor else None


def _para_data(valor):
    return datetime.strptime(valor, FORMATO_DATA) if valor else None


def _texto_data_hora(valor):
    return valor.strftime(FORMATO_DATA_HORA) if valor else None


def _texto_data(valor):
    return valor.strftime(FORMATO_DATA) if valor else None


# ==============================================================
# OBJETOS DE VALOR
# ==============================================================
class Etiqueta:
    def __init__(self, nome="", cor="#94A3B8"):
        self.nome = nome
        self.cor = cor

    def para_dict(self):
        return {"nome": self.nome, "cor": self.cor}

    @staticmethod
    def de_dict(dados):
        dados = dados or {}
        return Etiqueta(dados.get("nome", ""), dados.get("cor", "#94A3B8"))


class Status:
    def __init__(self, nome, cor):
        self.nome = nome
        self.cor = cor

    def para_dict(self):
        return {"nome": self.nome, "cor": self.cor}

    @staticmethod
    def de_dict(dados):
        return Status(dados["nome"], dados["cor"])


# ==============================================================
# USUARIO
# ==============================================================
class Usuario:
    def __init__(self, id, nome, email, senha, nome_exibicao=None, foto_perfil=None,
                 codigo_recuperacao=None, expiracao_codigo=None, notificacoes=None, favoritos=None,
                 setores=None, conta_mestre=False):
        self.id = id
        self.nome = nome
        self.email = email
        self.senha = senha
        self.nome_exibicao = nome_exibicao
        self.foto_perfil = foto_perfil
        self.codigo_recuperacao = codigo_recuperacao
        self.expiracao_codigo = expiracao_codigo
        self.notificacoes = notificacoes or []
        self.favoritos = favoritos or []
        # Uma conta comum pode ter acesso a MAIS de um setor (ex.: um supervisor
        # que acompanha Frota e Manutenção) - por isso lista, nao um campo unico.
        self.setores = setores or []
        self.conta_mestre = conta_mestre
        # False so quando o registro veio do disco SEM a chave `conta_mestre`: sinal de
        # que quem gravou por ultimo nao conhece essa funcionalidade (versao antiga do
        # programa) - ver `SistemaGestaoHG._aplicar_permissoes`. Nao e gravado.
        self.permissao_explicita = True

    @property
    def nome_display(self):
        return self.nome_exibicao or self.nome

    def para_dict(self):
        """Forma completa, usada apenas na gravacao em disco."""
        return {
            "id": self.id,
            "nome": self.nome,
            "email": self.email,
            "senha": self.senha,
            "nome_exibicao": self.nome_exibicao,
            "foto_perfil": self.foto_perfil,
            "codigo_recuperacao": self.codigo_recuperacao,
            "expiracao_codigo": self.expiracao_codigo,
            "notificacoes": self.notificacoes,
            "favoritos": self.favoritos,
            "setores": self.setores,
            "conta_mestre": self.conta_mestre,
        }

    def para_json(self):
        """Forma enviada ao navegador: sem hash de senha nem codigo de recuperacao."""
        return {
            "id": self.id,
            "nome": self.nome,
            "email": self.email,
            "nome_exibicao": self.nome_exibicao,
            "nome_display": self.nome_display,
            "foto_perfil": self.foto_perfil,
            "notificacoes": self.notificacoes,
            "favoritos": self.favoritos,
            "setores": self.setores,
            "conta_mestre": self.conta_mestre,
        }

    @staticmethod
    def de_dict(dados):
        setores = dados.get("setores")
        if setores is None:
            # Registro do formato anterior (um setor unico como string) ou sem
            # nenhum setor gravado ainda - os dois viram uma lista, nunca quebram.
            legado = dados.get("setor")
            setores = [legado] if legado else []

        usuario = Usuario(
            dados["id"],
            dados["nome"],
            dados["email"],
            dados["senha"],
            dados.get("nome_exibicao"),
            dados.get("foto_perfil"),
            dados.get("codigo_recuperacao"),
            dados.get("expiracao_codigo"),
            dados.get("notificacoes", []),
            dados.get("favoritos", []),
            setores,
            # Registro sem a chave "conta_mestre" e de antes desta funcionalidade
            # existir - nessa epoca TODO usuario enxergava tudo, entao o padrao
            # seguro (sem quebrar ninguem que ja tinha acesso) e True. A partir
            # da primeira gravacao esse registro passa a ter a chave explicita,
            # e cadastros novos sempre nascem com False (ver rota /api/cadastro).
            dados.get("conta_mestre", True),
        )
        usuario.permissao_explicita = "conta_mestre" in dados
        return usuario


# ==============================================================
# TAREFA
# ==============================================================
class Tarefa:
    def __init__(self, id, titulo, descricao, status, responsavel=None, data_limite=None, etiqueta=None,
                 cidade=None, data_previsao=None, data_vencimento=None, data_conclusao=None,
                 prioridade="Média", anexos=None, historico=None, comentarios=None, dependencias=None,
                 data_inicio_real=None, data_fim_real=None, veiculo_placa=None):
        self.id = id
        self.titulo = titulo
        self.descricao = descricao
        self.status = status
        self.responsavel = responsavel
        self.data_limite = data_limite
        self.etiqueta = etiqueta or Etiqueta()
        self.cidade = cidade
        self.data_previsao = data_previsao
        self.data_vencimento = data_vencimento or data_limite
        self.data_conclusao = data_conclusao
        self.concluida = status.nome == "Concluída"
        self.prioridade = prioridade or "Média"
        self.anexos = anexos or []
        self.historico = historico or []
        self.comentarios = comentarios or []
        self.dependencias = dependencias or []
        # Datas reais de execucao: quando o trabalho de fato comecou/terminou,
        # em contraste com data_previsao/data_vencimento (o planejado). Ver
        # SistemaGestaoHG._aplicar_automacoes_de_data.
        self.data_inicio_real = data_inicio_real
        self.data_fim_real = data_fim_real
        # Só preenchido quando a tarefa nasce da tela "Agendar Demanda" (Frota
        # & Manutenção) - liga essa demanda a um veículo por Placa, para
        # aparecer no Prontuário dele. Uma tarefa comum (criada em
        # Estrutura/Demandas) nunca tem isso preenchido. Ver
        # SistemaGestaoHG.tarefas_do_veiculo.
        self.veiculo_placa = (veiculo_placa or "").strip().upper() or None

        if self.concluida:
            if not self.data_conclusao:
                self.data_conclusao = datetime.now()
        else:
            self.data_conclusao = None

    def adicionar_historico(self, acao, usuario_nome):
        self.historico.append({
            "acao": acao,
            "usuario_nome": usuario_nome,
            "data": datetime.now().strftime(FORMATO_DATA_HORA),
        })

    def aplicar_status(self, status, usuario_nome=None, data_conclusao_manual=None):
        """Troca o status e mantem a data de conclusao coerente com ele.

        `data_conclusao_manual` permite que quem edita a tarefa corrija a data
        de conclusao (ex.: lancar retroativamente uma tarefa concluida ha dias).
        """
        self.status = status
        self.concluida = status.nome == "Concluída"
        if self.concluida:
            if data_conclusao_manual:
                self.data_conclusao = data_conclusao_manual
            elif not self.data_conclusao:
                self.data_conclusao = datetime.now()
                if usuario_nome:
                    self.adicionar_historico("Concluiu a tarefa", usuario_nome)
        else:
            self.data_conclusao = None

    @property
    def prioridade_cor(self):
        return CORES_PRIORIDADE.get(self.prioridade, "#94A3B8")

    @property
    def responsavel_nome(self):
        if not self.responsavel:
            return "Não atribuído"
        return self.responsavel.nome_display

    @property
    def responsavel_foto(self):
        return self.responsavel.foto_perfil if self.responsavel else None

    @property
    def responsavel_email(self):
        return self.responsavel.email if self.responsavel else None

    @property
    def atrasada(self):
        # O prazo é uma data, não um instante: a tarefa só atrasa depois que o
        # dia do vencimento termina. Sem isso, tudo que vence hoje já nasce vermelho.
        data_ref = self.data_vencimento or self.data_limite
        if self.concluida or not data_ref:
            return False
        fim_do_prazo = data_ref.replace(hour=23, minute=59, second=59, microsecond=999999)
        return datetime.now() > fim_do_prazo

    def para_dict(self):
        return {
            "id": self.id,
            "titulo": self.titulo,
            "descricao": self.descricao,
            "status": self.status.para_dict(),
            "responsavel_id": self.responsavel.id if self.responsavel else None,
            "responsavel_nome": self.responsavel_nome,
            "responsavel_foto": self.responsavel_foto,
            "responsavel_email": self.responsavel_email,
            "data_limite": _texto_data_hora(self.data_limite),
            "cidade": self.cidade,
            "data_previsao": _texto_data(self.data_previsao),
            "data_vencimento": _texto_data(self.data_vencimento),
            "data_conclusao": _texto_data_hora(self.data_conclusao),
            "data_inicio_real": _texto_data(self.data_inicio_real),
            "data_fim_real": _texto_data(self.data_fim_real),
            "etiqueta": self.etiqueta.para_dict(),
            "prioridade": self.prioridade,
            "prioridade_cor": self.prioridade_cor,
            "anexos": self.anexos,
            "historico": self.historico,
            "comentarios": self.comentarios,
            "dependencias": self.dependencias,
            "concluida": self.concluida,
            "atrasada": self.atrasada,
            "veiculo_placa": self.veiculo_placa,
        }

    @staticmethod
    def de_dict(dados, sistema):
        status = Status.de_dict(dados["status"])
        responsavel = sistema.buscar_usuario(dados.get("responsavel_id"))
        return Tarefa(
            dados["id"],
            dados["titulo"],
            dados["descricao"],
            status,
            responsavel,
            _para_data_hora(dados.get("data_limite")),
            Etiqueta.de_dict(dados.get("etiqueta")),
            dados.get("cidade"),
            _para_data(dados.get("data_previsao")),
            _para_data(dados.get("data_vencimento")),
            _para_data_hora(dados.get("data_conclusao")),
            dados.get("prioridade", "Média"),
            dados.get("anexos", []),
            dados.get("historico", []),
            dados.get("comentarios", []),
            dados.get("dependencias", []),
            _para_data(dados.get("data_inicio_real")),
            _para_data(dados.get("data_fim_real")),
            dados.get("veiculo_placa"),
        )


# ==============================================================
# PROJETO E PASTA
# ==============================================================
class Projeto:
    def __init__(self, id, nome):
        self.id = id
        self.nome = nome
        self.tarefas = []

    def progresso(self):
        if not self.tarefas:
            return 0
        concluidas = sum(1 for t in self.tarefas if t.concluida)
        return round((concluidas / len(self.tarefas)) * 100)

    def para_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "progresso": self.progresso(),
            "tarefas": [t.para_dict() for t in self.tarefas],
        }

    @staticmethod
    def de_dict(dados, sistema):
        projeto = Projeto(dados["id"], dados["nome"])
        projeto.tarefas = [Tarefa.de_dict(t, sistema) for t in dados.get("tarefas", [])]
        return projeto


class Pasta:
    def __init__(self, id, nome, cor_fundo="#0D3B8C", setor=None):
        self.id = id
        self.nome = nome
        self.cor_fundo = cor_fundo
        self.projetos = []
        # None/"" = pasta "sem setor", visivel a todo mundo (legado, ou uma area
        # deliberadamente compartilhada). Ver SistemaGestaoHG.usuario_pode_acessar_pasta.
        self.setor = setor or None

    def para_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "cor_fundo": self.cor_fundo,
            "setor": self.setor,
            "projetos": [p.para_dict() for p in self.projetos],
        }

    @staticmethod
    def de_dict(dados, sistema):
        pasta = Pasta(dados["id"], dados["nome"], dados.get("cor_fundo", "#0D3B8C"), dados.get("setor"))
        pasta.projetos = [Projeto.de_dict(p, sistema) for p in dados.get("projetos", [])]
        return pasta


# ==============================================================
# SISTEMA
# ==============================================================
class SistemaGestaoHG:
    _base_ilegivel = False
    """Estado completo da aplicacao. Recarregado do disco a cada requisicao da
    API (ver `backend.routes` - `before_request`), e sob trava de arquivo em
    toda escrita (ver `transacao`), porque varios processos (um por PC) leem e
    gravam o mesmo arquivo compartilhado."""

    def __init__(self):
        self.pastas = []
        self.usuarios = []
        self.anotacoes = {}
        self.sessoes = {}
        self.cidades = []
        self.setores = []
        self.etiquetas_salvas = []
        # Ultimo conteudo BOM lido do arquivo de permissoes: sustenta uma leitura que falhe
        # por um instante (rede) sem soltar ninguem para "Conta Mestre" por engano.
        self._permissoes_cache = None
        # E-mail de quem esta mudando permissoes na transacao atual (so para auditoria).
        self._autor_mudanca = None
        self._aviso_permissoes_em = 0.0
        self.status_padrao = [
            Status("Pendente", "#F5B800"),
            Status("Em Andamento", "#2563EB"),
            Status("Concluída", "#059669"),
            Status("Cancelada", "#DC2626"),
        ]
        self._lock = threading.RLock()
        self._ultima_gravacao_automatica = 0.0
        self._criar_pastas_de_arquivos()
        if self._carregar_e_atualizar():
            self.salvar_dados()
        # Primeira execucao desta versao: cria permissoes_contas.json a partir do que as
        # contas tem hoje (nao toca em nenhum arquivo que ja exista).
        self._salvar_permissoes_json()

    # ---------- infraestrutura ----------
    def _criar_pastas_de_arquivos(self):
        for pasta in (PASTA_FOTOS, PASTA_ANEXOS):
            try:
                os.makedirs(pasta, exist_ok=True)
            except Exception as erro:
                log.warning("Nao foi possivel criar a pasta %s: %s", pasta, erro)

    def obter_pasta_fotos(self):
        return PASTA_FOTOS

    def obter_pasta_anexos(self):
        return PASTA_ANEXOS

    def gerar_id(self):
        return str(uuid.uuid4())[:8]

    # ---------- persistencia ----------
    def _carregar_dados(self):
        """So le do disco para dentro dos atributos em memoria - nunca grava
        nada aqui (isso e o que permite chamar isto a vontade em toda leitura,
        sem transformar todo GET num respingo de escritas).

        Excecao deliberada: se o arquivo AINDA nao existe (primeiiro uso desta
        base), grava o admin inicial imediatamente. Sem isso, cada chamada
        recriaria um admin com um `gerar_id()` (uuid) diferente do anterior -
        e como esta funcao roda de novo dentro do MESMO request (uma vez no
        `before_request`, outra dentro do `transacao()` do proprio /api/login),
        a sessao seria criada para um id que o `transacao()` imediatamente
        substituiria por outro, quebrando o login no primeiro uso do sistema.
        """
        if not os.path.exists(ARQUIVO_DADOS):
            self.usuarios = [Usuario(self.gerar_id(), "Admin", "admin@redehg.com", hash_senha(self._senha_inicial_admin()),
                                      conta_mestre=True)]
            self.pastas = []
            self.anotacoes = {}
            self.sessoes = {}
            self.cidades = []
            self.setores = [SETOR_PADRAO]
            self.etiquetas_salvas = []
            self.salvar_dados()
            return

        try:
            with open(ARQUIVO_DADOS, "r", encoding="utf-8-sig") as f:
                dados = json.load(f)

            # usuarios.json e a fonte preferida; o array dentro do arquivo de dados
            # e o fallback para bases antigas.
            usuarios_json = self._carregar_usuarios_json()
            if usuarios_json:
                self.usuarios = usuarios_json
            else:
                self.usuarios = [Usuario.de_dict(u) for u in dados.get("usuarios", [])]

            self.pastas = [Pasta.de_dict(p, self) for p in dados.get("pastas", [])]
            self.anotacoes = dados.get("anotacoes", {})
            self.sessoes = dados.get("sessoes", {})
            self.cidades = dados.get("cidades", [])
            self.setores = dados.get("setores") or [SETOR_PADRAO]
            self.etiquetas_salvas = dados.get("etiquetas_salvas", [])
            self._base_ilegivel = False
        except Exception as erro:
            # O arquivo EXISTE mas nao foi lido (truncado, rede instavel, registro invalido...). Seguir
            # com o estado vazio e gravar na proxima escrita APAGARIA a base inteira: bloqueia as gravacoes.
            self._base_ilegivel = True
            log.error("Erro ao carregar dados de %s: %s. Gravacoes bloqueadas para proteger a base.", ARQUIVO_DADOS, erro)

        if not self.usuarios and not self._base_ilegivel:
            self.usuarios.append(Usuario(self.gerar_id(), "Admin", "admin@redehg.com", hash_senha(self._senha_inicial_admin()),
                                          conta_mestre=True))

        self._aplicar_permissoes()

    def _senha_inicial_admin(self):
        """Senha da conta Admin criada numa base nova: ALEATORIA (ou HGHUB_ADMIN_SENHA). Nunca fixa no codigo.
        A aleatoria fica em SENHA_INICIAL_ADMIN.txt na pasta de dados, para quem instalou ler e apagar."""
        definida = os.environ.get("HGHUB_ADMIN_SENHA")
        if definida:
            return definida
        senha = secrets.token_urlsafe(9)
        try:
            with open(caminho_arquivo_dados("SENHA_INICIAL_ADMIN.txt"), "w", encoding="utf-8") as f:
                f.write("Conta inicial do sistema\nE-mail: admin@redehg.com\nSenha: " + senha +
                        "\n\nApos o primeiro acesso, APAGUE este arquivo.\n")
            log.warning("Conta admin criada numa base nova; a senha inicial esta em SENHA_INICIAL_ADMIN.txt (pasta de dados).")
        except OSError:
            log.error("Conta admin criada, mas nao foi possivel gravar a senha inicial; defina HGHUB_ADMIN_SENHA.")
        return senha

    def _aplicar_automacoes_de_data(self):
        """Regra 4 do negocio: o status segue a data real, nao so o clique do usuario.

        - `data_inicio_real` alcançada numa tarefa Pendente -> vira "Em Andamento".
        - `data_fim_real` alcançada -> vira "Concluída" (com a propria data_fim_real
          como data de conclusao, ja que ela representa quando o trabalho de fato
          terminou). Isso tem prioridade sobre a regra de inicio.
        Retorna True se alguma tarefa mudou, para o chamador decidir se vale
        persistir a mudanca em disco agora.
        """
        hoje = datetime.now()
        mudou = False
        status_andamento = self.buscar_status("Em Andamento")
        status_concluida = self.buscar_status("Concluída")

        for tarefa in self.todas_tarefas():
            if tarefa.concluida or tarefa.status.nome == "Cancelada":
                continue
            if tarefa.data_fim_real and hoje >= tarefa.data_fim_real:
                tarefa.status = status_concluida
                tarefa.concluida = True
                tarefa.data_conclusao = tarefa.data_fim_real
                mudou = True
            elif (tarefa.status.nome == "Pendente" and tarefa.data_inicio_real
                    and hoje >= tarefa.data_inicio_real):
                tarefa.status = status_andamento
                mudou = True

        return mudou

    def _sincronizar_setores_com_pastas(self):
        """Regra 2 do negocio, versao inteligente: as pastas que a empresa ja
        tem SAO os setores - ninguem precisa recadastrar nada.

        Toda pasta sem setor definido assume o proprio nome como setor (ex.:
        a pasta "Frota" vira o Setor "Frota"), e esse nome entra na lista de
        setores conhecidos. Uma pasta que ja tem um setor explicito (inclusive
        atribuido manualmente por uma Conta Mestre depois) nunca e tocada aqui.
        Roda em todo carregamento, junto com a automacao de datas.
        """
        mudou = False
        for pasta in self.pastas:
            if not pasta.setor:
                pasta.setor = pasta.nome
                mudou = True
            if self.registrar_setor(pasta.setor):
                mudou = True
        return mudou

    def _carregar_e_atualizar(self):
        """Recarrega do disco e aplica automacao de datas + setores em memoria."""
        self._carregar_dados()
        mudou_datas = self._aplicar_automacoes_de_data()
        mudou_setores = self._sincronizar_setores_com_pastas()
        return mudou_datas or mudou_setores

    def recarregar(self):
        """Caminho de LEITURA (sem trava DE ARQUIVO): sempre reflete o disco
        mais recente de qualquer outro processo, mais a automacao de datas ja
        computada.

        `self._lock` (em memoria, threading.RLock - nunca espera rede) protege
        so a recarga+atribuicao contra outra THREAD do mesmo processo dentro
        de `transacao()`: sem isso, `_carregar_e_atualizar()` podia reatribuir
        `self.sessoes`/`self.usuarios`/etc. NO MEIO de um login/logout/edicao
        em outra thread (Flask roda com `threaded=True`), fazendo a sessao ou
        a mudanca recem-criada "desaparecer" silenciosamente - foi exatamente
        o bug relatado de uma Conta Mestre levando 403 do nada. Continua
        deliberadamente SEM `bloqueio_arquivo` (a trava de arquivo, entre
        processos/PCs, que pode ser lenta numa rede) - so a`transacao()` paga
        esse custo, porque so ela de fato precisa de exclusao entre PCs.

        Se a automacao mudou algo, tenta persistir - mas com timeout curto e
        falha silenciosa (so um aviso no log), porque isto roda a cada
        requisicao da API e uma rede lenta/ocupada nao pode travar uma leitura.
        A proxima escrita real (via `transacao`) persiste de qualquer forma.
        """
        with self._lock:
            mudou = self._carregar_e_atualizar()
        if not mudou:
            return

        agora = time.time()
        if agora - self._ultima_gravacao_automatica < INTERVALO_MINIMO_ENTRE_GRAVACOES_AUTOMATICAS:
            return
        self._ultima_gravacao_automatica = agora
        try:
            with bloqueio_arquivo(ARQUIVO_DADOS, tempo_limite=2):
                self.salvar_dados()
        except Exception as erro:
            log.warning("Automação de datas calculada, mas não foi possível persistir agora: %s", erro)

    @contextlib.contextmanager
    def transacao(self):
        """Todo caminho de ESCRITA passa por aqui: trava -> recarrega o estado
        mais recente do disco -> o chamador aplica a mudança -> grava -> destrava.

        Isso fecha o "lost update" ENTRE PROCESSOS/PCs: sem recarregar sob a
        trava de arquivo (`bloqueio_arquivo`), duas maquinas salvando ao mesmo
        tempo partiriam cada uma de uma copia em memória desatualizada, e o
        segundo save apagaria silenciosamente o primeiro.

        `self._lock` (em memoria) fecha a MESMA janela DENTRO do processo:
        sem ela, uma `recarregar()` de outra thread (chamada antes de toda
        requisicao da API, via `before_request`) podia reatribuir
        `self.sessoes`/`self.usuarios`/etc. bem no meio desta transacao,
        fazendo a mudança que o chamador acabou de aplicar (ex.: uma sessão de
        login recém-criada) sumir antes mesmo de `salvar_dados()` rodar - ver
        `recarregar()` para o relato completo do bug. É um `RLock`, então
        chamadas reentrantes (`salvar_dados()` também o adquire) não travam.
        """
        with bloqueio_arquivo(ARQUIVO_DADOS):
            with self._lock:
                self._carregar_e_atualizar()
                if self._base_ilegivel:
                    raise BaseIlegivelError("A base de dados nao pode ser lida; nada foi alterado.")
                yield self
                self.salvar_dados()

    # ---------- permissoes das contas (permissoes_contas.json) ----------
    def _ler_permissoes(self):
        """Le o arquivo de permissoes. Devolve (estado, contas):

        - ("ausente", None)     o arquivo ainda nao existe (primeira execucao desta versao);
        - ("ok", {email: ...})  lido (e guardado em `_permissoes_cache`);
        - ("ilegivel", cache)   erro de leitura/JSON: o ultimo conteudo bom em memoria, ou None.
        """
        try:
            if not os.path.exists(ARQUIVO_PERMISSOES):
                return "ausente", None
            with open(ARQUIVO_PERMISSOES, "r", encoding="utf-8") as f:
                dados = json.load(f)
            contas = dados.get("contas")
            if not isinstance(contas, dict):
                raise ValueError("a chave 'contas' esta ausente ou invalida")
            self._permissoes_cache = contas
            return "ok", contas
        except Exception as erro:
            agora = time.time()
            if agora - self._aviso_permissoes_em > 60:  # roda a cada requisicao: nao inunda o log
                self._aviso_permissoes_em = agora
                log.error("Nao foi possivel ler permissoes_contas.json (%s). Mantendo o ultimo estado conhecido.", erro)
            return "ilegivel", self._permissoes_cache

    def _aplicar_permissoes(self):
        """Faz o arquivo de permissoes valer sobre `conta_mestre`/`setores` de cada conta.

        Por que existe: usuarios.json e o arquivo de dados sao regravados por TODOS os
        PCs, inclusive por versoes antigas do programa que nao conhecem `conta_mestre`
        nem `setores` e os apagam ao salvar - e a leitura seguinte tratava a chave
        ausente como "Conta Mestre" (padrao de migracao, ver `Usuario.de_dict`). Resultado:
        todo mundo virava mestre, sem setor. O arquivo de permissoes e separado, nenhuma
        versao antiga o toca, e passa a ser a fonte da verdade:

        1. conta com entrada no arquivo -> vale o que o arquivo diz;
        2. sem entrada, mas o registro tem `conta_mestre` explicito -> foi gravada por uma
           versao que conhece permissoes: vale o proprio registro (a entrada nasce no
           proximo salvamento);
        3. sem entrada e registro sem a chave -> criada/regravada por versao antiga: sem
           permissao nenhuma (conta comum, sem setor) ate uma Conta Mestre liberar -
           falha FECHADA, nunca "mestre por omissao".

        Na primeira execucao (arquivo ainda inexistente) nada muda: o registro manda e o
        arquivo e criado a partir dele, entao ninguem perde o acesso que ja tinha.
        """
        estado, contas = self._ler_permissoes()
        if estado == "ilegivel" and contas is None:
            return
        if estado == "ausente":
            if self._permissoes_cache is None:
                return
            contas = self._permissoes_cache
        por_id = {c.get("id"): c for c in contas.values() if c.get("id")}
        for usuario in self.usuarios:
            entrada = contas.get((usuario.email or "").strip().lower()) or por_id.get(usuario.id)
            if entrada is not None:
                usuario.conta_mestre = bool(entrada.get("conta_mestre"))
                usuario.setores = [s.strip() for s in (entrada.get("setores") or [])
                                   if isinstance(s, str) and s.strip()]
            elif not usuario.permissao_explicita:
                usuario.conta_mestre = False
                usuario.setores = []

    def _salvar_permissoes_json(self):
        """Mantem permissoes_contas.json em dia com as contas. So acrescenta/atualiza:
        entradas de contas que nao estao em memoria nunca sao apagadas, e um arquivo
        existente que nao consiga ser lido NUNCA e sobrescrito (pior do que ficar sem
        gravar). So grava quando algo mudou.

        Roda dentro de `salvar_dados()` (portanto sob a trava de arquivo dos dados e a
        trava em memoria); so a criacao inicial, feita no construtor, usa trava propria."""
        try:
            existentes = None
            if os.path.exists(ARQUIVO_PERMISSOES):
                with open(ARQUIVO_PERMISSOES, "r", encoding="utf-8") as f:
                    existentes = json.load(f).get("contas")
                if not isinstance(existentes, dict):
                    raise ValueError("a chave 'contas' esta ausente ou invalida")
            elif self._permissoes_cache is not None:
                log.warning("permissoes_contas.json sumiu da pasta de dados; nao vou recria-lo sozinho "
                            "(pode ser uma falha momentanea da rede).")
                return
        except Exception as erro:
            log.error("permissoes_contas.json existe mas nao pode ser lido (%s): NAO sera sobrescrito.", erro)
            return

        if existentes is not None:
            self._gravar_permissoes(existentes)
            return
        # Criacao inicial: trava propria e rechecagem, para dois PCs iniciando juntos
        # nao semearem em cima um do outro.
        try:
            with bloqueio_arquivo(ARQUIVO_PERMISSOES, tempo_limite=5):
                if not os.path.exists(ARQUIVO_PERMISSOES):
                    self._gravar_permissoes({}, criando=True)
        except Exception as erro:
            log.error("Nao foi possivel criar permissoes_contas.json: %s", erro)

    def _gravar_permissoes(self, existentes, criando=False):
        agora = datetime.now().strftime(FORMATO_DATA_HORA)
        por_id = {c.get("id"): chave for chave, c in existentes.items() if c.get("id")}
        contas = {chave: dict(valor) for chave, valor in existentes.items()}
        mudou = criando
        for usuario in self.usuarios:
            chave = (usuario.email or "").strip().lower()
            if not chave:
                continue
            anterior = contas.get(chave)
            if anterior is None and usuario.id in por_id:
                anterior = contas.pop(por_id[usuario.id], None)
            atuais = {"conta_mestre": bool(usuario.conta_mestre), "setores": list(usuario.setores or [])}
            entrada = {"id": usuario.id, "nome": usuario.nome_display, "email": usuario.email, **atuais}
            if anterior is None or any(anterior.get(k) != v for k, v in atuais.items()):
                entrada["atualizado_em"] = agora
                entrada["atualizado_por"] = self._autor_mudanca or "sistema"
            else:
                entrada["atualizado_em"] = anterior.get("atualizado_em")
                entrada["atualizado_por"] = anterior.get("atualizado_por")
            if entrada != contas.get(chave):
                mudou = True
            contas[chave] = entrada
        if not mudou:
            return
        self._escrever_json(ARQUIVO_PERMISSOES, {
            "_leia_me": (
                "Quem e Conta Mestre e de quais setores cada conta e. Este arquivo vale para todos os "
                "computadores e e a fonte da verdade dessas permissoes (mudar 'conta_mestre' ou 'setores' "
                "no usuarios.json NAO tem efeito). Gerenciado pelo sistema (menu Gerenciar usuarios); se "
                "editar a mao, mantenha o JSON valido e nao apague contas."
            ),
            "atualizado_em": agora,
            "contas": contas,
        })
        self._permissoes_cache = contas
        self._autor_mudanca = None

    def _carregar_usuarios_json(self):
        try:
            if os.path.exists(ARQUIVO_USUARIOS):
                with open(ARQUIVO_USUARIOS, "r", encoding="utf-8") as f:
                    dados = json.load(f)
                lista = dados.get("usuarios", [])
                if lista:
                    return [Usuario.de_dict(u) for u in lista]
        except Exception as erro:
            log.error("Erro ao carregar usuarios.json: %s", erro)
        return None

    def _escrever_json(self, caminho, dados):
        """Grava de forma atomica: um crash no meio da escrita nunca trunca o arquivo.

        Importante porque o arquivo de producao pode estar em uma unidade de rede.
        """
        destino = os.path.dirname(os.path.abspath(caminho)) or "."
        os.makedirs(destino, exist_ok=True)
        descritor, temporario = tempfile.mkstemp(dir=destino, suffix=".tmp")
        try:
            with os.fdopen(descritor, "w", encoding="utf-8") as f:
                json.dump(dados, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            for tentativa in range(4):          # rede/antivirus seguram o arquivo por instantes (WinError 5)
                try:
                    os.replace(temporario, caminho)
                    break
                except PermissionError:
                    if tentativa == 3:
                        raise
                    time.sleep(0.25 * (tentativa + 1))
        except Exception:
            if os.path.exists(temporario):
                try:
                    os.remove(temporario)
                except OSError:
                    pass
            raise

    def _salvar_usuarios_json(self):
        try:
            criar_arquivos_automaticamente()
            self._escrever_json(ARQUIVO_USUARIOS, {"usuarios": [u.para_dict() for u in self.usuarios]})
        except Exception as erro:
            log.error("Erro ao salvar usuarios.json: %s", erro)

    def salvar_dados(self):
        with self._lock:
            if self._base_ilegivel:
                raise BaseIlegivelError("A base de dados nao pode ser lida; nada foi gravado para nao perde-la.")
            self._salvar_usuarios_json()
            self._salvar_permissoes_json()
            dados = {
                "usuarios": [u.para_dict() for u in self.usuarios],
                "pastas": [p.para_dict() for p in self.pastas],
                "anotacoes": self.anotacoes,
                "sessoes": self.sessoes,
                "cidades": self.cidades,
                "setores": self.setores,
                "etiquetas_salvas": self.etiquetas_salvas,
            }
            try:
                self._escrever_json(ARQUIVO_DADOS, dados)
            except Exception as erro:
                # NAO engolir: se a gravacao falhar o chamador precisa saber (antes a API respondia
                # "sucesso" e a alteracao se perdia em silencio).
                log.error("Erro ao salvar dados em %s: %s", ARQUIVO_DADOS, erro)
                raise

    # ---------- consultas ----------
    def buscar_usuario(self, usuario_id):
        if not usuario_id:
            return None
        return next((u for u in self.usuarios if u.id == usuario_id), None)

    def buscar_usuario_por_email(self, email):
        if not email:
            return None
        alvo = email.strip().lower()
        return next((u for u in self.usuarios if u.email.lower() == alvo), None)

    def buscar_pasta(self, pasta_id):
        return next((p for p in self.pastas if p.id == pasta_id), None)

    def buscar_projeto(self, projeto_id):
        for pasta in self.pastas:
            for projeto in pasta.projetos:
                if projeto.id == projeto_id:
                    return projeto
        return None

    def buscar_tarefa(self, tarefa_id):
        for pasta in self.pastas:
            for projeto in pasta.projetos:
                for tarefa in projeto.tarefas:
                    if tarefa.id == tarefa_id:
                        return tarefa
        return None

    def pasta_do_projeto(self, projeto_id):
        return next((p for p in self.pastas if any(pr.id == projeto_id for pr in p.projetos)), None)

    def pasta_da_tarefa(self, tarefa_id):
        for pasta in self.pastas:
            for projeto in pasta.projetos:
                if any(t.id == tarefa_id for t in projeto.tarefas):
                    return pasta
        return None

    def todas_tarefas(self):
        return [t for p in self.pastas for pr in p.projetos for t in pr.tarefas]

    def tarefas_do_veiculo(self, placa):
        """Demandas de manutenção agendadas para uma Placa (`Tarefa.veiculo_placa`)
        - a ponte entre "Agendar Demanda" (Frota & Manutenção) e o Prontuário
        do Veículo, sem misturar dado de planilha (Excel/Drive) com dado do
        sistema de tarefas (JSON) dentro de `backend/manutencao.py`."""
        alvo = (placa or "").strip().upper()
        if not alvo:
            return []
        return [
            {**t.para_dict(), "projeto_nome": pr.nome, "pasta_nome": p.nome}
            for p in self.pastas for pr in p.projetos for t in pr.tarefas
            if t.veiculo_placa == alvo
        ]

    def buscar_status(self, nome, padrao=None):
        return next((s for s in self.status_padrao if s.nome == nome), padrao or self.status_padrao[0])

    def tarefa_bloqueada(self, tarefa, indice=None):
        """Uma tarefa esta bloqueada enquanto qualquer dependencia nao for concluida."""
        if not tarefa.dependencias:
            return False
        if indice is None:
            indice = {t.id: t for t in self.todas_tarefas()}
        for dependencia_id in tarefa.dependencias:
            dependencia = indice.get(dependencia_id)
            if dependencia and not dependencia.concluida:
                return True
        return False

    def registrar_cidade(self, cidade):
        if cidade and cidade not in self.cidades:
            self.cidades.append(cidade)
            return True
        return False

    # ---------- setores e contas mestre ----------
    def registrar_setor(self, nome):
        nome = (nome or "").strip()
        if nome and nome not in self.setores:
            self.setores.append(nome)
            return True
        return False

    def usuario_pode_acessar_pasta(self, usuario, pasta):
        """Regra 2 do negocio: uma conta comum só acessa pastas de algum dos
        setores que ela tem (pode ter mais de um) - ou uma pasta sem setor
        definido (legado/área compartilhada). A Conta Mestre acessa tudo, sempre."""
        if not pasta:
            return True
        if not usuario:
            return not pasta.setor
        if usuario.conta_mestre:
            return True
        return not pasta.setor or pasta.setor in usuario.setores

    def pastas_visiveis(self, usuario):
        return [p for p in self.pastas if self.usuario_pode_acessar_pasta(usuario, p)]

    def _pastas_do_relatorio(self, usuario, setor_solicitado):
        """Escopo de setor para dashboard/exportação: uma conta comum nunca sai
        do próprio setor (mesmo que peça outro na URL); a Conta Mestre pode
        pedir um setor específico ou nada (= toda a empresa)."""
        if usuario and not usuario.conta_mestre:
            setores_usuario = usuario.setores or []
            return [p for p in self.pastas if not p.setor or p.setor in setores_usuario]
        if setor_solicitado:
            return [p for p in self.pastas if p.setor == setor_solicitado]
        return self.pastas

    # ---------- etiquetas pre-salvas ----------
    def salvar_etiqueta(self, nome, cor):
        nome = (nome or "").strip()
        if not nome:
            return None
        existente = next((e for e in self.etiquetas_salvas if e["nome"].lower() == nome.lower()), None)
        if existente:
            existente["cor"] = cor or existente["cor"]
            return existente
        nova = {"id": self.gerar_id(), "nome": nome, "cor": cor or "#94A3B8"}
        self.etiquetas_salvas.append(nova)
        return nova

    def remover_etiqueta(self, etiqueta_id):
        antes = len(self.etiquetas_salvas)
        self.etiquetas_salvas = [e for e in self.etiquetas_salvas if e["id"] != etiqueta_id]
        return len(self.etiquetas_salvas) != antes

    # ---------- autenticacao ----------
    def login(self, email, senha):
        usuario = self.buscar_usuario_por_email(email)
        if not usuario or not senha:
            return None
        if not verificar_senha(senha, usuario.senha):
            return None
        if senha_e_texto_puro(usuario.senha):
            usuario.senha = hash_senha(senha)
            self.salvar_dados()
        return usuario

    def criar_sessao(self, usuario_id):
        token = gerar_token()
        agora = datetime.now()
        # limpa as vencidas para o arquivo compartilhado nao crescer para sempre
        self.sessoes = {t: s for t, s in self.sessoes.items() if not self._sessao_vencida(s, agora)}
        self.sessoes[token] = {"usuario_id": usuario_id, "criada_em": agora.isoformat()}
        self.salvar_dados()
        return token

    @staticmethod
    def _sessao_vencida(sessao, agora=None):
        try:
            criada = datetime.fromisoformat(sessao.get("criada_em"))
        except (TypeError, ValueError):
            return False        # sessao antiga sem data: mantem (nao desloga ninguem de surpresa)
        return ((agora or datetime.now()) - criada).days >= SESSAO_DIAS

    def validar_sessao(self, token):
        if not token:
            return None
        sessao = self.sessoes.get(token)
        if not sessao or self._sessao_vencida(sessao):
            return None
        return self.buscar_usuario(sessao.get("usuario_id"))

    def encerrar_sessao(self, token):
        if token in self.sessoes:
            del self.sessoes[token]
            self.salvar_dados()
            return True
        return False

    # ---------- metricas ----------
    def metricas_dashboard(self, usuario=None, setor=None):
        pastas = self._pastas_do_relatorio(usuario, setor)
        tarefas = [t for p in pastas for pr in p.projetos for t in pr.tarefas]
        indice = {t.id: t for t in tarefas}

        concluidas = sum(1 for t in tarefas if t.concluida)
        atrasadas = sum(1 for t in tarefas if t.atrasada)
        concluidas_no_prazo = sum(1 for t in tarefas if t.concluida and not t.atrasada)

        contagem_responsaveis = Counter(t.responsavel_nome for t in tarefas if t.responsavel)
        contagem_cidades = Counter(t.cidade for t in tarefas if t.cidade)

        grafico_pastas = [{
            "nome": pasta.nome,
            "cor": pasta.cor_fundo,
            "concluidas": sum(1 for pr in pasta.projetos for t in pr.tarefas if t.concluida),
            "pendentes": sum(1 for pr in pasta.projetos for t in pr.tarefas if not t.concluida),
        } for pasta in pastas]

        return {
            "cards": {
                "total": len(tarefas),
                "pendentes": sum(1 for t in tarefas if t.status.nome == "Pendente"),
                "andamento": sum(1 for t in tarefas if t.status.nome == "Em Andamento"),
                "concluidas": concluidas,
                "atrasadas": atrasadas,
                "alta_prioridade": sum(1 for t in tarefas if t.prioridade == "Alta" and not t.concluida),
                "bloqueadas": sum(1 for t in tarefas if not t.concluida and self.tarefa_bloqueada(t, indice)),
                "taxa_cumprimento": round(concluidas_no_prazo / concluidas * 100, 1) if concluidas else 0,
            },
            "grafico": grafico_pastas,
            "grafico_prioridade": {
                "Alta": sum(1 for t in tarefas if t.prioridade == "Alta"),
                "Média": sum(1 for t in tarefas if t.prioridade == "Média"),
                "Baixa": sum(1 for t in tarefas if t.prioridade == "Baixa"),
            },
            "grafico_responsaveis": [{"nome": n, "total": c} for n, c in contagem_responsaveis.most_common(5)],
            "grafico_cidades": [{"nome": n, "total": c} for n, c in contagem_cidades.most_common(5)],
        }

    def estado_completo(self, usuario=None):
        """Snapshot que o frontend consome, com o bloqueio por dependencias
        resolvido e as pastas já filtradas pelo setor de quem está pedindo."""
        indice = {t.id: t for t in self.todas_tarefas()}
        pastas = []
        for pasta in self.pastas_visiveis(usuario):
            dados_pasta = pasta.para_dict()
            for projeto in dados_pasta["projetos"]:
                for tarefa in projeto["tarefas"]:
                    origem = indice.get(tarefa["id"])
                    tarefa["bloqueada"] = self.tarefa_bloqueada(origem, indice) if origem else False
            pastas.append(dados_pasta)

        return {
            "pastas": pastas,
            "usuarios": [u.para_json() for u in self.usuarios],
            "status": [s.para_dict() for s in self.status_padrao],
            "anotacoes": self.anotacoes,
            "cidades": self.cidades,
            "setores": self.setores,
            "etiquetas_salvas": self.etiquetas_salvas,
        }

    def exportar_dados_excel(self, usuario=None, setor=None):
        import pandas as pd

        pastas = self._pastas_do_relatorio(usuario, setor)
        linhas = [{
            "Setor": pasta.setor or "—",
            "Pasta": pasta.nome,
            "Projeto": projeto.nome,
            "Tarefa": tarefa.titulo,
            "Descrição": tarefa.descricao,
            "Status": tarefa.status.nome,
            "Prioridade": tarefa.prioridade,
            "Responsável": tarefa.responsavel_nome,
            "Cidade": tarefa.cidade or "",
            "Previsão": tarefa.data_previsao.strftime("%d/%m/%Y") if tarefa.data_previsao else "",
            "Vencimento": tarefa.data_vencimento.strftime("%d/%m/%Y") if tarefa.data_vencimento else "",
            "Início real": tarefa.data_inicio_real.strftime("%d/%m/%Y") if tarefa.data_inicio_real else "",
            "Fim real": tarefa.data_fim_real.strftime("%d/%m/%Y") if tarefa.data_fim_real else "",
            "Conclusão": tarefa.data_conclusao.strftime("%d/%m/%Y") if tarefa.data_conclusao else "",
            "Etiqueta": tarefa.etiqueta.nome,
            "Concluída": "Sim" if tarefa.concluida else "Não",
        } for pasta in pastas for projeto in pasta.projetos for tarefa in projeto.tarefas]

        nome_arquivo = f"relatorio_demandas_{setor}.xlsx" if (usuario and usuario.conta_mestre and setor) \
            else "relatorio_demandas.xlsx"
        caminho = caminho_arquivo_dados(nome_arquivo)
        pd.DataFrame(linhas).to_excel(caminho, index=False)
        return caminho


sistema = SistemaGestaoHG()
