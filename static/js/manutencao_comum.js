/**
 * Frota & Manutenção - peças compartilhadas pelo Dashboard (manutencao.js) e pelas
 * páginas de análise de cada card (manutencao_detalhe.js): apresentação de status,
 * configuração dos gráficos e as contas de saúde. Funções sem estado e sem DOM da
 * tela (só `token`/`opcoesGrafico` leem os tokens de tema do CSS).
 */

export function token(nome) {
  return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
}

export function corDoStatus(status) {
  if (status === 'PARAR URGENTE') return 'vermelho';
  if (status === 'PROGRAMAR PARADA') return 'laranja';
  if (status === 'EM DIA') return 'verde';
  return 'neutro';
}

export function badgeDoStatus(status) {
  if (status === 'PARAR URGENTE') return 'badge--red';
  if (status === 'PROGRAMAR PARADA') return 'badge--orange';
  if (status === 'EM DIA') return 'badge--green';
  return 'badge--neutral';
}

export function iconeDoStatus(status) {
  if (status === 'PARAR URGENTE') return 'ph-warning-circle';
  if (status === 'PROGRAMAR PARADA') return 'ph-warning';
  if (status === 'EM DIA') return 'ph-check-circle';
  return 'ph-question';
}

export function rotuloStatus(status) {
  if (status === 'PARAR URGENTE') return 'Parar urgente';
  if (status === 'PROGRAMAR PARADA') return 'Programar parada';
  if (status === 'EM DIA') return 'Em dia';
  return 'Sem dado';
}

export function formatarKm(numero) {
  return Number(numero).toLocaleString('pt-BR');
}

export function dataBRSimples(iso) {
  if (!iso) return '—';
  const [ano, mes, dia] = iso.split('-');
  return `${dia}/${mes}/${ano}`;
}

export function corDoScore(pct) {
  if (pct >= 80) return 'verde';
  if (pct >= 50) return 'laranja';
  return 'vermelho';
}

export function opcoesGrafico() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 420, easing: 'easeOutQuart' },
    plugins: {
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

export const ROTULOS_CATEGORIA = {
  revisao_oleo: { titulo: 'Revisão & Óleo', icone: 'ph-engine' },
  alinhamento: { titulo: 'Alinhamento', icone: 'ph-arrows-left-right' },
  quinta_roda: { titulo: '5ª roda / pino-rei', icone: 'ph-steering-wheel' },
  integridade_chassi: { titulo: 'Integridade de chassi', icone: 'ph-shield-check' },
  sr_tanques: { titulo: 'SR - Tanques', icone: 'ph-drop-half-bottom' },
  preventiva_carretas: { titulo: 'Preventiva geral (carreta)', icone: 'ph-clipboard-text' },
};

/** Ícone e título de cada frente saem de `ROTULOS_CATEGORIA` - um único lugar de
 * verdade, usado pelo Dashboard, pelas páginas de análise e pelo Prontuário. */
export function iconeFrente(chave) {
  return ROTULOS_CATEGORIA[chave]?.icone || 'ph-wrench';
}

export function tituloFrente(chave) {
  return ROTULOS_CATEGORIA[chave]?.titulo || chave;
}

/** Resumo de saúde de QUALQUER recorte, pela mesma regra do Health Score da
 * frota: saúde = % de itens 100% EM DIA entre os que têm status conhecido
 * ("sem dado" fica de fora do denominador - um item recém-cadastrado ainda
 * não é nem saudável nem um problema). `statusDe(v)` diz de qual status olhar:
 * o geral do veículo (Saúde da Frota) ou o de uma frente específica (cards
 * por frente).
 *
 * O denominador é sempre o UNIVERSO inteiro que o número representa, nunca
 * só a fatia que o card lista: "153 vencidos" é o numerador do problema, não
 * a população. Uma frente com 153 vencidos entre 164 veículos tem 11 que NÃO
 * estão vencidos, e são eles que contam para a saúde dela - calcular em cima
 * dos 153 daria 0% para toda frente, por construção. */
export function resumirSaude(lista, statusDe) {
  const r = { em_dia: 0, programar_parada: 0, parar_urgente: 0, sem_dado: 0 };
  lista.forEach((v) => {
    const status = statusDe(v);
    if (status === 'EM DIA') r.em_dia += 1;
    else if (status === 'PROGRAMAR PARADA') r.programar_parada += 1;
    else if (status === 'PARAR URGENTE') r.parar_urgente += 1;
    else r.sem_dado += 1;
  });
  r.elegiveis = r.em_dia + r.programar_parada + r.parar_urgente;
  r.pct = r.elegiveis ? Math.round((r.em_dia / r.elegiveis) * 100) : null;
  return r;
}

/** Todo veículo que TEM a frente (tem a chave em `categorias`) - o universo
 * sobre o qual a saúde daquela frente é medida, tanto no card quanto na página de
 * análise (um Cavalo não tem SR-Tanques, então não entra nessa conta). */
export function veiculosDaFrente(lista, chave) {
  return lista.filter((v) => chave in (v.categorias || {}));
}

/** Saúde de cada frente (Alinhamento, Chassi, 5ª roda...) sobre TODOS os
 * veículos que a têm - não só os vencidos (ver `resumirSaude`). Só entra a
 * frente que tenha pelo menos 1 veículo com status conhecido. Ordem: mais
 * vencidos primeiro (resposta imediata a "que tipo de manutenção está
 * pesando mais na frota agora"), desempatando pela pior saúde. */
export function calcularSaudePorFrente(lista) {
  const chaves = new Set();
  lista.forEach((v) => Object.keys(v.categorias || {}).forEach((c) => chaves.add(c)));

  return [...chaves]
    .map((chave) => ({
      chave,
      ...resumirSaude(veiculosDaFrente(lista, chave), (v) => v.categorias[chave]),
    }))
    .filter((f) => f.elegiveis > 0)
    .sort((a, b) => (b.parar_urgente - a.parar_urgente)
      || ((a.pct ?? 101) - (b.pct ?? 101))
      || tituloFrente(a.chave).localeCompare(tituloFrente(b.chave)));
}

/** Volume de PROGRAMAR PARADA/PARAR URGENTE por FRENTE (não mais por item de
 * óleo) - alimenta "Próximas Manutenções" e o cruzamento de frentes das páginas de
 * análise, já cruzando todas as categorias do Prontuário. Só entra uma frente que tenha
 * PELO MENOS 1 veículo da lista filtrada com ela em atenção (Programar
 * parada/Parar urgente) - uma frente onde todo mundo está em dia não gera
 * barra nenhuma (senão viraria uma barra fantasma, de altura zero, só porque
 * a frente existe no cadastro do veículo). Com "Apenas Cavalos", frentes
 * exclusivas de Carreta (SR-Tanques, preventiva de carretas) somem sozinhas,
 * e vice-versa, sem nenhuma lista hard-coded de "qual frente é de qual
 * tipo". */
export function calcularFrentesEmAtencao(lista) {
  const contagem = {};
  lista.forEach((v) => {
    Object.entries(v.categorias || {}).forEach(([chave, status]) => {
      if (status !== 'PROGRAMAR PARADA' && status !== 'PARAR URGENTE') return;
      if (!contagem[chave]) contagem[chave] = { item: chave, programar_parada: 0, parar_urgente: 0 };
      if (status === 'PROGRAMAR PARADA') contagem[chave].programar_parada += 1;
      else if (status === 'PARAR URGENTE') contagem[chave].parar_urgente += 1;
    });
  });
  return Object.values(contagem);
}

/** Ranking de quem mais precisa de atenção: veículos/implementos com mais frentes em
 * "Parar urgente" AO MESMO TEMPO, do pior para o melhor. `limite` corta a lista (Top 5 do
 * Dashboard); sem limite devolve todos os que têm pelo menos 1 frente vencida. */
export function rankingCriticos(lista, limite = null) {
  const itens = lista
    .map((v) => ({
      placa: v.placa,
      subtitulo: [v.marca, v.modelo, v.tipo].filter(Boolean).join(' · '),
      criticos: Object.values(v.categorias || {}).filter((status) => status === 'PARAR URGENTE').length,
    }))
    .filter((v) => v.criticos > 0)
    .sort((a, b) => b.criticos - a.criticos);
  return limite ? itens.slice(0, limite) : itens;
}
