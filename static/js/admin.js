/**
 * Setores, Conta Mestre e etiquetas pré-salvas.
 * Mantido separado de structure.js/auth.js porque atravessa os dois: setor
 * aparece no cadastro (antes do login) e na pasta/gestão de usuários (depois).
 */

import { api } from './api.js';
import {
  state, el, escapar, valor, definirValor, nomeDoUsuario, avatarHTML,
} from './state.js';
import { toast, abrirModal, confirmar, aplicarPerfil } from './ui.js';

/* ---------- Setores ---------- */

/**
 * Preenche um <select> com uma lista de setores. `vazio` é o texto da opção em
 * branco (ou null para omiti-la). Se `lista` não for passada, usa todos os
 * setores conhecidos (`state.dados.setores`) — toda pasta é, em algum momento,
 * um desses nomes, já que o próprio nome da pasta vira setor quando nenhum é escolhido.
 */
export function preencherSelectSetor(selectId, valorAtual = '', vazio = 'Usar o nome da pasta como setor', lista = null) {
  const select = el(selectId);
  if (!select) return;

  const opcoes = lista || state.dados.setores || [];
  select.innerHTML = (vazio !== null ? `<option value="">${escapar(vazio)}</option>` : '')
    + opcoes.map((nome) => `<option value="${escapar(nome)}">${escapar(nome)}</option>`).join('');
  select.value = valorAtual || '';
}

/** Fluxo de cadastro roda antes do login: busca os setores direto da API pública. */
export async function carregarSetoresParaCadastro() {
  const select = el('cad-setor');
  if (!select) return;
  try {
    const resposta = await api.setores();
    state.dados.setores = resposta.setores || [];
  } catch (erro) { /* mantém o que já tinha em cache, se algo houver */ }
  preencherSelectSetor('cad-setor', '', null);
}

export async function criarSetorInline(inputId, selectId) {
  const nome = valor(inputId);
  if (!nome) {
    toast('Informe o nome do setor', '', 'warning');
    return;
  }
  try {
    const resposta = await api.adicionarSetor(nome);
    state.dados.setores = resposta.setores || state.dados.setores;
    definirValor(inputId, '');
    preencherSelectSetor(selectId, nome, selectId === 'cad-setor' ? null : undefined);
    toast('Setor criado', nome);
  } catch (erro) { /* toast já exibido */ }
}

/* ---------- Etiquetas pré-salvas ---------- */
export function renderPickerEtiquetas() {
  const container = el('tarefa-etiquetas-salvas');
  if (!container) return;

  const etiquetas = state.dados.etiquetas_salvas || [];
  if (!etiquetas.length) {
    container.innerHTML = '<span class="muted-note">Nenhuma etiqueta salva ainda — crie uma abaixo.</span>';
    return;
  }

  container.innerHTML = etiquetas.map((etiqueta) => `
    <button type="button" class="chip" data-action="pick-etiqueta" data-nome="${escapar(etiqueta.nome)}" data-cor="${escapar(etiqueta.cor)}">
      <i class="ph-fill ph-circle" style="color:${escapar(etiqueta.cor)}"></i>${escapar(etiqueta.nome)}
    </button>`).join('');
}

export function selecionarEtiquetaSalva(nome, cor) {
  definirValor('tarefa-etiqueta-nome', nome);
  definirValor('tarefa-etiqueta-cor', cor);
  const preview = el('tarefa-etiqueta-preview');
  if (preview) preview.style.background = cor;
}

export async function salvarEtiquetaAtual() {
  const nome = valor('tarefa-etiqueta-nome');
  if (!nome) {
    toast('Digite o nome da etiqueta antes', 'Escreva a etiqueta no campo ao lado para poder salvá-la.', 'warning');
    return;
  }
  const cor = valor('tarefa-etiqueta-cor') || '#94A3B8';
  try {
    const resposta = await api.salvarEtiqueta(nome, cor);
    state.dados.etiquetas_salvas = resposta.etiquetas || state.dados.etiquetas_salvas;
    renderPickerEtiquetas();
    toast('Etiqueta salva', `"${nome}" já pode ser reutilizada em outras demandas.`);
  } catch (erro) { /* toast já exibido */ }
}

/* ---------- Gestão de usuários (Conta Mestre) ---------- */
function chipsDeSetores(usuario) {
  if (usuario.conta_mestre) {
    return '<p class="muted-note"><i class="ph-fill ph-crown-simple"></i> Acesso total — enxerga todos os setores.</p>';
  }

  const setores = state.dados.setores || [];
  if (!setores.length) {
    return '<p class="muted-note">Nenhum setor cadastrado ainda — crie uma pasta ou adicione um setor no cadastro.</p>';
  }

  const setoresUsuario = usuario.setores || [];
  return `<div class="chips">${setores.map((nome) => {
    const ativo = setoresUsuario.includes(nome);
    return `<button type="button" class="chip ${ativo ? 'is-on' : ''}" data-action="admin-toggle-setor" data-id="${escapar(usuario.id)}" data-setor="${escapar(nome)}">
        <i class="ph ${ativo ? 'ph-check' : 'ph-plus'}"></i>${escapar(nome)}
      </button>`;
  }).join('')}</div>`;
}

function linhaUsuario(usuario) {
  return `
    <div class="admin-user" data-id="${escapar(usuario.id)}">
      <div class="admin-user__top">
        ${avatarHTML(nomeDoUsuario(usuario), usuario.foto_perfil, 'sm')}
        <div class="admin-user__info">
          <p class="admin-user__name">${escapar(nomeDoUsuario(usuario))}</p>
          <p class="admin-user__email">${escapar(usuario.email)}</p>
        </div>
        <label class="switch" title="Conta Mestre: acessa e gerencia tudo, de todos os setores">
          <input type="checkbox" data-action="admin-mestre" data-id="${escapar(usuario.id)}" ${usuario.conta_mestre ? 'checked' : ''}>
          <span class="switch__track"><span class="switch__thumb"></span></span>
          <span class="switch__label">Mestre</span>
        </label>
      </div>
      <div class="admin-user__setores">${chipsDeSetores(usuario)}</div>
    </div>`;
}

/** Explica, no topo do modal, a regra que mais confunde: o setor só limita contas
 * COMUNS - quem é Mestre enxerga tudo, não importa o setor. Quando todas as contas
 * são Mestre (o estado de uma base migrada), avisa que o setor ainda não limita ninguém. */
function avisoDeContas(usuarios) {
  const total = usuarios.length;
  const mestres = usuarios.filter((u) => u.conta_mestre).length;
  if (total > 1 && mestres === total) {
    return `
      <div class="admin-aviso admin-aviso--alerta">
        <i class="ph-fill ph-warning"></i>
        <div>
          <strong>Todas as ${total} contas estão como Mestre</strong>
          <p>Conta Mestre enxerga todos os setores, então o setor ainda não limita ninguém. Para uma pessoa ver só o setor dela, desligue <b>Mestre</b> na conta dela e marque os setores.</p>
        </div>
      </div>`;
  }
  return `
    <div class="admin-aviso">
      <i class="ph-fill ph-info"></i>
      <div>
        <strong>${mestres} de ${total} contas são Mestre</strong>
        <p>Só contas comuns são limitadas pelos setores marcados. Quem é Mestre enxerga e gerencia tudo.</p>
      </div>
    </div>`;
}

export function renderGerenciarUsuarios() {
  const container = el('admin-usuarios-lista');
  if (!container) return;
  const usuarios = [...(state.dados.usuarios || [])]
    .sort((a, b) => nomeDoUsuario(a).localeCompare(nomeDoUsuario(b), 'pt-BR'));
  const aviso = el('admin-usuarios-aviso');
  if (aviso) aviso.innerHTML = avisoDeContas(usuarios);
  container.innerHTML = usuarios.map(linhaUsuario).join('');
}

export function abrirGerenciarUsuarios() {
  if (!state.usuario?.conta_mestre) return;
  renderGerenciarUsuarios();
  abrirModal('modal-usuarios');
}

/** Concede ou revoga o acesso de um usuário a UM setor (checkbox/chip), preservando os demais. */
export async function alternarSetorUsuario(usuarioId, nomeSetor) {
  const alvo = state.dados.usuarios.find((u) => u.id === usuarioId);
  if (!alvo) return;

  const atuais = alvo.setores || [];
  const concedendo = !atuais.includes(nomeSetor);
  const novaLista = concedendo ? [...atuais, nomeSetor] : atuais.filter((s) => s !== nomeSetor);

  try {
    const resposta = await api.gerenciarUsuario(usuarioId, { setores: novaLista });
    alvo.setores = resposta.usuario.setores;
    renderGerenciarUsuarios();
    toast(concedendo ? 'Acesso concedido' : 'Acesso removido', `${nomeSetor} · ${nomeDoUsuario(alvo)}`);
  } catch (erro) {
    renderGerenciarUsuarios(); // desfaz a mudança visual se a API recusou
  }
}

export async function alternarContaMestreUsuario(usuarioId, valorAtivo) {
  if (usuarioId === state.usuario?.id && !valorAtivo) {
    const ok = await confirmar({
      titulo: 'Remover seu próprio acesso de mestre?',
      texto: 'Você vai perder a visão de todos os setores imediatamente. Outra Conta Mestre precisará te promover de novo se for engano.',
      acao: 'Remover meu acesso',
      perigo: true,
    });
    if (!ok) {
      renderGerenciarUsuarios();
      return;
    }
  }

  try {
    const resposta = await api.gerenciarUsuario(usuarioId, { conta_mestre: valorAtivo });
    const alvo = state.dados.usuarios.find((u) => u.id === usuarioId);
    if (alvo) alvo.conta_mestre = resposta.usuario.conta_mestre;
    if (usuarioId === state.usuario?.id) {
      state.usuario.conta_mestre = resposta.usuario.conta_mestre;
      aplicarPerfil(state.usuario); // atualiza o crachá/menu do próprio usuário na hora
    }
    renderGerenciarUsuarios();
    toast(valorAtivo ? 'Promovido a Conta Mestre' : 'Rebaixado a conta comum');
  } catch (erro) {
    renderGerenciarUsuarios();
  }
}

/* ---------- Promoção/rebaixamento de Conta Mestre feitos na lista ---------- */
document.addEventListener('change', (evento) => {
  const caixaMestre = evento.target.closest('[data-action="admin-mestre"]');
  if (caixaMestre) {
    alternarContaMestreUsuario(caixaMestre.dataset.id, caixaMestre.checked);
  }
});
