/**
 * Frota & Manutenção - PÁGINA DE ANÁLISE de cada card do Dashboard.
 *
 * Clicar no card "Saúde da Frota" ou em qualquer card de frente (Alinhamento,
 * Integridade de chassi, 5ª roda...) abre aqui uma página inteira - não um painel
 * lateral - com a análise daquele recorte: saúde, distribuição, comparativos,
 * atraso, previsão de paradas, cortes por tipo/marca/modelo/idade/centro de custo e
 * a lista de veículos com busca e filtro.
 *
 * Tudo é calculado no cliente a partir da MESMA lista que o Dashboard já tem em
 * memória (`calcular_saude_frota` no backend: status por frente, `detalhes` com o
 * atraso/próxima data de cada frente, `ano`, `centro_custo`) - abrir a página não faz
 * chamada nova ao Drive. A lista e o segmentado Cavalos/Carretas/Ambos pertencem ao
 * Dashboard (manutencao.js), que os entrega via `configurarDetalheManutencao`.
 *
 * Regra de saúde (ver `resumirSaude`): sempre medida sobre o UNIVERSO inteiro do card
 * (todos os veículos que têm a frente), nunca só sobre a lista de vencidos.
 */

import { el, escapar, mostrar, qsa } from './state.js';
import {
  token, opcoesGrafico, corDoScore, badgeDoStatus, iconeDoStatus, rotuloStatus, formatarKm, dataBRSimples,
  iconeFrente, tituloFrente, resumirSaude, veiculosDaFrente, calcularSaudePorFrente,
  calcularFrentesEmAtencao, rankingCriticos,
} from './manutencao_comum.js';

const LINHAS_POR_PAGINA = 25;
const ORDEM_STATUS = { 'PARAR URGENTE': 0, 'PROGRAMAR PARADA': 1, 'EM DIA': 2 };
const NOME_CURTO_FRENTE = {
  revisao_oleo: 'Revisão & Óleo',
  alinhamento: 'Alinhamento',
  quinta_roda: '5ª roda',
  integridade_chassi: 'Chassi',
  sr_tanques: 'SR-Tanques',
  preventiva_carretas: 'Preventiva',
};
const FAIXAS_IDADE = [
  { nome: 'Até 3 anos', max: 3 },
  { nome: '4 a 6 anos', max: 6 },
  { nome: '7 a 10 anos', max: 10 },
  { nome: 'Mais de 10 anos', max: Infinity },
];

let contexto = null; // { obterVeiculos, obterFiltroTipo, definirFiltroTipo } - entregue pelo Dashboard
let escopo = null; // { tipo: 'saude' } | { tipo: 'frente', chave } - null = página fechada
let graficos = {};
let filtroStatus = 'todos';
let buscaTexto = '';
let limiteLinhas = LINHAS_POR_PAGINA;
let rolagemAnterior = 0;

export function configurarDetalheManutencao(dependencias) {
  contexto = dependencias;
}

export function detalheManutencaoAberto() {
  return escopo !== null;
}

/* ==========================================================
 * Abrir / fechar / atualizar
 * ========================================================== */
function areaDeRolagem() {
  return document.querySelector('.content');
}

/** Salto instantâneo: `.content` tem `scroll-behavior: smooth`, e atribuir `scrollTop`
 * faria a tela "deslizar" ao abrir/voltar em vez de já estar no lugar. */
function rolarPara(topo) {
  areaDeRolagem()?.scrollTo({ top: topo, behavior: 'instant' });
}

function abrir(novoEscopo) {
  if (!contexto) return;
  if (!detalheManutencaoAberto()) rolagemAnterior = areaDeRolagem()?.scrollTop || 0;
  escopo = novoEscopo;
  filtroStatus = 'todos';
  buscaTexto = '';
  limiteLinhas = LINHAS_POR_PAGINA;

  mostrar('manut-conteudo', false);
  mostrar('manut-detalhe', true);
  renderizarPagina();
  rolarPara(0);
  el('mdet-voltar')?.focus({ preventScroll: true });
}

export function abrirDetalheSaude() {
  abrir({ tipo: 'saude' });
}

export function abrirDetalheFrente(chave) {
  abrir({ tipo: 'frente', chave });
}

export function fecharDetalheManutencao() {
  if (!detalheManutencaoAberto()) return;
  destruirGraficos();
  escopo = null;
  mostrar('manut-detalhe', false);
  mostrar('manut-conteudo', true);
  rolarPara(rolagemAnterior);
}

/** O Dashboard chama isto sempre que seus dados/filtro/tema mudam: com a página aberta,
 * ela se redesenha em cima da lista nova (mantendo busca e filtro de status). */
export function atualizarDetalheManutencao() {
  if (!detalheManutencaoAberto()) return;
  mostrar('manut-conteudo', false);
  renderizarPagina();
}

/* ==========================================================
 * Gráficos (Chart.js)
 * ========================================================== */
function destruirGraficos() {
  Object.values(graficos).forEach((grafico) => grafico.destroy());
  graficos = {};
}

function criarGrafico(id, config) {
  const canvas = el(id);
  if (!canvas || typeof Chart === 'undefined') return;
  graficos[id]?.destroy();
  graficos[id] = new Chart(canvas.getContext('2d'), config);
}

/** '#rrggbb' + alfa -> 'rgba(...)'. Qualquer outro formato volta como veio. */
function comAlfa(cor, alfa) {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(cor || '');
  return m ? `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${alfa})` : cor;
}

const legenda = () => ({
  display: true,
  position: 'bottom',
  labels: {
    padding: 12, usePointStyle: true, pointStyle: 'circle', boxWidth: 7,
    color: token('--text-2'), font: { family: 'Inter', size: 11, weight: '500' },
  },
});

const escalaValores = (extra = {}) => ({
  beginAtZero: true,
  grid: { color: token('--border-soft') },
  border: { display: false },
  ticks: { color: token('--text-3'), font: { family: 'Inter', size: 10.5 }, precision: 0 },
  ...extra,
});

const escalaCategorias = (extra = {}) => ({
  grid: { display: false },
  border: { display: false },
  ticks: { color: token('--text-2'), font: { family: 'Inter', size: 11, weight: '500' } },
  ...extra,
});

function graficoRosca(id, resumo) {
  const rotulos = ['Em dia', 'Programar parada', 'Parar urgente'];
  const valores = [resumo.em_dia, resumo.programar_parada, resumo.parar_urgente];
  const cores = [token('--green'), token('--gold'), token('--red')];
  if (resumo.sem_dado) {
    rotulos.push('Sem dado');
    valores.push(resumo.sem_dado);
    cores.push(token('--text-3'));
  }
  const total = valores.reduce((a, b) => a + b, 0);
  const base = opcoesGrafico();
  criarGrafico(id, {
    type: 'doughnut',
    data: { labels: rotulos, datasets: [{ data: valores, backgroundColor: cores, borderWidth: 0, hoverOffset: 6 }] },
    options: {
      ...base,
      cutout: '68%',
      plugins: {
        ...base.plugins,
        legend: legenda(),
        tooltip: {
          ...base.plugins.tooltip,
          callbacks: { label: (c) => ` ${c.label}: ${c.parsed} (${Math.round((c.parsed / total) * 100)}%)` },
        },
      },
    },
  });
}

/** Barras horizontais 100% empilhadas: a composição Em dia / A vencer / Vencido de cada
 * grupo. Em % (e não em quantidade) para grupos de tamanhos diferentes serem comparáveis;
 * o tamanho do grupo vai no rótulo e as quantidades no tooltip. */
function graficoComposicao(id, grupos) {
  const parte = (g, chave) => (g.resumo.elegiveis ? (g.resumo[chave] / g.resumo.elegiveis) * 100 : 0);
  const serie = (rotulo, chave, cor) => ({
    label: rotulo, chave, data: grupos.map((g) => parte(g, chave)), backgroundColor: cor, maxBarThickness: 24,
  });
  const base = opcoesGrafico();
  criarGrafico(id, {
    type: 'bar',
    data: {
      labels: grupos.map((g) => `${g.nome} (${g.n})`),
      datasets: [
        serie('Em dia', 'em_dia', token('--green')),
        serie('Programar parada', 'programar_parada', token('--gold')),
        serie('Parar urgente', 'parar_urgente', token('--red')),
      ],
    },
    options: {
      ...base,
      indexAxis: 'y',
      interaction: { mode: 'index', intersect: false },
      plugins: {
        ...base.plugins,
        legend: legenda(),
        tooltip: {
          ...base.plugins.tooltip,
          callbacks: {
            label: (c) => ` ${c.dataset.label}: ${grupos[c.dataIndex].resumo[c.dataset.chave]} (${Math.round(c.parsed.x)}%)`,
          },
        },
      },
      scales: {
        x: { ...escalaValores({ stacked: true, max: 100 }), ticks: { ...escalaValores().ticks, callback: (v) => `${v}%` } },
        y: escalaCategorias({ stacked: true }),
      },
    },
  });
}

/** Saúde (%) de cada frente, a frente em análise em destaque e as outras esmaecidas. */
function graficoSaudeFrentes(id, frentes, destaque) {
  const cores = { verde: token('--green'), laranja: token('--gold'), vermelho: token('--red'), neutro: token('--text-3') };
  const corDe = (f) => cores[f.pct == null ? 'neutro' : corDoScore(f.pct)];
  const base = opcoesGrafico();
  criarGrafico(id, {
    type: 'bar',
    data: {
      labels: frentes.map((f) => `${tituloFrente(f.chave)} · ${f.pct ?? '—'}%`),
      datasets: [{
        label: 'Saúde',
        data: frentes.map((f) => f.pct ?? 0),
        backgroundColor: frentes.map((f) => (destaque && f.chave !== destaque ? comAlfa(corDe(f), 0.38) : corDe(f))),
        borderRadius: 5,
        maxBarThickness: 26,
      }],
    },
    options: {
      ...base,
      indexAxis: 'y',
      plugins: {
        ...base.plugins,
        legend: { display: false },
        tooltip: {
          ...base.plugins.tooltip,
          callbacks: {
            label: (c) => {
              const f = frentes[c.dataIndex];
              return ` ${f.em_dia} de ${f.elegiveis} em dia · ${f.parar_urgente} vencido${f.parar_urgente === 1 ? '' : 's'}`;
            },
          },
        },
      },
      scales: {
        x: { ...escalaValores({ max: 100 }), ticks: { ...escalaValores().ticks, callback: (v) => `${v}%` } },
        y: escalaCategorias(),
      },
    },
  });
}

function graficoPrevisao(id, meses) {
  const base = opcoesGrafico();
  criarGrafico(id, {
    type: 'bar',
    data: {
      labels: meses.map((m) => m.rotulo),
      datasets: [{ label: 'Manutenções previstas', data: meses.map((m) => m.total), backgroundColor: token('--brand'), borderRadius: 5, maxBarThickness: 44 }],
    },
    options: { ...base, plugins: { ...base.plugins, legend: { display: false } }, scales: { x: escalaCategorias(), y: escalaValores() } },
  });
}

function graficoAtraso(id, grupo) {
  const vermelho = token('--red');
  const base = opcoesGrafico();
  criarGrafico(id, {
    type: 'bar',
    data: {
      labels: grupo.buckets.map((b) => b.rotulo),
      datasets: [{
        label: 'Veículos vencidos',
        data: grupo.buckets.map((b) => b.total),
        backgroundColor: [comAlfa(vermelho, 0.4), comAlfa(vermelho, 0.58), comAlfa(vermelho, 0.78), vermelho],
        borderRadius: 5,
        maxBarThickness: 52,
      }],
    },
    options: { ...base, plugins: { ...base.plugins, legend: { display: false } }, scales: { x: escalaCategorias(), y: escalaValores() } },
  });
}

function graficoCruzamento(id, frentes) {
  const base = opcoesGrafico();
  criarGrafico(id, {
    type: 'bar',
    data: {
      labels: frentes.map((f) => tituloFrente(f.item)),
      datasets: [
        { label: 'Parar urgente', data: frentes.map((f) => f.parar_urgente), backgroundColor: token('--red'), borderRadius: 4, maxBarThickness: 22 },
        { label: 'Programar parada', data: frentes.map((f) => f.programar_parada), backgroundColor: token('--gold'), borderRadius: 4, maxBarThickness: 22 },
      ],
    },
    options: {
      ...base,
      indexAxis: 'y',
      interaction: { mode: 'index', intersect: false },
      plugins: { ...base.plugins, legend: legenda() },
      scales: { x: escalaValores({ stacked: true }), y: escalaCategorias({ stacked: true }) },
    },
  });
}

/* ==========================================================
 * Cálculos (puros: recebem a lista, devolvem números)
 * ========================================================== */
const plural = (nomeTipo) => `${nomeTipo}s`;
const anoAtual = () => new Date().getFullYear();

function agrupar(lista, nomeDe, statusDe) {
  const grupos = new Map();
  lista.forEach((v) => {
    const nome = nomeDe(v);
    if (!nome) return;
    if (!grupos.has(nome)) grupos.set(nome, []);
    grupos.get(nome).push(v);
  });
  return [...grupos].map(([nome, itens]) => ({ nome, n: itens.length, resumo: resumirSaude(itens, statusDe) }));
}

function faixaDeIdade(ano) {
  const idade = Math.max(0, anoAtual() - ano);
  return FAIXAS_IDADE.find((f) => idade <= f.max).nome;
}

function isoLocal(data) {
  const dois = (n) => String(n).padStart(2, '0');
  return `${data.getFullYear()}-${dois(data.getMonth() + 1)}-${dois(data.getDate())}`;
}

/** Datas previstas (só itens por TEMPO têm calendário) nos próximos 6 meses a partir do
 * mês atual. `chave` = uma frente; sem ela, todas as frentes de cada veículo. */
function calcularPrevisao(base, chave) {
  const agora = new Date();
  const meses = Array.from({ length: 6 }, (_, i) => {
    const d = new Date(agora.getFullYear(), agora.getMonth() + i, 1);
    return {
      chave: isoLocal(d).slice(0, 7),
      rotulo: d.toLocaleDateString('pt-BR', { month: 'short', year: '2-digit' }).replace('.', ''),
      total: 0,
    };
  });
  const indice = new Map(meses.map((m, i) => [m.chave, i]));
  const hoje = isoLocal(agora);
  const limite30 = isoLocal(new Date(agora.getFullYear(), agora.getMonth(), agora.getDate() + 30));

  let comData = 0;
  let proximos30 = 0;
  base.forEach((v) => {
    const datas = chave ? [v.detalhes?.[chave]?.proxima].filter(Boolean) : (v.proximas || []);
    datas.forEach((iso) => {
      comData += 1;
      if (iso >= hoje && iso <= limite30) proximos30 += 1;
      const posicao = indice.get(String(iso).slice(0, 7));
      if (posicao != null) meses[posicao].total += 1;
    });
  });
  return { meses, comData, proximos30 };
}

/** O quanto os vencidos de uma frente estão atrasados, em faixas, separado por unidade
 * (dias e km não se misturam). Só entra quem a planilha tem número confiável. */
function calcularAtraso(base, chave) {
  const FAIXAS = {
    dias: { limites: [30, 90, 180], rotulos: ['Até 30 dias', '31 a 90 dias', '91 a 180 dias', 'Mais de 180 dias'], sufixo: 'dias' },
    km: { limites: [1000, 5000, 10000], rotulos: ['Até 1.000 km', '1.001 a 5.000 km', '5.001 a 10.000 km', 'Mais de 10.000 km'], sufixo: 'km' },
  };
  const itens = { dias: [], km: [] };
  let vencidos = 0;
  base.forEach((v) => {
    if (v.categorias[chave] !== 'PARAR URGENTE') return;
    vencidos += 1;
    const d = v.detalhes?.[chave];
    if (!d || d.falta == null || d.falta >= 0) return;
    itens[d.unidade === 'km' ? 'km' : 'dias'].push({ placa: v.placa, valor: -d.falta });
  });

  const grupos = ['dias', 'km'].filter((u) => itens[u].length).map((unidade) => {
    const { limites, rotulos, sufixo } = FAIXAS[unidade];
    const buckets = rotulos.map((rotulo) => ({ rotulo, total: 0 }));
    itens[unidade].forEach(({ valor }) => {
      const posicao = limites.findIndex((limite) => valor <= limite);
      buckets[posicao === -1 ? limites.length : posicao].total += 1;
    });
    const maior = itens[unidade].reduce((a, b) => (b.valor > a.valor ? b : a));
    const media = itens[unidade].reduce((soma, i) => soma + i.valor, 0) / itens[unidade].length;
    return { unidade, sufixo, n: itens[unidade].length, buckets, maior, media };
  });
  const comNumero = grupos.reduce((soma, g) => soma + g.n, 0);
  return { grupos, vencidos, semNumero: vencidos - comNumero };
}

/* ==========================================================
 * Modelo da página: tudo que a tela mostra, calculado uma vez
 * ========================================================== */
function montarModelo() {
  const todos = contexto.obterVeiculos();
  const chave = escopo.tipo === 'frente' ? escopo.chave : null;
  const base = chave ? veiculosDaFrente(todos, chave) : todos;
  const statusDe = chave ? (v) => v.categorias[chave] : (v) => v.status_geral;
  const resumo = resumirSaude(base, statusDe);

  const porTipo = ['Cavalo', 'Carreta']
    .map((nome) => {
      const itens = base.filter((v) => v.tipo === nome);
      return itens.length ? { nome, n: itens.length, resumo: resumirSaude(itens, statusDe) } : null;
    })
    .filter(Boolean);

  const marcasTodas = agrupar(base, (v) => (v.marca || '').trim() || 'Sem marca', statusDe).sort((a, b) => b.n - a.n);
  const modelos = agrupar(base, (v) => [v.marca, v.modelo].filter(Boolean).join(' ').trim() || null, statusDe)
    .filter((g) => g.n >= 2 && g.resumo.parar_urgente > 0)
    .sort((a, b) => (b.resumo.parar_urgente / b.n) - (a.resumo.parar_urgente / a.n) || b.n - a.n);

  const comAno = base.filter((v) => v.ano);
  const porIdade = FAIXAS_IDADE
    .map(({ nome }) => {
      const itens = comAno.filter((v) => faixaDeIdade(v.ano) === nome);
      return itens.length ? { nome, n: itens.length, resumo: resumirSaude(itens, statusDe) } : null;
    })
    .filter(Boolean);

  const centros = agrupar(base, (v) => (v.centro_custo || '').toString().trim() || null, statusDe).sort((a, b) => b.n - a.n);

  const comparativo = calcularSaudePorFrente(todos).sort((a, b) => (a.pct ?? 101) - (b.pct ?? 101));
  const previsao = calcularPrevisao(base, chave);
  const vencidosDaFrente = chave ? base.filter((v) => v.categorias[chave] === 'PARAR URGENTE') : [];

  return {
    todos, chave, base, statusDe, resumo, porTipo, marcasTodas, modelos, porIdade, centros,
    comAnoPct: base.length ? comAno.length / base.length : 0,
    comparativo, previsao,
    atraso: chave ? calcularAtraso(base, chave) : null,
    cruzamento: chave
      ? calcularFrentesEmAtencao(vencidosDaFrente).filter((f) => f.item !== chave)
        .sort((a, b) => (b.parar_urgente + b.programar_parada) - (a.parar_urgente + a.programar_parada))
      : [],
    vencidosDaFrente,
    criticos: chave ? [] : rankingCriticos(base),
  };
}

/* ==========================================================
 * Destaques: frases geradas dos próprios números
 * ========================================================== */
function numeroBR(n) {
  return Number(n).toLocaleString('pt-BR');
}

function gerarDestaques(m) {
  const itens = [];
  const add = (tom, icone, texto) => itens.push({ tom, icone, texto });

  if (m.porTipo.length === 2 && m.porTipo.every((t) => t.resumo.pct != null)) {
    const [pior, melhor] = [...m.porTipo].sort((a, b) => a.resumo.pct - b.resumo.pct);
    const dif = melhor.resumo.pct - pior.resumo.pct;
    if (dif >= 10) {
      add('gold', 'ph-scales', `${plural(pior.nome)} têm saúde de ${pior.resumo.pct}%, contra ${melhor.resumo.pct}% dos ${plural(melhor.nome)} - ${dif} pontos de diferença.`);
    }
  }

  const marcas = m.marcasTodas.filter((g) => g.n >= 3 && g.resumo.pct != null).sort((a, b) => a.resumo.pct - b.resumo.pct);
  if (marcas.length >= 2) {
    const pior = marcas[0];
    const melhor = marcas[marcas.length - 1];
    // Só vale destacar quando a diferença é relevante (93% x 94% é ruído).
    if (melhor.resumo.pct - pior.resumo.pct >= 10) {
      add('brand', 'ph-trademark', `Por marca, a pior saúde é ${pior.nome} (${pior.resumo.pct}%, ${pior.n} veículos) e a melhor é ${melhor.nome} (${melhor.resumo.pct}%, ${melhor.n} veículos).`);
    }
  }

  const novos = m.porIdade.find((g) => g.nome === FAIXAS_IDADE[0].nome);
  const velhos = m.porIdade.find((g) => g.nome === FAIXAS_IDADE[FAIXAS_IDADE.length - 1].nome);
  if (novos && velhos && novos.n >= 3 && velhos.n >= 3 && novos.resumo.pct != null && velhos.resumo.pct != null
      && Math.abs(novos.resumo.pct - velhos.resumo.pct) >= 10) {
    add('brand', 'ph-hourglass-medium', `Veículos com mais de 10 anos têm saúde de ${velhos.resumo.pct}%, contra ${novos.resumo.pct}% dos de até 3 anos.`);
  }

  m.atraso?.grupos.forEach((g) => {
    add('red', 'ph-clock-countdown', `Maior atraso em ${g.sufixo}: ${numeroBR(Math.round(g.maior.valor))} ${g.sufixo} (${g.maior.placa}); média de ${numeroBR(Math.round(g.media))} ${g.sufixo} entre os ${g.n} veículo${g.n === 1 ? '' : 's'} com valor na planilha.`);
  });

  if (m.previsao.comData > 0) {
    const n = m.previsao.proximos30;
    add('brand', 'ph-calendar-check', `${n} manutenç${n === 1 ? 'ão prevista' : 'ões previstas'} nos próximos 30 dias${m.chave ? '' : ' (somando as frentes controladas por data)'}.`);
  }

  if (m.comparativo.length >= 2) {
    if (m.chave) {
      const posicao = m.comparativo.findIndex((f) => f.chave === m.chave) + 1;
      if (posicao === 1) add('red', 'ph-trend-down', 'É a frente com a pior saúde da frota.');
      else if (posicao === m.comparativo.length) add('green', 'ph-trend-up', 'É a frente com a melhor saúde da frota.');
      else if (posicao > 0) add('gold', 'ph-ranking', `É a ${posicao}ª pior frente em saúde (de ${m.comparativo.length}).`);
    } else {
      const pior = m.comparativo[0];
      const melhor = m.comparativo[m.comparativo.length - 1];
      add('red', 'ph-trend-down', `A frente mais crítica é ${tituloFrente(pior.chave)} (${pior.pct}% em dia, ${pior.parar_urgente} vencidos); a mais saudável é ${tituloFrente(melhor.chave)} (${melhor.pct}%).`);
    }
  }

  const topo = m.cruzamento[0];
  if (topo && m.vencidosDaFrente.length >= 3) {
    const quantos = topo.parar_urgente + topo.programar_parada;
    const parte = Math.round((quantos / m.vencidosDaFrente.length) * 100);
    if (parte >= 30) {
      add('gold', 'ph-link', `Dos ${m.vencidosDaFrente.length} veículos vencidos aqui, ${quantos} (${parte}%) também estão em atenção em ${tituloFrente(topo.item)}.`);
    }
  }

  const tres = m.criticos.filter((c) => c.criticos >= 3).length;
  if (tres > 0) {
    add('red', 'ph-siren', `${tres} veículo${tres === 1 ? '' : 's'} ${tres === 1 ? 'tem' : 'têm'} 3 ou mais frentes vencidas ao mesmo tempo - ${tres === 1 ? 'é o primeiro candidato' : 'são os primeiros candidatos'} a uma parada.`);
  }
  return itens.slice(0, 7);
}

/* ==========================================================
 * HTML da página
 * ========================================================== */
function tomDoScore(pct) {
  return pct == null ? 'neutro' : corDoScore(pct);
}

const SELO = { verde: 'Saudável', laranja: 'Em atenção', vermelho: 'Crítica', neutro: 'Sem dados' };

function htmlHero(m) {
  const { resumo } = m;
  const tom = tomDoScore(resumo.pct);
  const nome = m.chave ? tituloFrente(m.chave) : 'Saúde da Frota';
  const pctDe = (n) => (resumo.elegiveis ? `${Math.round((n / resumo.elegiveis) * 100)}% do total` : '');
  const tile = (rotulo, valor, sub, classe, icone) => `
    <div class="mdet-kpi mdet-kpi--${classe}">
      <p class="mdet-kpi__valor">${numeroBR(valor)}</p>
      <p class="mdet-kpi__rotulo"><i class="ph ${icone}"></i>${rotulo}</p>
      ${sub ? `<p class="mdet-kpi__sub">${sub}</p>` : ''}
    </div>`;

  const frase = m.chave
    ? `${resumo.em_dia} de ${resumo.elegiveis} veículos com ${tituloFrente(m.chave)} em dia.`
    : `${resumo.em_dia} de ${resumo.elegiveis} veículos/implementos 100% em dia, cruzando todas as frentes.`;

  return `
    <section class="mdet-hero mdet-hero--${tom}">
      <div class="mdet-anel" role="img" aria-label="Saúde ${resumo.pct ?? 'sem dados'}%">
        <svg viewBox="0 0 36 36" aria-hidden="true">
          <circle class="mdet-anel__trilho" cx="18" cy="18" r="15.9155"/>
          ${resumo.pct > 0 ? `<circle class="mdet-anel__arco" cx="18" cy="18" r="15.9155" stroke-dasharray="${resumo.pct} 100"/>` : ''}
        </svg>
        <div class="mdet-anel__pct">${resumo.pct == null ? '—' : `${resumo.pct}%`}<small>em dia</small></div>
      </div>
      <div class="mdet-hero__texto">
        <p class="mdet-hero__eyebrow"><i class="ph ${m.chave ? iconeFrente(m.chave) : 'ph-heartbeat'}"></i>${m.chave ? 'Análise da frente' : 'Análise geral da frota'}</p>
        <h2 class="mdet-hero__titulo">${escapar(nome)}</h2>
        <p class="mdet-hero__frase">${escapar(frase)}</p>
        <span class="mdet-selo mdet-selo--${tom}">${SELO[tom]}</span>
      </div>
      <div class="mdet-kpis">
        ${tile('Monitorados', resumo.elegiveis, resumo.sem_dado ? `+${resumo.sem_dado} sem dado` : '', 'brand', 'ph-truck')}
        ${tile('Em dia', resumo.em_dia, pctDe(resumo.em_dia), 'green', 'ph-check-circle')}
        ${tile('A vencer', resumo.programar_parada, pctDe(resumo.programar_parada), 'gold', 'ph-warning')}
        ${tile('Vencidos', resumo.parar_urgente, pctDe(resumo.parar_urgente), 'red', 'ph-warning-circle')}
      </div>
    </section>`;
}

function htmlDestaques(m) {
  const itens = gerarDestaques(m);
  if (!itens.length) return '';
  return `
    <section class="mdet-destaques">
      <h3><i class="ph ph-lightbulb"></i>Destaques</h3>
      <ul>
        ${itens.map((d) => `<li class="mdet-destaque mdet-destaque--${d.tom}"><i class="ph ${d.icone}"></i><span>${escapar(d.texto)}</span></li>`).join('')}
      </ul>
    </section>`;
}

/** Monta os cartões da grade na ordem em que aparecem. Cada um declara o que desenhar
 * depois que o HTML estiver na tela (`desenhar`). Os que não têm dado suficiente nem
 * entram - sem cartão vazio. */
function montarCartoes(m) {
  const cartoes = [];
  const grafico = (id, titulo, sub, altura, desenhar) => cartoes.push({
    titulo, sub, desenhar,
    corpo: `<div class="mdet-card__grafico" style="height:${altura}px"><canvas id="${id}"></canvas></div>`,
  });
  const alturaBarras = (n) => Math.max(190, n * 44 + 78);

  grafico('mdet-g-rosca', 'Distribuição por status',
    m.chave ? `Todos os ${m.resumo.elegiveis + m.resumo.sem_dado} veículos com ${tituloFrente(m.chave)}` : `Toda a frota filtrada (${m.resumo.elegiveis + m.resumo.sem_dado} veículos/implementos)`,
    260, () => graficoRosca('mdet-g-rosca', m.resumo));

  if (m.comparativo.length >= 2) {
    grafico('mdet-g-frentes', 'Saúde por frente',
      m.chave ? 'Esta frente em destaque, comparada às demais' : 'Do pior para o melhor - onde a frota mais perde saúde',
      alturaBarras(m.comparativo.length), () => graficoSaudeFrentes('mdet-g-frentes', m.comparativo, m.chave));
  }

  if (m.porTipo.length === 2) {
    grafico('mdet-g-tipo', 'Saúde por tipo de veículo', 'Composição de cada tipo (em %); entre parênteses, quantos veículos',
      alturaBarras(2), () => graficoComposicao('mdet-g-tipo', m.porTipo));
  }

  if (m.previsao.comData > 0) {
    grafico('mdet-g-previsao', 'Previsão de paradas', 'Próximos 6 meses. Só entra o que é controlado por data - itens por km não têm calendário',
      250, () => graficoPrevisao('mdet-g-previsao', m.previsao.meses));
  }

  if (m.atraso && m.atraso.grupos.length) {
    const unidades = m.atraso.grupos;
    const nota = m.atraso.semNumero > 0 ? ` · ${m.atraso.semNumero} vencido${m.atraso.semNumero === 1 ? '' : 's'} sem valor de atraso na planilha` : '';
    cartoes.push({
      titulo: 'Quanto está atrasado',
      sub: `Só os vencidos, em faixas de atraso${nota}`,
      corpo: unidades.map((g, i) => `
        ${unidades.length > 1 ? `<p class="mdet-card__subtitulo">Em ${g.sufixo} (${g.n} veículo${g.n === 1 ? '' : 's'})</p>` : ''}
        <div class="mdet-card__grafico" style="height:${unidades.length > 1 ? 190 : 250}px"><canvas id="mdet-g-atraso-${i}"></canvas></div>`).join(''),
      desenhar: () => unidades.forEach((g, i) => graficoAtraso(`mdet-g-atraso-${i}`, g)),
    });
  }

  if (m.cruzamento.length) {
    grafico('mdet-g-cruzamento', 'O que mais está vencido nesses mesmos veículos',
      `Outras frentes em atenção nos ${m.vencidosDaFrente.length} veículos vencidos em ${tituloFrente(m.chave)}`,
      alturaBarras(m.cruzamento.length), () => graficoCruzamento('mdet-g-cruzamento', m.cruzamento));
  }

  if (!m.chave && m.criticos.length) {
    cartoes.push({
      titulo: 'Veículos mais críticos',
      sub: 'Mais frentes vencidas ao mesmo tempo - clique para abrir o prontuário',
      corpo: `<ul class="mdet-lista">${m.criticos.slice(0, 10).map((v, i) => `
        <li><button type="button" class="mdet-linha" data-action="abrir-prontuario" data-placa="${escapar(v.placa)}">
          <span class="mdet-linha__rank">${i + 1}º</span>
          <span class="mdet-linha__id"><strong>${escapar(v.placa)}</strong><span>${escapar(v.subtitulo) || '—'}</span></span>
          <span class="badge badge--red">${v.criticos} vencida${v.criticos === 1 ? '' : 's'}</span>
        </button></li>`).join('')}</ul>`,
    });
  }

  const marcas = m.marcasTodas.slice(0, 8);
  if (m.marcasTodas.length >= 2) {
    grafico('mdet-g-marca', 'Saúde por marca', `Composição de cada marca (em %); ${marcas.length < m.marcasTodas.length ? `as ${marcas.length} maiores de ${m.marcasTodas.length}` : 'todas as marcas'}`,
      alturaBarras(marcas.length), () => graficoComposicao('mdet-g-marca', marcas));
  }

  if (m.modelos.length) {
    cartoes.push({
      titulo: 'Modelos com mais vencidos',
      sub: 'Proporção de vencidos por modelo (a partir de 2 veículos do modelo)',
      corpo: `<ul class="mdet-lista">${m.modelos.slice(0, 8).map((g) => {
        const taxa = Math.round((g.resumo.parar_urgente / g.n) * 100);
        return `
        <li class="mdet-linha mdet-linha--modelo">
          <span class="mdet-linha__id"><strong>${escapar(g.nome)}</strong><span>${g.resumo.parar_urgente} de ${g.n} vencido${g.n === 1 ? '' : 's'}</span></span>
          <span class="mdet-barra" aria-hidden="true"><span style="width:${taxa}%"></span></span>
          <span class="mdet-linha__pct">${taxa}%</span>
        </li>`;
      }).join('')}</ul>`,
    });
  }

  if (m.porIdade.length >= 2 && m.comAnoPct >= 0.5) {
    grafico('mdet-g-idade', 'Saúde por idade do veículo', 'Pelo ano do modelo (ou de fabricação); composição em %',
      alturaBarras(m.porIdade.length), () => graficoComposicao('mdet-g-idade', m.porIdade));
  }

  if (m.centros.length >= 2) {
    const centros = m.centros.slice(0, 8);
    grafico('mdet-g-centro', 'Saúde por centro de custo', 'Só os Cavalos têm centro de custo no cadastro; composição em %',
      alturaBarras(centros.length), () => graficoComposicao('mdet-g-centro', centros));
  }

  // Cartão que sobra sozinho na última linha ocupa a largura toda.
  cartoes.forEach((c, i) => {
    c.largura = (i === cartoes.length - 1 && cartoes.length % 2 === 1) ? 12 : 6;
  });
  return cartoes;
}

function htmlCartoes(cartoes) {
  return `<div class="mdet-grid">${cartoes.map((c) => `
    <section class="mdet-card mdet-card--${c.largura}">
      <header class="mdet-card__head"><h3>${escapar(c.titulo)}</h3>${c.sub ? `<p>${escapar(c.sub)}</p>` : ''}</header>
      ${c.corpo}
    </section>`).join('')}</div>`;
}

function htmlTabela(m) {
  return `
    <section class="mdet-card mdet-card--12 mdet-tabela">
      <header class="mdet-tabela__topo">
        <div class="mdet-card__head">
          <h3>Veículos</h3>
          <p id="mdet-tabela-sub"></p>
        </div>
        <div class="input-icon mdet-tabela__busca">
          <i class="ph ph-magnifying-glass"></i>
          <input type="search" id="mdet-busca" class="input" placeholder="Buscar placa, marca ou modelo..." autocomplete="off" value="${escapar(buscaTexto)}">
        </div>
      </header>
      <div class="chips mdet-chips" id="mdet-chips"></div>
      <div class="data-table-wrap">
        <table class="data-table mdet-tabela__tabela">
          <thead id="mdet-thead"></thead>
          <tbody id="mdet-tabela-corpo"></tbody>
        </table>
      </div>
      <div class="mdet-tabela__mais" id="mdet-mais"></div>
    </section>`;
}

/* ==========================================================
 * Tabela de veículos (busca, filtro de status, ordenação, "mostrar mais")
 * ========================================================== */
function textoSituacao(detalhe) {
  if (!detalhe || detalhe.falta == null) return { texto: '—', negativo: false };
  const { unidade, falta } = detalhe;
  const absoluto = Math.abs(falta);
  if (unidade === 'km') {
    return falta < 0
      ? { texto: `Excedeu ${formatarKm(absoluto)} km`, negativo: true }
      : { texto: `Faltam ${formatarKm(absoluto)} km`, negativo: false };
  }
  if (falta < 0) return { texto: `Vencido há ${absoluto} dia${absoluto === 1 ? '' : 's'}`, negativo: true };
  if (falta === 0) return { texto: 'Vence hoje', negativo: false };
  return { texto: `Faltam ${absoluto} dia${absoluto === 1 ? '' : 's'}`, negativo: false };
}

const ROTULO_CHIP = { todos: 'Todos', vencidos: 'Vencidos', a_vencer: 'A vencer', em_dia: 'Em dia', sem_dado: 'Sem dado' };
const STATUS_DO_CHIP = { vencidos: 'PARAR URGENTE', a_vencer: 'PROGRAMAR PARADA', em_dia: 'EM DIA' };

function baseDaTabela() {
  const todos = contexto.obterVeiculos();
  return escopo.tipo === 'frente' ? veiculosDaFrente(todos, escopo.chave) : todos;
}

function statusDaLinha(v) {
  return escopo.tipo === 'frente' ? v.categorias[escopo.chave] : v.status_geral;
}

function tagsDeFrentes(v) {
  const entradas = Object.entries(v.categorias || {})
    .filter(([chave, status]) => chave !== escopo.chave && (status === 'PARAR URGENTE' || status === 'PROGRAMAR PARADA'))
    .sort(([, a], [, b]) => ORDEM_STATUS[a] - ORDEM_STATUS[b]);
  if (!entradas.length) return '<span class="mdet-neutro">—</span>';
  return `<span class="mdet-tags">${entradas.map(([chave, status]) => `
    <span class="mdet-tag mdet-tag--${status === 'PARAR URGENTE' ? 'red' : 'gold'}" title="${escapar(tituloFrente(chave))}: ${rotuloStatus(status)}">
      <i class="ph ${iconeFrente(chave)}"></i>${escapar(NOME_CURTO_FRENTE[chave] || tituloFrente(chave))}
    </span>`).join('')}</span>`;
}

function linhasOrdenadas(lista) {
  const chave = escopo.chave;
  const severidade = (v) => ORDEM_STATUS[statusDaLinha(v)] ?? 3;
  const criticos = (v) => Object.values(v.categorias || {}).filter((s) => s === 'PARAR URGENTE').length;
  return [...lista].sort((a, b) => {
    if (escopo.tipo === 'saude') {
      return criticos(b) - criticos(a) || severidade(a) - severidade(b) || a.placa.localeCompare(b.placa);
    }
    const da = a.detalhes?.[chave]?.falta;
    const db = b.detalhes?.[chave]?.falta;
    return severidade(a) - severidade(b)
      || (da == null ? 1 : 0) - (db == null ? 1 : 0)
      || (da ?? 0) - (db ?? 0)
      || a.placa.localeCompare(b.placa);
  });
}

function renderizarTabela() {
  const corpo = el('mdet-tabela-corpo');
  if (!corpo || !escopo) return;

  const base = baseDaTabela();
  const contagem = { todos: base.length, vencidos: 0, a_vencer: 0, em_dia: 0, sem_dado: 0 };
  base.forEach((v) => {
    const s = statusDaLinha(v);
    if (s === 'PARAR URGENTE') contagem.vencidos += 1;
    else if (s === 'PROGRAMAR PARADA') contagem.a_vencer += 1;
    else if (s === 'EM DIA') contagem.em_dia += 1;
    else contagem.sem_dado += 1;
  });

  const chips = el('mdet-chips');
  if (chips) {
    chips.innerHTML = Object.keys(ROTULO_CHIP)
      .filter((chave) => chave === 'todos' || contagem[chave] > 0)
      .map((chave) => `<button type="button" class="chip ${filtroStatus === chave ? 'is-on' : ''}" data-action="manut-detalhe-chip" data-chave="${chave}">
        ${ROTULO_CHIP[chave]} <span class="mdet-chip__n">${contagem[chave]}</span></button>`).join('');
  }

  const busca = buscaTexto.trim().toLowerCase();
  const filtrada = base.filter((v) => {
    const s = statusDaLinha(v);
    if (filtroStatus === 'sem_dado' ? temStatusConhecido(s) : (filtroStatus !== 'todos' && s !== STATUS_DO_CHIP[filtroStatus])) return false;
    if (!busca) return true;
    return [v.placa, v.marca, v.modelo, v.tipo].filter(Boolean).join(' ').toLowerCase().includes(busca);
  });
  const ordenada = linhasOrdenadas(filtrada);

  const frente = escopo.tipo === 'frente';
  const cabecalho = frente
    ? ['Placa', 'Tipo', 'Marca · Modelo', 'Status', 'Situação', 'Próxima data', 'Outras frentes em atenção']
    : ['Placa', 'Tipo', 'Marca · Modelo', 'Status geral', 'Frentes em atenção', 'Vencidas'];
  el('mdet-thead').innerHTML = `<tr>${cabecalho.map((c) => `<th>${c}</th>`).join('')}</tr>`;

  const sub = el('mdet-tabela-sub');
  if (sub) sub.textContent = `${filtrada.length} de ${base.length} veículo${base.length === 1 ? '' : 's'}${busca || filtroStatus !== 'todos' ? ' (filtrado)' : ''}`;

  if (!ordenada.length) {
    corpo.innerHTML = `<tr><td colspan="${cabecalho.length}"><div class="empty empty--sm"><p class="empty__text">Nenhum veículo com esse filtro.</p></div></td></tr>`;
    mostrarMais(0);
    return;
  }

  corpo.innerHTML = ordenada.slice(0, limiteLinhas).map((v) => {
    const status = statusDaLinha(v);
    const marcaModelo = [v.marca, v.modelo].filter(Boolean).join(' · ') || '—';
    const comuns = `
      <td><button type="button" class="mdet-placa" data-action="abrir-prontuario" data-placa="${escapar(v.placa)}">${escapar(v.placa)}</button></td>
      <td><span class="badge ${v.tipo === 'Carreta' ? 'badge--brand' : 'badge--neutral'}">${escapar(v.tipo || '—')}</span></td>
      <td>${escapar(marcaModelo)}</td>
      <td><span class="badge ${badgeDoStatus(status)}"><i class="ph ${iconeDoStatus(status)}"></i>${rotuloStatus(status)}</span></td>`;
    if (frente) {
      const detalhe = v.detalhes?.[escopo.chave];
      const situacao = textoSituacao(detalhe);
      return `<tr class="mdet-linha-tabela" data-action="abrir-prontuario" data-placa="${escapar(v.placa)}">${comuns}
        <td class="${situacao.negativo ? 'mdet-neg' : ''}">${situacao.texto}</td>
        <td>${detalhe?.proxima ? dataBRSimples(detalhe.proxima) : '—'}</td>
        <td>${tagsDeFrentes(v)}</td></tr>`;
    }
    const vencidas = Object.values(v.categorias || {}).filter((s) => s === 'PARAR URGENTE').length;
    return `<tr class="mdet-linha-tabela" data-action="abrir-prontuario" data-placa="${escapar(v.placa)}">${comuns}
      <td>${tagsDeFrentes(v)}</td>
      <td class="${vencidas ? 'mdet-neg' : ''}">${vencidas}</td></tr>`;
  }).join('');

  mostrarMais(Math.max(0, ordenada.length - limiteLinhas));
}

/** "Sem dado" = qualquer coisa que não seja um dos 3 status conhecidos. */
function temStatusConhecido(status) {
  return status === 'EM DIA' || status === 'PROGRAMAR PARADA' || status === 'PARAR URGENTE';
}

function mostrarMais(restantes) {
  const area = el('mdet-mais');
  if (!area) return;
  area.innerHTML = restantes > 0
    ? `<button type="button" class="btn btn--secondary" data-action="manut-detalhe-mais">Mostrar mais ${Math.min(restantes, LINHAS_POR_PAGINA)} <span class="mdet-chip__n">${restantes} restantes</span></button>`
    : '';
}

export function filtrarStatusDetalhe(chave) {
  if (!ROTULO_CHIP[chave]) return;
  filtroStatus = chave;
  limiteLinhas = LINHAS_POR_PAGINA;
  renderizarTabela();
}

export function mostrarMaisDetalhe() {
  limiteLinhas += LINHAS_POR_PAGINA;
  renderizarTabela();
}

/* ==========================================================
 * Página
 * ========================================================== */
function atualizarTopo() {
  const nome = escopo.tipo === 'frente' ? tituloFrente(escopo.chave) : 'Saúde da Frota';
  const atual = el('mdet-trilha-atual');
  if (atual) atual.textContent = nome;
  const ativo = contexto.obterFiltroTipo();
  qsa('[data-action="manut-filtro-tipo"]').forEach((botao) => botao.classList.toggle('is-active', botao.dataset.tipo === ativo));
}

function renderizarPagina() {
  const corpo = el('mdet-corpo');
  if (!corpo || !escopo) return;
  destruirGraficos();
  atualizarTopo();

  const m = montarModelo();
  if (!m.base.length) {
    corpo.innerHTML = `
      <div class="card empty">
        <div class="empty__icon"><i class="ph ph-file-dashed"></i></div>
        <p class="empty__title">Nenhum veículo para analisar</p>
        <p class="empty__text">Não há veículos com ${m.chave ? escapar(tituloFrente(m.chave)) : 'dados'} neste filtro. Troque o filtro de tipo acima.</p>
      </div>`;
    return;
  }

  const cartoes = montarCartoes(m);
  corpo.innerHTML = htmlHero(m) + htmlDestaques(m) + htmlCartoes(cartoes) + htmlTabela(m);
  cartoes.forEach((c) => c.desenhar?.());
  renderizarTabela();
}

/* ---------- Teclado e busca ---------- */
document.addEventListener('input', (evento) => {
  if (evento.target.id !== 'mdet-busca') return;
  buscaTexto = evento.target.value;
  limiteLinhas = LINHAS_POR_PAGINA;
  renderizarTabela();
});

document.addEventListener('keydown', (evento) => {
  // Esc volta ao Dashboard - mas só se nenhum modal (ex.: o Prontuário aberto a partir da
  // tabela) acabou de tratar a tecla.
  if (evento.key !== 'Escape' || evento.defaultPrevented || !detalheManutencaoAberto()) return;
  if (document.querySelector('.modal:not(.hidden)')) return;
  fecharDetalheManutencao();
});
