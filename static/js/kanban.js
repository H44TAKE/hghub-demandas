/**
 * Quadro Kanban: colunas por status e arrastar-e-soltar nativo.
 */

import {
  state, el, escapar, cortar, avatarHTML, dataBR, venceEm,
  todasAsTarefas, metaPrioridade, metaStatus,
} from './state.js';
import { alternarStatusTarefa } from './structure.js';

const COLUNAS_PADRAO = ['Pendente', 'Em Andamento', 'Concluída', 'Cancelada'];

const ICONES_COLUNA = {
  'Pendente': 'ph-clock',
  'Em Andamento': 'ph-spinner-gap',
  'Concluída': 'ph-check-circle',
  'Cancelada': 'ph-prohibit',
};

let ligado = false;

function htmlCartao(tarefa, indice) {
  const prioridade = metaPrioridade(tarefa.prioridade);
  const atrasada = tarefa.atrasada && !tarefa.concluida;
  const vencimento = venceEm(tarefa);

  const badges = [];
  if (tarefa.prioridade) {
    badges.push(`<span class="badge ${prioridade.badge}"><i class="ph ${prioridade.icone}"></i>${escapar(tarefa.prioridade)}</span>`);
  }
  if (tarefa.etiqueta?.nome) {
    badges.push(`<span class="badge badge--solid" style="background:${escapar(tarefa.etiqueta.cor || '#94A3B8')}">${escapar(cortar(tarefa.etiqueta.nome, 18))}</span>`);
  }
  if (tarefa.bloqueada && !tarefa.concluida) {
    badges.push('<span class="badge badge--neutral"><i class="ph ph-lock-simple"></i>Bloqueada</span>');
  }

  let dataPill = '';
  if (tarefa.concluida && tarefa.data_conclusao) {
    dataPill = `<span class="date-pill date-pill--done"><i class="ph ph-check-circle"></i>${dataBR(tarefa.data_conclusao)}</span>`;
  } else if (vencimento) {
    dataPill = `<span class="date-pill ${atrasada ? 'date-pill--late' : 'date-pill--due'}">
        <i class="ph ${atrasada ? 'ph-warning' : 'ph-hourglass-medium'}"></i>${dataBR(vencimento)}</span>`;
  }

  const classe = [
    'kcard',
    tarefa.prioridade === 'Alta' ? 'kcard--alta' : '',
    tarefa.prioridade === 'Média' ? 'kcard--media' : '',
    tarefa.prioridade === 'Baixa' ? 'kcard--baixa' : '',
  ].filter(Boolean).join(' ');

  return `
    <article class="${classe}" draggable="true" data-id="${escapar(tarefa.id)}"
             style="animation-delay:${Math.min(indice, 8) * 35}ms">
      <div class="kcard__top">
        <div class="kcard__badges">${badges.join('')}</div>
        <div class="kcard__acoes">
          <button class="icon-btn kcard__edit" data-action="edit-task" data-id="${escapar(tarefa.id)}" aria-label="Editar demanda">
            <i class="ph ph-pencil-simple"></i>
          </button>
          <button class="icon-btn icon-btn--danger kcard__delete" data-action="delete-task" data-id="${escapar(tarefa.id)}" aria-label="Excluir demanda">
            <i class="ph ph-trash"></i>
          </button>
        </div>
      </div>

      <h4 class="kcard__title">${escapar(tarefa.titulo)}</h4>

      <p class="kcard__project">
        <i class="ph ph-stack"></i><span>${escapar(tarefa.projetoNome)}</span>
      </p>

      ${tarefa.cidade ? `<p class="kcard__project"><i class="ph ph-map-pin"></i><span>${escapar(tarefa.cidade)}</span></p>` : ''}

      ${dataPill ? `<div>${dataPill}</div>` : ''}

      <div class="kcard__foot">
        <span class="person">
          ${avatarHTML(tarefa.responsavel_nome, tarefa.responsavel_foto, 'xs')}
          <span class="person__name">${escapar((tarefa.responsavel_nome || 'Não atribuído').split(' ')[0])}</span>
        </span>
        ${tarefa.comentarios?.length
          ? `<span class="person__name"><i class="ph ph-chat-circle-dots"></i> ${tarefa.comentarios.length}</span>` : ''}
      </div>
    </article>`;
}

export function renderKanban() {
  const board = el('kanban-board');
  if (!board) return;

  const nomes = state.dados.status?.length
    ? state.dados.status.map((s) => s.nome)
    : COLUNAS_PADRAO;

  const tarefas = todasAsTarefas();

  board.innerHTML = nomes.map((nome) => {
    const meta = metaStatus(nome);
    const daColuna = tarefas.filter((t) => t.status?.nome === nome);

    const corpo = daColuna.length
      ? daColuna.map(htmlCartao).join('')
      : `<div class="drop-hint"><i class="ph ph-tray"></i> Solte um cartão aqui</div>`;

    return `
      <section class="column" data-status="${escapar(nome)}">
        <header class="column__head">
          <span class="dot" style="background:${meta.cor}"></span>
          <h3 class="column__name">${escapar(nome)}</h3>
          <span class="column__count num">${daColuna.length}</span>
        </header>
        <div class="column__body">${corpo}</div>
      </section>`;
  }).join('');

  ligarArrastar(board);
}

/* ---------- Arrastar e soltar ---------- */
function ligarArrastar(board) {
  if (ligado) return;
  ligado = true;

  board.addEventListener('dragstart', (evento) => {
    const cartao = evento.target.closest('.kcard');
    if (!cartao) return;
    evento.dataTransfer.setData('text/plain', cartao.dataset.id);
    evento.dataTransfer.effectAllowed = 'move';
    requestAnimationFrame(() => cartao.classList.add('is-dragging'));
  });

  board.addEventListener('dragend', () => {
    board.querySelectorAll('.is-dragging').forEach((node) => node.classList.remove('is-dragging'));
    board.querySelectorAll('.is-target').forEach((node) => node.classList.remove('is-target'));
  });

  board.addEventListener('dragover', (evento) => {
    const coluna = evento.target.closest('.column');
    if (!coluna) return;
    evento.preventDefault();
    evento.dataTransfer.dropEffect = 'move';
    board.querySelectorAll('.is-target').forEach((node) => {
      if (node !== coluna) node.classList.remove('is-target');
    });
    coluna.classList.add('is-target');
  });

  board.addEventListener('dragleave', (evento) => {
    const coluna = evento.target.closest('.column');
    if (coluna && !coluna.contains(evento.relatedTarget)) coluna.classList.remove('is-target');
  });

  board.addEventListener('drop', async (evento) => {
    const coluna = evento.target.closest('.column');
    if (!coluna) return;
    evento.preventDefault();
    coluna.classList.remove('is-target');

    const id = evento.dataTransfer.getData('text/plain');
    const novoStatus = coluna.dataset.status;
    if (!id || !novoStatus) return;

    const tarefa = todasAsTarefas().find((t) => t.id === id);
    if (tarefa?.status?.nome === novoStatus) return;

    await alternarStatusTarefa(id, novoStatus);
  });
}
