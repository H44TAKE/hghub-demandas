# -*- coding: utf-8 -*-
"""Camada HTTP: paginas, arquivos servidos e a API JSON consumida pelo frontend.

Contrato de resposta: toda rota da API devolve `{"success": true|false, ...}`.
Erros de validacao respondem com HTTP 4xx e uma chave `message` legivel, que o
cliente exibe diretamente no toast.

Concorrencia: `_atualizar_estado` recarrega o `sistema` do disco antes de
QUALQUER rota da API (inclusive leituras), porque cada PC roda seu proprio
processo apontando para o mesmo arquivo de rede - sem isso, um processo nunca
enxergaria o que outro PC acabou de salvar. Toda rota que muda dado abre
`with sistema.transacao():`, que trava o arquivo, recarrega de novo (fecha a
janela entre o passo acima e agora) e so entao aplica a mudanca. Ver
`backend/models.py` para os detalhes de `recarregar`/`transacao`.

Setores: `usuario_pode_acessar_pasta` decide se quem fez a requisicao pode ver
ou mexer numa pasta (e, por extensao, nos projetos/tarefas dentro dela). Toda
rota que resolve uma pasta a partir de um id de projeto/tarefa faz essa
checagem antes de aplicar a mudanca - ver `backend/models.py` para a regra.
"""

import base64
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request, send_file, send_from_directory

from . import manutencao
from .models import Etiqueta, Pasta, Projeto, Tarefa, Usuario, sistema
from .utils import (
    BASE_DIR,
    caminho_arquivo_dados,
    LimitadorTentativas,
    enviar_email_recuperacao,
    extrair_token,
    gerar_codigo_recuperacao,
    hash_senha,
    ler_sessao_local,
    remover_sessao_local,
    salvar_sessao_local,
)

log = logging.getLogger("hghub")

paginas = Blueprint("paginas", __name__)
arquivos = Blueprint("arquivos", __name__)
api = Blueprint("api", __name__, url_prefix="/api")


@api.before_request
def _atualizar_estado():
    """Sempre parte do que ha de mais recente no arquivo compartilhado.

    Sem isto, um usuario so veria o que outro PC salvou depois de reiniciar o
    proprio app. Barato o suficiente (o arquivo e pequeno) para rodar em toda
    requisicao, inclusive leituras.
    """
    sistema.recarregar()


@api.before_request
def exigir_login_nas_rotas_privadas():
    """Todo o resto da API exige sessao valida (antes, exportar, dados, perfil... respondiam a qualquer um)."""
    if request.method == "OPTIONS":
        return None
    if (request.endpoint or "").rsplit(".", 1)[-1] in ROTAS_PUBLICAS:
        return None
    if not usuario_da_requisicao():
        return erro("Faça login para continuar.", 401)
    return None


def erro(mensagem, codigo=400, **extra):
    return jsonify({"success": False, "message": mensagem, **extra}), codigo


def corpo():
    return request.get_json(silent=True) or {}


# Unicas rotas da API que funcionam SEM sessao (login, cadastro, recuperacao e a lista de setores do formulario).
ROTAS_PUBLICAS = frozenset({
    "api_login", "api_cadastro", "api_sessao_validar", "api_sessao_encerrar", "api_sessao_arquivo",
    "api_recuperacao_solicitar", "api_recuperacao_validar", "api_recuperacao_redefinir", "api_setores",
})
limite_login = LimitadorTentativas(5, 15 * 60)
limite_codigo = LimitadorTentativas(5, 15 * 60)
limite_pedidos_codigo = LimitadorTentativas(3, 15 * 60)


def _espere(segundos):
    minutos = max(1, (segundos + 59) // 60)
    return erro(f"Muitas tentativas. Tente de novo em {minutos} minuto{'s' if minutos > 1 else ''}.", 429)


def _so_o_proprio_ou_mestre(usuario_id):
    """None se o requisitante for o dono do perfil (ou Conta Mestre); senao uma resposta 403."""
    usuario = usuario_da_requisicao()
    if usuario and (usuario.id == usuario_id or usuario.conta_mestre):
        return None
    return erro("Você só pode acessar o seu próprio perfil.", 403)


def usuario_da_requisicao():
    """Usuario autenticado pelo token, quando enviado."""
    return sistema.validar_sessao(extrair_token(request))


def exigir_acesso_manutencao(usuario):
    """Checa a regra de acesso do módulo de Manutenção (setor Manutenção ou
    Conta Mestre); se negar, loga o suficiente para diagnosticar uma sessão
    que devia ter passado e não passou (sem expor o token em si) - útil para
    um 403 inesperado que não se reproduz em teste automatizado."""
    if manutencao.usuario_pode_ver_manutencao(usuario):
        return None
    if usuario:
        log.warning(
            "Manutenção: acesso negado para %s (conta_mestre=%s, setores=%s).",
            usuario.email, usuario.conta_mestre, usuario.setores,
        )
    else:
        log.warning("Manutenção: acesso negado - nenhuma sessão válida na requisição (token ausente/expirado).")
    return erro("Você não tem acesso ao módulo de Manutenção.", 403)


def nome_do_autor(dados):
    """Quem assinou a acao no historico: o token manda, o corpo e o fallback."""
    usuario = usuario_da_requisicao()
    if usuario:
        return usuario.nome_display
    return dados.get("usuario_nome") or "Sistema"


def exigir_conta_mestre():
    """Retorna None se o requisitante for Conta Mestre, ou uma resposta 403."""
    usuario = usuario_da_requisicao()
    if not usuario or not usuario.conta_mestre:
        return erro("Apenas a Conta Mestre pode fazer isso.", 403)
    return None


# ==============================================================
# PAGINA E ARQUIVOS
# ==============================================================
@paginas.route("/")
def index():
    return render_template("index.html")


@paginas.route("/apresentacao")
def apresentacao():
    """Showcase cinematográfico (motion design puro, sem nenhuma tela do app) -
    não lê nem mostra dado nenhum, então não exige sessão."""
    return render_template("apresentacao.html")


@arquivos.route("/logo.png")
def serve_logo():
    candidatos = [
        caminho_arquivo_dados("logo.png"),
        os.path.join(BASE_DIR, "static", "img", "logo.png"),
        os.path.join(BASE_DIR, "logo.png"),
        os.path.join(os.getcwd(), "logo.png"),
    ]
    for caminho in candidatos:
        if os.path.exists(caminho):
            return send_file(caminho, mimetype="image/png")
    return "", 404


@arquivos.route("/fotos_usuarios/<path:filename>")
def serve_foto_usuario(filename):
    pasta = sistema.obter_pasta_fotos()
    if os.path.exists(os.path.join(pasta, filename)):
        return send_from_directory(pasta, filename)
    return serve_logo()


EXTENSOES_ANEXO_INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt"}


@arquivos.route("/anexos_tarefas/<path:filename>")
def serve_anexo(filename):
    pasta = sistema.obter_pasta_anexos()
    if os.path.exists(os.path.join(pasta, filename)):
        # So tipos sem script abrem na tela; .html/.svg e o resto sao BAIXADOS (um anexo .html aberto na
        # origem do app podia ler o token de sessao e agir como o usuario - XSS armazenado).
        seguro = os.path.splitext(filename)[1].lower() in EXTENSOES_ANEXO_INLINE
        resposta = send_from_directory(pasta, filename, as_attachment=not seguro)
        resposta.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
        return resposta
    return jsonify({"success": False, "message": "Arquivo não encontrado"}), 404


# ==============================================================
# AUTENTICACAO E SESSAO
# ==============================================================
@api.post("/login")
def api_login():
    dados = corpo()
    chave = "login:" + str(dados.get("email") or "").strip().lower()
    espera = limite_login.segundos_restantes(chave)
    if espera:
        return _espere(espera)
    usuario = sistema.login(dados.get("email"), dados.get("senha"))
    if not usuario:
        limite_login.registrar(chave)
        return erro("E-mail ou senha inválidos.", 401)
    limite_login.limpar(chave)

    with sistema.transacao():
        token = sistema.criar_sessao(usuario.id)
    salvar_sessao_local(token, usuario.id)
    return jsonify({"success": True, "usuario": usuario.para_json(), "token": token})


@api.post("/sessao/validar")
def api_sessao_validar():
    usuario = sistema.validar_sessao(extrair_token(request))
    if usuario:
        return jsonify({"success": True, "valida": True, "usuario": usuario.para_json()})
    return jsonify({"success": False, "valida": False, "message": "Sessão inválida ou expirada."}), 401


@api.post("/sessao/encerrar")
def api_sessao_encerrar():
    with sistema.transacao():
        sistema.encerrar_sessao(extrair_token(request))
    remover_sessao_local()
    return jsonify({"success": True, "message": "Sessão encerrada."})


@api.get("/sessao/arquivo")
def api_sessao_arquivo():
    dados = ler_sessao_local()
    if dados:
        usuario = sistema.validar_sessao(dados.get("token"))
        if usuario:
            return jsonify({"success": True, "token": dados.get("token"), "usuario": usuario.para_json()})
        remover_sessao_local()
    return jsonify({"success": False, "message": "Nenhuma sessão salva."})


@api.post("/cadastro")
def api_cadastro():
    dados = corpo()
    nome = (dados.get("nome") or "").strip()
    email = (dados.get("email") or "").strip()
    senha = dados.get("senha") or ""
    setor = (dados.get("setor") or "").strip()

    if not nome or not email or not senha:
        return erro("Preencha todos os campos.")
    if len(senha) < 4:
        return erro("A senha deve ter pelo menos 4 caracteres.")
    if not setor:
        return erro("Selecione o setor da sua conta.")

    with sistema.transacao():
        if sistema.buscar_usuario_por_email(email):
            return erro("E-mail já cadastrado.")
        sistema.registrar_setor(setor)
        # Toda conta nova nasce comum, presa ao proprio setor. Só uma Conta
        # Mestre já existente pode promover alguém depois (POST /usuario/<id>/gerenciar).
        sistema.usuarios.append(Usuario(sistema.gerar_id(), nome, email, hash_senha(senha),
                                         setores=[setor], conta_mestre=False))

    return jsonify({"success": True, "message": "Conta criada com sucesso."})


# ==============================================================
# RECUPERACAO DE SENHA
# ==============================================================
@api.post("/recuperacao/solicitar")
def api_recuperacao_solicitar():
    email = (corpo().get("email") or "").strip()
    if not email:
        return erro("Informe o e-mail.")

    # A resposta é sempre a mesma: não revelamos quais e-mails existem na base.
    resposta = jsonify({"success": True, "message": "Se o e-mail estiver cadastrado, um código foi enviado."})
    chave_pedido = "pedido:" + email.lower()
    if limite_pedidos_codigo.segundos_restantes(chave_pedido):
        return resposta                      # mesma resposta de sempre, mas sem gerar nem enviar outro codigo
    limite_pedidos_codigo.registrar(chave_pedido)

    with sistema.transacao():
        usuario = sistema.buscar_usuario_por_email(email)
        if not usuario:
            return resposta
        codigo = gerar_codigo_recuperacao()
        usuario.codigo_recuperacao = codigo
        usuario.expiracao_codigo = (datetime.now() + timedelta(minutes=10)).isoformat()

    def enviar():
        sucesso, mensagem = enviar_email_recuperacao(email, codigo)
        if not sucesso:
            log.error("Falha no envio do e-mail de recuperação: %s", mensagem)

    threading.Thread(target=enviar, daemon=True).start()
    return resposta


def _validar_codigo(usuario, codigo):
    """Retorna None quando o codigo e valido, ou uma resposta de erro.

    Chame sempre dentro de `with sistema.transacao():` quando a validacao pode
    limpar o codigo (expirado) - ela grava esse efeito colateral.
    """
    if not usuario or not usuario.codigo_recuperacao:
        return erro("Nenhuma solicitação de recuperação encontrada.")
    if usuario.expiracao_codigo and datetime.now() > datetime.fromisoformat(usuario.expiracao_codigo):
        usuario.codigo_recuperacao = None
        usuario.expiracao_codigo = None
        return erro("Código expirado. Solicite um novo.")
    if usuario.codigo_recuperacao != codigo:
        return erro("Código incorreto.")
    return None


@api.post("/recuperacao/validar")
def api_recuperacao_validar():
    dados = corpo()
    email = (dados.get("email") or "").strip()
    codigo = (dados.get("codigo") or "").strip()
    if not email or not codigo:
        return erro("Preencha todos os campos.")

    chave = "codigo:" + email.lower()
    espera = limite_codigo.segundos_restantes(chave)
    if espera:
        return _espere(espera)
    with sistema.transacao():
        falha = _validar_codigo(sistema.buscar_usuario_por_email(email), codigo)
        if falha:
            limite_codigo.registrar(chave)
            return falha
    return jsonify({"success": True, "message": "Código válido."})


@api.post("/recuperacao/redefinir")
def api_recuperacao_redefinir():
    dados = corpo()
    email = (dados.get("email") or "").strip()
    codigo = (dados.get("codigo") or "").strip()
    nova_senha = dados.get("nova_senha") or ""
    confirmar_senha = dados.get("confirmar_senha") or ""

    if not email or not codigo or not nova_senha or not confirmar_senha:
        return erro("Preencha todos os campos.")
    if nova_senha != confirmar_senha:
        return erro("As senhas não coincidem.")
    if len(nova_senha) < 4:
        return erro("A senha deve ter pelo menos 4 caracteres.")

    chave = "codigo:" + email.lower()
    espera = limite_codigo.segundos_restantes(chave)
    if espera:
        return _espere(espera)
    with sistema.transacao():
        usuario = sistema.buscar_usuario_por_email(email)
        falha = _validar_codigo(usuario, codigo)
        if falha:
            limite_codigo.registrar(chave)
            return falha
        limite_codigo.limpar(chave)
        usuario.senha = hash_senha(nova_senha)
        usuario.codigo_recuperacao = None
        usuario.expiracao_codigo = None

    return jsonify({"success": True, "message": "Senha redefinida com sucesso."})


# ==============================================================
# PERFIL E GESTAO DE USUARIOS (CONTA MESTRE)
# ==============================================================
@api.post("/perfil/atualizar")
def api_perfil_atualizar():
    dados = corpo()
    negado = _so_o_proprio_ou_mestre(dados.get("usuario_id"))
    if negado:
        return negado

    with sistema.transacao():
        usuario = sistema.buscar_usuario(dados.get("usuario_id"))
        if not usuario:
            return erro("Usuário não encontrado.", 404)

        if "nome_exibicao" in dados:
            usuario.nome_exibicao = (dados.get("nome_exibicao") or "").strip() or None

        if "foto_perfil" in dados:
            foto = dados.get("foto_perfil")
            if foto and foto.startswith("data:image"):
                try:
                    _, codificado = foto.split(",", 1)
                    imagem = base64.b64decode(codificado)
                    nome_arquivo = f"perfil_{usuario.id}_{uuid.uuid4().hex[:12]}.jpg"
                    with open(os.path.join(sistema.obter_pasta_fotos(), nome_arquivo), "wb") as f:
                        f.write(imagem)

                    anterior = usuario.foto_perfil
                    usuario.foto_perfil = f"/fotos_usuarios/{nome_arquivo}"

                    if anterior and not anterior.startswith("data:"):
                        caminho_antigo = os.path.join(sistema.obter_pasta_fotos(), os.path.basename(anterior))
                        if os.path.exists(caminho_antigo):
                            try:
                                os.remove(caminho_antigo)
                            except OSError:
                                pass
                except Exception as falha:
                    log.error("Erro ao salvar foto de perfil: %s", falha)
                    return erro("Não foi possível salvar a foto.", 500)
            else:
                usuario.foto_perfil = foto or None

        resultado = jsonify({"success": True, "usuario": usuario.para_json()})

    return resultado


@api.post("/usuario/<usuario_id>/gerenciar")
def api_usuario_gerenciar(usuario_id):
    """Só a Conta Mestre define os setores de outra conta ou a promove/rebaixa."""
    negado = exigir_conta_mestre()
    if negado:
        return negado

    dados = corpo()
    with sistema.transacao():
        alvo = sistema.buscar_usuario(usuario_id)
        if not alvo:
            return erro("Usuário não encontrado.", 404)

        # Nunca deixa o sistema sem nenhuma Conta Mestre: sem ela ninguem mais consegue
        # gerenciar setores/acessos (e recuperar isso exigiria editar arquivo a mao).
        if "conta_mestre" in dados and not dados.get("conta_mestre") and alvo.conta_mestre:
            outras = [u for u in sistema.usuarios if u.conta_mestre and u.id != alvo.id]
            if not outras:
                return erro("Precisa existir pelo menos uma Conta Mestre. Promova outra conta antes de remover esta.")

        requisitante = usuario_da_requisicao()
        sistema._autor_mudanca = requisitante.email if requisitante else None

        if "setores" in dados:
            novos_setores = [s.strip() for s in (dados.get("setores") or []) if s and s.strip()]
            alvo.setores = novos_setores
            for nome in novos_setores:
                sistema.registrar_setor(nome)
        if "conta_mestre" in dados:
            alvo.conta_mestre = bool(dados.get("conta_mestre"))

        resultado = jsonify({"success": True, "usuario": alvo.para_json()})

    return resultado


# ==============================================================
# SETORES
# ==============================================================
@api.get("/setores")
def api_setores():
    return jsonify({"success": True, "setores": sistema.setores})


@api.post("/setores/adicionar")
def api_setores_adicionar():
    nome = (corpo().get("nome") or "").strip()
    if not nome:
        return erro("Informe o nome do setor.")
    with sistema.transacao():
        sistema.registrar_setor(nome)
    return jsonify({"success": True, "setores": sistema.setores})


# ==============================================================
# ETIQUETAS PRE-SALVAS
# ==============================================================
@api.get("/etiquetas")
def api_etiquetas():
    return jsonify({"success": True, "etiquetas": sistema.etiquetas_salvas})


@api.post("/etiquetas")
def api_etiquetas_salvar():
    dados = corpo()
    nome = (dados.get("nome") or "").strip()
    if not nome:
        return erro("Informe o nome da etiqueta.")

    with sistema.transacao():
        etiqueta = sistema.salvar_etiqueta(nome, dados.get("cor"))

    return jsonify({"success": True, "etiqueta": etiqueta, "etiquetas": sistema.etiquetas_salvas})


@api.delete("/etiquetas/<etiqueta_id>")
def api_etiquetas_excluir(etiqueta_id):
    with sistema.transacao():
        sistema.remover_etiqueta(etiqueta_id)
    return jsonify({"success": True, "etiquetas": sistema.etiquetas_salvas})


# ==============================================================
# ESTRUTURA: PASTAS
# ==============================================================
@api.post("/pasta")
def api_pasta_salvar():
    dados = corpo()
    nome = (dados.get("nome") or "").strip()
    if not nome:
        return erro("O nome da pasta é obrigatório.")

    cor = dados.get("cor") or "#0D3B8C"
    pasta_id = dados.get("id")
    requisitante = usuario_da_requisicao()

    with sistema.transacao():
        if pasta_id:
            pasta = sistema.buscar_pasta(pasta_id)
            if not pasta:
                return erro("Pasta não encontrada.", 404)
            if not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
                return erro("Você não tem acesso a esta pasta.", 403)

            pasta.nome = nome
            pasta.cor_fundo = cor
            # Reatribuir uma pasta a outro setor é uma decisão de escopo global:
            # só a Conta Mestre faz isso, para uma conta comum não conseguir
            # "sequestrar" uma pasta compartilhada para outro setor.
            if requisitante and requisitante.conta_mestre and "setor" in dados:
                novo_setor = (dados.get("setor") or "").strip() or None
                pasta.setor = novo_setor
                if novo_setor:
                    sistema.registrar_setor(novo_setor)
        else:
            pedido = (dados.get("setor") or "").strip() or None
            if requisitante and requisitante.conta_mestre:
                setor = pedido
            else:
                # Uma conta comum só cria pasta dentro de um setor que ela já
                # tem acesso; um valor fora da própria lista é ignorado.
                setores_permitidos = requisitante.setores if requisitante else []
                if pedido in setores_permitidos:
                    setor = pedido
                else:
                    setor = setores_permitidos[0] if setores_permitidos else None
            if setor:
                sistema.registrar_setor(setor)
            sistema.pastas.append(Pasta(sistema.gerar_id(), nome, cor, setor))

    return jsonify({"success": True})


@api.delete("/pasta")
def api_pasta_excluir():
    pasta_id = corpo().get("id")
    requisitante = usuario_da_requisicao()

    with sistema.transacao():
        pasta = sistema.buscar_pasta(pasta_id)
        if pasta and not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
            return erro("Você não tem acesso a esta pasta.", 403)
        sistema.pastas = [p for p in sistema.pastas if p.id != pasta_id]

    return jsonify({"success": True})


# ==============================================================
# ESTRUTURA: PROJETOS
# ==============================================================
@api.post("/projeto")
def api_projeto_salvar():
    dados = corpo()
    nome = (dados.get("nome") or "").strip()
    if not nome:
        return erro("O nome do projeto é obrigatório.")

    projeto_id = dados.get("id")
    requisitante = usuario_da_requisicao()

    with sistema.transacao():
        if projeto_id:
            projeto = sistema.buscar_projeto(projeto_id)
            if not projeto:
                return erro("Projeto não encontrado.", 404)
            pasta = sistema.pasta_do_projeto(projeto_id)
            if not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
                return erro("Você não tem acesso a este projeto.", 403)
            projeto.nome = nome
        else:
            pasta = sistema.buscar_pasta(dados.get("id_pasta"))
            if not pasta:
                return erro("Pasta não encontrada.", 404)
            if not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
                return erro("Você não tem acesso a esta pasta.", 403)
            pasta.projetos.append(Projeto(sistema.gerar_id(), nome))

    return jsonify({"success": True})


@api.delete("/projeto")
def api_projeto_excluir():
    projeto_id = corpo().get("id")
    requisitante = usuario_da_requisicao()

    with sistema.transacao():
        pasta = sistema.pasta_do_projeto(projeto_id)
        if pasta and not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
            return erro("Você não tem acesso a este projeto.", 403)
        for pasta_atual in sistema.pastas:
            pasta_atual.projetos = [pr for pr in pasta_atual.projetos if pr.id != projeto_id]

    return jsonify({"success": True})


# ==============================================================
# TAREFAS
# ==============================================================
def _data_do_formulario(valor):
    if not valor:
        return None
    try:
        return datetime.strptime(valor[:10], "%Y-%m-%d")
    except ValueError:
        return None


@api.post("/tarefa")
def api_tarefa_salvar():
    dados = corpo()
    titulo = (dados.get("titulo") or "").strip()
    if not titulo:
        return erro("O título da demanda é obrigatório.")

    autor = nome_do_autor(dados)
    requisitante = usuario_da_requisicao()
    cidade = (dados.get("cidade") or "").strip() or None
    etiqueta = Etiqueta(dados.get("etiqueta_nome", ""), dados.get("etiqueta_cor", "#94A3B8"))
    data_vencimento = _data_do_formulario(dados.get("data_vencimento"))
    data_previsao = _data_do_formulario(dados.get("data_previsao"))
    data_limite = _data_do_formulario(dados.get("data_limite")) or data_vencimento
    data_inicio_real = _data_do_formulario(dados.get("data_inicio_real"))
    data_fim_real = _data_do_formulario(dados.get("data_fim_real"))
    data_conclusao_manual = _data_do_formulario(dados.get("data_conclusao"))
    prioridade = dados.get("prioridade") or "Média"
    dependencias = dados.get("dependencias") or []
    # Só vem preenchido quando o modal foi aberto a partir de "Agendar Demanda"
    # (Frota & Manutenção) - ver o campo opcional `#tarefa-veiculo-grupo` no
    # modal compartilhado. Uma tarefa comum de Estrutura/Demandas manda "".
    veiculo_placa = (dados.get("veiculo_placa") or "").strip().upper() or None

    with sistema.transacao():
        status = sistema.buscar_status(dados.get("status"))
        responsavel = sistema.buscar_usuario(dados.get("responsavel_id"))
        if cidade:
            sistema.registrar_cidade(cidade)

        tarefa_id = dados.get("id")
        if tarefa_id:
            tarefa = sistema.buscar_tarefa(tarefa_id)
            if not tarefa:
                return erro("Tarefa não encontrada.", 404)
            pasta = sistema.pasta_da_tarefa(tarefa_id)
            if not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
                return erro("Você não tem acesso a esta demanda.", 403)

            tarefa.titulo = titulo
            tarefa.descricao = dados.get("descricao") or ""
            tarefa.responsavel = responsavel
            tarefa.data_limite = data_limite
            tarefa.etiqueta = etiqueta
            tarefa.cidade = cidade
            tarefa.data_previsao = data_previsao
            tarefa.data_vencimento = data_vencimento
            tarefa.data_inicio_real = data_inicio_real
            tarefa.data_fim_real = data_fim_real
            tarefa.prioridade = prioridade
            tarefa.dependencias = dependencias
            tarefa.veiculo_placa = veiculo_placa
            tarefa.aplicar_status(status, autor, data_conclusao_manual)
            tarefa.adicionar_historico("Editou a tarefa", autor)
            resultado = jsonify({"success": True, "id": tarefa.id})
        else:
            projeto = sistema.buscar_projeto(dados.get("id_projeto"))
            if not projeto:
                return erro("Projeto não encontrado.", 404)
            pasta = sistema.pasta_do_projeto(dados.get("id_projeto"))
            if not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
                return erro("Você não tem acesso a este projeto.", 403)

            nova = Tarefa(
                id=sistema.gerar_id(), titulo=titulo, descricao=dados.get("descricao") or "",
                status=status, responsavel=responsavel, data_limite=data_limite, etiqueta=etiqueta,
                cidade=cidade, data_previsao=data_previsao, data_vencimento=data_vencimento,
                data_conclusao=data_conclusao_manual, prioridade=prioridade, dependencias=dependencias,
                data_inicio_real=data_inicio_real, data_fim_real=data_fim_real, veiculo_placa=veiculo_placa,
            )
            nova.adicionar_historico("Criou a tarefa", autor)
            projeto.tarefas.append(nova)

            if responsavel:
                responsavel.notificacoes.insert(0, {
                    "id": sistema.gerar_id(),
                    "tipo": "atribuicao",
                    "mensagem": f"Você foi atribuído à tarefa: {nova.titulo}",
                    "tarefa_id": nova.id,
                    "tarefa_titulo": nova.titulo,
                    "data": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "lida": False,
                })
            resultado = jsonify({"success": True, "id": nova.id})

    return resultado


@api.patch("/tarefa")
def api_tarefa_status():
    dados = corpo()
    autor = nome_do_autor(dados)
    requisitante = usuario_da_requisicao()

    with sistema.transacao():
        tarefa = sistema.buscar_tarefa(dados.get("id"))
        if not tarefa:
            return erro("Tarefa não encontrada.", 404)
        pasta = sistema.pasta_da_tarefa(dados.get("id"))
        if not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
            return erro("Você não tem acesso a esta demanda.", 403)

        nome_status = dados.get("status")
        status = sistema.buscar_status(nome_status, tarefa.status)

        if nome_status == "Concluída" and sistema.tarefa_bloqueada(tarefa):
            return erro("Esta tarefa está bloqueada por dependências não concluídas.", 409)

        tarefa.aplicar_status(status, autor)
        tarefa.adicionar_historico(f"Alterou status para '{status.nome}'", autor)

    return jsonify({"success": True})


@api.delete("/tarefa")
def api_tarefa_excluir():
    """Exclusão física e definitiva de uma demanda (Estrutura OU Agendar
    Demanda de Manutenção - é a mesma Tarefa por baixo). Duas garantias além
    da remoção em si: 404 se a demanda já não existir mais (outra pessoa
    excluiu primeiro - nunca finge sucesso silencioso) e limpeza de qualquer
    referência a este id na lista `dependencias` de OUTRAS tarefas, para não
    deixar um id órfão perseguindo o Quadro de Dependências para sempre."""
    tarefa_id = corpo().get("id")
    requisitante = usuario_da_requisicao()

    with sistema.transacao():
        tarefa = sistema.buscar_tarefa(tarefa_id)
        if not tarefa:
            return erro("Esta demanda já não existe mais (pode ter sido excluída por outra pessoa).", 404)

        pasta = sistema.pasta_da_tarefa(tarefa_id)
        if pasta and not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
            return erro("Você não tem acesso a esta demanda.", 403)

        titulo = tarefa.titulo
        for pasta_atual in sistema.pastas:
            for projeto in pasta_atual.projetos:
                projeto.tarefas = [t for t in projeto.tarefas if t.id != tarefa_id]
                for outra in projeto.tarefas:
                    if tarefa_id in outra.dependencias:
                        outra.dependencias = [d for d in outra.dependencias if d != tarefa_id]

    autor = requisitante.nome if requisitante else "desconhecido"
    log.info("Demanda '%s' (%s) excluída por %s.", titulo, tarefa_id, autor)
    return jsonify({"success": True})


@api.post("/tarefa/<tarefa_id>/anexo")
def api_tarefa_anexo(tarefa_id):
    dados = corpo()
    requisitante = usuario_da_requisicao()

    nome_arquivo = os.path.basename(dados.get("nome_arquivo") or "arquivo")
    conteudo = dados.get("conteudo") or ""
    if "," in conteudo:
        conteudo = conteudo.split(",", 1)[1]

    try:
        binario = base64.b64decode(conteudo)
    except Exception:
        return erro("Arquivo inválido.")
    if not binario:
        return erro("Arquivo vazio.")

    with sistema.transacao():
        tarefa = sistema.buscar_tarefa(tarefa_id)
        if not tarefa:
            return erro("Tarefa não encontrada.", 404)
        pasta = sistema.pasta_da_tarefa(tarefa_id)
        if not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
            return erro("Você não tem acesso a esta demanda.", 403)

        extensao = os.path.splitext(nome_arquivo)[1] or ".bin"
        nome_unico = f"anexo_{tarefa_id}_{uuid.uuid4().hex[:12]}{extensao}"
        pasta_anexos = sistema.obter_pasta_anexos()
        os.makedirs(pasta_anexos, exist_ok=True)

        try:
            with open(os.path.join(pasta_anexos, nome_unico), "wb") as f:
                f.write(binario)
        except Exception as falha:
            log.error("Erro ao gravar anexo: %s", falha)
            return erro("Não foi possível salvar o arquivo.", 500)

        autor = nome_do_autor(dados)
        anexo = {
            "id": sistema.gerar_id(),
            "nome": nome_arquivo,
            "caminho": f"/anexos_tarefas/{nome_unico}",
            "data": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "usuario_nome": autor,
        }
        tarefa.anexos.append(anexo)
        tarefa.adicionar_historico(f"Anexou arquivo: {nome_arquivo}", autor)
        resultado = jsonify({"success": True, "anexo": anexo})

    return resultado


@api.post("/tarefa/<tarefa_id>/comentario")
def api_tarefa_comentario(tarefa_id):
    dados = corpo()
    texto = (dados.get("texto") or "").strip()
    if not texto:
        return erro("O comentário não pode estar vazio.")

    requisitante = usuario_da_requisicao()

    with sistema.transacao():
        tarefa = sistema.buscar_tarefa(tarefa_id)
        if not tarefa:
            return erro("Tarefa não encontrada.", 404)
        pasta = sistema.pasta_da_tarefa(tarefa_id)
        if not sistema.usuario_pode_acessar_pasta(requisitante, pasta):
            return erro("Você não tem acesso a esta demanda.", 403)

        autor = nome_do_autor(dados)
        comentario = {
            "id": sistema.gerar_id(),
            "texto": texto,
            "usuario_nome": autor,
            "usuario_foto": dados.get("usuario_foto"),
            "data": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        tarefa.comentarios.append(comentario)
        tarefa.adicionar_historico("Adicionou um comentário", autor)
        resultado = jsonify({"success": True, "comentario": comentario})

    return resultado


# ==============================================================
# NOTIFICACOES E FAVORITOS
# ==============================================================
@api.get("/usuario/<usuario_id>/notificacoes")
def api_notificacoes(usuario_id):
    negado = _so_o_proprio_ou_mestre(usuario_id)
    if negado:
        return negado
    usuario = sistema.buscar_usuario(usuario_id)
    if not usuario:
        return erro("Usuário não encontrado.", 404)
    return jsonify({"success": True, "notificacoes": usuario.notificacoes})


@api.post("/usuario/<usuario_id>/notificacoes/lidas")
def api_notificacoes_lidas(usuario_id):
    negado = _so_o_proprio_ou_mestre(usuario_id)
    if negado:
        return negado
    with sistema.transacao():
        usuario = sistema.buscar_usuario(usuario_id)
        if not usuario:
            return erro("Usuário não encontrado.", 404)
        for notificacao in usuario.notificacoes:
            notificacao["lida"] = True

    return jsonify({"success": True})


@api.post("/usuario/<usuario_id>/favoritos")
def api_favoritos(usuario_id):
    negado = _so_o_proprio_ou_mestre(usuario_id)
    if negado:
        return negado
    item_id = corpo().get("item_id")
    if not item_id:
        return erro("Item inválido.")

    with sistema.transacao():
        usuario = sistema.buscar_usuario(usuario_id)
        if not usuario:
            return erro("Usuário não encontrado.", 404)

        if item_id in usuario.favoritos:
            usuario.favoritos.remove(item_id)
            acao = "removido"
        else:
            usuario.favoritos.append(item_id)
            acao = "adicionado"

        resultado = jsonify({"success": True, "acao": acao, "favoritos": usuario.favoritos})

    return resultado


# ==============================================================
# CIDADES, ANOTACOES E DADOS AGREGADOS
# ==============================================================
@api.get("/cidades")
def api_cidades():
    return jsonify({"success": True, "cidades": sistema.cidades})


@api.post("/cidades/adicionar")
def api_cidades_adicionar():
    cidade = (corpo().get("cidade") or "").strip()
    with sistema.transacao():
        sistema.registrar_cidade(cidade)
    return jsonify({"success": True, "cidades": sistema.cidades})


@api.post("/anotacao")
def api_anotacao():
    dados = corpo()
    chave = dados.get("chave")
    if not chave:
        return erro("Chave da anotação ausente.")
    with sistema.transacao():
        sistema.anotacoes[chave] = dados.get("texto", "")
    return jsonify({"success": True})


@api.get("/dados")
def api_dados():
    return jsonify(sistema.estado_completo(usuario_da_requisicao()))


@api.get("/dashboard")
def api_dashboard():
    usuario = usuario_da_requisicao()
    setor = request.args.get("setor") or None
    return jsonify(sistema.metricas_dashboard(usuario, setor))


@api.get("/exportar")
def api_exportar():
    usuario = usuario_da_requisicao()
    setor = request.args.get("setor") or None
    try:
        caminho = sistema.exportar_dados_excel(usuario, setor)
    except Exception as falha:
        log.error("Erro ao exportar relatório: %s", falha)
        return erro("Não foi possível gerar o relatório.", 500)
    return send_file(caminho, as_attachment=True)


# ==============================================================
# MANUTENCAO PREVENTIVA (GOOGLE SHEETS)
# ==============================================================
@api.get("/manutencao")
def api_manutencao():
    """Frota & Manutenção: le a planilha de revisões/óleo direto do Google
    Sheets (ver backend/manutencao.py). Acesso restrito ao setor Manutenção
    ou à Conta Mestre - a mesma regra vale para esconder o item no menu."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    if request.args.get("forcar"):
        # Botão "atualizar": a próxima leitura confere o arquivo no Drive agora. Navegar
        # entre as abas NÃO manda isto - uma vez baixada, a planilha vale para todas.
        manutencao.forcar_atualizacao()

    try:
        veiculos, modificado_em = manutencao.buscar_veiculos_e_metadata()
    except manutencao.PlanilhaNaoConfigurada as motivo:
        # Estado esperado antes de alguém configurar a integração - não é uma
        # falha, então responde 200 e deixa o frontend mostrar um aviso calmo.
        return jsonify({"success": True, "configurado": False, "mensagem": str(motivo), "veiculos": []})
    except Exception as falha:
        log.error("Erro ao buscar dados de manutenção no Sheets: %s", falha)
        return erro("Não foi possível carregar os dados de manutenção agora.", 502)

    return jsonify({
        "success": True, "configurado": True, "veiculos": veiculos,
        "modificado_em": modificado_em,
    })


@api.post("/manutencao/atualizar")
def api_manutencao_atualizar():
    """Write-back: edita UM campo (célula de entrada bruta) de UMA placa
    direto no .xlsx no Drive - ver backend/manutencao.py para a lista de
    campos permitidos e como a concorrência é tratada."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    dados = corpo()
    placa = (dados.get("placa") or "").strip()
    campo = (dados.get("campo") or "").strip()
    valor = dados.get("valor")
    modificado_em = dados.get("modificado_em")
    valor_anterior = dados.get("valor_anterior")
    if not placa or not campo:
        return erro("Informe a placa e o campo a atualizar.", 400)

    try:
        resultado = manutencao.atualizar_campo_veiculo(placa, campo, valor, modificado_em, valor_anterior)
    except manutencao.ConflitoEdicao as motivo:
        return erro(str(motivo), 409, valor_atual=motivo.valor_atual, modificado_em=motivo.modificado_em)
    except manutencao.PlanilhaNaoConfigurada as motivo:
        return erro(str(motivo), 503)
    except ValueError as motivo:
        return erro(str(motivo), 400)
    except Exception as falha:
        log.error("Erro ao gravar manutenção (placa=%s, campo=%s): %s", placa, campo, falha)
        return erro("Não foi possível salvar a alteração agora.", 502)

    autor = usuario.nome if usuario else "desconhecido"
    log.info("Manutenção: %s alterou '%s' da placa %s.", autor, campo, placa.upper())
    return jsonify({"success": True, "modificado_em": resultado["modificado_em"]})


@api.post("/manutencao/importar")
def api_manutencao_importar():
    """Importação de emergência: substitui o .xlsx inteiro no Drive por um
    arquivo enviado pelo usuário (drag & drop), depois de validar que tem a
    estrutura mínima esperada. Mesmo padrão de upload em base64 já usado em
    anexos de tarefa (`/api/tarefa/<id>/anexo`)."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    dados = corpo()
    conteudo = dados.get("conteudo") or ""
    nome_arquivo = os.path.basename(dados.get("nome_arquivo") or "planilha.xlsx")
    if not nome_arquivo.lower().endswith(".xlsx"):
        return erro("Envie um arquivo .xlsx.", 400)
    if "," in conteudo:
        conteudo = conteudo.split(",", 1)[1]

    try:
        binario = base64.b64decode(conteudo)
    except Exception:
        return erro("Arquivo inválido.", 400)
    if not binario:
        return erro("Arquivo vazio.", 400)
    if len(binario) > 30 * 1024 * 1024:
        return erro("Arquivo muito grande (limite de 30 MB).", 400)

    try:
        resultado = manutencao.substituir_planilha_no_drive(binario)
    except ValueError as motivo:
        return erro(str(motivo), 400)
    except manutencao.PlanilhaNaoConfigurada as motivo:
        return erro(str(motivo), 503)
    except Exception as falha:
        log.error("Erro ao importar planilha de manutenção: %s", falha)
        return erro("Não foi possível enviar o arquivo ao Drive agora.", 502)

    autor = usuario.nome if usuario else "desconhecido"
    log.info("Manutenção: %s substituiu a planilha inteira via import de emergência (%s).", autor, nome_arquivo)
    return jsonify({"success": True, "modificado_em": resultado["modificado_em"]})


@api.get("/manutencao/categoria/<chave>")
def api_manutencao_categoria(chave):
    """Materializa uma ou mais categorias do Prontuário (Alinhamento, Chassi &
    5ª Roda, Carretas...) como tabelas com a frota inteira - alimenta as abas
    de sub-navegação da tela de Manutenção. `chave` aceita uma lista separada
    por vírgula (ex.: "alinhamento,integridade_chassi") para montar o
    Prontuário só UMA vez e servir várias categorias de uma tacada só, em vez
    de uma chamada cara e independente por categoria. Mesma regra de acesso
    das demais rotas do módulo."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    chaves = [c.strip() for c in chave.split(",") if c.strip()]
    try:
        categorias = manutencao.linhas_categorias(chaves)
    except ValueError as motivo:
        return erro(str(motivo), 400)
    except manutencao.PlanilhaNaoConfigurada as motivo:
        return jsonify({"success": True, "configurado": False, "mensagem": str(motivo), "categorias": {}})
    except Exception as falha:
        log.error("Erro ao montar categoria(s) '%s' de manutenção: %s", chave, falha)
        return erro("Não foi possível carregar esses dados agora.", 502)

    return jsonify({"success": True, "configurado": True, "categorias": categorias})


@api.get("/manutencao/cadastro")
def api_manutencao_cadastro_listar():
    """Cadastro de Frota: lista os Cavalos (aba Frota) e Carretas (aba
    info-carretas) já cadastrados - alimenta a sub-aba "Cadastro de Frota" da
    tela de Manutenção. Mesma regra de acesso das demais rotas do módulo."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    try:
        veiculos, modificado_em = manutencao.listar_cadastro()
    except manutencao.PlanilhaNaoConfigurada as motivo:
        return jsonify({"success": True, "configurado": False, "mensagem": str(motivo), "veiculos": []})
    except Exception as falha:
        log.error("Erro ao listar cadastro de frota: %s", falha)
        return erro("Não foi possível carregar o cadastro agora.", 502)

    return jsonify({"success": True, "configurado": True, "veiculos": veiculos, "modificado_em": modificado_em})


@api.post("/manutencao/cadastro")
def api_manutencao_cadastro_criar():
    """Write-back: cadastra um Cavalo ou uma Carreta novo direto no .xlsx no
    Drive - a linha é sempre uma linha nova (nunca desloca as existentes,
    ver `backend/manutencao.py` para o porquê disso ser seguro para as
    fórmulas de VLOOKUP das outras 19 abas)."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    dados = corpo()
    tipo = (dados.get("tipo") or "").strip().lower()
    modificado_em = dados.get("modificado_em")

    try:
        resultado = manutencao.cadastrar_veiculo(tipo, dados, modificado_em)
    except manutencao.ConflitoEdicao as motivo:
        return erro(str(motivo), 409)
    except manutencao.PlanilhaNaoConfigurada as motivo:
        return erro(str(motivo), 503)
    except ValueError as motivo:
        return erro(str(motivo), 400)
    except Exception as falha:
        log.error("Erro ao cadastrar veículo/carreta (tipo=%s): %s", tipo, falha)
        return erro("Não foi possível salvar o cadastro agora.", 502)

    autor = usuario.nome if usuario else "desconhecido"
    log.info("Manutenção: %s cadastrou %s (placa %s).", autor, tipo, resultado["placa"])
    return jsonify({"success": True, "modificado_em": resultado["modificado_em"], "placa": resultado["placa"]})


@api.post("/manutencao/cadastro/<placa>/visibilidade")
def api_manutencao_cadastro_visibilidade(placa):
    """Gerenciar: faz um veículo aparecer ou não nas telas de Manutenção (Dashboard,
    análises, abas, Resumo). NÃO altera a planilha - a escolha fica em
    `veiculos_ocultos.json` na pasta compartilhada (ver `manutencao.definir_visibilidade`).
    Corpo: {"visivel": true|false}. Mesma regra de acesso do cadastro."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    dados = corpo()
    if "visivel" not in dados:
        return erro("Informe se o veículo deve aparecer (visivel: true/false).")
    try:
        resultado = manutencao.definir_visibilidade(placa, bool(dados["visivel"]), usuario.email if usuario else None)
    except ValueError as motivo:
        return erro(str(motivo), 400)
    except Exception as falha:
        log.error("Erro ao mudar a visibilidade do veículo %s: %s", placa, falha)
        return erro("Não foi possível salvar essa mudança agora.", 502)

    return jsonify({"success": True, **resultado})


@api.get("/manutencao/resumo-analitico")
def api_manutencao_resumo_analitico():
    """Painel de Guerra: recria (melhor) a aba "RESUMO ANALITICO" da planilha
    original, cruzando tempo e km em cada categoria - ver
    backend/manutencao.py e [[Módulo de Manutenção]]. Mesma regra de acesso
    das demais rotas do módulo."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    try:
        linhas = manutencao.resumo_analitico_matriz()
    except manutencao.PlanilhaNaoConfigurada as motivo:
        return jsonify({"success": True, "configurado": False, "mensagem": str(motivo), "linhas": []})
    except Exception as falha:
        log.error("Erro ao montar o resumo analítico: %s", falha)
        return erro("Não foi possível carregar o resumo analítico agora.", 502)

    return jsonify({"success": True, "configurado": True, "linhas": linhas})


@api.get("/manutencao/saude-frota")
def api_manutencao_saude_frota():
    """Dashboard Geral "PRO MAX": Health Score + filtro Apenas Cavalos/Apenas
    Carretas/Ambos, cruzando TODAS as frentes de manutenção (não só a aba
    "Revisões e óleo" como a antiga `calcular_metricas()`) - ver
    `calcular_saude_frota` em backend/manutencao.py e [[Módulo de Manutenção]].
    Mesma regra de acesso das demais rotas do módulo."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    try:
        prontuario = manutencao.montar_prontuario_frota()
    except manutencao.PlanilhaNaoConfigurada as motivo:
        return jsonify({"success": True, "configurado": False, "mensagem": str(motivo), "veiculos": []})
    except Exception as falha:
        log.error("Erro ao montar a saúde da frota: %s", falha)
        return erro("Não foi possível carregar os indicadores da frota agora.", 502)

    veiculos = manutencao.calcular_saude_frota(prontuario)
    return jsonify({"success": True, "configurado": True, "veiculos": veiculos})


@api.get("/manutencao/prontuario/<placa>")
def api_manutencao_prontuario(placa):
    """Prontuário do Veículo: cruza as demais abas do ERP de manutenção (5ª
    roda, integridade de chassi, SR-tanques, preventiva de carretas,
    alinhamento) por Placa - ver backend/manutencao.py e
    [[🧠 Dicionario de Dados - ERP Manutencao]]. Mesma regra de acesso de
    `/api/manutencao`."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    try:
        prontuario = manutencao.montar_prontuario_frota()
    except manutencao.PlanilhaNaoConfigurada as motivo:
        return jsonify({"success": True, "configurado": False, "mensagem": str(motivo)})
    except Exception as falha:
        log.error("Erro ao montar o prontuário de manutenção: %s", falha)
        return erro("Não foi possível carregar o prontuário agora.", 502)

    registro = prontuario.get(placa.strip().upper())
    if not registro:
        return erro(f"Nenhum registro de manutenção encontrado para a placa '{placa}'.", 404)

    return jsonify({"success": True, "configurado": True, "prontuario": registro})


NOME_PROJETO_AGENDA_MANUTENCAO = "Agenda de Manutenção"


def _projeto_agenda_manutencao():
    """Acha (ou cria, na primeira vez que alguém agenda uma demanda) o Projeto
    que guarda as demandas da tela "Agendar Demanda" - dentro da MESMA Pasta
    que já concede o setor Manutenção (nunca cria uma pasta nova se uma pasta
    desse setor já existir, para não duplicar o que o usuário já vê no menu
    Estrutura/Demandas: a intenção é reaproveitar, não criar um silo)."""
    pasta_manutencao = next((p for p in sistema.pastas if p.setor == manutencao.SETOR_MANUTENCAO), None)
    if not pasta_manutencao:
        pasta_manutencao = Pasta(sistema.gerar_id(), manutencao.SETOR_MANUTENCAO, "#0D3B8C", manutencao.SETOR_MANUTENCAO)
        sistema.pastas.append(pasta_manutencao)

    projeto = next((p for p in pasta_manutencao.projetos if p.nome == NOME_PROJETO_AGENDA_MANUTENCAO), None)
    if not projeto:
        projeto = Projeto(sistema.gerar_id(), NOME_PROJETO_AGENDA_MANUTENCAO)
        pasta_manutencao.projetos.append(projeto)
    return projeto


@api.get("/manutencao/demandas")
def api_manutencao_demandas_listar():
    """Demandas agendadas na tela "Agendar Demanda" - todas, ou só de uma
    placa (`?placa=XXX`, é assim que o Prontuário do Veículo mostra "Demandas
    agendadas" para aquele veículo). São Tarefas de verdade (mesmo Kanban,
    Calendário, dependências e etiquetas de Estrutura/Demandas - o próprio
    `#modal-tarefa` de lá é reaproveitado tal e qual, ver `abrirNovaTarefa`/
    `abrirEdicaoTarefa` em `structure.js`), só filtradas pelo campo
    `veiculo_placa` - ver SistemaGestaoHG.tarefas_do_veiculo.

    Também devolve `projeto_id`: o Projeto "Agenda de Manutenção" é
    auto-provisionado aqui mesmo numa leitura (não só na escrita) porque o
    frontend precisa desse id ANTES de abrir o modal de criação (ele não
    escolhe projeto - só o veículo). Provisionar numa leitura é seguro e
    idempotente: na pior das hipóteses cria uma Pasta/Projeto vazios um pouco
    mais cedo do que a primeira demanda de verdade."""
    usuario = usuario_da_requisicao()
    negado = exigir_acesso_manutencao(usuario)
    if negado:
        return negado

    placa = (request.args.get("placa") or "").strip()
    if placa:
        demandas = sistema.tarefas_do_veiculo(placa)
    else:
        pasta_manutencao = next((p for p in sistema.pastas if p.setor == manutencao.SETOR_MANUTENCAO), None)
        demandas = []
        if pasta_manutencao:
            for projeto in pasta_manutencao.projetos:
                for tarefa in projeto.tarefas:
                    if tarefa.veiculo_placa:
                        demandas.append({**tarefa.para_dict(), "projeto_nome": projeto.nome, "pasta_nome": pasta_manutencao.nome})
    demandas.sort(key=lambda d: d.get("data_vencimento") or "9999-99-99")

    with sistema.transacao():
        projeto_agenda = _projeto_agenda_manutencao()
        projeto_id = projeto_agenda.id

    return jsonify({"success": True, "demandas": demandas, "projeto_id": projeto_id})


# ==============================================================
# ATUALIZACAO AUTOMATICA (GitHub Releases) - ver backend/atualizador.py
# ==============================================================
from . import rotas_atualizacao  # noqa: E402

rotas_atualizacao.registrar(api, erro)
