/**
 * Frota & Manutenção: le a integração com o Google Sheets (backend/manutencao.py)
 * e desenha o dashboard de revisões/óleo em cards expansíveis.
 * Visível só para quem tem o setor "Manutenção" ou é Conta Mestre — a mesma
 * checagem que o backend já faz é usada aqui só para decidir o que mostrar.
 */

import { api, sessao } from './api.js';
import { state, el, qsa, mostrar, escapar, valor, nomeDoUsuario } from './state.js';
import { esqueleto, toast, abrirModal, fecharModal } from './ui.js';
import {
  token, corDoStatus, badgeDoStatus, iconeDoStatus, rotuloStatus, formatarKm, dataBRSimples, corDoScore,
  opcoesGrafico, ROTULOS_CATEGORIA, iconeFrente, tituloFrente, calcularSaudePorFrente,
  calcularFrentesEmAtencao, rankingCriticos,
} from './manutencao_comum.js';
import {
  configurarDetalheManutencao, atualizarDetalheManutencao, fecharDetalheManutencao, detalheManutencaoAberto,
} from './manutencao_detalhe.js';

const ROTULOS_ITEM = {
  revisao: { titulo: 'Próxima revisão', icone: 'ph-wrench' },
  oleo_motor: { titulo: 'Óleo do motor', icone: 'ph-drop' },
  oleo_cambio: { titulo: 'Óleo do câmbio', icone: 'ph-gear-six' },
  oleo_diferencial: { titulo: 'Óleo do diferencial', icone: 'ph-circle-dashed' },
  aditivo: { titulo: 'Aditivo / ARLA', icone: 'ph-flask' },
};

let veiculos = [];
const expandidos = new Set();
let saudeFrotaVeiculos = []; // lista achatada (Cavalo+Carreta) de calcular_saude_frota() - base do Health Score e do filtro
let filtroTipoFrota = 'ambos'; // 'cavalo' | 'carreta' | 'ambos'
let graficoSaude = null;
let graficoProximas = null;
let graficoHealth = null;

/* ---------- Write-back: edição inline de campos (KM, datas de última troca) ---------- */
let modificadoEmAtual = null; // modifiedTime do Drive na última leitura - vai junto em toda escrita (concorrência otimista)
let edicaoAtiva = null; // { cod, campo } do campo em edição agora, ou null
let salvandoCampo = null; // { cod, campo } do campo aguardando o backend, ou null
let conflitoPendente = null; // { cod, campo, valorNovo, valorAnterior, valorAtualServidor, modificadoEm } enquanto o modal de conflito está aberto

const LEGENDA_CAMPO = {
  km_atual: 'KM atual',
  'revisao.ultima_data': 'Última data de revisão',
  'revisao.ultima_km': 'Última km de revisão',
  'aditivo.ultima_data': 'Última data de aditivo/ARLA',
  'aditivo.ultima_km': 'Última km de aditivo/ARLA',
  'oleo_cambio.ultima_data': 'Última data de óleo do câmbio',
  'oleo_cambio.ultima_km': 'Última km de óleo do câmbio',
  'oleo_diferencial.ultima_data': 'Última data de óleo do diferencial',
  'oleo_diferencial.ultima_km': 'Última km de óleo do diferencial',
  'oleo_motor.ultima_data': 'Última data de óleo do motor',
  'oleo_motor.ultima_km': 'Última km de óleo do motor',
};


/* ---------- Apresentação de status ---------- */





/** % percorrida do intervalo entre a última e a próxima troca. null quando não dá para calcular. */
function progresso(item, kmAtual) {
  const { ultima_km: ultima, proxima_km: proxima } = item;
  if (ultima == null || proxima == null || kmAtual == null || proxima <= ultima) return null;
  const pct = ((kmAtual - ultima) / (proxima - ultima)) * 100;
  return Math.max(0, Math.min(100, pct));
}

/* ---------- Campo editável inline (write-back) ---------- */
/** Um campo write-back nos 3 estados possíveis: leitura, edição, salvando.
 * `campo` é exatamente a chave que o backend aceita (`CAMPOS_EDITAVEIS` em
 * backend/manutencao.py) - ex.: 'km_atual', 'revisao.ultima_data'. */
function campoEditavel({ cod, campo, rotulo, icone, valorAtual, tipo, formatador, pequeno = false }) {
  const idInput = `edicao-${campo.replace(/\./g, '-')}-${cod}`;

  if (salvandoCampo?.cod === cod && salvandoCampo?.campo === campo) {
    return `
      <div class="mfield">
        <span class="mfield__label"><i class="ph ${icone}"></i>${rotulo}</span>
        <span class="mfield__spinner"><i class="ph ph-spinner-gap"></i>Salvando…</span>
      </div>`;
  }

  if (edicaoAtiva?.cod === cod && edicaoAtiva?.campo === campo) {
    return `
      <div class="mfield">
        <span class="mfield__label"><i class="ph ${icone}"></i>${rotulo}</span>
        <div class="mfield__form">
          <input type="${tipo === 'data' ? 'date' : 'number'}" id="${idInput}"
                 value="${valorAtual ?? ''}" data-cod="${escapar(cod)}" data-campo="${campo}" data-action-enter="1">
          <button type="button" class="icon-btn icon-btn--sm" data-action="salvar-manut-campo" data-cod="${escapar(cod)}" data-campo="${campo}" title="Salvar"><i class="ph ph-check"></i></button>
          <button type="button" class="icon-btn icon-btn--sm" data-action="cancelar-manut-edicao" title="Cancelar"><i class="ph ph-x"></i></button>
        </div>
      </div>`;
  }

  const temValor = valorAtual != null && valorAtual !== '';
  return `
    <div class="mfield">
      <span class="mfield__label"><i class="ph ${icone}"></i>${rotulo}</span>
      <button type="button" class="mfield__valor ${pequeno ? 'mfield__valor--sm' : ''}" data-action="editar-manut-campo" data-cod="${escapar(cod)}" data-campo="${campo}" title="Clique para editar">
        ${temValor ? escapar(formatador ? formatador(valorAtual) : String(valorAtual)) : 'Sem dado'}
        <i class="ph ph-pencil-simple"></i>
      </button>
    </div>`;
}

/* ---------- Templates ---------- */
function linhaItem(chave, item, kmAtual, cod = null) {
  const rotulo = ROTULOS_ITEM[chave];
  const cor = corDoStatus(item.status);
  const pct = progresso(item, kmAtual);

  const metaProxima = item.proxima_km != null
    ? `Próxima troca: ${formatarKm(item.proxima_km)} km`
    : 'Sem previsão cadastrada';
  const metaFalta = item.km_falta != null
    ? `${item.km_falta < 0 ? 'Excedeu em' : 'Faltam'} ${formatarKm(Math.abs(item.km_falta))} km`
    : '';

  const edicaoUltima = cod ? `
      <div class="mprog-row__edicao">
        ${campoEditavel({
          cod, campo: `${chave}.ultima_data`, rotulo: 'Última data', icone: 'ph-calendar',
          valorAtual: item.ultima_data, tipo: 'data', formatador: dataBRSimples, pequeno: true,
        })}
        ${campoEditavel({
          cod, campo: `${chave}.ultima_km`, rotulo: 'Última km', icone: 'ph-gauge',
          valorAtual: item.ultima_km, tipo: 'numero', formatador: (n) => `${formatarKm(n)} km`, pequeno: true,
        })}
      </div>` : '';

  return `
    <div class="mprog-row">
      <div class="mprog-row__head">
        <span class="mprog-row__label">
          <i class="ph ${rotulo.icone}"></i>${rotulo.titulo}${item.viscosidade ? ` · ${escapar(item.viscosidade)}` : ''}
        </span>
        <span class="badge ${badgeDoStatus(item.status)}"><i class="ph ${iconeDoStatus(item.status)}"></i>${rotuloStatus(item.status)}</span>
      </div>
      <div class="mprog"><div class="mprog__fill mprog__fill--${cor}" style="width:${pct ?? 0}%"></div></div>
      <div class="mprog-row__meta">
        <span>${metaProxima}</span>
        <span>${metaFalta}</span>
      </div>
      ${edicaoUltima}
    </div>`;
}

/** O card desta aba resume o veículo pelo status da PRÓXIMA REVISÃO, e só
 * dela - de propósito, diferente do `status_geral` (que é o pior status
 * entre as 5 frentes: revisão, aditivo e os 3 óleos, e continua sendo usado
 * assim no Dashboard Geral/Health Score/Prontuário). Pedido do setor: óleo
 * ou aditivo vencidos não devem acender o aviso vermelho aqui - só a revisão
 * em si. As outras 4 linhas expansíveis continuam com o badge individual de
 * cada uma (ver `linhaItem`), então aquele detalhe não se perde. */
function statusProximaRevisao(v) {
  return v.revisao?.status ?? null;
}

function cardVeiculo(v) {
  const aberto = expandidos.has(v.cod);
  const subtitulo = [v.marca, v.modelo, v.ano_fab].filter(Boolean).join(' · ');
  const status = statusProximaRevisao(v);

  return `
    <article class="mcard">
      <button type="button" class="mcard__head" data-action="toggle-manut-card" data-cod="${escapar(v.cod)}">
        <span class="mcard__dot mcard__dot--${corDoStatus(status)}"></span>
        <div class="mcard__id">
          <strong>${escapar(v.placa)}</strong>
          <span>${escapar(subtitulo)}</span>
        </div>
        <div class="mcard__km">
          <span class="num">${v.km_atual != null ? formatarKm(v.km_atual) : '—'}</span>
          <span class="mcard__km-label">km atual</span>
        </div>
        <span class="badge ${badgeDoStatus(status)}"><i class="ph ${iconeDoStatus(status)}"></i>${rotuloStatus(status)}</span>
        <i class="ph ph-caret-down mcard__caret ${aberto ? 'is-open' : ''}"></i>
      </button>
      <div class="mcard__body ${aberto ? '' : 'hidden'}">
        ${campoEditavel({
          cod: v.cod, campo: 'km_atual', rotulo: 'KM atual', icone: 'ph-gauge',
          valorAtual: v.km_atual, tipo: 'numero', formatador: (n) => `${formatarKm(n)} km`,
        })}
        ${linhaItem('revisao', v.revisao, v.km_atual, v.cod)}
        ${linhaItem('oleo_motor', v.oleo_motor, v.km_atual, v.cod)}
        ${linhaItem('oleo_cambio', v.oleo_cambio, v.km_atual, v.cod)}
        ${linhaItem('oleo_diferencial', v.oleo_diferencial, v.km_atual, v.cod)}
        ${linhaItem('aditivo', v.aditivo, v.km_atual, v.cod)}
        ${v.observacoes ? `<p class="field__hint"><i class="ph ph-note"></i> ${escapar(v.observacoes)}</p>` : ''}
        <button type="button" class="btn btn--secondary btn--block" data-action="abrir-prontuario" data-placa="${escapar(v.placa)}">
          <i class="ph ph-file-text"></i> Ver prontuário completo
        </button>
      </div>
    </article>`;
}

/* ---------- Dashboard Geral da Frota (KPIs + gráficos, cruzando TODAS as
 * frentes de manutenção - óleo, alinhamento, chassi, 5ª roda, SR-tanques,
 * preventiva de carretas - para Cavalos e Carretas juntos) ----------
 * Fonte: `calcular_saude_frota()` (backend/manutencao.py), buscada uma vez
 * (`carregarSaudeFrota`) e depois filtrada/agregada 100% no cliente a cada
 * clique no segmentado Cavalos/Carretas/Ambos - nenhum novo round-trip ao
 * Drive por causa do filtro. */
/** Filtra a lista completa (Cavalo+Carreta) pelo segmentado do topo do Dashboard. */
function veiculosSaudeFiltrados() {
  if (filtroTipoFrota === 'ambos') return saudeFrotaVeiculos;
  return saudeFrotaVeiculos.filter((v) => (v.tipo || '').toLowerCase() === filtroTipoFrota);
}

/** {total, em_dia, programar_parada, parar_urgente, sem_dado} a partir de uma
 * lista de veículos (já filtrada por tipo) - mesmo formato que os cards e o
 * gráfico de rosca já esperavam da antiga `calcular_metricas()`. */
function agregarSaudeFrota(lista) {
  const agregado = { total: lista.length, em_dia: 0, programar_parada: 0, parar_urgente: 0, sem_dado: 0 };
  lista.forEach((v) => {
    if (v.status_geral === 'EM DIA') agregado.em_dia += 1;
    else if (v.status_geral === 'PROGRAMAR PARADA') agregado.programar_parada += 1;
    else if (v.status_geral === 'PARAR URGENTE') agregado.parar_urgente += 1;
    else agregado.sem_dado += 1; // recém-cadastrado, ainda sem nenhuma frente com dado lançado
  });
  return agregado;
}



/** Health Score: % da frota (filtrada) 100% em dia cruzando todas as
 * frentes. Veículos "sem dado" ficam de fora do denominador - de propósito,
 * mesma convenção de `agregarSaudeFrota`: um veículo recém-cadastrado sem
 * nenhuma manutenção lançada ainda não é nem "saudável" nem "um problema". */

function renderHealthScore(agregado) {
  const elegiveis = agregado.em_dia + agregado.programar_parada + agregado.parar_urgente;
  const pct = elegiveis ? Math.round((agregado.em_dia / elegiveis) * 100) : null;
  const cor = pct == null ? 'neutro' : corDoScore(pct);

  const cartao = el('manut-health-card');
  if (cartao) cartao.className = `mhero-health mhero-health--clicavel mhero-health--${cor}`;

  const valorEl = el('manut-health-valor');
  if (valorEl) valorEl.textContent = pct == null ? '—' : `${pct}%`;

  const legenda = el('manut-health-legenda');
  if (legenda) {
    legenda.textContent = pct == null
      ? 'Sem dados suficientes ainda para calcular o Health Score.'
      : `${agregado.em_dia} de ${elegiveis} veículos/implementos 100% em dia, cruzando todas as frentes.`;
  }

  const canvas = el('manut-chart-health');
  if (!canvas || typeof Chart === 'undefined') return;
  graficoHealth?.destroy();

  const cores = { verde: token('--green'), laranja: token('--gold'), vermelho: token('--red'), neutro: token('--text-3') };
  graficoHealth = new Chart(canvas.getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: ['Em dia', 'Atenção'],
      datasets: [{
        data: pct == null ? [1] : [pct, 100 - pct],
        backgroundColor: [cores[cor], token('--surface-3')],
        borderWidth: 0,
      }],
    },
    options: {
      ...opcoesGrafico(),
      cutout: '80%',
      plugins: { ...opcoesGrafico().plugins, legend: { display: false }, tooltip: { enabled: false } },
    },
  });
}

/** Card de uma frente: saúde (anel + "N de M em dia") e vencidos (número
 * grande), com uma barrinha de composição Em dia / A vencer / Vencido. O
 * anel e a barra usam o mesmo universo do painel que o clique abre. */
function cardFrente(f) {
  const titulo = tituloFrente(f.chave);
  const tom = f.pct == null ? 'neutro' : corDoScore(f.pct);
  const vencidos = f.parar_urgente;
  const rotuloVencidos = `${titulo} vencido${vencidos === 1 ? '' : 's'}`;
  const segmento = (qtd, classe) => (qtd
    ? `<span class="mfrente-card__seg mfrente-card__seg--${classe}" style="flex:${qtd} 1 0"></span>`
    : '');

  return `
    <button type="button" class="mfrente-card mfrente-card--${tom}" data-action="abrir-detalhe-frente" data-categoria="${escapar(f.chave)}"
            aria-label="${escapar(titulo)}: saúde ${f.pct}%, ${f.em_dia} de ${f.elegiveis} em dia, ${vencidos} vencido${vencidos === 1 ? '' : 's'}. Abrir página de análise.">
      <i class="ph ph-arrow-up-right mhero-kpi__seta"></i>
      <div class="mfrente-card__conteudo">
        <div class="mfrente-card__saude">
          <div class="mfrente-card__anel">
            <svg viewBox="0 0 36 36" aria-hidden="true">
              <circle class="mfrente-card__trilho" cx="18" cy="18" r="15.9155"/>
              ${f.pct > 0 ? `<circle class="mfrente-card__arco" cx="18" cy="18" r="15.9155" stroke-dasharray="${f.pct} 100"/>` : ''}
            </svg>
            <span class="mfrente-card__pct">${f.pct}%</span>
          </div>
          <div class="mfrente-card__saude-texto">
            <span class="mfrente-card__saude-rotulo">Saúde</span>
            <span class="mfrente-card__saude-detalhe">${f.em_dia} de ${f.elegiveis} em dia</span>
          </div>
        </div>
        <div class="mfrente-card__info">
          <div class="mfrente-card__corpo">
            <span class="mfrente-card__icone${vencidos ? '' : ' mfrente-card__icone--ok'}"><i class="ph ${iconeFrente(f.chave)}"></i></span>
            <div>
              <p class="mfrente-card__valor">${vencidos}</p>
              <p class="mfrente-card__rotulo">${escapar(rotuloVencidos)}</p>
            </div>
          </div>
          <div class="mfrente-card__barra" aria-hidden="true">
            ${segmento(f.em_dia, 'ok')}${segmento(f.programar_parada, 'atencao')}${segmento(f.parar_urgente, 'vencido')}
          </div>
        </div>
      </div>
    </button>`;
}

function renderCardsFrente(lista) {
  const container = el('manut-cat-cards');
  if (!container) return;

  const frentes = calcularSaudePorFrente(lista);
  container.innerHTML = frentes.length
    ? frentes.map(cardFrente).join('')
    : `<div class="card empty">
         <div class="empty__icon"><i class="ph ph-file-dashed"></i></div>
         <p class="empty__title">Nenhuma frente com dados</p>
         <p class="empty__text">Ainda não há manutenção lançada para este filtro.</p>
       </div>`;
}

/** "Previsão de Paradas": soma, mês a mês (próximos 6 meses, a partir do mês
 * atual), quantas "próximas datas" (`v.proximas`, já calculadas pelo
 * backend - `_proxima_data_categoria`) caem em cada um - só itens rastreados
 * por TEMPO entram aqui, itens por KM não têm data de calendário para
 * prever. */
function calcularPrevisaoParadas(lista) {
  const hoje = new Date();
  const meses = Array.from({ length: 6 }, (_, i) => {
    const d = new Date(hoje.getFullYear(), hoje.getMonth() + i, 1);
    return {
      chave: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`,
      rotulo: d.toLocaleDateString('pt-BR', { month: 'short', year: '2-digit' }).replace('.', ''),
      total: 0,
    };
  });
  const indicePorChave = new Map(meses.map((m, i) => [m.chave, i]));

  lista.forEach((v) => {
    (v.proximas || []).forEach((iso) => {
      const indice = indicePorChave.get(String(iso).slice(0, 7));
      if (indice != null) meses[indice].total += 1;
    });
  });
  return meses;
}

let graficoPrevisao = null;

function renderGraficoPrevisao(lista) {
  const canvas = el('manut-chart-previsao');
  if (!canvas || typeof Chart === 'undefined') return;
  graficoPrevisao?.destroy();

  const meses = calcularPrevisaoParadas(lista);

  graficoPrevisao = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: meses.map((m) => m.rotulo),
      datasets: [{
        label: 'Manutenções programadas',
        data: meses.map((m) => m.total),
        backgroundColor: token('--brand'),
        borderRadius: 5,
        maxBarThickness: 44,
      }],
    },
    options: {
      ...opcoesGrafico(),
      plugins: { ...opcoesGrafico().plugins, legend: { display: false } },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: { color: token('--text-2'), font: { family: 'Inter', size: 11.5, weight: '500' } },
        },
        y: {
          beginAtZero: true,
          grid: { color: token('--border-soft') },
          border: { display: false },
          ticks: { color: token('--text-3'), font: { family: 'Inter', size: 11.5 }, precision: 0 },
        },
      },
    },
  });
}

/** "Top 5 Críticos" (Hall of Shame): os 5 veículos/implementos com mais
 * frentes em "Parar urgente" AO MESMO TEMPO - a lista de quem mais precisa
 * de atenção imediata do gestor, ordenada pela gravidade. Clicar na placa
 * abre o Prontuário completo (mesma ação já usada em toda a tela). */
function calcularHallDaVergonha(lista) {
  return rankingCriticos(lista, 5);
}

function renderHallVergonha(lista) {
  const container = el('manut-hall-vergonha');
  if (!container) return;

  const itens = calcularHallDaVergonha(lista);
  if (!itens.length) {
    container.innerHTML = `
      <div class="empty empty--sm">
        <div class="empty__icon" style="background:var(--green-soft);color:var(--green)"><i class="ph ph-trophy"></i></div>
        <p class="empty__title">Nenhum crítico agora</p>
        <p class="empty__text">Nenhum veículo com mais de uma frente em "Parar urgente".</p>
      </div>`;
    return;
  }

  container.innerHTML = itens.map((v, indice) => `
    <button type="button" class="mhero-hall__row" data-action="abrir-prontuario" data-placa="${escapar(v.placa)}">
      <span class="mhero-hall__rank">${indice + 1}º</span>
      <span class="mhero-hall__id">
        <strong>${escapar(v.placa)}</strong>
        <span>${escapar(v.subtitulo) || '—'}</span>
      </span>
      <span class="badge badge--red">${v.criticos} crítico${v.criticos > 1 ? 's' : ''}</span>
    </button>`).join('');
}


function renderGraficoSaude(metricas) {
  const canvas = el('manut-chart-saude');
  if (!canvas || typeof Chart === 'undefined') return;

  graficoSaude?.destroy();

  const labels = ['Em dia', 'Programar parada', 'Parar urgente'];
  const valores = [metricas.em_dia || 0, metricas.programar_parada || 0, metricas.parar_urgente || 0];
  const cores = [token('--green'), token('--gold'), token('--red')];
  if (metricas.sem_dado) {
    // Veículo recém-cadastrado, ainda sem nenhuma troca/revisão lançada -
    // entra como fatia neutra para o total do gráfico sempre bater com o
    // "Veículos monitorados" do KPI.
    labels.push('Sem dado');
    valores.push(metricas.sem_dado);
    cores.push(token('--text-3'));
  }
  const vazio = valores.every((v) => v === 0);

  graficoSaude = new Chart(canvas.getContext('2d'), {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: vazio ? [1] : valores,
        backgroundColor: vazio ? [token('--surface-3')] : cores,
        borderWidth: 0,
        hoverOffset: 6,
      }],
    },
    options: {
      ...opcoesGrafico(),
      cutout: '75%',
      plugins: {
        ...opcoesGrafico().plugins,
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

function renderGraficoProximas(metricas) {
  const canvas = el('manut-chart-proximas');
  if (!canvas || typeof Chart === 'undefined') return;

  graficoProximas?.destroy();

  const itens = metricas.proximas_manutencoes || [];
  const labels = itens.map((i) => ROTULOS_CATEGORIA[i.item]?.titulo || i.item);

  graficoProximas = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Parar urgente',
          data: itens.map((i) => i.parar_urgente || 0),
          backgroundColor: token('--red'),
          borderRadius: 5,
          maxBarThickness: 30,
        },
        {
          label: 'Programar parada',
          data: itens.map((i) => i.programar_parada || 0),
          backgroundColor: token('--gold'),
          borderRadius: 5,
          maxBarThickness: 30,
        },
      ],
    },
    options: {
      ...opcoesGrafico(),
      interaction: { mode: 'index', intersect: false },
      plugins: {
        ...opcoesGrafico().plugins,
        legend: {
          display: true,
          position: 'bottom',
          labels: {
            padding: 14,
            usePointStyle: true,
            pointStyle: 'circle',
            boxWidth: 7,
            color: token('--text-2'),
            font: { family: 'Inter', size: 11.5, weight: '500' },
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { display: false },
          border: { display: false },
          ticks: { color: token('--text-2'), font: { family: 'Inter', size: 11, weight: '500' } },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: token('--border-soft') },
          border: { display: false },
          ticks: { color: token('--text-3'), font: { family: 'Inter', size: 11 }, precision: 0 },
        },
      },
    },
  });
}

/* ---------- Filtro local (busca + status) ---------- */
function veiculosFiltrados() {
  const busca = (valor('manut-busca') || '').toLowerCase();
  const status = valor('manut-filtro-status');

  return veiculos.filter((v) => {
    if (status && statusProximaRevisao(v) !== status) return false;
    if (!busca) return true;
    const alvo = [v.placa, v.marca, v.modelo, v.cod].filter(Boolean).join(' ').toLowerCase();
    return alvo.includes(busca);
  });
}

function renderLista() {
  const container = el('manut-lista');
  if (!container) return;

  const filtrados = veiculosFiltrados();

  container.innerHTML = filtrados.length
    ? filtrados
      .slice()
      .sort((a, b) => {
        const ordem = { 'PARAR URGENTE': 0, 'PROGRAMAR PARADA': 1, 'EM DIA': 2 };
        return (ordem[statusProximaRevisao(a)] ?? 3) - (ordem[statusProximaRevisao(b)] ?? 3);
      })
      .map(cardVeiculo).join('')
    : `<div class="card empty">
         <div class="empty__icon"><i class="ph ph-magnifying-glass"></i></div>
         <p class="empty__title">Nenhum veículo encontrado</p>
         <p class="empty__text">Ajuste a busca ou o filtro de status.</p>
       </div>`;
}

export function filtrarManutencao() {
  renderLista();
}

export function alternarCardManutencao(cod) {
  if (expandidos.has(cod)) expandidos.delete(cod); else expandidos.add(cod);
  renderLista();
}

/* ---------- Write-back: orquestração da edição inline ---------- */
/** Aplica o valor recém-salvo no objeto local (otimista) - evita recarregar a
 * lista inteira do Drive logo depois de salvar, o que traria de volta o
 * arquivo com o cache de fórmulas zerado (ver aviso de recálculo abaixo). */
function aplicarValorLocal(v, campo, valorBruto) {
  const numerico = (texto) => (texto === '' || texto == null ? null : Number(texto));
  if (campo.includes('.')) {
    const [chave, sub] = campo.split('.');
    if (!v[chave]) return;
    v[chave][sub] = sub.endsWith('_km') ? numerico(valorBruto) : (valorBruto || null);
  } else {
    v[campo] = campo === 'km_atual' ? numerico(valorBruto) : (valorBruto || null);
  }
}

/** Lê o valor bruto atual de um campo no objeto local - o espelho de leitura
 * de `aplicarValorLocal`, usado para mandar "o que eu via na tela antes de
 * editar" junto com toda escrita (concorrência otimista por célula, não por
 * arquivo inteiro - ver `atualizar_campo_veiculo` no backend). */
function valorBrutoDoCampo(v, campo) {
  if (campo.includes('.')) {
    const [chave, sub] = campo.split('.');
    return v[chave] ? v[chave][sub] ?? null : null;
  }
  return v[campo] ?? null;
}

export function editarCampoManutencao(cod, campo) {
  edicaoAtiva = { cod, campo };
  renderLista();
  const idInput = `edicao-${campo.replace(/\./g, '-')}-${cod}`;
  requestAnimationFrame(() => el(idInput)?.focus());
}

export function cancelarEdicaoManutencao() {
  edicaoAtiva = null;
  renderLista();
}

async function executarSalvamentoCampo(cod, campo, valorBruto, valorAnterior) {
  const v = veiculos.find((x) => x.cod === cod);
  if (!v) { cancelarEdicaoManutencao(); return; }

  salvandoCampo = { cod, campo };
  renderLista();

  try {
    const resposta = await api.atualizarManutencao({
      placa: v.placa, campo, valor: valorBruto === '' ? null : valorBruto,
      modificado_em: modificadoEmAtual, valor_anterior: valorAnterior,
    });
    modificadoEmAtual = resposta.modificado_em || modificadoEmAtual;
    invalidarDadosManutencao(); // a planilha mudou: Dashboard e abas se refazem na próxima visita
    aplicarValorLocal(v, campo, valorBruto);
    toast('Salvo com sucesso', `${LEGENDA_CAMPO[campo] || campo} atualizado(a).`);
    toast(
      'Recálculo pendente',
      'O status e as próximas datas dessa categoria podem ficar temporariamente em branco até a planilha ser reaberta no Excel ou Google Sheets pela equipe - o app continua mostrando os últimos valores conhecidos até lá.',
      'warning',
    );
  } catch (erro) {
    if (erro.status === 409 && erro.dados) {
      abrirConflitoManutencao(cod, campo, valorBruto, erro.dados);
    } else {
      toast('Não foi possível salvar', erro.message || 'Tente novamente.', 'error');
    }
  } finally {
    salvandoCampo = null;
    renderLista();
  }
}

export async function salvarCampoManutencao(cod, campo) {
  const idInput = `edicao-${campo.replace(/\./g, '-')}-${cod}`;
  const input = el(idInput);
  const valorBruto = input ? input.value : '';
  const v = veiculos.find((x) => x.cod === cod);
  if (!v) { cancelarEdicaoManutencao(); return; }

  const valorAnterior = valorBrutoDoCampo(v, campo);
  edicaoAtiva = null;
  await executarSalvamentoCampo(cod, campo, valorBruto, valorAnterior);
}

/* ---------- Conflito de edição: alguém mais alterou a MESMA célula ---------- */
/** Em vez de um toast de erro sem saída, mostra o valor atual (que veio junto
 * do 409) e deixa o usuário escolher: aceitar o valor mais recente, ou
 * sobrescrever mesmo assim - a escolha "no mínimo, um botão de Atualizar"
 * pedida, só que já resolvida no campo certo, sem precisar recarregar a
 * frota inteira. */
function abrirConflitoManutencao(cod, campo, valorNovo, dadosConflito) {
  const v = veiculos.find((x) => x.cod === cod);
  if (!v) return;

  conflitoPendente = {
    cod, campo, valorNovo,
    valorAtualServidor: dadosConflito.valor_atual ?? null,
    modificadoEm: dadosConflito.modificado_em || null,
  };

  const rotulo = LEGENDA_CAMPO[campo] || campo;
  const formatar = (bruto) => {
    if (bruto == null || bruto === '') return 'Sem dado';
    if (campo.includes('.ultima_km') || campo === 'km_atual') return `${formatarKm(bruto)} km`;
    if (campo.includes('.ultima_data')) return dataBRSimples(bruto);
    return String(bruto);
  };

  el('conflito-manut-campo').textContent = `${escapar(v.placa)} · ${escapar(rotulo)}`;
  el('conflito-manut-valor-novo').textContent = formatar(valorNovo);
  el('conflito-manut-valor-atual').textContent = formatar(conflitoPendente.valorAtualServidor);
  abrirModal('modal-conflito-manutencao');
}

export function usarValorAtualConflito() {
  if (!conflitoPendente) return;
  const { cod, campo, valorAtualServidor, modificadoEm } = conflitoPendente;
  const v = veiculos.find((x) => x.cod === cod);
  if (v) aplicarValorLocal(v, campo, valorAtualServidor);
  if (modificadoEm) modificadoEmAtual = modificadoEm;
  conflitoPendente = null;
  fecharModal('modal-conflito-manutencao');
  renderLista();
  toast('Atualizado', 'O campo foi atualizado com o valor mais recente da planilha.', 'info');
}

export async function sobrescreverConflito() {
  if (!conflitoPendente) return;
  const { cod, campo, valorNovo, valorAtualServidor, modificadoEm } = conflitoPendente;
  conflitoPendente = null;
  fecharModal('modal-conflito-manutencao');
  if (modificadoEm) modificadoEmAtual = modificadoEm;
  // Agora que o usuário viu o valor atual e decidiu sobrescrever mesmo assim,
  // manda esse valor como "anterior" - a checagem por célula do backend passa
  // porque, do ponto de vista dele, nada mudou desde essa leitura mais nova.
  await executarSalvamentoCampo(cod, campo, valorNovo, valorAtualServidor);
}

document.addEventListener('keydown', (evento) => {
  const campo = evento.target.closest('[data-action-enter]');
  if (!campo) return;
  if (evento.key === 'Enter') {
    evento.preventDefault();
    salvarCampoManutencao(campo.dataset.cod, campo.dataset.campo);
  } else if (evento.key === 'Escape') {
    evento.preventDefault();
    cancelarEdicaoManutencao();
  }
});

/* ==========================================================
 * Sub-navegação (Dashboard / Motor e Óleo / Alinhamento / Chassi & 5ª Roda /
 * Carretas) - materializa as demais categorias do Prontuário como tabelas com
 * a frota inteira (uma placa por linha), em vez de uma placa de cada vez.
 * Cada aba de categoria é buscada e cacheada só na primeira vez que é aberta.
 * ========================================================== */
const ABAS_CATEGORIA = {
  alinhamento: [{ chave: 'alinhamento', container: 'manut-cat-alinhamento' }],
  integridade: [{ chave: 'integridade_chassi', container: 'manut-cat-integridade_chassi' }],
  'quinta-roda': [{ chave: 'quinta_roda', container: 'manut-cat-quinta_roda' }],
  carretas: [
    { chave: 'preventiva_carretas', container: 'manut-cat-preventiva_carretas' },
  ],
  'sr-tanques': [{ chave: 'sr_tanques', container: 'manut-cat-sr_tanques' }],
};
const CATEGORIAS_DUPLAS = new Set(['integridade_chassi']);
const abasCarregadas = new Set();

/** SR-Tanques é uma categoria só-por-tempo, sem barra de progresso (regra dos
 * 180 dias - ver `_calcular_sr_tanques` no backend): o corpo do card mostra
 * só a Última revisão e a Próxima (+180 dias) lado a lado - o Badge de status
 * já aparece no cabeçalho do card/seção, então não se repete aqui. */
function corpoSrTanques(item) {
  return `
    <div class="mprog-row mprog-row--sr">
      <div class="mprog-row__data">
        <span class="mprog-row__data-label">Última revisão</span>
        <span class="mprog-row__data-valor">${dataBRSimples(item.ultima_data)}</span>
      </div>
      <i class="ph ph-arrow-right mprog-row__seta"></i>
      <div class="mprog-row__data mprog-row__data--fim">
        <span class="mprog-row__data-label">Próxima revisão <small>(+180 dias)</small></span>
        <span class="mprog-row__data-valor">${dataBRSimples(item.proxima)}</span>
      </div>
    </div>`;
}

function cardCategoriaLinha(chave, linha) {
  const subtitulo = [linha.marca, linha.modelo, linha.tipo].filter(Boolean).join(' · ') || '—';
  const item = linha.item;

  if (chave === 'sr_tanques') {
    return `
      <article class="mcard">
        <div class="mcard__head">
          <span class="mcard__dot mcard__dot--${corDoStatus(item.status)}"></span>
          <div class="mcard__id">
            <strong>${escapar(linha.placa)}</strong>
            <span>${escapar(subtitulo)}</span>
          </div>
          <span class="badge ${badgeDoStatus(item.status)}"><i class="ph ${iconeDoStatus(item.status)}"></i>${rotuloStatus(item.status)}</span>
        </div>
        <div class="mcard__body">${corpoSrTanques(item)}</div>
      </article>`;
  }

  const ehDupla = CATEGORIAS_DUPLAS.has(chave);
  const status = ehDupla ? item.status_geral : item.status;
  const corpo = ehDupla
    ? `${linhaCategoria('Por tempo', 'ph-calendar', item.tempo)}${linhaCategoria('Por quilometragem', 'ph-gauge', item.km)}`
    : linhaCategoria(item.unidade === 'km' ? 'Por quilometragem' : 'Por tempo', item.unidade === 'km' ? 'ph-gauge' : 'ph-calendar', item);

  return `
    <article class="mcard">
      <div class="mcard__head">
        <span class="mcard__dot mcard__dot--${corDoStatus(status)}"></span>
        <div class="mcard__id">
          <strong>${escapar(linha.placa)}</strong>
          <span>${escapar(subtitulo)}</span>
        </div>
        <span class="badge ${badgeDoStatus(status)}"><i class="ph ${iconeDoStatus(status)}"></i>${rotuloStatus(status)}</span>
      </div>
      <div class="mcard__body">${corpo}</div>
    </article>`;
}

function renderizarCategoriaNoContainer(chave, containerId, linhas) {
  const container = el(containerId);
  if (!container) return;

  if (!linhas.length) {
    container.innerHTML = `<div class="card empty">
      <div class="empty__icon"><i class="ph ph-file-dashed"></i></div>
      <p class="empty__title">Nenhum dado encontrado</p>
      <p class="empty__text">Essa categoria não aparece em nenhum veículo no momento.</p>
    </div>`;
    return;
  }

  const ordem = { 'PARAR URGENTE': 0, 'PROGRAMAR PARADA': 1, 'EM DIA': 2 };
  const ehDupla = CATEGORIAS_DUPLAS.has(chave);
  const statusDe = (l) => (ehDupla ? l.item.status_geral : l.item.status);
  container.innerHTML = linhas
    .slice()
    .sort((a, b) => (ordem[statusDe(a)] ?? 3) - (ordem[statusDe(b)] ?? 3))
    .map((linha) => cardCategoriaLinha(chave, linha))
    .join('');
}

/** Busca TODAS as categorias de uma aba numa ÚNICA chamada (o backend monta o
 * Prontuário só uma vez e fatia por categoria) - antes, uma aba com N
 * categorias (ex.: "Carretas" = 3) disparava N chamadas caras e concorrentes,
 * cada uma remontando o Prontuário inteiro do zero. */
async function carregarCategoriasDaAba(aba) {
  const config = ABAS_CATEGORIA[aba];
  if (!config) return;

  config.forEach(({ container }) => {
    const el_ = el(container);
    if (el_) el_.innerHTML = esqueleto(3, 88);
  });

  const chaves = config.map((c) => c.chave);
  try {
    const resposta = await api.categoriasManutencao(chaves);
    if (resposta.configurado === false) {
      config.forEach(({ container }) => {
        const el_ = el(container);
        if (el_) {
          el_.innerHTML = `<div class="card empty">
            <div class="empty__icon"><i class="ph ph-plug"></i></div>
            <p class="empty__title">Integração ainda não configurada</p>
            <p class="empty__text">${escapar(resposta.mensagem || '')}</p>
          </div>`;
        }
      });
      return;
    }
    const categorias = resposta.categorias || {};
    config.forEach(({ chave, container }) => renderizarCategoriaNoContainer(chave, container, categorias[chave] || []));
  } catch (erro) {
    config.forEach(({ container }) => {
      const el_ = el(container);
      if (el_) {
        el_.innerHTML = `<div class="card empty">
          <p class="empty__title">Não foi possível carregar esses dados agora.</p>
        </div>`;
      }
    });
  }
}

const CARREGADORES_ABA_EXTRA = { cadastro: carregarCadastroFrota, resumo: carregarResumoAnalitico, agendar: carregarDemandas };

export function trocarAbaManutencao(aba) {
  qsa('.mtab', el('manut-tabs')).forEach((botao) => botao.classList.toggle('is-active', botao.dataset.tab === aba));
  ['dashboard', 'oleo', 'alinhamento', 'integridade', 'quinta-roda', 'carretas', 'sr-tanques', 'cadastro', 'resumo', 'agendar'].forEach((nome) => {
    mostrar(`manut-painel-${nome}`, nome === aba);
  });

  if (ABAS_CATEGORIA[aba] && !abasCarregadas.has(aba)) {
    abasCarregadas.add(aba);
    carregarCategoriasDaAba(aba);
  }
  if (CARREGADORES_ABA_EXTRA[aba] && !abasCarregadas.has(aba)) {
    abasCarregadas.add(aba);
    CARREGADORES_ABA_EXTRA[aba]();
  }
}

/* ==========================================================
 * Sincronização / Importar Base — import de emergência: substitui a
 * planilha INTEIRA no Drive por um .xlsx enviado por arrastar-e-soltar.
 * Upload feito com XMLHttpRequest (não `fetch`) para ter uma barra de
 * progresso real, medida nos bytes de verdade sendo enviados ao servidor.
 * ========================================================== */
export function abrirImportarManutencao() {
  mostrar('manut-dropzone-progresso', false);
  const preenchimento = el('manut-dropzone-fill');
  if (preenchimento) {
    preenchimento.style.width = '0%';
    preenchimento.classList.remove('is-complete');
  }
  abrirModal('modal-importar');
}

export function enviarArquivoManutencao(arquivo) {
  if (!arquivo.name.toLowerCase().endsWith('.xlsx')) {
    toast('Arquivo inválido', 'Envie um arquivo .xlsx.', 'warning');
    return;
  }
  if (arquivo.size > 30 * 1024 * 1024) {
    toast('Arquivo muito grande', 'O limite para importação é de 30 MB.', 'warning');
    return;
  }

  const progresso = el('manut-dropzone-progresso');
  const preenchimento = el('manut-dropzone-fill');
  const percentual = el('manut-dropzone-percentual');
  el('manut-dropzone-nome').textContent = arquivo.name;
  mostrar(progresso, true);
  preenchimento.classList.remove('is-complete');
  preenchimento.style.width = '0%';
  percentual.textContent = '0%';

  const leitor = new FileReader();
  leitor.onload = () => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/manutencao/importar');
    xhr.setRequestHeader('Content-Type', 'application/json');
    const tokenSessao = sessao.obter();
    if (tokenSessao) xhr.setRequestHeader('Authorization', `Bearer ${tokenSessao}`);

    xhr.upload.onprogress = (evento) => {
      if (!evento.lengthComputable) return;
      const pct = Math.round((evento.loaded / evento.total) * 100);
      preenchimento.style.width = `${pct}%`;
      percentual.textContent = `${pct}%`;
    };

    xhr.onload = () => {
      let dados = {};
      try { dados = JSON.parse(xhr.responseText); } catch (falha) { /* resposta vazia */ }

      if (xhr.status >= 200 && xhr.status < 300 && dados.success !== false) {
        percentual.textContent = 'Concluído';
        preenchimento.style.width = '100%';
        preenchimento.classList.add('is-complete');
        toast('Planilha sincronizada', 'A base foi substituída no Google Drive com sucesso.');
        abasCarregadas.clear();
        setTimeout(() => {
          fecharModal('modal-importar');
          renderManutencao(true);
        }, 900);
      } else {
        toast('Não foi possível sincronizar', dados.message || 'Tente novamente.', 'error');
        mostrar(progresso, false);
      }
    };
    xhr.onerror = () => {
      toast('Sem conexão', 'Não foi possível falar com o servidor.', 'error');
      mostrar(progresso, false);
    };
    xhr.send(JSON.stringify({ nome_arquivo: arquivo.name, conteudo: leitor.result }));
  };
  leitor.readAsDataURL(arquivo);
}

/* ==========================================================
 * Cadastro de Frota — CRUD real: lista os Cavalos (aba Frota) e Carretas
 * (aba info-carretas) já cadastrados, com busca + paginação, e um formulário
 * de cadastro novo que grava direto na planilha (write-back cirúrgico, mesma
 * arquitetura do resto do módulo - nunca desloca nenhuma linha existente).
 * ========================================================== */
const CADASTRO_POR_PAGINA = 12;
let cadastroVeiculos = [];
let cadastroPagina = 1;
let modificadoEmCadastro = null;
let tipoCadastroAtivo = 'cavalo';

function cadastroFiltrados() {
  const busca = (valor('cadastro-busca') || '').toLowerCase();
  if (!busca) return cadastroVeiculos;
  return cadastroVeiculos.filter((v) => [v.placa, v.marca, v.modelo, v.cod, v.tipo]
    .filter(Boolean).join(' ').toLowerCase().includes(busca));
}

function renderTabelaCadastro() {
  const corpo = el('cadastro-tabela-corpo');
  if (!corpo) return;

  const filtrados = cadastroFiltrados();
  if (!filtrados.length) {
    mostrar('cadastro-tabela-wrap', false);
    mostrar('cadastro-vazio', true);
    mostrar('cadastro-paginacao', false);
    return;
  }
  mostrar('cadastro-tabela-wrap', true);
  mostrar('cadastro-vazio', false);

  const totalPaginas = Math.max(1, Math.ceil(filtrados.length / CADASTRO_POR_PAGINA));
  cadastroPagina = Math.min(cadastroPagina, totalPaginas);
  const inicio = (cadastroPagina - 1) * CADASTRO_POR_PAGINA;

  corpo.innerHTML = filtrados.slice(inicio, inicio + CADASTRO_POR_PAGINA).map((v) => `
    <tr class="${v.oculto ? 'is-oculto' : ''}">
      <td><span class="badge ${v.tipo === 'Carreta' ? 'badge--brand' : 'badge--neutral'}">${escapar(v.tipo || '—')}</span></td>
      <td><strong>${escapar(v.placa)}</strong>${v.oculto ? ' <span class="badge badge--neutral">Oculto</span>' : ''}</td>
      <td>${escapar(v.marca || '—')}</td>
      <td>${escapar(v.modelo || '—')}</td>
      <td>${v.ano_fab ?? '—'}${v.ano_mod && v.ano_mod !== v.ano_fab ? `/${v.ano_mod}` : ''}</td>
      <td>${escapar((v.tipo === 'Carreta' ? v.implemento : v.centro_custo) || '—')}</td>
      <td class="data-table__acoes">
        <label class="switch" title="${v.oculto ? 'Não aparece nas telas — ligue para voltar a aparecer' : 'Aparece nas telas — desligue para esconder'}">
          <input type="checkbox" data-action="visibilidade-veiculo" data-placa="${escapar(v.placa)}" ${v.oculto ? '' : 'checked'}
                 aria-label="${escapar(v.placa)} aparece nas telas de Manutenção">
          <span class="switch__track"><span class="switch__thumb"></span></span>
          <span class="switch__label">${v.oculto ? 'Oculto' : 'Aparece'}</span>
        </label>
      </td>
    </tr>`).join('');

  mostrar('cadastro-paginacao', totalPaginas > 1);
  const info = el('cadastro-pagina-info');
  if (info) {
    const ocultos = filtrados.filter((v) => v.oculto).length;
    info.textContent = `Página ${cadastroPagina} de ${totalPaginas} · ${filtrados.length} cadastrados${ocultos ? ` · ${ocultos} oculto${ocultos > 1 ? 's' : ''}` : ''}`;
  }
}

export function filtrarCadastro() {
  cadastroPagina = 1;
  renderTabelaCadastro();
}

export function mudarPaginaCadastro(delta) {
  cadastroPagina += delta;
  renderTabelaCadastro();
}

function carregarCadastroFrota() {
  const corpo = el('cadastro-tabela-corpo');
  if (corpo) corpo.innerHTML = `<tr><td colspan="7">${esqueleto(1, 20)}</td></tr>`;

  api.cadastroFrota().then((resposta) => {
    if (resposta.configurado === false) {
      mostrar('cadastro-tabela-wrap', false);
      mostrar('cadastro-paginacao', false);
      const vazio = el('cadastro-vazio');
      mostrar(vazio, true);
      if (vazio) {
        vazio.innerHTML = `<div class="empty__icon"><i class="ph ph-plug"></i></div>
          <p class="empty__title">Integração ainda não configurada</p>
          <p class="empty__text">${escapar(resposta.mensagem || '')}</p>`;
      }
      return;
    }
    cadastroVeiculos = resposta.veiculos || [];
    modificadoEmCadastro = resposta.modificado_em || null;
    renderTabelaCadastro();
  }).catch(() => {
    if (corpo) corpo.innerHTML = `<tr><td colspan="7">Não foi possível carregar o cadastro agora.</td></tr>`;
  });
}

function recarregarCadastro() {
  abasCarregadas.delete('cadastro');
  carregarCadastroFrota();
}

/** Gerenciar: liga/desliga se um veículo APARECE nas telas de Manutenção (Dashboard,
 * análises, abas por categoria, Revisões e Resumo). Não altera a planilha - a escolha
 * fica em `veiculos_ocultos.json` (ver `manutencao.definir_visibilidade`), então não
 * precisa de confirmação: desfazer é ligar de novo. O veículo continua na lista do
 * Cadastro (esmaecido) e o Prontuário dele ainda abre se a placa for pesquisada. */
async function alternarVisibilidadeVeiculo(caixa) {
  const { placa } = caixa.dataset;
  const visivel = caixa.checked;
  caixa.disabled = true;
  try {
    await api.definirVisibilidadeVeiculo(placa, visivel);
    const veiculo = cadastroVeiculos.find((v) => v.placa === placa);
    if (veiculo) veiculo.oculto = !visivel;
    renderTabelaCadastro();
    toast(visivel ? 'Veículo visível' : 'Veículo oculto',
      visivel ? `${placa} voltou a aparecer nas telas.` : `${placa} não aparece mais nas telas de Manutenção.`);
    atualizarTelasAposVisibilidade();
  } catch (erro) {
    caixa.checked = !visivel;
    caixa.disabled = false;
    toast('Não foi possível salvar', erro.message || 'Tente novamente.', 'error');
  }
}

/** Dashboard, Revisões e abas por categoria foram montados com a lista anterior: refaz
 * a lista de Revisões e a saúde da frota agora, e as demais abas na próxima visita.
 * Barato - o servidor já tem a planilha processada, só reaplica o filtro de ocultos. */
function atualizarTelasAposVisibilidade() {
  abasCarregadas.clear();
  abasCarregadas.add('cadastro');
  api.manutencao().then((resposta) => {
    if (resposta.configurado === false) return;
    veiculos = resposta.veiculos || [];
    modificadoEmAtual = resposta.modificado_em || modificadoEmAtual;
    renderLista();
  }).catch(() => { /* a lista de Revisões se corrige na próxima atualização */ });
  carregarSaudeFrota();
}

document.addEventListener('change', (evento) => {
  const caixa = evento.target.closest?.('[data-action="visibilidade-veiculo"]');
  if (caixa) alternarVisibilidadeVeiculo(caixa);
});

/* ---------- Modal "Novo Veículo/Carreta" ---------- */
export function abrirNovoVeiculo() {
  el('form-novo-veiculo')?.reset();
  selecionarTipoCadastro('cavalo');
  abrirModal('modal-novo-veiculo');
}

export function selecionarTipoCadastro(tipo) {
  tipoCadastroAtivo = tipo;
  qsa('[data-action="tipo-cadastro"]').forEach((botao) => botao.classList.toggle('is-active', botao.dataset.tipo === tipo));
  mostrar('nv-campos-cavalo', tipo === 'cavalo');
  mostrar('nv-campos-carreta', tipo === 'carreta');
}

export async function salvarNovoVeiculo() {
  const placa = (valor('nv-placa') || '').trim();
  if (!placa) {
    toast('Informe a placa', 'A placa é obrigatória para o cadastro.', 'warning');
    return;
  }

  const dados = { tipo: tipoCadastroAtivo, placa, modificado_em: modificadoEmCadastro };
  if (tipoCadastroAtivo === 'cavalo') {
    Object.assign(dados, {
      cod: valor('nv-cod'), marca: valor('nv-marca'), modelo: valor('nv-modelo'),
      ano_fab: valor('nv-ano-fab'), ano_mod: valor('nv-ano-mod'), centro_custo: valor('nv-centro-custo'),
    });
  } else {
    Object.assign(dados, {
      uf: valor('nv-uf'), renavam: valor('nv-renavam'), chassi: valor('nv-chassi'),
      marca: valor('nv-marca-carreta'), modelo: valor('nv-modelo-carreta'),
      ano_fab: valor('nv-ano-fab-carreta'), ano_mod: valor('nv-ano-mod-carreta'),
      eixos: valor('nv-eixos'), capacidade: valor('nv-capacidade'), implemento: valor('nv-implemento'),
    });
  }

  const botao = document.querySelector('[data-action="salvar-novo-veiculo"]');
  if (botao) { botao.disabled = true; botao.classList.add('is-loading'); }

  try {
    const resposta = await api.cadastrarVeiculo(dados);
    modificadoEmCadastro = resposta.modificado_em || modificadoEmCadastro;
    toast('Veículo cadastrado', `${resposta.placa} foi adicionado(a) à frota com sucesso.`);
    fecharModal('modal-novo-veiculo');
    invalidarDadosManutencao(); // o veículo novo precisa aparecer no Dashboard e nas abas
    recarregarCadastro();
    abasCarregadas.add('cadastro'); // a lista do Cadastro acabou de ser recarregada logo acima
  } catch (erro) {
    toast('Não foi possível cadastrar', erro.message || 'Tente novamente.', 'error');
  } finally {
    if (botao) { botao.disabled = false; botao.classList.remove('is-loading'); }
  }
}

/* ==========================================================
 * Resumo Analítico — "Painel de Guerra": matriz Placa × categoria com badges
 * coloridos, recriando (melhor) a aba RESUMO ANALITICO original. Somente
 * leitura: toda coluna vem de dados já cruzados por `montar_prontuario_frota`
 * (pior status entre tempo e km, não só tempo como a fórmula original) ou de
 * 2 campos manuais lidos direto da aba - ver backend/manutencao.py.
 * ========================================================== */
let resumoLinhas = [];

function badgeSimNao(valorBooleano) {
  return valorBooleano
    ? '<span class="badge badge--green"><i class="ph ph-check-circle"></i>Sim</span>'
    : '<span class="badge badge--neutral">Não</span>';
}

function celulaStatusMatriz(status) {
  if (!status) return '<span class="badge badge--neutral">—</span>';
  return `<span class="badge ${badgeDoStatus(status)}"><i class="ph ${iconeDoStatus(status)}"></i>${rotuloStatus(status)}</span>`;
}

function resumoFiltrados() {
  const busca = (valor('resumo-busca') || '').toLowerCase();
  if (!busca) return resumoLinhas;
  return resumoLinhas.filter((l) => [l.placa, l.marca, l.modelo].filter(Boolean).join(' ').toLowerCase().includes(busca));
}

function renderTabelaResumo() {
  const corpo = el('resumo-tabela-corpo');
  if (!corpo) return;
  const filtradas = resumoFiltrados();
  corpo.innerHTML = filtradas.length
    ? filtradas.map((l) => `
      <tr>
        <td>
          <strong>${escapar(l.placa)}</strong>
          <div class="text-muted">${escapar([l.marca, l.modelo].filter(Boolean).join(' · ') || '—')}</div>
        </td>
        <td>${celulaStatusMatriz(l.revisao)}</td>
        <td>${celulaStatusMatriz(l.quinta_roda)}</td>
        <td>${celulaStatusMatriz(l.integridade)}</td>
        <td>${celulaStatusMatriz(l.sr_tanques)}</td>
        <td>${badgeSimNao(l.no_patio)}</td>
      </tr>`).join('')
    : '<tr><td colspan="6">Nenhum veículo encontrado.</td></tr>';
}

export function filtrarResumoAnalitico() {
  renderTabelaResumo();
}

function carregarResumoAnalitico() {
  const corpo = el('resumo-tabela-corpo');
  if (corpo) corpo.innerHTML = `<tr><td colspan="6">${esqueleto(1, 20)}</td></tr>`;

  api.resumoAnalitico().then((resposta) => {
    if (resposta.configurado === false) {
      if (corpo) corpo.innerHTML = `<tr><td colspan="6">${escapar(resposta.mensagem || 'Integração ainda não configurada.')}</td></tr>`;
      return;
    }
    resumoLinhas = resposta.linhas || [];
    renderTabelaResumo();
  }).catch(() => {
    if (corpo) corpo.innerHTML = '<tr><td colspan="6">Não foi possível carregar o resumo analítico agora.</td></tr>';
  });
}

/* ---------- Busca inteligente de placa (autocomplete compartilhado) ----------
 * Usado tanto por "Agendar Demanda" (abaixo) quanto pelo Prontuário (mais
 * adiante no arquivo) - por isso vive antes dos dois, em vez de dentro de
 * qualquer um. */
let placasAutocomplete = null;
let promessaPlacasAutocomplete = null;

export function garantirPlacasAutocomplete() {
  if (placasAutocomplete) return Promise.resolve(placasAutocomplete);
  if (promessaPlacasAutocomplete) return promessaPlacasAutocomplete;

  promessaPlacasAutocomplete = api.cadastroFrota().then((resposta) => {
    placasAutocomplete = resposta.configurado === false ? [] : (resposta.veiculos || []);
    return placasAutocomplete;
  }).catch(() => {
    promessaPlacasAutocomplete = null;
    return [];
  });
  return promessaPlacasAutocomplete;
}

/** Envolve em `<mark>` o trecho de `texto` que bate com `busca` (case-insensitive) -
 * a "Busca Dinâmica de Verdade" pedida: destaca visualmente o que a pessoa
 * digitou dentro de cada sugestão, em vez de só listar placas soltas. */
function destacarMatch(texto, busca) {
  if (!busca) return escapar(texto);
  const indice = texto.toLowerCase().indexOf(busca.toLowerCase());
  if (indice === -1) return escapar(texto);
  const antes = texto.slice(0, indice);
  const meio = texto.slice(indice, indice + busca.length);
  const depois = texto.slice(indice + busca.length);
  return `${escapar(antes)}<mark class="autocomplete-lista__match">${escapar(meio)}</mark>${escapar(depois)}`;
}

/** Fábrica de um campo de placa com sugestões (Busca Inteligente) - usada tanto
 * pelo Prontuário (seleciona = abre o prontuário na hora) quanto por "Agendar
 * Demanda" (seleciona = só guarda a placa escolhida no formulário). Cada
 * instância cuida do próprio par input/lista, sem compartilhar estado com a
 * outra. */
export function criarAutocompletePlaca(inputId, listaId, aoSelecionar) {
  let sugestoes = [];
  let indiceAtivo = -1;
  let buscaAtiva = '';

  function fechar() {
    const lista = el(listaId);
    if (lista) { lista.classList.add('hidden'); lista.innerHTML = ''; }
    el(inputId)?.setAttribute('aria-expanded', 'false');
    indiceAtivo = -1;
    sugestoes = [];
  }

  function selecionar(indice) {
    const escolhida = sugestoes[indice];
    if (!escolhida) return;
    fechar();
    aoSelecionar(escolhida);
  }

  function render(lista_, busca) {
    sugestoes = lista_;
    indiceAtivo = -1;
    buscaAtiva = busca || '';
    const lista = el(listaId);
    if (!lista) return;

    lista.innerHTML = lista_.length
      ? lista_.map((v, indice) => `
        <button type="button" class="autocomplete-lista__item" data-indice="${indice}">
          <strong>${destacarMatch(v.placa, buscaAtiva)}</strong>
          <span>${escapar([v.tipo, v.marca, v.modelo].filter(Boolean).join(' · '))}</span>
        </button>`).join('')
      : '<p class="autocomplete-lista__vazio">Nenhuma placa encontrada</p>';
    lista.classList.remove('hidden');
    el(inputId)?.setAttribute('aria-expanded', 'true');

    // `mousedown` (não `click`): dispara antes do `blur` do input, que senão
    // fecharia a lista primeiro e cancelaria a seleção por clique.
    qsa('.autocomplete-lista__item', lista).forEach((item) => {
      item.addEventListener('mousedown', (evento) => {
        evento.preventDefault();
        selecionar(Number(item.dataset.indice));
      });
    });
  }

  function filtrar() {
    const busca = (valor(inputId) || '').trim().toLowerCase();
    if (!busca) { fechar(); return; }

    garantirPlacasAutocomplete().then((veiculos) => {
      if ((valor(inputId) || '').trim().toLowerCase() !== busca) return; // resposta velha - já digitou outra coisa

      const comeca = [];
      const contem = [];
      veiculos.forEach((v) => {
        const placa = (v.placa || '').toLowerCase();
        const outros = [v.marca, v.modelo].filter(Boolean).join(' ').toLowerCase();
        if (placa.startsWith(busca)) comeca.push(v);
        else if (placa.includes(busca) || outros.includes(busca)) contem.push(v);
      });
      render([...comeca, ...contem].slice(0, 8), busca);
    });
  }

  function navegar(direcao) {
    if (!sugestoes.length) return;
    indiceAtivo = (indiceAtivo + direcao + sugestoes.length) % sugestoes.length;
    qsa('.autocomplete-lista__item', el(listaId)).forEach((item, i) => item.classList.toggle('is-ativo', i === indiceAtivo));
  }

  function confirmarAtivo() {
    if (indiceAtivo < 0 || !sugestoes[indiceAtivo]) return false;
    selecionar(indiceAtivo);
    return true;
  }

  document.addEventListener('click', (evento) => {
    if (!evento.target.closest(`#${listaId}`) && evento.target.id !== inputId) fechar();
  });

  return { filtrar, navegar, confirmarAtivo, fechar, selecionarPorIndice: selecionar };
}

/* ==========================================================
 * Agendar Demanda — cria demandas de manutenção que são Tarefas de verdade
 * (backend/models.py: `Tarefa.veiculo_placa`), reaproveitando o MESMO editor
 * de Estrutura/Demandas - `abrirNovaTarefa`/`abrirEdicaoTarefa`/`salvarTarefa`
 * de `structure.js`, sem nenhuma cópia: mesmo Kanban, Calendário, automação
 * de status por data, dependências e etiquetas salvas. O backend resolve
 * sozinho o Projeto onde essas tarefas moram (auto-provisionado dentro da
 * Pasta do setor Manutenção) - esta seção só cuida da tabela de listagem e
 * de saber o id desse Projeto para poder abrir o editor compartilhado.
 * ========================================================== */
let demandas = [];
let projetoAgendaManutencaoId = null;
let promessaProjetoAgenda = null;

function badgeStatusTarefa(status) {
  const cor = status?.cor || '#94A3B8';
  return `<span class="badge" style="background:${cor}1f;color:${cor};border-color:${cor}55">${escapar(status?.nome || 'Pendente')}</span>`;
}

function demandasFiltradas() {
  const busca = (valor('demanda-busca') || '').toLowerCase();
  if (!busca) return demandas;
  return demandas.filter((d) => [d.veiculo_placa, d.titulo].filter(Boolean).join(' ').toLowerCase().includes(busca));
}

function renderTabelaDemandas() {
  const corpo = el('demanda-tabela-corpo');
  if (!corpo) return;
  const filtradas = demandasFiltradas();

  mostrar('demanda-tabela-wrap', !!filtradas.length);
  mostrar('demanda-vazio', !filtradas.length);
  if (!filtradas.length) return;

  corpo.innerHTML = filtradas.map((d) => `
    <tr class="tr-clicavel" data-action="editar-demanda" data-id="${escapar(d.id)}" title="Clique para editar - abre o mesmo editor de Estrutura/Demandas">
      <td><strong>${escapar(d.veiculo_placa || '—')}</strong></td>
      <td>${escapar(d.titulo)}</td>
      <td>${escapar(d.responsavel_nome || 'Não atribuído')}</td>
      <td>${d.data_inicio_real ? dataBRSimples(d.data_inicio_real) : '—'}</td>
      <td>${d.data_fim_real ? dataBRSimples(d.data_fim_real) : '—'}</td>
      <td>${badgeStatusTarefa(d.status)}</td>
    </tr>`).join('');
}

export function filtrarDemandas() {
  renderTabelaDemandas();
}

function carregarDemandas() {
  const corpo = el('demanda-tabela-corpo');
  if (corpo) corpo.innerHTML = `<tr><td colspan="6">${esqueleto(1, 20)}</td></tr>`;

  api.demandasManutencao().then((resposta) => {
    demandas = (resposta.demandas || []).slice().sort((a, b) => (a.concluida === b.concluida ? 0 : a.concluida ? 1 : -1));
    projetoAgendaManutencaoId = resposta.projeto_id || null;
    renderTabelaDemandas();
  }).catch(() => {
    if (corpo) corpo.innerHTML = '<tr><td colspan="6">Não foi possível carregar as demandas agendadas agora.</td></tr>';
  });
}

export function recarregarDemandas() {
  abasCarregadas.delete('agendar');
  carregarDemandas();
}

/** Garante que já sabemos o id do Projeto "Agenda de Manutenção" antes de
 * abrir o editor compartilhado de tarefa - normalmente já está em cache
 * (a aba "Agendar Demanda" busca isso ao ser aberta), mas o botão "Agendar"
 * dentro do Prontuário pode ser o primeiro contato da sessão com esse dado. */
export function garantirProjetoAgendaManutencao() {
  if (projetoAgendaManutencaoId) return Promise.resolve(projetoAgendaManutencaoId);
  if (promessaProjetoAgenda) return promessaProjetoAgenda;

  promessaProjetoAgenda = api.demandasManutencao().then((resposta) => {
    projetoAgendaManutencaoId = resposta.projeto_id || null;
    return projetoAgendaManutencaoId;
  }).catch(() => {
    promessaProjetoAgenda = null;
    return null;
  });
  return promessaProjetoAgenda;
}

/* ---------- Orquestração ---------- */
// Uma vez carregados, os dados valem para TODAS as abas: voltar para a tela de Manutenção
// (menu, trocar de aba) NÃO baixa nada de novo. Só recarrega no botão "atualizar", depois
// de importar uma planilha, ou se os dados já tiverem mais que isto (o servidor também
// guarda a planilha por esse mesmo tempo antes de conferir o Drive de novo).
const VALIDADE_DADOS_MS = 5 * 60 * 1000;
let dadosCarregadosEm = 0;

/** Marca os dados da tela como velhos: a próxima entrada em Manutenção busca tudo de novo.
 * Chamar depois de qualquer gravação na planilha. */
function invalidarDadosManutencao() {
  dadosCarregadosEm = 0;
  abasCarregadas.clear();
}

export async function renderManutencao(forcar) {
  const naoConfigurado = el('manut-nao-configurado');
  const conteudo = el('manut-conteudo');
  // Voltar para a tela de Manutenção (clicar no menu, recarregar) sempre cai no Dashboard,
  // nunca numa página de análise que tinha ficado aberta.
  fecharDetalheManutencao();

  const recarregar = forcar === true || !dadosCarregadosEm || (Date.now() - dadosCarregadosEm) > VALIDADE_DADOS_MS;
  if (!recarregar) {
    naoConfigurado?.classList.add('hidden');
    if (!detalheManutencaoAberto()) conteudo?.classList.remove('hidden');
    renderDashboardSaudeFrota(); // só redesenha com o que já está na memória (gráficos após a tela reaparecer)
    return;
  }

  const container = el('manut-lista');
  if (container && !container.children.length) {
    container.innerHTML = esqueleto(4, 88);
  }
  naoConfigurado?.classList.add('hidden');
  abasCarregadas.clear(); // dados novos: as abas de categoria também precisam vir frescas na próxima visita

  try {
    const resposta = await api.manutencao(forcar === true);
    veiculos = resposta.veiculos || [];

    if (resposta.configurado === false) {
      dadosCarregadosEm = 0;
      naoConfigurado?.classList.remove('hidden');
      conteudo?.classList.add('hidden');
      const msg = el('manut-nao-configurado-msg');
      if (msg && resposta.mensagem) msg.textContent = resposta.mensagem;
      return;
    }

    dadosCarregadosEm = Date.now();
    if (!detalheManutencaoAberto()) conteudo?.classList.remove('hidden');
    modificadoEmAtual = resposta.modificado_em || null;
    renderLista();
    carregarSaudeFrota();
  } catch (erro) {
    dadosCarregadosEm = 0;
    if (container) {
      container.innerHTML = `<div class="card empty">
        <p class="empty__title">Não foi possível carregar os dados de manutenção.</p>
      </div>`;
    }
  }
}

/* Os gráficos leem cores dos tokens CSS: ao trocar o tema, precisam ser refeitos. */
/** Recalcula (100% no cliente) os KPIs, o Health Score e os 2 gráficos do
 * Dashboard Geral a partir da lista completa `saudeFrotaVeiculos` + do
 * segmentado Cavalos/Carretas/Ambos ativo - chamado tanto na carga inicial
 * quanto a cada clique no filtro, sem nenhum round-trip ao Drive. */
function renderDashboardSaudeFrota() {
  const filtrados = veiculosSaudeFiltrados();
  const agregado = agregarSaudeFrota(filtrados);
  renderGraficoSaude(agregado);
  renderHealthScore(agregado);
  renderGraficoProximas({ proximas_manutencoes: calcularFrentesEmAtencao(filtrados) });
  renderCardsFrente(filtrados);
  renderGraficoPrevisao(filtrados);
  renderHallVergonha(filtrados);
  atualizarDetalheManutencao(); // se uma página de análise estiver aberta, acompanha filtro/dados/tema
}

// A página de análise de cada card (manutencao_detalhe.js) lê a mesma lista filtrada do
// Dashboard e usa o mesmo segmentado Cavalos/Carretas/Ambos - recebe isso daqui em vez
// de importar este módulo de volta (evita dependência circular).
configurarDetalheManutencao({
  obterVeiculos: () => veiculosSaudeFiltrados(),
  obterFiltroTipo: () => filtroTipoFrota,
  definirFiltroTipo: (tipo) => aplicarFiltroTipoFrota(tipo),
});

/** Clique num botão do segmentado "Apenas Cavalos / Apenas Carretas / Ambos". */
export function aplicarFiltroTipoFrota(tipo) {
  filtroTipoFrota = tipo;
  qsa('[data-action="manut-filtro-tipo"]').forEach((botao) => botao.classList.toggle('is-active', botao.dataset.tipo === tipo));
  renderDashboardSaudeFrota();
}

async function carregarSaudeFrota() {
  const cartoes = el('manut-cat-cards');
  if (cartoes && !cartoes.children.length) {
    cartoes.innerHTML = Array.from({ length: 5 }, () => '<div class="mfrente-card" aria-hidden="true"><div class="skeleton" style="height:116px"></div></div>').join('');
  }
  try {
    const resposta = await api.saudeFrota();
    saudeFrotaVeiculos = resposta.veiculos || [];
  } catch (erro) {
    saudeFrotaVeiculos = [];
  }
  renderDashboardSaudeFrota();
}

document.addEventListener('tema:alterado', () => {
  if (state.view !== 'manutencao') return;
  renderDashboardSaudeFrota();
});

/* ==========================================================
 * Prontuário do Veículo — cruza todas as demais abas do ERP de manutenção
 * (5ª roda, integridade de chassi, SR-tanques, preventiva de carretas,
 * alinhamento) por Placa. Ver backend/manutencao.py e
 * [[🧠 Dicionario de Dados - ERP Manutencao]] no Segundo Cérebro.
 * ========================================================== */

/** % percorrida entre a última e a próxima condição, aceitando itens por km OU por dias. */
function progressoCategoria(item) {
  if (!item) return null;
  if (item.unidade === 'km') {
    if (item.km_atual == null || item.proxima == null || item.periodicidade == null) return null;
    const ultima = item.proxima - item.periodicidade;
    if (item.proxima <= ultima) return null;
    return Math.max(0, Math.min(100, ((item.km_atual - ultima) / (item.proxima - ultima)) * 100));
  }
  if (item.unidade === 'dias' && item.ultima_data && item.proxima) {
    const ultima = new Date(item.ultima_data).getTime();
    const proxima = new Date(item.proxima).getTime();
    if (!(proxima > ultima)) return null;
    return Math.max(0, Math.min(100, ((Date.now() - ultima) / (proxima - ultima)) * 100));
  }
  return null;
}


/** Uma linha `.mprog-row` para qualquer categoria do prontuário (km ou dias). */
function linhaCategoria(titulo, icone, item) {
  if (!item) return '';
  const cor = corDoStatus(item.status);
  const pct = progressoCategoria(item);
  const unidadeTexto = item.unidade === 'km' ? 'km' : 'dias';

  const metaReferencia = item.unidade === 'km'
    ? `KM atual: ${item.km_atual != null ? formatarKm(item.km_atual) : '—'} · Próxima: ${item.proxima != null ? `${formatarKm(item.proxima)} km` : '—'}`
    : `Última: ${dataBRSimples(item.ultima_data)} · Próxima: ${dataBRSimples(item.proxima)}`;
  const metaFalta = item.falta != null
    ? `${item.falta < 0 ? 'Atrasado em' : 'Faltam'} ${formatarKm(Math.abs(Math.round(item.falta)))} ${unidadeTexto}`
    : '';

  return `
    <div class="mprog-row">
      <div class="mprog-row__head">
        <span class="mprog-row__label"><i class="ph ${icone}"></i>${escapar(titulo)}</span>
        <span class="badge ${badgeDoStatus(item.status)}"><i class="ph ${iconeDoStatus(item.status)}"></i>${rotuloStatus(item.status)}</span>
      </div>
      ${pct != null ? `<div class="mprog"><div class="mprog__fill mprog__fill--${cor}" style="width:${pct}%"></div></div>` : ''}
      <div class="mprog-row__meta">
        <span>${metaReferencia}</span>
        <span>${metaFalta}</span>
      </div>
      ${item.observacoes ? `<p class="field__hint"><i class="ph ph-note"></i> ${escapar(item.observacoes)}</p>` : ''}
    </div>`;
}

/** Seção de uma categoria "dupla" (tempo + km) - o status geral é o pior dos dois. */
function secaoCategoriaDupla(chave, dupla) {
  if (!dupla) return '';
  const rotulo = ROTULOS_CATEGORIA[chave];
  return `
    <div class="prontuario__secao">
      <div class="prontuario__secao-head">
        <h4><i class="ph ${rotulo.icone}"></i>${rotulo.titulo}</h4>
        <span class="badge ${badgeDoStatus(dupla.status_geral)}"><i class="ph ${iconeDoStatus(dupla.status_geral)}"></i>${rotuloStatus(dupla.status_geral)}</span>
      </div>
      ${linhaCategoria('Por tempo', 'ph-calendar', dupla.tempo)}
      ${linhaCategoria('Por quilometragem', 'ph-gauge', dupla.km)}
    </div>`;
}

/** Seção de uma categoria simples (um único item: km OU dias). */
function secaoCategoriaSimples(chave, item) {
  if (!item) return '';
  const rotulo = ROTULOS_CATEGORIA[chave];
  return `
    <div class="prontuario__secao">
      <div class="prontuario__secao-head"><h4><i class="ph ${rotulo.icone}"></i>${rotulo.titulo}</h4></div>
      ${linhaCategoria(rotulo.titulo, rotulo.icone, item)}
    </div>`;
}

/** Seção do SR-Tanques no Prontuário - mesmo corpo minimalista (só as 2
 * datas) do card da aba dedicada, ver `corpoSrTanques`. */
function secaoSrTanques(item) {
  if (!item) return '';
  const rotulo = ROTULOS_CATEGORIA.sr_tanques;
  return `
    <div class="prontuario__secao">
      <div class="prontuario__secao-head">
        <h4><i class="ph ${rotulo.icone}"></i>${rotulo.titulo}</h4>
        <span class="badge ${badgeDoStatus(item.status)}"><i class="ph ${iconeDoStatus(item.status)}"></i>${rotuloStatus(item.status)}</span>
      </div>
      ${corpoSrTanques(item)}
    </div>`;
}

function secaoDemandasAgendadas(placa, listaDemandas) {
  const linhas = (listaDemandas || []).map((d) => `
    <div class="mprog-row tr-clicavel" data-action="editar-demanda" data-id="${escapar(d.id)}" title="Clique para editar - abre o mesmo editor de Estrutura/Demandas">
      <div class="mprog-row__head">
        <span class="mprog-row__label"><i class="ph ph-wrench"></i>${escapar(d.titulo)}</span>
        ${badgeStatusTarefa(d.status)}
      </div>
      <div class="mprog-row__meta">
        <span>${d.responsavel_nome && d.responsavel_nome !== 'Não atribuído' ? escapar(d.responsavel_nome) : 'Sem responsável definido'}</span>
        <span>${d.data_inicio_real ? `Início ${dataBRSimples(d.data_inicio_real)}` : ''}${d.data_fim_real ? ` · Fim previsto ${dataBRSimples(d.data_fim_real)}` : ''}</span>
      </div>
    </div>`).join('');

  return `
    <div class="prontuario__secao">
      <div class="prontuario__secao-head">
        <h4><i class="ph ph-calendar-plus"></i>Demandas agendadas</h4>
        <button type="button" class="btn btn--secondary" style="height:30px;padding:0 12px;font-size:12.5px" data-action="nova-demanda-placa" data-placa="${escapar(placa)}">
          <i class="ph ph-plus"></i> Agendar
        </button>
      </div>
      ${linhas || '<p class="field__hint"><i class="ph ph-info"></i> Nenhuma demanda agendada para este veículo ainda.</p>'}
    </div>`;
}

function renderizarProntuario(dados, listaDemandas) {
  const cad = dados.cadastro || {};
  const subtitulo = [cad.marca, cad.modelo, cad.tipo].filter(Boolean).join(' · ');
  const cats = dados.categorias || {};
  const secoes = [secaoDemandasAgendadas(dados.placa, listaDemandas)];

  if (cats.revisao_oleo) {
    const v = cats.revisao_oleo;
    secoes.push(`
      <div class="prontuario__secao">
        <div class="prontuario__secao-head">
          <h4><i class="ph ph-engine"></i>Revisão &amp; Óleo</h4>
          <span class="badge ${badgeDoStatus(v.status_geral)}"><i class="ph ${iconeDoStatus(v.status_geral)}"></i>${rotuloStatus(v.status_geral)}</span>
        </div>
        ${linhaItem('revisao', v.revisao, v.km_atual)}
        ${linhaItem('oleo_motor', v.oleo_motor, v.km_atual)}
        ${linhaItem('oleo_cambio', v.oleo_cambio, v.km_atual)}
        ${linhaItem('oleo_diferencial', v.oleo_diferencial, v.km_atual)}
        ${linhaItem('aditivo', v.aditivo, v.km_atual)}
      </div>`);
  }

  secoes.push(secaoCategoriaSimples('alinhamento', cats.alinhamento));
  secoes.push(secaoCategoriaSimples('quinta_roda', cats.quinta_roda));
  secoes.push(secaoCategoriaDupla('integridade_chassi', cats.integridade_chassi));
  secoes.push(secaoSrTanques(cats.sr_tanques));
  secoes.push(secaoCategoriaSimples('preventiva_carretas', cats.preventiva_carretas));

  const corpo = secoes.filter(Boolean).join('') || `
    <div class="empty empty--sm">
      <div class="empty__icon"><i class="ph ph-file-dashed"></i></div>
      <p class="empty__title">Nenhum dado de manutenção encontrado</p>
      <p class="empty__text">Essa placa não aparece em nenhuma aba de manutenção conhecida.</p>
    </div>`;

  return `
    <div class="prontuario__head">
      <span class="mcard__dot mcard__dot--${corDoStatus(dados.status_geral)}"></span>
      <div class="mcard__id">
        <strong>${escapar(dados.placa)}</strong>
        <span>${subtitulo ? escapar(subtitulo) : 'Sem cadastro completo nesta placa'}</span>
      </div>
      <span class="badge ${badgeDoStatus(dados.status_geral)}"><i class="ph ${iconeDoStatus(dados.status_geral)}"></i>${rotuloStatus(dados.status_geral)}</span>
    </div>
    <div class="prontuario__lista">${corpo}</div>`;
}

/* ---------- Busca inteligente de placa (autocomplete) ---------- */
const autocompleteProntuario = criarAutocompletePlaca('prontuario-busca', 'prontuario-sugestoes', (v) => {
  const campo = el('prontuario-busca');
  if (campo) campo.value = v.placa;
  abrirProntuario(v.placa);
});

export function filtrarSugestoesPlaca() { autocompleteProntuario.filtrar(); }
export function navegarSugestaoPlaca(direcao) { autocompleteProntuario.navegar(direcao); }
export function confirmarSugestaoAtiva() { return autocompleteProntuario.confirmarAtivo(); }

export async function abrirProntuario(placaClicada) {
  autocompleteProntuario.fechar();
  const placa = (placaClicada || valor('prontuario-busca') || '').trim().toUpperCase();
  if (!placa) {
    toast('Digite uma placa', 'Informe a placa do cavalo ou da carreta para consultar o prontuário completo.', 'warning');
    return;
  }

  el('titulo-prontuario').textContent = `Prontuário — ${placa}`;
  el('corpo-prontuario').innerHTML = esqueleto(3, 72);
  abrirModal('modal-prontuario');

  try {
    const [resposta, demandasVeiculo] = await Promise.all([
      api.prontuario(placa),
      api.demandasManutencao(placa).then((r) => r.demandas || []).catch(() => []),
    ]);
    if (resposta.configurado === false) {
      el('corpo-prontuario').innerHTML = `<div class="card empty">
        <div class="empty__icon"><i class="ph ph-plug"></i></div>
        <p class="empty__title">Integração ainda não configurada</p>
        <p class="empty__text">${escapar(resposta.mensagem || '')}</p>
      </div>`;
      return;
    }
    el('corpo-prontuario').innerHTML = renderizarProntuario(resposta.prontuario, demandasVeiculo);
  } catch (erro) {
    const mensagem = erro?.status === 404
      ? `Nenhum registro de manutenção encontrado para a placa "${placa}". Confira se digitou corretamente.`
      : 'Não foi possível carregar o prontuário agora.';
    el('corpo-prontuario').innerHTML = `<div class="card empty">
      <div class="empty__icon"><i class="ph ph-magnifying-glass"></i></div>
      <p class="empty__title">${escapar(mensagem)}</p>
    </div>`;
  }
}
