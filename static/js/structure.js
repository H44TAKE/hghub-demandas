/**
 * Estrutura organizacional: pastas, projetos, tarefas e o editor de demandas.
 * Também exporta as ações de tarefa reaproveitadas pelo quadro e pelo calendário.
 */

import { api, carregarDados } from './api.js';
import {
  state, el, qs, valor, definirValor, mostrar, escapar, cortar, plural,
  avatarHTML, dataBR, paraData, venceEm, todasAsTarefas, buscarTarefa,
  metaPrioridade, nomeDoUsuario, nomeDoAutor, PALETA_PASTAS, marcarInvalido, limparInvalido,
} from './state.js';
import {
  toast, abrirModal, fecharModal, confirmar, atualizarViewAtual, renderNotificacoes,
  registrarGuardaFechamento,
} from './ui.js';
import { preencherSelectSetor, renderPickerEtiquetas } from './admin.js';
import { criarAutocompletePlaca, recarregarDemandas } from './manutencao.js';

/** Recarrega os dados do servidor e redesenha o que está na tela. */
export async function sincronizar() {
  await carregarDados(state);
  renderNotificacoes();
  atualizarViewAtual();
}

/* ============================================================
   FILTROS
   ============================================================ */
export function lerFiltros() {
  state.filtros = {
    busca: valor('filtro-busca').toLowerCase(),
    status: valor('filtro-status'),
    prioridade: valor('filtro-prioridade'),
    responsavel: valor('filtro-responsavel'),
  };
}

export function aplicarFiltros() {
  lerFiltros();
  renderEstrutura();
}

export function limparFiltros() {
  ['filtro-busca', 'filtro-status', 'filtro-prioridade', 'filtro-responsavel']
    .forEach((id) => definirValor(id, ''));
  aplicarFiltros();
}

function temFiltro() {
  const { busca, status, prioridade, responsavel } = state.filtros;
  return Boolean(busca || status || prioridade || responsavel);
}

function passaNosFiltros(tarefa) {
  const { busca, status, prioridade, responsavel } = state.filtros;

  if (busca) {
    const alvo = [tarefa.titulo, tarefa.responsavel_nome, tarefa.cidade, tarefa.descricao, tarefa.etiqueta?.nome]
      .filter(Boolean).join(' ').toLowerCase();
    if (!alvo.includes(busca)) return false;
  }
  if (status && tarefa.status?.nome !== status) return false;
  if (prioridade && tarefa.prioridade !== prioridade) return false;
  if (responsavel && tarefa.responsavel_id !== responsavel) return false;
  return true;
}

function preencherFiltroResponsaveis() {
  const select = el('filtro-responsavel');
  if (!select) return;

  const selecionado = select.value;
  select.innerHTML = '<option value="">Todos os responsáveis</option>'
    + state.dados.usuarios.map((u) =>
      `<option value="${escapar(u.id)}">${escapar(nomeDoUsuario(u))}</option>`).join('');
  select.value = selecionado;
}

/* ============================================================
   RENDERIZAÇÃO
   ============================================================ */
function badgesDaTarefa(tarefa) {
  const badges = [];

  if (tarefa.prioridade) {
    const meta = metaPrioridade(tarefa.prioridade);
    badges.push(`<span class="badge ${meta.badge}"><i class="ph ${meta.icone}"></i>${escapar(tarefa.prioridade)}</span>`);
  }
  if (tarefa.etiqueta?.nome) {
    badges.push(`<span class="badge badge--solid" style="background:${escapar(tarefa.etiqueta.cor || '#94A3B8')}">${escapar(tarefa.etiqueta.nome)}</span>`);
  }
  if (tarefa.bloqueada && !tarefa.concluida) {
    badges.push('<span class="badge badge--neutral"><i class="ph ph-lock-simple"></i>Bloqueada</span>');
  }
  return badges.join('');
}

function datasDaTarefa(tarefa) {
  const pills = [];
  const atrasada = tarefa.atrasada && !tarefa.concluida;

  if (tarefa.data_previsao) {
    pills.push(`<span class="date-pill date-pill--plan"><i class="ph ph-calendar-plus"></i>Prev. ${dataBR(tarefa.data_previsao)}</span>`);
  }

  const vencimento = venceEm(tarefa);
  if (vencimento) {
    const classe = tarefa.concluida ? 'date-pill--plan' : (atrasada ? 'date-pill--late' : 'date-pill--due');
    const icone = atrasada ? 'ph-warning' : 'ph-hourglass-medium';
    pills.push(`<span class="date-pill ${classe}"><i class="ph ${icone}"></i>Venc. ${dataBR(vencimento)}</span>`);
  }

  if (tarefa.concluida && tarefa.data_conclusao) {
    pills.push(`<span class="date-pill date-pill--done"><i class="ph ph-check-circle"></i>Concl. ${dataBR(tarefa.data_conclusao)}</span>`);
  }

  return pills.length ? `<div class="task__dates">${pills.join('')}</div>` : '';
}

function htmlTarefa(tarefa) {
  const meta = metaPrioridade(tarefa.prioridade);
  const classes = [
    'task',
    tarefa.prioridade === 'Alta' ? 'task--alta' : '',
    tarefa.prioridade === 'Média' ? 'task--media' : '',
    tarefa.prioridade === 'Baixa' ? 'task--baixa' : '',
    tarefa.concluida ? 'is-done' : '',
    tarefa.bloqueada && !tarefa.concluida ? 'is-blocked' : '',
  ].filter(Boolean).join(' ');

  const proximoStatus = tarefa.concluida ? 'Pendente' : 'Concluída';
  const badges = badgesDaTarefa(tarefa);

  return `
    <div class="${classes}" data-task-id="${escapar(tarefa.id)}">
      <button class="check ${tarefa.concluida ? 'is-done' : ''}"
              data-action="toggle-task" data-id="${escapar(tarefa.id)}" data-status="${proximoStatus}"
              aria-label="${tarefa.concluida ? 'Reabrir demanda' : 'Concluir demanda'}">
        <i class="ph-fill ph-check"></i>
      </button>

      <div class="task__body">
        <div class="task__line">
          <span class="task__title">${escapar(tarefa.titulo)}</span>
          ${badges}
        </div>

        <div class="task__meta">
          <span class="task__meta-item">
            ${avatarHTML(tarefa.responsavel_nome, tarefa.responsavel_foto, 'xs')}
            <span>${escapar(tarefa.responsavel_nome || 'Não atribuído')}</span>
          </span>
          ${tarefa.cidade ? `<span class="task__meta-item"><i class="ph ph-map-pin"></i>${escapar(tarefa.cidade)}</span>` : ''}
          ${tarefa.comentarios?.length ? `<span class="task__meta-item"><i class="ph ph-chat-circle-dots"></i>${tarefa.comentarios.length}</span>` : ''}
          ${tarefa.anexos?.length ? `<span class="task__meta-item"><i class="ph ph-paperclip"></i>${tarefa.anexos.length}</span>` : ''}
        </div>

        ${datasDaTarefa(tarefa)}
      </div>

      <div class="task__actions">
        <button class="icon-btn" data-action="edit-task" data-id="${escapar(tarefa.id)}" aria-label="Editar demanda">
          <i class="ph ph-pencil-simple"></i>
        </button>
        <button class="icon-btn icon-btn--danger" data-action="delete-task" data-id="${escapar(tarefa.id)}" aria-label="Excluir demanda">
          <i class="ph ph-trash"></i>
        </button>
      </div>
    </div>`;
}

function htmlProjeto(projeto, tarefasVisiveis) {
  const recolhido = state.recolhidos.projetos.has(projeto.id);
  const total = projeto.tarefas.length;
  const concluidas = projeto.tarefas.filter((t) => t.concluida).length;
  const pendentes = total - concluidas;
  const progresso = projeto.progresso || 0;

  const corpo = tarefasVisiveis.length
    ? tarefasVisiveis.map(htmlTarefa).join('')
    : `<div class="empty empty--sm">
         <p class="empty__text">${temFiltro() ? 'Nenhuma demanda corresponde aos filtros.' : 'Nenhuma demanda neste projeto ainda.'}</p>
       </div>`;

  return `
    <article class="project">
      <div class="project__head ${recolhido ? 'is-collapsed' : ''}" data-action="toggle-project" data-id="${escapar(projeto.id)}">
        <i class="ph ph-caret-down project__caret"></i>
        <span class="project__name">${escapar(projeto.nome)}</span>

        <div class="project__counts">
          <span class="badge badge--neutral num" title="Total de demandas"><i class="ph ph-list-checks"></i>${total}</span>
          ${concluidas ? `<span class="badge badge--green num" title="Concluídas"><i class="ph ph-check"></i>${concluidas}</span>` : ''}
          ${pendentes ? `<span class="badge badge--gold num" title="Em aberto"><i class="ph ph-clock"></i>${pendentes}</span>` : ''}
        </div>

        <div class="project__progress">
          <div class="progress"><div class="progress__fill ${progresso === 100 ? 'is-complete' : ''}" style="width:${progresso}%"></div></div>
          <span class="project__pct">${progresso}%</span>
        </div>

        <div class="project__actions" data-action="noop">
          <button class="btn btn--secondary btn--sm" data-action="new-task" data-projeto="${escapar(projeto.id)}">
            <i class="ph ph-plus"></i> Demanda
          </button>
          <button class="icon-btn" data-action="edit-project" data-id="${escapar(projeto.id)}" data-nome="${escapar(projeto.nome)}" aria-label="Renomear projeto">
            <i class="ph ph-pencil-simple"></i>
          </button>
          <button class="icon-btn icon-btn--danger" data-action="delete-project" data-id="${escapar(projeto.id)}" aria-label="Excluir projeto">
            <i class="ph ph-trash"></i>
          </button>
        </div>
      </div>

      <div class="project__body ${recolhido ? 'is-hidden' : ''}">${corpo}</div>
    </article>`;
}

function htmlPasta(pasta, projetosVisiveis) {
  const recolhida = state.recolhidos.pastas.has(pasta.id);
  const favorito = state.usuario?.favoritos?.includes(`pasta_${pasta.id}`);
  const cor = pasta.cor_fundo || '#0D3B8C';
  const totalProjetos = pasta.projetos.length;
  const totalTarefas = pasta.projetos.reduce((soma, p) => soma + p.tarefas.length, 0);

  const corpo = projetosVisiveis.length
    ? projetosVisiveis.map((item) => htmlProjeto(item.projeto, item.tarefas)).join('')
    : `<div class="empty empty--sm">
         <div class="empty__icon"><i class="ph ph-stack"></i></div>
         <p class="empty__text">${temFiltro() ? 'Nenhum projeto corresponde aos filtros.' : 'Esta pasta ainda não tem projetos.'}</p>
       </div>`;

  return `
    <section class="folder">
      <div class="folder__head ${recolhida ? 'is-collapsed' : ''}" data-action="toggle-folder" data-id="${escapar(pasta.id)}">
        <span class="folder__accent" style="background:${escapar(cor)}"></span>
        <i class="ph ph-caret-down folder__caret"></i>
        <span class="folder__icon" style="background:${escapar(cor)}"><i class="ph-fill ph-folder"></i></span>

        <div class="folder__info">
          <h3 class="folder__name">${escapar(pasta.nome)}</h3>
          <p class="folder__meta">${totalProjetos} ${plural(totalProjetos, 'projeto', 'projetos')} · ${totalTarefas} ${plural(totalTarefas, 'demanda', 'demandas')}</p>
        </div>

        <div class="folder__actions" data-action="noop">
          <button class="icon-btn icon-btn--gold ${favorito ? 'is-on' : ''}" data-action="toggle-fav" data-item="pasta_${escapar(pasta.id)}" aria-label="Favoritar pasta">
            <i class="${favorito ? 'ph-fill' : 'ph'} ph-star"></i>
          </button>
          <button class="btn btn--secondary btn--sm" data-action="new-project" data-pasta="${escapar(pasta.id)}">
            <i class="ph ph-plus"></i> Projeto
          </button>
          <button class="icon-btn" data-action="edit-folder" data-id="${escapar(pasta.id)}" data-nome="${escapar(pasta.nome)}" data-cor="${escapar(cor)}" data-setor="${escapar(pasta.setor || '')}" aria-label="Editar pasta">
            <i class="ph ph-pencil-simple"></i>
          </button>
          <button class="icon-btn icon-btn--danger" data-action="delete-folder" data-id="${escapar(pasta.id)}" aria-label="Excluir pasta">
            <i class="ph ph-trash"></i>
          </button>
        </div>
      </div>

      <div class="folder__body ${recolhida ? 'is-hidden' : ''}">${corpo}</div>
    </section>`;
}

function renderFavoritos() {
  const bloco = el('favoritos-bloco');
  const lista = el('favoritos-lista');
  if (!bloco || !lista) return;

  const favoritos = (state.usuario?.favoritos || [])
    .map((chave) => {
      const [tipo, id] = chave.split('_');
      if (tipo !== 'pasta') return null;
      const pasta = state.dados.pastas.find((p) => p.id === id);
      return pasta ? { chave, pasta } : null;
    })
    .filter(Boolean);

  if (!favoritos.length) {
    bloco.classList.add('hidden');
    return;
  }

  bloco.classList.remove('hidden');
  lista.innerHTML = favoritos.map(({ chave, pasta }) => `
    <div class="fav-card">
      <span class="folder__icon" style="background:${escapar(pasta.cor_fundo || '#0D3B8C')};width:26px;height:26px;font-size:14px">
        <i class="ph-fill ph-folder"></i>
      </span>
      <div class="fav-card__info">
        <p class="fav-card__name">${escapar(pasta.nome)}</p>
        <p class="fav-card__meta">${pasta.projetos.length} ${plural(pasta.projetos.length, 'projeto', 'projetos')}</p>
      </div>
      <button class="icon-btn icon-btn--gold is-on" data-action="toggle-fav" data-item="${escapar(chave)}" aria-label="Remover dos favoritos">
        <i class="ph-fill ph-star"></i>
      </button>
    </div>`).join('');
}

export function renderEstrutura() {
  const container = el('lista-pastas');
  if (!container) return;

  preencherFiltroResponsaveis();
  renderFavoritos();

  if (!state.dados.pastas.length) {
    container.innerHTML = `
      <div class="card empty">
        <div class="empty__icon"><i class="ph ph-folder-notch-plus"></i></div>
        <p class="empty__title">Comece pela primeira pasta</p>
        <p class="empty__text">Pastas representam áreas ou setores. Dentro delas ficam os projetos e, neles, as demandas do dia a dia.</p>
        <button class="btn btn--primary" data-action="new-folder"><i class="ph ph-plus"></i> Criar pasta</button>
      </div>`;
    return;
  }

  const filtrando = temFiltro();
  const blocos = [];

  state.dados.pastas.forEach((pasta) => {
    const projetos = (pasta.projetos || []).map((projeto) => ({
      projeto,
      tarefas: (projeto.tarefas || []).filter(passaNosFiltros),
    })).filter((item) => !filtrando || item.tarefas.length > 0);

    if (filtrando && !projetos.length) return;
    blocos.push(htmlPasta(pasta, projetos));
  });

  container.innerHTML = blocos.length ? blocos.join('') : `
    <div class="card empty">
      <div class="empty__icon"><i class="ph ph-magnifying-glass"></i></div>
      <p class="empty__title">Nenhuma demanda encontrada</p>
      <p class="empty__text">Ajuste a busca ou limpe os filtros para ver toda a estrutura novamente.</p>
      <button class="btn btn--secondary" data-action="clear-filters"><i class="ph ph-arrow-counter-clockwise"></i> Limpar filtros</button>
    </div>`;
}

/* ============================================================
   RECOLHER / EXPANDIR
   ============================================================ */
export function alternarPasta(id) {
  const conjunto = state.recolhidos.pastas;
  if (conjunto.has(id)) conjunto.delete(id); else conjunto.add(id);
  renderEstrutura();
}

export function alternarProjeto(id) {
  const conjunto = state.recolhidos.projetos;
  if (conjunto.has(id)) conjunto.delete(id); else conjunto.add(id);
  renderEstrutura();
}

export async function alternarFavorito(item) {
  if (!state.usuario) return;
  try {
    const resposta = await api.alternarFavorito(state.usuario.id, item);
    state.usuario.favoritos = resposta.favoritos;
    renderEstrutura();
  } catch (erro) { /* toast já exibido */ }
}

/* ============================================================
   PASTA
   ============================================================ */
function montarPaleta(corAtual) {
  const container = el('pasta-cores');
  if (!container) return;

  container.innerHTML = PALETA_PASTAS.map((cor) => `
    <button type="button" class="swatch ${cor.toLowerCase() === String(corAtual).toLowerCase() ? 'is-on' : ''}"
            style="background:${cor}" data-cor="${cor}" aria-label="Cor ${cor}"></button>`).join('');

  container.onclick = (evento) => {
    const botao = evento.target.closest('.swatch');
    if (!botao) return;
    definirValor('pasta-cor', botao.dataset.cor);
    container.querySelectorAll('.swatch').forEach((s) => s.classList.remove('is-on'));
    botao.classList.add('is-on');
  };
}

export function abrirModalPasta(id = '', nome = '', cor = '#0D3B8C', setor = '') {
  el('titulo-pasta').textContent = id ? 'Editar pasta' : 'Nova pasta';
  definirValor('pasta-id', id);
  definirValor('pasta-nome', nome);
  definirValor('pasta-cor', cor);
  montarPaleta(cor);

  // Reatribuir o setor de uma pasta já existente é coisa de Conta Mestre; ao
  // criar uma nova, uma conta comum escolhe entre os próprios setores.
  const campoSetor = el('campo-pasta-setor');
  const mestre = Boolean(state.usuario?.conta_mestre);
  if (campoSetor) {
    if (mestre) {
      campoSetor.classList.remove('hidden');
      preencherSelectSetor('pasta-setor', setor);
    } else if (!id) {
      campoSetor.classList.remove('hidden');
      // Sem opção em branco aqui: uma conta comum só pode escolher entre os
      // setores que ela mesma já tem acesso (o backend recusaria qualquer outro).
      preencherSelectSetor('pasta-setor', setor, null, state.usuario?.setores || []);
    } else {
      campoSetor.classList.add('hidden');
    }
  }

  abrirModal('modal-pasta');
}

export async function salvarPasta() {
  const nome = valor('pasta-nome');
  if (!nome) {
    toast('Nome obrigatório', 'Dê um nome para a pasta.', 'warning');
    marcarInvalido('pasta-nome', 'Dê um nome para a pasta.');
    el('pasta-nome').focus();
    return;
  }
  limparInvalido('pasta-nome');

  const id = valor('pasta-id');
  const dados = { id, nome, cor: valor('pasta-cor') };
  const campoSetorVisivel = !el('campo-pasta-setor')?.classList.contains('hidden');
  if (campoSetorVisivel) dados.setor = valor('pasta-setor');

  try {
    await api.salvarPasta(dados);
    fecharModal('modal-pasta');
    await sincronizar();
    toast(id ? 'Pasta atualizada' : 'Pasta criada', nome);
  } catch (erro) { /* toast já exibido */ }
}

export async function excluirPasta(id) {
  const pasta = state.dados.pastas.find((p) => p.id === id);
  const ok = await confirmar({
    titulo: 'Excluir pasta',
    texto: `A pasta "${pasta?.nome || ''}" e todos os projetos e demandas dentro dela serão removidos permanentemente.`,
    acao: 'Excluir pasta',
    perigo: true,
  });
  if (!ok) return;

  try {
    await api.excluirPasta(id);
    await sincronizar();
    toast('Pasta excluída', 'A estrutura foi atualizada.');
  } catch (erro) { /* toast já exibido */ }
}

/* ============================================================
   PROJETO
   ============================================================ */
export function abrirModalProjeto(pastaId, id = '', nome = '') {
  el('titulo-projeto').textContent = id ? 'Renomear projeto' : 'Novo projeto';
  definirValor('projeto-id', id);
  definirValor('projeto-pasta-id', pastaId || '');
  definirValor('projeto-nome', nome);
  abrirModal('modal-projeto');
}

export async function salvarProjeto() {
  const nome = valor('projeto-nome');
  if (!nome) {
    toast('Nome obrigatório', 'Dê um nome para o projeto.', 'warning');
    marcarInvalido('projeto-nome', 'Dê um nome para o projeto.');
    el('projeto-nome').focus();
    return;
  }
  limparInvalido('projeto-nome');

  const id = valor('projeto-id');
  try {
    await api.salvarProjeto({ id, id_pasta: valor('projeto-pasta-id'), nome });
    fecharModal('modal-projeto');
    await sincronizar();
    toast(id ? 'Projeto renomeado' : 'Projeto criado', nome);
  } catch (erro) { /* toast já exibido */ }
}

export async function excluirProjeto(id) {
  const ok = await confirmar({
    titulo: 'Excluir projeto',
    texto: 'Todas as demandas deste projeto serão removidas permanentemente.',
    acao: 'Excluir projeto',
    perigo: true,
  });
  if (!ok) return;

  try {
    await api.excluirProjeto(id);
    await sincronizar();
    toast('Projeto excluído', 'A estrutura foi atualizada.');
  } catch (erro) { /* toast já exibido */ }
}

/* ============================================================
   EDITOR DE DEMANDA
   ============================================================ */
function preencherResponsaveis(selecionado = '') {
  const select = el('tarefa-responsavel');
  if (!select) return;
  select.innerHTML = '<option value="">Sem responsável definido</option>'
    + state.dados.usuarios.map((u) =>
      `<option value="${escapar(u.id)}">${escapar(nomeDoUsuario(u))}</option>`).join('');
  select.value = selecionado || '';
}

function preencherCidades() {
  const lista = el('lista-cidades');
  if (!lista) return;
  lista.innerHTML = (state.dados.cidades || [])
    .map((cidade) => `<option value="${escapar(cidade)}"></option>`).join('');
}

export function trocarAba(aba) {
  ['anexos', 'comentarios', 'historico'].forEach((nome) => {
    qs(`.tab[data-tab="${nome}"]`)?.classList.toggle('is-active', nome === aba);
    mostrar(`painel-${nome}`, nome === aba);
  });
}

export function alternarAvancado(forcar) {
  const secao = el('tarefa-avancado');
  if (!secao) return;

  const abrindo = forcar !== undefined ? forcar : secao.classList.contains('hidden');
  secao.classList.toggle('hidden', !abrindo);
  el('icone-avancado').className = abrindo ? 'ph ph-caret-up' : 'ph ph-caret-down';
  el('rotulo-avancado').textContent = abrindo ? 'Menos opções' : 'Mais opções';
}

function renderDependencias() {
  const container = el('tarefa-dependencias-lista');
  if (!container) return;

  const atual = state.tarefaEmEdicao;
  const selecionadas = JSON.parse(valor('tarefa-dependencias') || '[]');
  const busca = valor('tarefa-dependencias-busca').toLowerCase();

  const todas = todasAsTarefas().filter((t) => !t.concluida && t.id !== atual?.id);
  if (!todas.length) {
    container.innerHTML = '<span class="muted-note">Nenhuma outra demanda em aberto para vincular.</span>';
    return;
  }

  // As já vinculadas continuam visíveis mesmo fora do filtro, para não "sumir" uma escolha feita.
  const candidatas = todas.filter((t) => selecionadas.includes(t.id)
    || !busca
    || [t.titulo, t.projetoNome, t.pastaNome].filter(Boolean).join(' ').toLowerCase().includes(busca));

  if (!candidatas.length) {
    container.innerHTML = '<span class="muted-note">Nenhuma demanda encontrada para essa busca.</span>';
    return;
  }

  container.innerHTML = candidatas.map((tarefa) => {
    const ativa = selecionadas.includes(tarefa.id);
    return `<button type="button" class="chip ${ativa ? 'is-on' : ''}" data-action="toggle-dep" data-id="${escapar(tarefa.id)}" title="${escapar(tarefa.pastaNome)} · ${escapar(tarefa.projetoNome)}">
        <i class="ph ${ativa ? 'ph-check' : 'ph-link-simple'}"></i>${escapar(cortar(tarefa.titulo, 28))}
      </button>`;
  }).join('');
}

/** Chamada pelo campo de busca do editor de demanda ao filtrar dependências. */
export function filtrarDependencias() {
  renderDependencias();
}

export function alternarDependencia(id) {
  const selecionadas = JSON.parse(valor('tarefa-dependencias') || '[]');
  const indice = selecionadas.indexOf(id);
  if (indice >= 0) selecionadas.splice(indice, 1); else selecionadas.push(id);
  definirValor('tarefa-dependencias', JSON.stringify(selecionadas));
  renderDependencias();
}

function renderAnexos(anexos = []) {
  const container = el('lista-anexos');
  const contador = el('conta-anexos');
  if (!container) return;

  contador.textContent = anexos.length ? String(anexos.length) : '';
  contador.classList.toggle('hidden', !anexos.length);

  container.innerHTML = anexos.length ? anexos.map((anexo) => `
    <div class="file-row">
      <i class="ph ph-file" style="color:var(--text-3);font-size:16px"></i>
      <span class="file-row__name">${escapar(anexo.nome)}</span>
      <span class="comment-row__time">${escapar(dataBR(anexo.data))}</span>
      <a class="icon-btn" href="${escapar(anexo.caminho)}" target="_blank" rel="noopener" aria-label="Baixar anexo">
        <i class="ph ph-download-simple"></i>
      </a>
    </div>`).join('')
    : '<span class="muted-note">Nenhum anexo ainda.</span>';
}

function renderComentarios(comentarios = []) {
  const container = el('lista-comentarios');
  const contador = el('conta-comentarios');
  if (!container) return;

  contador.textContent = comentarios.length ? String(comentarios.length) : '';
  contador.classList.toggle('hidden', !comentarios.length);

  container.innerHTML = comentarios.length ? comentarios.map((comentario) => `
    <div class="comment-row">
      ${avatarHTML(comentario.usuario_nome, comentario.usuario_foto, 'sm')}
      <div style="min-width:0;flex:1">
        <div class="comment-row__head">
          <span class="comment-row__author">${escapar(comentario.usuario_nome)}</span>
          <span class="comment-row__time">${escapar(dataBR(comentario.data))}</span>
        </div>
        <p class="comment-row__text">${escapar(comentario.texto)}</p>
      </div>
    </div>`).join('')
    : '<span class="muted-note">Nenhum comentário ainda.</span>';
}

function renderHistorico(historico = []) {
  const container = el('lista-historico');
  if (!container) return;

  container.innerHTML = historico.length ? historico.slice().reverse().map((registro) => `
    <div class="history-row">
      <span class="history-row__dot"></span>
      <div>
        <p style="font-size:12.5px"><strong>${escapar(registro.usuario_nome)}</strong>
          <span style="color:var(--text-2)">${escapar(registro.acao)}</span></p>
        <p class="history-row__time">${escapar(registro.data)}</p>
      </div>
    </div>`).join('')
    : '<span class="muted-note">Nenhuma alteração registrada.</span>';
}

/* Campo "Veículo" (opcional): só aparece quando o editor é aberto a partir de
 * "Agendar Demanda" (Frota & Manutenção) ou ao editar uma tarefa que já tem
 * `veiculo_placa` - uma demanda comum de Estrutura nunca precisa disso.
 * Reaproveita a MESMA fábrica de autocomplete do Prontuário/Cadastro de
 * Frota (`criarAutocompletePlaca`, em manutencao.js). */
const autocompleteVeiculoTarefa = criarAutocompletePlaca('tarefa-veiculo-placa', 'tarefa-veiculo-placa-sugestoes', (v) => {
  definirValor('tarefa-veiculo-placa', v.placa);
});

function mostrarCampoVeiculo(mostrarCampo, placaPreenchida = '') {
  mostrar('tarefa-veiculo-grupo', mostrarCampo);
  definirValor('tarefa-veiculo-placa', mostrarCampo ? placaPreenchida : '');
  if (!mostrarCampo) autocompleteVeiculoTarefa.fechar();
}

export function filtrarSugestoesVeiculoTarefa() { autocompleteVeiculoTarefa.filtrar(); }
export function navegarSugestaoVeiculoTarefa(direcao) { autocompleteVeiculoTarefa.navegar(direcao); }
export function confirmarSugestaoAtivaVeiculoTarefa() { return autocompleteVeiculoTarefa.confirmarAtivo(); }

/** Instantâneo dos campos do editor de demanda, usado para detectar edição não salva. */
const CAMPOS_TAREFA = [
  'tarefa-titulo', 'tarefa-descricao', 'tarefa-status', 'tarefa-prioridade', 'tarefa-responsavel',
  'tarefa-cidade', 'tarefa-previsao', 'tarefa-vencimento', 'tarefa-inicio-real', 'tarefa-fim-real',
  'tarefa-conclusao', 'tarefa-etiqueta-nome', 'tarefa-etiqueta-cor', 'tarefa-dependencias', 'tarefa-veiculo-placa',
];
let instantaneoTarefa = null;

function capturarEstadoTarefa() {
  return CAMPOS_TAREFA.map((id) => valor(id)).join('');
}

function marcarTarefaComoLimpa() {
  instantaneoTarefa = capturarEstadoTarefa();
}

function tarefaTemAlteracoes() {
  return instantaneoTarefa !== null && capturarEstadoTarefa() !== instantaneoTarefa;
}

registrarGuardaFechamento('modal-tarefa', tarefaTemAlteracoes);

function limparEditor() {
  ['tarefa-titulo', 'tarefa-descricao', 'tarefa-cidade', 'tarefa-previsao', 'tarefa-vencimento',
    'tarefa-inicio-real', 'tarefa-fim-real', 'tarefa-conclusao', 'tarefa-etiqueta-nome',
    'tarefa-dependencias-busca'].forEach((id) => definirValor(id, ''));
  definirValor('tarefa-status', 'Pendente');
  definirValor('tarefa-prioridade', 'Média');
  definirValor('tarefa-dependencias', '[]');
  definirValor('tarefa-etiqueta-cor', '#F5B800');
  el('tarefa-etiqueta-preview').style.background = '#F5B800';
  renderPickerEtiquetas();
  mostrar('tarefa-excluir-btn', false); // só existe algo pra excluir depois que a demanda já foi salva uma vez
}

/**
 * @param {string} projetoId  projeto onde a tarefa nasce.
 * @param {string} [veiculoPlaca]  presente (mesmo `''`) só quando aberto a
 *   partir de "Agendar Demanda" (Frota & Manutenção) - revela o campo
 *   "Veículo" e, se vier uma placa, já a preenche (ex.: clicar em "Agendar"
 *   dentro do Prontuário de um veículo específico). `undefined` (omitido) =
 *   fluxo comum de Estrutura/Demandas, sem o campo.
 */
export function abrirNovaTarefa(projetoId, veiculoPlaca) {
  state.tarefaEmEdicao = null;
  limparEditor();

  const ehDemandaDeManutencao = veiculoPlaca !== undefined;
  el('titulo-tarefa').textContent = ehDemandaDeManutencao ? 'Agendar Demanda' : 'Nova demanda';
  el('subtitulo-tarefa').textContent = ehDemandaDeManutencao
    ? 'Vira uma demanda de verdade: aparece no Kanban, no Calendário e no Prontuário do veículo'
    : 'Descreva o que precisa ser feito';
  definirValor('tarefa-id', '');
  definirValor('tarefa-projeto-id', projetoId || '');
  mostrarCampoVeiculo(ehDemandaDeManutencao, veiculoPlaca || '');

  preencherResponsaveis('');
  preencherCidades();
  renderAnexos([]);
  renderComentarios([]);
  renderHistorico([]);
  renderDependencias();
  trocarAba('anexos');
  alternarAvancado(false);

  marcarTarefaComoLimpa();
  abrirModal('modal-tarefa');
  setTimeout(() => el(ehDemandaDeManutencao && !veiculoPlaca ? 'tarefa-veiculo-placa' : 'tarefa-titulo')?.focus(), 120);
}

export function abrirEdicaoTarefa(id) {
  const tarefa = buscarTarefa(id);
  if (!tarefa) {
    toast('Demanda não encontrada', 'Ela pode ter sido removida por outra pessoa.', 'error');
    return;
  }

  state.tarefaEmEdicao = tarefa;
  limparEditor();

  el('titulo-tarefa').textContent = 'Editar demanda';
  el('subtitulo-tarefa').textContent = `${tarefa.pastaNome} · ${tarefa.projetoNome}`;

  definirValor('tarefa-id', tarefa.id);
  definirValor('tarefa-projeto-id', '');
  mostrar('tarefa-excluir-btn', true);
  el('tarefa-excluir-btn').dataset.id = tarefa.id;
  definirValor('tarefa-titulo', tarefa.titulo);
  definirValor('tarefa-descricao', tarefa.descricao);
  definirValor('tarefa-status', tarefa.status?.nome || 'Pendente');
  definirValor('tarefa-prioridade', tarefa.prioridade || 'Média');
  definirValor('tarefa-cidade', tarefa.cidade || '');
  definirValor('tarefa-previsao', tarefa.data_previsao || '');
  definirValor('tarefa-vencimento', tarefa.data_vencimento || '');
  definirValor('tarefa-inicio-real', tarefa.data_inicio_real || '');
  definirValor('tarefa-fim-real', tarefa.data_fim_real || '');
  // data_conclusao vem como "AAAA-MM-DD HH:MM:SS"; o campo é só de data.
  definirValor('tarefa-conclusao', tarefa.data_conclusao ? tarefa.data_conclusao.slice(0, 10) : '');
  definirValor('tarefa-etiqueta-nome', tarefa.etiqueta?.nome || '');
  definirValor('tarefa-dependencias', JSON.stringify(tarefa.dependencias || []));
  mostrarCampoVeiculo(Boolean(tarefa.veiculo_placa), tarefa.veiculo_placa || '');

  const cor = tarefa.etiqueta?.cor || '#F5B800';
  definirValor('tarefa-etiqueta-cor', cor);
  el('tarefa-etiqueta-preview').style.background = cor;

  preencherResponsaveis(tarefa.responsavel_id || '');
  preencherCidades();
  renderAnexos(tarefa.anexos);
  renderComentarios(tarefa.comentarios);
  renderHistorico(tarefa.historico);
  renderDependencias();
  trocarAba('anexos');

  const temExtras = Boolean(tarefa.anexos?.length || tarefa.comentarios?.length
    || tarefa.historico?.length || tarefa.dependencias?.length);
  alternarAvancado(temExtras);

  marcarTarefaComoLimpa();
  abrirModal('modal-tarefa');
}

export async function salvarTarefa(botao) {
  const titulo = valor('tarefa-titulo');
  if (!titulo) {
    toast('Título obrigatório', 'Descreva o que precisa ser feito.', 'warning');
    marcarInvalido('tarefa-titulo', 'Descreva o que precisa ser feito.');
    el('tarefa-titulo').focus();
    return;
  }
  limparInvalido('tarefa-titulo');

  const veiculoVisivel = !el('tarefa-veiculo-grupo')?.classList.contains('hidden');
  const veiculoPlaca = veiculoVisivel ? valor('tarefa-veiculo-placa').trim().toUpperCase() : '';
  if (veiculoVisivel && !veiculoPlaca) {
    toast('Selecione o veículo', 'Digite a placa e escolha uma das sugestões da lista.', 'warning');
    marcarInvalido('tarefa-veiculo-placa', 'Selecione o veículo desta demanda.');
    el('tarefa-veiculo-placa').focus();
    return;
  }
  limparInvalido('tarefa-veiculo-placa');

  const vencimento = valor('tarefa-vencimento');
  const previsao = valor('tarefa-previsao');

  if (previsao && vencimento && paraData(previsao) > paraData(vencimento)) {
    toast('Datas invertidas', 'A previsão não pode ser depois do vencimento.', 'warning');
    marcarInvalido('tarefa-vencimento', 'Não pode ser antes da previsão.');
    el('tarefa-vencimento').focus();
    return;
  }
  limparInvalido('tarefa-vencimento');

  const dados = {
    id: valor('tarefa-id'),
    id_projeto: valor('tarefa-projeto-id'),
    titulo,
    descricao: el('tarefa-descricao').value.trim(),
    status: valor('tarefa-status'),
    prioridade: valor('tarefa-prioridade'),
    responsavel_id: valor('tarefa-responsavel'),
    cidade: valor('tarefa-cidade'),
    data_previsao: previsao,
    data_vencimento: vencimento,
    data_limite: vencimento,
    data_inicio_real: valor('tarefa-inicio-real'),
    data_fim_real: valor('tarefa-fim-real'),
    data_conclusao: valor('tarefa-conclusao'),
    etiqueta_nome: valor('tarefa-etiqueta-nome'),
    etiqueta_cor: valor('tarefa-etiqueta-cor'),
    dependencias: JSON.parse(valor('tarefa-dependencias') || '[]'),
    veiculo_placa: veiculoPlaca,
    usuario_nome: nomeDoAutor(),
  };

  if (botao) botao.disabled = true;
  try {
    await api.salvarTarefa(dados);
    instantaneoTarefa = null;
    fecharModal('modal-tarefa');
    await sincronizar();
    toast(dados.id ? 'Demanda atualizada' : 'Demanda criada', titulo);
    state.tarefaEmEdicao = null;
    // Tem placa vinculada (veio de "Agendar Demanda" ou de uma edição
    // que já tinha isso) - a tabela dessa aba e a seção do Prontuário usam
    // um endpoint próprio, fora do `sincronizar()` genérico acima.
    if (veiculoPlaca) recarregarDemandas();
  } catch (erro) { /* toast já exibido */ } finally {
    if (botao) botao.disabled = false;
  }
}

export async function excluirTarefa(id) {
  const tarefa = buscarTarefa(id);
  const ok = await confirmar({
    titulo: 'Excluir demanda',
    texto: `"${tarefa?.titulo || 'Esta demanda'}" será removida permanentemente, junto com anexos, comentários e histórico.`,
    acao: 'Excluir demanda',
    perigo: true,
  });
  if (!ok) return;

  try {
    await api.excluirTarefa(id);
    instantaneoTarefa = null;
    fecharModal('modal-tarefa');
    await sincronizar();
    toast('Demanda excluída', 'A lista foi atualizada.');
    if (tarefa?.veiculo_placa) recarregarDemandas();
  } catch (erro) { /* toast já exibido */ }
}

export async function alternarStatusTarefa(id, novoStatus) {
  const tarefa = buscarTarefa(id);
  if (tarefa?.bloqueada && novoStatus === 'Concluída') {
    toast('Demanda bloqueada', 'Conclua primeiro as dependências vinculadas a ela.', 'warning');
    return;
  }

  try {
    await api.alterarStatus({ id, status: novoStatus, usuario_nome: nomeDoAutor() });
    await sincronizar();
    if (tarefa?.veiculo_placa) recarregarDemandas();
  } catch (erro) { /* toast já exibido */ }
}

/* ---------- Anexos e comentários ---------- */
export function enviarAnexo(evento) {
  const arquivo = evento.target.files?.[0];
  if (!arquivo) return;

  if (!state.tarefaEmEdicao) {
    toast('Salve a demanda antes', 'Anexos só podem ser adicionados depois que a demanda existe.', 'warning');
    evento.target.value = '';
    return;
  }

  if (arquivo.size > 24 * 1024 * 1024) {
    toast('Arquivo muito grande', 'O limite por anexo é de 24 MB.', 'warning');
    evento.target.value = '';
    return;
  }

  const leitor = new FileReader();
  leitor.onload = async (leitura) => {
    try {
      const resposta = await api.anexar(state.tarefaEmEdicao.id, {
        nome_arquivo: arquivo.name,
        conteudo: leitura.target.result,
        usuario_nome: nomeDoAutor(),
      });
      state.tarefaEmEdicao.anexos = [...(state.tarefaEmEdicao.anexos || []), resposta.anexo];
      renderAnexos(state.tarefaEmEdicao.anexos);
      toast('Arquivo anexado', arquivo.name);
    } catch (erro) { /* toast já exibido */ }
  };

  leitor.readAsDataURL(arquivo);
  evento.target.value = '';
}

export async function adicionarComentario() {
  const campo = el('novo-comentario');
  const texto = campo.value.trim();
  if (!texto) return;

  if (!state.tarefaEmEdicao) {
    toast('Salve a demanda antes', 'Comentários só podem ser adicionados depois que a demanda existe.', 'warning');
    return;
  }

  try {
    const resposta = await api.comentar(state.tarefaEmEdicao.id, {
      texto,
      usuario_nome: nomeDoAutor(),
      usuario_foto: state.usuario?.foto_perfil || null,
    });
    state.tarefaEmEdicao.comentarios = [...(state.tarefaEmEdicao.comentarios || []), resposta.comentario];
    renderComentarios(state.tarefaEmEdicao.comentarios);
    campo.value = '';
  } catch (erro) { /* toast já exibido */ }
}
