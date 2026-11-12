/**
 * Atualizações do sistema: aviso discreto, tela "Atualizações" (versão, novidades, barra de progresso,
 * restaurar versão anterior) e verificação sem travar a interface.
 */

import { api } from './api.js';
import { el, escapar } from './state.js';
import { abrirModal, toast } from './ui.js';

const FASES_ATIVAS = new Set(['verificando', 'baixando', 'aplicando']);
let estado = null;
let temporizador = null;
let lembreteDispensado = false;

const requisicao = (caminho, metodo = 'GET') => api.atualizacao(caminho, metodo);

function atualizarAviso() {
  const aviso = el('aviso-atualizacao');
  const ponto = el('atualizacao-ponto');
  const ha = estado && ['disponivel', 'pronta', 'adiada'].includes(estado.fase) && estado.versao_nova;
  ponto?.classList.toggle('hidden', !ha);
  if (!aviso) return;
  const mostrar = ha && estado.fase !== 'adiada' && !lembreteDispensado;
  aviso.classList.toggle('hidden', !mostrar);
  if (mostrar) {
    el('aviso-atualizacao-versoes').textContent = `Instalada: ${estado.versao_instalada} · Nova: ${estado.versao_nova}`;
  }
}

function descreverFase(e) {
  switch (e.fase) {
    case 'desligado': return { titulo: 'Atualização automática indisponível', texto: 'Este programa ainda não está ligado a um repositório de atualizações.' };
    case 'verificando': return { titulo: 'Procurando atualizações…', texto: 'Isso leva alguns segundos.' };
    case 'atualizado': return { titulo: 'Você está na versão mais recente', texto: `Versão ${e.versao_instalada}.` };
    case 'disponivel': return { titulo: `Nova versão ${e.versao_nova} disponível`, texto: `Você está na ${e.versao_instalada}.` };
    case 'baixando': return { titulo: `Baixando a versão ${e.versao_nova}…`, texto: 'Pode continuar usando o sistema.' };
    case 'pronta': return { titulo: `Versão ${e.versao_nova} pronta para instalar`, texto: 'O sistema vai fechar e abrir de novo sozinho. Seus dados não são alterados.' };
    case 'aplicando': return { titulo: 'Instalando…', texto: 'O sistema vai reabrir em instantes.' };
    case 'erro': return { titulo: 'Não foi possível atualizar', texto: e.erro || 'Tente novamente.' };
    default: return { titulo: 'Atualizações', texto: 'Clique em "Verificar agora".' };
  }
}

function desenhar() {
  const corpo = el('atualizacao-corpo');
  const acoes = el('atualizacao-acoes');
  if (!corpo || !estado) return;
  const e = estado;
  const d = descreverFase(e);
  el('atualizacao-versao').textContent = `Versão instalada: ${e.versao_instalada}`;
  const notas = (e.notas || []).map((n) => `<li>${escapar(n)}</li>`).join('');
  const barra = e.fase === 'baixando' || e.fase === 'pronta'
    ? `<div class="atualizacao__barra" role="progressbar" aria-valuenow="${e.progresso}" aria-valuemin="0" aria-valuemax="100">
         <i style="width:${e.progresso}%"></i></div><p class="atualizacao__pct">${e.progresso}%</p>` : '';
  corpo.innerHTML = `
    <p class="atualizacao__titulo">${escapar(d.titulo)}</p>
    <p class="atualizacao__texto">${escapar(d.texto)}</p>
    ${barra}
    ${notas ? `<p class="atualizacao__rotulo">Novidades</p><ul class="atualizacao__notas">${notas}</ul>` : ''}`;

  const botoes = [];
  const ocupado = FASES_ATIVAS.has(e.fase);
  if (e.fase === 'disponivel') botoes.push(['atualizacao-baixar', 'Baixar e atualizar', 'btn--primary']);
  if (e.fase === 'pronta') botoes.push(['atualizacao-aplicar', 'Atualizar agora', 'btn--primary']);
  if (e.fase === 'erro' && e.versao_nova) botoes.push(['atualizacao-baixar', 'Tentar de novo', 'btn--primary']);
  if (['disponivel', 'pronta'].includes(e.fase)) botoes.push(['atualizacao-lembrar', 'Lembrar mais tarde', 'btn--secondary']);
  if (e.url_novidades && ['disponivel', 'pronta'].includes(e.fase)) botoes.push(['atualizacao-novidades', 'Ver novidades', 'btn--secondary']);
  if (e.configurado) botoes.push(['atualizacao-verificar', 'Verificar agora', 'btn--secondary', ocupado]);
  if (e.pode_restaurar) botoes.push(['atualizacao-restaurar', 'Voltar à versão anterior', 'btn--secondary', ocupado]);
  acoes.innerHTML = botoes.map(([acao, rotulo, classe, off]) =>
    `<button type="button" class="btn ${classe}" data-action="${acao}" ${off ? 'disabled' : ''}>${escapar(rotulo)}</button>`).join('');
}

async function atualizarEstado() {
  try {
    estado = await requisicao('/estado');
  } catch (falha) {
    return;
  }
  atualizarAviso();
  desenhar();
  clearTimeout(temporizador);
  const modalAberto = !el('modal-atualizacoes')?.classList.contains('hidden');
  if (FASES_ATIVAS.has(estado.fase) || (modalAberto && estado.fase === 'baixando')) {
    temporizador = setTimeout(atualizarEstado, 700);
  }
}

export async function iniciarAtualizacoes() {
  // Espera o servidor fazer a 1ª verificação (acontece em segundo plano ~8 s depois de abrir).
  await atualizarEstado();
  setInterval(atualizarEstado, 5 * 60 * 1000);
  try {
    const { novidade } = await requisicao('/novidade');
    if (novidade) toast('Atualização concluída', `O sistema foi atualizado da versão ${novidade.de} para a ${novidade.para}.`, 'success');
  } catch (falha) { /* silencioso */ }
}

export async function abrirAtualizacoes() {
  abrirModal('modal-atualizacoes');
  await atualizarEstado();
}

async function acao(caminho, mensagemErro) {
  try {
    const r = await requisicao(caminho, 'POST');
    if (r.fase) estado = r;
    desenhar();
    atualizarEstado();
    return r;
  } catch (falha) {
    toast(mensagemErro, falha.message, 'error');
    return null;
  }
}

export const verificarAtualizacao = () => acao('/verificar', 'Não foi possível verificar');
export const baixarAtualizacao = () => acao('/baixar', 'Não foi possível baixar');
export async function aplicarAtualizacao() {
  const r = await acao('/aplicar', 'Não foi possível instalar');
  if (r?.message) toast('Instalando', r.message, 'info');
}
export async function restaurarVersaoAnterior() {
  const r = await acao('/restaurar', 'Não foi possível voltar');
  if (r?.message) toast('Voltando', r.message, 'info');
}
export function lembrarMaisTarde() {
  lembreteDispensado = true;
  atualizarAviso();
  return acao('/lembrar', 'Não foi possível adiar');
}
export function verNovidades() {
  if (estado?.url_novidades) window.open(estado.url_novidades, '_blank', 'noopener');
}
