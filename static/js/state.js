/**
 * Estado compartilhado e utilitários puros.
 * Nenhum outro módulo guarda cópia dos dados: tudo lê daqui.
 */

export const state = {
  dados: { pastas: [], usuarios: [], status: [], cidades: [], anotacoes: {}, setores: [], etiquetas_salvas: [] },
  usuario: null,
  view: 'dashboard',
  filtros: { busca: '', status: '', prioridade: '', responsavel: '' },
  recolhidos: { pastas: new Set(), projetos: new Set() },
  tarefaEmEdicao: null,
  mesCalendario: new Date(),
};

/* ---------- DOM ---------- */
export const el = (id) => document.getElementById(id);
export const qs = (sel, raiz = document) => raiz.querySelector(sel);
export const qsa = (sel, raiz = document) => Array.from(raiz.querySelectorAll(sel));

export function mostrar(alvo, visivel) {
  const node = typeof alvo === 'string' ? el(alvo) : alvo;
  if (node) node.classList.toggle('hidden', !visivel);
}

export function valor(id) {
  const node = el(id);
  return node ? node.value.trim() : '';
}

export function definirValor(id, v) {
  const node = el(id);
  if (node) node.value = v ?? '';
}

/**
 * Marca um campo como inválido (borda vermelha + mensagem abaixo dele), além do toast.
 * A marca some sozinha assim que o usuário volta a digitar no campo.
 */
export function marcarInvalido(id, mensagem) {
  const campo = el(id);
  if (!campo) return;

  campo.classList.add('is-invalid');
  const wrapper = campo.closest('.field') || campo.parentElement;
  if (!wrapper) return;

  let erro = wrapper.querySelector(':scope > .field__error');
  if (!erro) {
    erro = document.createElement('p');
    erro.className = 'field__error';
    wrapper.appendChild(erro);
  }
  erro.innerHTML = `<i class="ph ph-warning-circle"></i>${escapar(mensagem)}`;

  campo.addEventListener('input', () => limparInvalido(id), { once: true });
}

export function limparInvalido(id) {
  const campo = el(id);
  if (!campo) return;

  campo.classList.remove('is-invalid');
  const wrapper = campo.closest('.field') || campo.parentElement;
  wrapper?.querySelector(':scope > .field__error')?.remove();
}

/* ---------- Texto ---------- */
export function escapar(texto) {
  if (texto === null || texto === undefined) return '';
  return String(texto)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function cortar(texto, limite) {
  const t = String(texto || '');
  return t.length > limite ? `${t.slice(0, limite)}…` : t;
}

export function plural(n, singular, pluralForma) {
  return n === 1 ? singular : pluralForma;
}

/* ---------- Datas ----------
 * Sempre no fuso local. `new Date('2025-03-10')` seria meia-noite UTC e cairia
 * no dia anterior no Brasil, jogando as tarefas para a célula errada do calendário.
 */
export function paraData(entrada) {
  if (!entrada) return null;
  if (entrada instanceof Date) return isNaN(entrada) ? null : entrada;

  const texto = String(entrada).trim();
  const partes = texto.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (partes) {
    return new Date(
      Number(partes[1]), Number(partes[2]) - 1, Number(partes[3]),
      Number(partes[4] || 0), Number(partes[5] || 0), Number(partes[6] || 0),
    );
  }
  const fallback = new Date(texto);
  return isNaN(fallback.getTime()) ? null : fallback;
}

export function dataBR(entrada) {
  const data = paraData(entrada);
  if (!data) return '—';
  return data.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

export function inicioDoDia(data = new Date()) {
  return new Date(data.getFullYear(), data.getMonth(), data.getDate());
}

export function diasAte(entrada) {
  const data = paraData(entrada);
  if (!data) return null;
  const dia = 24 * 60 * 60 * 1000;
  return Math.round((inicioDoDia(data) - inicioDoDia()) / dia);
}

export function chaveDoDia(data) {
  return `${data.getFullYear()}-${data.getMonth()}-${data.getDate()}`;
}

export const MESES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];

export const DIAS_SEMANA = ['Domingo', 'Segunda-feira', 'Terça-feira', 'Quarta-feira',
  'Quinta-feira', 'Sexta-feira', 'Sábado'];

/* ---------- Domínio ---------- */
export function nomeDoUsuario(usuario) {
  if (!usuario) return '';
  return usuario.nome_exibicao || usuario.nome || '';
}

export function nomeDoAutor() {
  return nomeDoUsuario(state.usuario) || 'Sistema';
}

export function todasAsTarefas() {
  const lista = [];
  state.dados.pastas.forEach((pasta) => {
    (pasta.projetos || []).forEach((projeto) => {
      (projeto.tarefas || []).forEach((tarefa) => {
        lista.push({ ...tarefa, projetoNome: projeto.nome, projetoId: projeto.id, pastaNome: pasta.nome });
      });
    });
  });
  return lista;
}

export function buscarTarefa(id) {
  return todasAsTarefas().find((t) => t.id === id) || null;
}

export function venceEm(tarefa) {
  return tarefa.data_vencimento || tarefa.data_limite || null;
}

/* ---------- Apresentação ---------- */
export function iniciais(nome) {
  const limpo = String(nome || '').trim();
  return limpo ? limpo.charAt(0).toUpperCase() : '?';
}

function indiceDeCor(nome) {
  const texto = String(nome || '');
  let soma = 0;
  for (let i = 0; i < texto.length; i += 1) soma += texto.charCodeAt(i);
  return soma % 6;
}

/**
 * Avatar como string HTML. `tamanho` aceita 'xs' | 'sm' | 'md' | 'lg'.
 */
export function avatarHTML(nome, foto, tamanho = 'sm') {
  const classe = tamanho ? ` avatar--${tamanho}` : '';
  if (!nome || nome === 'Não atribuído') {
    return `<span class="avatar${classe} avatar--empty" title="Não atribuído"><i class="ph ph-user"></i></span>`;
  }
  if (foto) {
    return `<span class="avatar${classe}" title="${escapar(nome)}" style="background-image:url('${escapar(foto)}')"></span>`;
  }
  return `<span class="avatar${classe} avatar-c${indiceDeCor(nome)}" title="${escapar(nome)}">${escapar(iniciais(nome))}</span>`;
}

export const CORES_STATUS = {
  'Pendente': { cor: 'var(--gold)', badge: 'badge--gold', icone: 'ph-clock' },
  'Em Andamento': { cor: 'var(--brand)', badge: 'badge--brand', icone: 'ph-spinner-gap' },
  'Concluída': { cor: 'var(--green)', badge: 'badge--green', icone: 'ph-check-circle' },
  'Cancelada': { cor: 'var(--text-3)', badge: 'badge--neutral', icone: 'ph-prohibit' },
};

export function metaStatus(nome) {
  return CORES_STATUS[nome] || CORES_STATUS['Pendente'];
}

export const PRIORIDADES = {
  'Alta': { badge: 'badge--red', icone: 'ph-arrow-up', classe: 'alta' },
  'Média': { badge: 'badge--orange', icone: 'ph-minus', classe: 'media' },
  'Baixa': { badge: 'badge--neutral', icone: 'ph-arrow-down', classe: 'baixa' },
};

export function metaPrioridade(nome) {
  return PRIORIDADES[nome] || PRIORIDADES['Média'];
}

export const PALETA_PASTAS = [
  '#0D3B8C', '#2563EB', '#0EA5E9', '#059669', '#10B981',
  '#F5B800', '#F97316', '#DC2626', '#8B5CF6', '#64748B',
];
