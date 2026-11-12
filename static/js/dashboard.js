/**
 * Visão geral: indicadores, gráficos do Chart.js e prazos em foco.
 */

import { api, baixarExportacao } from './api.js';
import {
  state, el, escapar, cortar, plural, dataBR, venceEm, diasAte, todasAsTarefas,
} from './state.js';
import { esqueleto, toast } from './ui.js';

let graficoPastas = null;
let graficoPrioridade = null;
let ultimasMetricas = null;

function token(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

/* ---------- Indicadores ---------- */
const INDICADORES = [
  { chave: 'total', rotulo: 'Demandas registradas', icone: 'ph-stack', tom: 'brand', nota: 'Todas as pastas' },
  { chave: 'pendentes', rotulo: 'Pendentes', icone: 'ph-clock', tom: 'gold', nota: 'Aguardando início' },
  { chave: 'andamento', rotulo: 'Em andamento', icone: 'ph-spinner-gap', tom: 'brand', nota: 'Sendo executadas' },
  { chave: 'concluidas', rotulo: 'Concluídas', icone: 'ph-check-circle', tom: 'green', nota: 'Trabalho entregue' },
  { chave: 'atrasadas', rotulo: 'Atrasadas', icone: 'ph-warning', tom: 'red', nota: 'Prazo estourado' },
  { chave: 'alta_prioridade', rotulo: 'Alta prioridade', icone: 'ph-arrow-up', tom: 'red', nota: 'Em aberto e urgentes' },
  { chave: 'bloqueadas', rotulo: 'Bloqueadas', icone: 'ph-lock-simple', tom: 'neutral', nota: 'Esperando dependências' },
  { chave: 'taxa_cumprimento', rotulo: 'Prazos cumpridos', icone: 'ph-percent', tom: 'green', nota: 'Das demandas concluídas', sufixo: '%' },
];

function renderIndicadores(cards) {
  const container = el('kpis');
  if (!container) return;

  container.innerHTML = INDICADORES.map((indicador) => `
    <div class="kpi kpi--${indicador.tom}">
      <div class="kpi__head">
        <span class="kpi__icon"><i class="ph ${indicador.icone}"></i></span>
        <span class="kpi__label">${escapar(indicador.rotulo)}</span>
      </div>
      <p class="kpi__value">${cards[indicador.chave] ?? 0}${indicador.sufixo ? `<small>${indicador.sufixo}</small>` : ''}</p>
      <p class="kpi__foot">${escapar(indicador.nota)}</p>
    </div>`).join('');
}

/* ---------- Gráficos ---------- */
function opcoesBase() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 420, easing: 'easeOutQuart' },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: token('--text'),
        titleColor: token('--surface'),
        bodyColor: token('--surface'),
        padding: 11,
        cornerRadius: 8,
        displayColors: true,
        boxPadding: 4,
        titleFont: { family: 'Inter Tight', size: 12.5, weight: '600' },
        bodyFont: { family: 'Inter', size: 12 },
      },
    },
  };
}

function renderGraficoPastas(dados) {
  const canvas = el('chart-pastas');
  if (!canvas || typeof Chart === 'undefined') return;

  graficoPastas?.destroy();

  const vazio = !dados.length;
  const labels = vazio ? ['Sem pastas'] : dados.map((d) => cortar(d.nome, 14));

  graficoPastas = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Concluídas',
          data: vazio ? [0] : dados.map((d) => d.concluidas),
          backgroundColor: token('--green'),
          borderRadius: 5,
          maxBarThickness: 34,
        },
        {
          label: 'Pendentes',
          data: vazio ? [0] : dados.map((d) => d.pendentes),
          backgroundColor: token('--gold'),
          borderRadius: 5,
          maxBarThickness: 34,
        },
      ],
    },
    options: {
      ...opcoesBase(),
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          stacked: true,
          grid: { display: false },
          border: { display: false },
          ticks: { color: token('--text-2'), font: { family: 'Inter', size: 11.5, weight: '500' } },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: token('--border-soft') },
          border: { display: false },
          ticks: { color: token('--text-3'), font: { family: 'Inter', size: 11.5 }, precision: 0 },
        },
      },
    },
  });
}

function renderGraficoPrioridade(distribuicao) {
  const canvas = el('chart-prioridade');
  if (!canvas || typeof Chart === 'undefined') return;

  graficoPrioridade?.destroy();

  const valores = [distribuicao.Alta || 0, distribuicao['Média'] || 0, distribuicao.Baixa || 0];
  const vazio = valores.every((v) => v === 0);

  graficoPrioridade = new Chart(canvas.getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: ['Alta', 'Média', 'Baixa'],
      datasets: [{
        data: vazio ? [1] : valores,
        backgroundColor: vazio
          ? [token('--surface-3')]
          : [token('--red'), token('--orange'), token('--text-3')],
        borderWidth: 0,
        hoverOffset: 6,
      }],
    },
    options: {
      ...opcoesBase(),
      cutout: '68%',
      plugins: {
        ...opcoesBase().plugins,
        legend: {
          display: !vazio,
          position: 'bottom',
          labels: {
            padding: 16,
            usePointStyle: true,
            pointStyle: 'circle',
            boxWidth: 7,
            color: token('--text-2'),
            font: { family: 'Inter', size: 12, weight: '500' },
          },
        },
      },
    },
  });
}

/* ---------- Listas ---------- */
function renderRanking(containerId, itens, rotuloVazio) {
  const container = el(containerId);
  if (!container) return;

  if (!itens.length) {
    container.innerHTML = `<div class="empty empty--sm"><p class="empty__text">${escapar(rotuloVazio)}</p></div>`;
    return;
  }

  const maximo = Math.max(...itens.map((i) => i.total));
  container.innerHTML = itens.map((item) => `
    <div class="rank__row">
      <span class="rank__name">${escapar(item.nome)}</span>
      <span class="rank__bar"><span style="width:${Math.round((item.total / maximo) * 100)}%"></span></span>
      <span class="rank__value">${item.total}</span>
    </div>`).join('');
}

function renderPrazos() {
  const container = el('deadline-list');
  if (!container) return;

  const pendentes = todasAsTarefas()
    .filter((tarefa) => !tarefa.concluida && venceEm(tarefa))
    .map((tarefa) => ({ tarefa, dias: diasAte(venceEm(tarefa)) }))
    .filter((item) => item.dias !== null)
    .sort((a, b) => a.dias - b.dias);

  const atrasadas = pendentes.filter((i) => i.dias < 0).slice(0, 4);
  const proximas = pendentes.filter((i) => i.dias >= 0).slice(0, 8);
  const lista = [...atrasadas, ...proximas];

  if (!lista.length) {
    container.innerHTML = `
      <div class="empty empty--sm">
        <div class="empty__icon" style="background:var(--green-soft);color:var(--green)"><i class="ph ph-check-circle"></i></div>
        <p class="empty__title">Tudo em dia</p>
        <p class="empty__text">Nenhuma demanda em aberto com prazo definido.</p>
      </div>`;
    return;
  }

  container.innerHTML = lista.map(({ tarefa, dias }) => {
    let estilo = 'background:var(--surface-3);color:var(--text-2)';
    let cor = 'color:var(--text-3)';
    let icone = 'ph-calendar-blank';
    let texto = `Em ${dias} ${plural(dias, 'dia', 'dias')}`;

    if (dias < 0) {
      const atraso = Math.abs(dias);
      estilo = 'background:var(--red-soft);color:var(--red)';
      cor = 'color:var(--red)';
      icone = 'ph-warning-circle';
      texto = `${atraso} ${plural(atraso, 'dia', 'dias')} de atraso`;
    } else if (dias === 0) {
      estilo = 'background:var(--gold-soft);color:var(--gold-ink)';
      cor = 'color:var(--gold-ink)';
      icone = 'ph-fire';
      texto = 'Vence hoje';
    } else if (dias === 1) {
      estilo = 'background:var(--gold-soft);color:var(--gold-ink)';
      cor = 'color:var(--gold-ink)';
      icone = 'ph-clock';
      texto = 'Vence amanhã';
    } else if (dias <= 3) {
      estilo = 'background:var(--brand-soft);color:var(--brand)';
      cor = 'color:var(--brand)';
      icone = 'ph-calendar-dot';
    }

    return `
      <button class="deadline" data-action="edit-task" data-id="${escapar(tarefa.id)}">
        <span class="deadline__mark" style="${estilo}"><i class="ph ${icone}"></i></span>
        <span class="deadline__body">
          <span class="deadline__title">${escapar(tarefa.titulo)}</span>
          <span class="deadline__meta">${escapar(tarefa.projetoNome)}</span>
          <span class="deadline__when" style="${cor}">
            <i class="ph ph-calendar-blank"></i>${escapar(texto)} · ${dataBR(venceEm(tarefa))}
          </span>
        </span>
      </button>`;
  }).join('');
}

/* ---------- Filtro por setor (só Conta Mestre) ---------- */
function prepararFiltroSetor() {
  const bloco = el('dash-filtro-setor');
  const select = el('dash-setor');
  if (!bloco || !select) return;

  if (!state.usuario?.conta_mestre) {
    bloco.classList.add('hidden');
    return;
  }

  bloco.classList.remove('hidden');
  const atual = select.value;
  select.innerHTML = '<option value="">Todos os setores</option>'
    + (state.dados.setores || []).map((nome) => `<option value="${escapar(nome)}">${escapar(nome)}</option>`).join('');
  select.value = atual || '';
}

export function mudarSetorDashboard() {
  renderDashboard();
}

export function exportarSetorAtual() {
  const setor = el('dash-setor')?.value;
  if (!setor) {
    toast('Selecione um setor', 'Escolha um setor na lista para exportar só ele — ou use "Exportar Excel" no menu para tudo.', 'warning');
    return;
  }
  toast('Gerando relatório', `O Excel do setor "${setor}" será baixado em instantes.`, 'info');
  baixarExportacao(setor).catch((falha) => toast('Não foi possível exportar', falha.message, 'error'));
}

/* ---------- Orquestração ---------- */
export async function renderDashboard() {
  prepararFiltroSetor();

  const kpis = el('kpis');
  if (kpis && !kpis.children.length) {
    kpis.innerHTML = Array.from({ length: 8 }, () =>
      '<div class="kpi"><div class="skeleton" style="height:64px"></div></div>').join('');
  }

  try {
    const setor = state.usuario?.conta_mestre ? (el('dash-setor')?.value || null) : null;
    const metricas = await api.dashboard(setor);
    ultimasMetricas = metricas;

    renderIndicadores(metricas.cards || {});
    renderGraficoPastas(metricas.grafico || []);
    renderGraficoPrioridade(metricas.grafico_prioridade || {});
    renderRanking('rank-responsaveis', metricas.grafico_responsaveis || [], 'Nenhuma demanda atribuída ainda.');
    renderRanking('rank-cidades', metricas.grafico_cidades || [], 'Nenhuma cidade informada ainda.');
    renderPrazos();
  } catch (erro) {
    if (kpis) {
      kpis.innerHTML = `<div class="kpi" style="grid-column:1/-1">
        <p class="kpi__label">Não foi possível carregar os indicadores.</p>
      </div>`;
    }
  }
}

/* Os gráficos leem cores dos tokens CSS: ao trocar o tema, precisam ser refeitos. */
document.addEventListener('tema:alterado', () => {
  if (!ultimasMetricas || state.view !== 'dashboard') return;
  renderGraficoPastas(ultimasMetricas.grafico || []);
  renderGraficoPrioridade(ultimasMetricas.grafico_prioridade || {});
});

export { esqueleto };
