/**
 * Calendário mensal: distribui as demandas pelo dia de vencimento
 * e abre o detalhamento de um dia específico.
 */

import {
  state, el, escapar, cortar, plural, avatarHTML, dataBR, paraData, venceEm,
  todasAsTarefas, chaveDoDia, metaStatus, metaPrioridade, MESES, DIAS_SEMANA,
} from './state.js';
import { abrirModal } from './ui.js';

function agruparPorDia() {
  const mapa = new Map();

  todasAsTarefas().forEach((tarefa) => {
    const data = paraData(venceEm(tarefa));
    if (!data) return;
    const chave = chaveDoDia(data);
    if (!mapa.has(chave)) mapa.set(chave, []);
    mapa.get(chave).push(tarefa);
  });

  return mapa;
}

export function renderCalendario() {
  const grade = el('cal-grid');
  const titulo = el('cal-titulo');
  if (!grade || !titulo) return;

  const ano = state.mesCalendario.getFullYear();
  const mes = state.mesCalendario.getMonth();
  titulo.textContent = `${MESES[mes]} de ${ano}`;

  const primeiroDiaSemana = new Date(ano, mes, 1).getDay();
  const diasNoMes = new Date(ano, mes + 1, 0).getDate();
  const hoje = new Date();
  const porDia = agruparPorDia();

  const celulas = [];

  for (let i = 0; i < primeiroDiaSemana; i += 1) {
    celulas.push('<div class="cal-day cal-day--empty"></div>');
  }

  for (let dia = 1; dia <= diasNoMes; dia += 1) {
    const tarefas = porDia.get(`${ano}-${mes}-${dia}`) || [];
    const ehHoje = dia === hoje.getDate() && mes === hoje.getMonth() && ano === hoje.getFullYear();

    const classes = ['cal-day'];
    if (ehHoje) classes.push('cal-day--today');
    else if (tarefas.length) classes.push('cal-day--has');

    const eventos = tarefas.slice(0, 2).map((tarefa) => {
      const cor = tarefa.status?.cor || '#94A3B8';
      return `<span class="cal-event"><span class="dot" style="background:${escapar(cor)}"></span><span>${escapar(cortar(tarefa.titulo, 20))}</span></span>`;
    }).join('');

    const excedente = tarefas.length > 2
      ? `<span class="cal-more">+${tarefas.length - 2} ${plural(tarefas.length - 2, 'demanda', 'demandas')}</span>`
      : '';

    celulas.push(`
      <button type="button" class="${classes.join(' ')}" data-action="open-day" data-dia="${dia}">
        <span class="cal-day__num">
          ${dia}
          ${tarefas.length ? `<span class="cal-day__count num">${tarefas.length}</span>` : ''}
        </span>
        ${eventos}${excedente}
      </button>`);
  }

  grade.innerHTML = celulas.join('');
}

export function mudarMes(delta) {
  const data = state.mesCalendario;
  state.mesCalendario = new Date(data.getFullYear(), data.getMonth() + delta, 1);
  renderCalendario();
}

export function irParaHoje() {
  state.mesCalendario = new Date();
  renderCalendario();
}

/* ---------- Detalhe do dia ---------- */
function htmlTarefaDoDia(tarefa) {
  const status = metaStatus(tarefa.status?.nome);
  const prioridade = metaPrioridade(tarefa.prioridade);
  const atrasada = tarefa.atrasada && !tarefa.concluida;

  const linhas = [
    `<span class="day-task__item"><i class="ph ph-folder"></i><span>${escapar(tarefa.pastaNome)}</span></span>`,
    `<span class="day-task__item"><i class="ph ph-stack"></i><span>${escapar(tarefa.projetoNome)}</span></span>`,
    `<span class="day-task__item">${avatarHTML(tarefa.responsavel_nome, tarefa.responsavel_foto, 'xs')}<span>${escapar(tarefa.responsavel_nome || 'Não atribuído')}</span></span>`,
    `<span class="day-task__item"><i class="ph ${prioridade.icone}"></i><span>Prioridade ${escapar(tarefa.prioridade || 'Média')}</span></span>`,
  ];

  if (tarefa.cidade) {
    linhas.push(`<span class="day-task__item"><i class="ph ph-map-pin"></i><span>${escapar(tarefa.cidade)}</span></span>`);
  }
  if (tarefa.data_previsao) {
    linhas.push(`<span class="day-task__item"><i class="ph ph-calendar-plus"></i><span>Previsão ${dataBR(tarefa.data_previsao)}</span></span>`);
  }
  linhas.push(`<span class="day-task__item" ${atrasada ? 'style="color:var(--red)"' : ''}>
      <i class="ph ph-hourglass-medium"></i><span>Vencimento ${dataBR(venceEm(tarefa))}</span></span>`);
  if (tarefa.data_conclusao) {
    linhas.push(`<span class="day-task__item" style="color:var(--green-ink)">
        <i class="ph ph-check-circle"></i><span>Concluída ${dataBR(tarefa.data_conclusao)}</span></span>`);
  }

  return `
    <article class="day-task day-task--${prioridade.classe} ${tarefa.concluida ? 'is-done' : ''}">
      <div class="day-task__head">
        <h4 class="day-task__title">${escapar(tarefa.titulo)}</h4>
        <span class="badge ${status.badge}"><i class="ph ${status.icone}"></i>${escapar(tarefa.status?.nome || 'Pendente')}</span>
      </div>

      ${tarefa.descricao ? `<p class="day-task__desc">${escapar(cortar(tarefa.descricao, 260))}</p>` : ''}

      <div class="day-task__grid">${linhas.join('')}</div>

      ${tarefa.etiqueta?.nome
        ? `<div><span class="badge badge--solid" style="background:${escapar(tarefa.etiqueta.cor || '#94A3B8')}"><i class="ph ph-tag"></i>${escapar(tarefa.etiqueta.nome)}</span></div>`
        : ''}

      <div style="display:flex;justify-content:flex-end">
        <button class="btn btn--secondary btn--sm" data-action="edit-task" data-id="${escapar(tarefa.id)}">
          <i class="ph ph-pencil-simple"></i> Abrir demanda
        </button>
      </div>
    </article>`;
}

export function abrirDia(dia) {
  const ano = state.mesCalendario.getFullYear();
  const mes = state.mesCalendario.getMonth();
  const data = new Date(ano, mes, dia);

  const tarefas = todasAsTarefas().filter((tarefa) => {
    const vencimento = paraData(venceEm(tarefa));
    return vencimento
      && vencimento.getDate() === dia
      && vencimento.getMonth() === mes
      && vencimento.getFullYear() === ano;
  });

  // Em datas por extenso o mês vai em minúscula: "5 de setembro".
  el('titulo-dia').textContent = `${DIAS_SEMANA[data.getDay()]}, ${dia} de ${MESES[mes].toLowerCase()}`;
  el('subtitulo-dia').textContent = tarefas.length
    ? `${tarefas.length} ${plural(tarefas.length, 'demanda vence', 'demandas vencem')} neste dia`
    : 'Nenhuma demanda vence neste dia';

  el('corpo-dia').innerHTML = tarefas.length
    ? tarefas.map(htmlTarefaDoDia).join('')
    : `<div class="empty">
         <div class="empty__icon"><i class="ph ph-calendar-check"></i></div>
         <p class="empty__title">Dia livre</p>
         <p class="empty__text">Nenhuma demanda tem vencimento marcado para esta data.</p>
       </div>`;

  abrirModal('modal-dia');
}
