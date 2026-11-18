/**
 * "Meu dia": so o que e MEU e exige atencao - atrasadas, hoje, amanha e os proximos 7 dias.
 * E a tela inicial: abrir o app ja mostra o que fazer, sem procurar.
 */

import { state, el, escapar, dataBR, diasAte, venceEm, todasAsTarefas, plural } from './state.js';
import { alternarStatusTarefa } from './structure.js';

const GRUPOS = [
  { chave: 'atrasadas', titulo: 'Atrasadas', icone: 'ph-warning', tom: 'late', cabe: (d) => d < 0 },
  { chave: 'hoje', titulo: 'Vencem hoje', icone: 'ph-hourglass-medium', tom: 'due', cabe: (d) => d === 0 },
  { chave: 'amanha', titulo: 'Amanhã', icone: 'ph-calendar-dot', tom: 'due', cabe: (d) => d === 1 },
  { chave: 'semana', titulo: 'Próximos 7 dias', icone: 'ph-calendar-blank', tom: 'due', cabe: (d) => d >= 2 && d <= 7 },
];

function linha(tarefa) {
  const dias = diasAte(venceEm(tarefa));
  const quando = dias < 0 ? `${-dias} dia${dias === -1 ? '' : 's'} de atraso` : dataBR(venceEm(tarefa));
  return `
    <div class="meudia__item">
      <div class="meudia__texto">
        <p class="meudia__titulo">${escapar(tarefa.titulo)}</p>
        <p class="meudia__meta">${escapar(tarefa.pastaNome)} › ${escapar(tarefa.projetoNome)} · ${escapar(quando)}${tarefa.etiqueta?.nome ? ` · ${escapar(tarefa.etiqueta.nome)}` : ''}</p>
      </div>
      <div class="meudia__acoes">
        <button class="btn btn--secondary btn--sm" data-action="edit-task" data-id="${escapar(tarefa.id)}"><i class="ph ph-pencil-simple"></i> Abrir</button>
        <button class="btn btn--primary btn--sm" data-action="meudia-concluir" data-id="${escapar(tarefa.id)}"><i class="ph ph-check"></i> Concluir</button>
      </div>
    </div>`;
}

export function renderMeuDia() {
  const container = el('meudia-lista');
  if (!container) return;
  const eu = state.usuario?.id;
  const minhas = todasAsTarefas().filter((t) => t.responsavel_id === eu && !t.concluida && t.status?.nome !== 'Cancelada');
  const comPrazo = minhas.map((t) => ({ t, d: diasAte(venceEm(t)) })).filter((x) => x.d !== null);

  const blocos = GRUPOS.map((g) => {
    const itens = comPrazo.filter((x) => g.cabe(x.d)).sort((a, b) => a.d - b.d).map((x) => x.t);
    if (!itens.length) return '';
    return `
      <section class="meudia__grupo meudia__grupo--${g.tom}">
        <h3 class="meudia__grupo-titulo"><i class="ph ${g.icone}"></i> ${g.titulo} <span class="badge badge--neutral num">${itens.length}</span></h3>
        ${itens.map(linha).join('')}
      </section>`;
  }).join('');

  const semPrazo = minhas.length - comPrazo.length;
  container.innerHTML = blocos || `
    <div class="card empty">
      <div class="empty__icon"><i class="ph ph-sun-horizon"></i></div>
      <p class="empty__title">Nada urgente para você</p>
      <p class="empty__text">Nenhuma demanda sua está atrasada ou vence nos próximos 7 dias.</p>
    </div>`;
  if (semPrazo > 0) {
    container.insertAdjacentHTML('beforeend',
      `<p class="muted-note">Você ainda tem ${semPrazo} ${plural(semPrazo, 'demanda sem prazo', 'demandas sem prazo')} — veja em Estrutura.</p>`);
  }
}

export function concluirMinhaDemanda(id) {
  return alternarStatusTarefa(id, 'Concluída');
}
