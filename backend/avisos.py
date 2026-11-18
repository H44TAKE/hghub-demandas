# -*- coding: utf-8 -*-
"""E-mail SEMANAL de vencimentos: cada responsavel recebe UM e-mail por semana com o que esta atrasado e o que
vence nos proximos 7 dias.

Todos os PCs rodam o app, mas so UM envia por semana: o controle fica em `avisos_semanais.json` (pasta de dados),
sob a mesma trava das demais gravacoes. Se o PC estava desligado no dia combinado, o envio acontece no proximo dia
util da MESMA semana. Configuracao em `configuracao.json`:
    "avisos_semanais": {"ativo": true, "dia_semana": 0, "hora": 8}      (0 = segunda ... 4 = sexta)
"""

import html
import logging
import os
import threading
from datetime import datetime

from . import armazenamento
from .utils import ARQUIVO_TRAVA, bloqueio_arquivo, caminho_dado, carregar_config, enviar_email

log = logging.getLogger("hghub")

ARQUIVO_AVISOS = caminho_dado("avisos_semanais.json")
MAXIMO_POR_EXECUCAO = 60


def configuracao():
    cfg = carregar_config().get("avisos_semanais") or {}
    return {"ativo": cfg.get("ativo", True) is not False, "hora": int(cfg.get("hora", 8)),
            "dia_semana": min(4, max(0, int(cfg.get("dia_semana", 0))))}


def _pendencias_por_responsavel(sistema, hoje):
    """{usuario_id: {"atrasadas": [...], "semana": [...]}} so de demandas abertas (semana = hoje ate 7 dias)."""
    saida = {}
    for pasta in sistema.pastas:
        for projeto in pasta.projetos:
            for tarefa in projeto.tarefas:
                if tarefa.concluida or tarefa.status.nome == "Cancelada" or not tarefa.responsavel:
                    continue
                venc = tarefa.data_vencimento or tarefa.data_limite
                if not venc:
                    continue
                dias = (venc.date() - hoje.date()).days
                grupo = "atrasadas" if dias < 0 else ("semana" if dias <= 7 else None)
                if grupo:
                    saida.setdefault(tarefa.responsavel.id, {"atrasadas": [], "semana": []})[grupo].append(
                        (dias, tarefa.titulo, f"{pasta.nome} › {projeto.nome}"))
    return saida


def _corpo_html(usuario, itens):
    def bloco(titulo, lista, cor):
        if not lista:
            return ""
        linhas = "".join(
            f"<li><b>{html.escape(t)}</b> <span style='color:#5b667b'>— {html.escape(onde)}"
            f"{f' ({-d} dia(s) de atraso)' if d < 0 else (' (hoje)' if d == 0 else f' (em {d} dia(s))')}</span></li>"
            for d, t, onde in sorted(lista))
        return f"<h3 style='color:{cor};margin:16px 0 6px'>{titulo} ({len(lista)})</h3><ul>{linhas}</ul>"
    return (
        "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:640px'>"
        f"<h2 style='color:#0D3B8C'>Olá, {html.escape(usuario.nome_display.split()[0])}</h2>"
        "<p>Resumo da semana: suas demandas que precisam de atenção.</p>"
        + bloco("Atrasadas", itens["atrasadas"], "#a91b1b")
        + bloco("Vencem nos próximos 7 dias", itens["semana"], "#0D3B8C")
        + "<p style='color:#5b667b;margin-top:20px'>REDE HG · Sistema de Demandas. Abra o aplicativo para ver tudo na tela "
          "<i>Meu dia</i>.</p></div>")


def enviar_avisos_da_semana(sistema, agora=None, enviar=enviar_email):
    """Envia o resumo da semana, uma unica vez por semana (entre todos os PCs). Devolve quantos e-mails saíram,
    ou None se nada foi feito (desligado, ainda nao e o dia/hora, fim de semana ou outro PC ja enviou)."""
    agora = agora or datetime.now()
    cfg = configuracao()
    dia_util = agora.weekday() < 5
    chegou = agora.weekday() > cfg["dia_semana"] or (agora.weekday() == cfg["dia_semana"] and agora.hour >= cfg["hora"])
    if not cfg["ativo"] or not dia_util or not chegou:
        return None
    ano, semana, _ = agora.isocalendar()
    hoje = f"{ano}-S{semana:02d}"
    try:
        with bloqueio_arquivo(ARQUIVO_TRAVA, tempo_limite=3):
            try:
                controle = armazenamento.ler_json(ARQUIVO_AVISOS) if os.path.exists(ARQUIVO_AVISOS) else {}
            except armazenamento.ArmazenamentoError:
                return None            # arquivo de controle ilegivel: na duvida NAO envia (melhor nao avisar do que repetir)
            if controle.get("ultima_semana") == hoje:
                return None
            # marca ANTES de enviar: se o envio demorar/falhar no meio, nenhum outro PC repete a leva da semana
            armazenamento.escrever_json(ARQUIVO_AVISOS, {
                "_formato": armazenamento.FORMATO, "ultima_semana": hoje, "por": os.environ.get("COMPUTERNAME", "?"),
                "iniciado_em": agora.strftime("%Y-%m-%d %H:%M:%S")}, forcar=True)
            sistema.recarregar()
            if sistema._base_ilegivel:
                return None
            pendencias = _pendencias_por_responsavel(sistema, agora)
            enviados = 0
            for usuario_id, itens in list(pendencias.items())[:MAXIMO_POR_EXECUCAO]:
                usuario = sistema.buscar_usuario(usuario_id)
                if not usuario or not usuario.email:
                    continue
                ok, mensagem = enviar(usuario.email, "Suas demandas da semana - REDE HG", _corpo_html(usuario, itens))
                if ok:
                    enviados += 1
                else:
                    log.error("Aviso semanal nao enviado para %s: %s", usuario.email, mensagem)
            log.info("Avisos semanais: %s e-mail(s) enviado(s).", enviados)
            return enviados
    except TimeoutError:
        return None
    except Exception as erro:
        log.error("Falha nos avisos semanais: %s", erro)
        return None


def iniciar_avisos_periodicos(sistema, intervalo_segundos=1800):
    """Confere a cada 30 min (so o primeiro PC da semana, a partir do dia/hora configurados, envia)."""
    def laco():
        threading.Event().wait(120)
        while True:
            try:
                enviar_avisos_da_semana(sistema)
            except Exception as erro:
                log.error("Avisos diarios: %s", erro)
            threading.Event().wait(intervalo_segundos)
    threading.Thread(target=laco, daemon=True, name="avisos-diarios").start()
