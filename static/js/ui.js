/**
 * Camada de interface: toasts, modais, tema, navegação, sidebar e notificações.
 * Não conhece a API — quem precisa do servidor é quem chama.
 */

import {
  state, el, qs, qsa, escapar, avatarHTML, nomeDoUsuario, iniciais,
  todasAsTarefas, venceEm, diasAte, dataBR, plural, cortar,
} from './state.js';

/* Ganchos preenchidos pelo main.js para evitar dependência circular com a API. */
export const hooks = {
  aoAbrirNotificacoes: null,
};

/* ---------- TOASTS ---------- */
const ICONES_TOAST = {
  success: 'ph-fill ph-check-circle',
  error: 'ph-fill ph-warning-circle',
  warning: 'ph-fill ph-warning',
  info: 'ph-fill ph-info',
};

export function toast(titulo, texto = '', tipo = 'success') {
  const caixa = el('toasts');
  if (!caixa) return;

  const node = document.createElement('div');
  node.className = `toast toast--${tipo}`;
  node.innerHTML = `
    <span class="toast__icon"><i class="${ICONES_TOAST[tipo] || ICONES_TOAST.info}"></i></span>
    <div class="toast__body">
      <p class="toast__title">${escapar(titulo)}</p>
      ${texto ? `<p class="toast__text">${escapar(texto)}</p>` : ''}
    </div>
    <button class="toast__close" aria-label="Fechar aviso"><i class="ph ph-x"></i></button>
  `;

  const remover = () => {
    node.classList.add('is-leaving');
    setTimeout(() => node.remove(), 220);
  };

  qs('.toast__close', node).addEventListener('click', remover);
  caixa.appendChild(node);

  const tempo = tipo === 'error' ? 6000 : 4000;
  setTimeout(remover, tempo);

  while (caixa.children.length > 4) caixa.firstElementChild.remove();
}

/* ---------- BARRA DE PROGRESSO GLOBAL ---------- */
/** Barra fina no topo (estilo YouTube/GitHub) que avisa "o app está
 * trabalhando" em QUALQUER chamada ao servidor - ligada direto em
 * `api.js#requisicao`, então cobre a tela inteira (abrir o app, sincronizar,
 * salvar um campo) sem cada tela precisar lembrar de acioná-la. Como não dá
 * para saber o progresso real de um `fetch` comum (só o upload por
 * `XMLHttpRequest` da importação de planilha tem isso, e já tem sua própria
 * barra dedicada), simula avanço plausível e nunca chega a 100% sozinha -
 * só quando a última requisição em voo termina, o que suporta N chamadas
 * concorrentes sem esconder a barra cedo demais. */
let requisicoesEmVoo = 0;
let temporizadorExibicao = null;
let temporizadorAvanco = null;

function barraGlobal() {
  return el('progresso-global');
}

function avancarBarra() {
  const barra = barraGlobal();
  if (!barra) return;
  const atual = parseFloat(barra.dataset.pct || '0');
  // Avança rápido no início, desacelera perto do teto simulado (90%) - o
  // salto final para 100% só acontece ao concluir de verdade.
  const passo = atual < 40 ? 18 : atual < 70 ? 7 : atual < 88 ? 2 : 0.4;
  const proximo = Math.min(90, atual + passo);
  barra.dataset.pct = String(proximo);
  barra.style.width = `${proximo}%`;
}

export function iniciarCarregamentoGlobal() {
  requisicoesEmVoo += 1;
  if (requisicoesEmVoo > 1) return; // já em andamento - só a 1ª chamada liga a barra

  const barra = barraGlobal();
  if (!barra) return;
  // Debounce de ~150ms: uma resposta quase instantânea nunca chega a piscar
  // a barra na tela, evitando o efeito "flash" em navegações rápidas.
  temporizadorExibicao = setTimeout(() => {
    barra.dataset.pct = '0';
    barra.style.width = '0%';
    barra.classList.remove('is-completando');
    barra.classList.add('is-ativa');
    temporizadorAvanco = setInterval(avancarBarra, 220);
  }, 150);
}

export function concluirCarregamentoGlobal() {
  requisicoesEmVoo = Math.max(0, requisicoesEmVoo - 1);
  if (requisicoesEmVoo > 0) return; // ainda há outras chamadas em voo

  clearTimeout(temporizadorExibicao);
  clearInterval(temporizadorAvanco);

  const barra = barraGlobal();
  if (!barra || !barra.classList.contains('is-ativa')) return;

  barra.dataset.pct = '100';
  barra.style.width = '100%';
  barra.classList.add('is-completando');
  setTimeout(() => {
    barra.classList.remove('is-ativa', 'is-completando');
    barra.style.width = '0%';
  }, 260);
}

/* ---------- MODAIS ---------- */
const pilha = [];
const guardas = new Map();
let focoAnterior = null;

const FOCAVEIS = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function abrirModal(id) {
  const modal = el(id);
  if (!modal || pilha.includes(id)) return;

  if (!pilha.length) {
    focoAnterior = document.activeElement;
    el('modal-backdrop')?.classList.remove('hidden');
  }

  pilha.push(id);
  modal.classList.remove('hidden');
  modal.style.zIndex = String(110 + pilha.length * 2);

  requestAnimationFrame(() => {
    const primeiro = qsa(FOCAVEIS, modal).find((node) => node.offsetParent !== null && !node.classList.contains('icon-btn'));
    (primeiro || qs('.modal__card', modal))?.focus?.();
  });
}

/** Registra (ou remove, passando null) uma checagem de "tem alteração não salva?" para um modal. */
export function registrarGuardaFechamento(id, verificarSujo) {
  if (verificarSujo) guardas.set(id, verificarSujo); else guardas.delete(id);
}

function fecharModalImediato(id) {
  const modal = el(id);
  if (!modal) return;

  modal.classList.add('hidden');
  modal.style.zIndex = '';

  const indice = pilha.indexOf(id);
  if (indice >= 0) pilha.splice(indice, 1);

  if (!pilha.length) {
    el('modal-backdrop')?.classList.add('hidden');
    if (focoAnterior && document.contains(focoAnterior)) focoAnterior.focus();
    focoAnterior = null;
  }
}

export function fecharModal(id) {
  const guarda = pilha.includes(id) ? guardas.get(id) : null;
  if (guarda && guarda()) {
    confirmar({
      titulo: 'Descartar alterações?',
      texto: 'Esta demanda tem alterações que ainda não foram salvas. Se sair agora, elas serão perdidas.',
      acao: 'Descartar',
      perigo: true,
    }).then((confirmado) => {
      if (confirmado) fecharModalImediato(id);
    });
    return;
  }
  fecharModalImediato(id);
}

export function fecharTopo() {
  if (pilha.length) fecharModal(pilha[pilha.length - 1]);
}

export function modalAberto() {
  return pilha.length > 0;
}

/** Prende o Tab dentro do modal do topo. */
function prenderFoco(evento) {
  if (!pilha.length || evento.key !== 'Tab') return;
  const modal = el(pilha[pilha.length - 1]);
  if (!modal) return;

  const focaveis = qsa(FOCAVEIS, modal).filter((node) => node.offsetParent !== null);
  if (!focaveis.length) return;

  const primeiro = focaveis[0];
  const ultimo = focaveis[focaveis.length - 1];

  if (evento.shiftKey && document.activeElement === primeiro) {
    evento.preventDefault();
    ultimo.focus();
  } else if (!evento.shiftKey && document.activeElement === ultimo) {
    evento.preventDefault();
    primeiro.focus();
  }
}

document.addEventListener('keydown', (evento) => {
  if (evento.key === 'Escape') {
    if (pilha.length) {
      evento.preventDefault();
      fecharTopo();
    } else {
      fecharNotificacoes();
    }
    return;
  }
  prenderFoco(evento);
});

/* ---------- CONFIRMAÇÃO ---------- */
export function confirmar({ titulo, texto, acao = 'Confirmar', perigo = false }) {
  return new Promise((resolve) => {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.id = `confirm-${Date.now()}`;
    modal.innerHTML = `
      <div class="modal__card modal__card--narrow">
        <div class="modal__head">
          <div class="modal__heading">
            <div class="modal__mark" style="${perigo ? 'background:var(--red-soft);color:var(--red)' : ''}">
              <i class="ph ${perigo ? 'ph-trash' : 'ph-question'}"></i>
            </div>
            <div>
              <h3 class="modal__title">${escapar(titulo)}</h3>
            </div>
          </div>
        </div>
        <div class="modal__body"><p style="font-size:13.5px;color:var(--text-2);line-height:1.6">${escapar(texto)}</p></div>
        <div class="modal__foot">
          <button class="btn btn--secondary" data-confirm="nao">Cancelar</button>
          <button class="btn ${perigo ? 'btn--danger' : 'btn--primary'}" data-confirm="sim">${escapar(acao)}</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);
    abrirModal(modal.id);

    const finalizar = (resultado) => {
      fecharModal(modal.id);
      modal.remove();
      resolve(resultado);
    };

    modal.addEventListener('click', (evento) => {
      const escolha = evento.target.closest('[data-confirm]');
      if (escolha) finalizar(escolha.dataset.confirm === 'sim');
    });

    // Esc fecha o modal pela pilha; o nó órfão precisa sumir junto.
    const observador = new MutationObserver(() => {
      if (modal.classList.contains('hidden')) {
        observador.disconnect();
        modal.remove();
        resolve(false);
      }
    });
    observador.observe(modal, { attributes: true, attributeFilter: ['class'] });
  });
}

/* ---------- TEMA ---------- */
export function aplicarTema(tema) {
  document.documentElement.setAttribute('data-theme', tema);
  try { localStorage.setItem('hg_theme', tema); } catch (e) { /* modo privado */ }

  const icone = el('theme-icon');
  const rotulo = el('theme-label');
  if (icone) icone.className = tema === 'dark' ? 'ph ph-sun' : 'ph ph-moon';
  if (rotulo) rotulo.textContent = tema === 'dark' ? 'Claro' : 'Escuro';

  document.dispatchEvent(new CustomEvent('tema:alterado', { detail: { tema } }));
}

export function alternarTema() {
  const atual = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  aplicarTema(atual === 'dark' ? 'light' : 'dark');
}

export function temaAtual() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

/* ---------- SIDEBAR ---------- */
export function alternarSidebar() {
  const app = el('app-screen');
  if (!app) return;
  const recolhida = app.classList.toggle('is-collapsed');
  try { localStorage.setItem('hg_sidebar', recolhida ? '1' : '0'); } catch (e) { /* ignora */ }
}

export function restaurarSidebar() {
  try {
    if (localStorage.getItem('hg_sidebar') === '1') el('app-screen')?.classList.add('is-collapsed');
  } catch (e) { /* ignora */ }
}

/* ---------- NAVEGAÇÃO ---------- */
const views = new Map();

const TITULOS = {
  dashboard: ['Visão geral', 'Métricas e prazos da operação.'],
  pastas: ['Estrutura', 'Pastas, projetos e demandas da equipe.'],
  quadro: ['Quadro', 'Gestão visual do fluxo de trabalho.'],
  calendario: ['Calendário', 'Vencimentos distribuídos no mês.'],
  manutencao: ['Frota & Manutenção', 'Revisões e trocas de óleo em dia.'],
};

export function registrarView(nome, render) {
  views.set(nome, render);
}

export function navegar(nome) {
  if (!TITULOS[nome]) return;
  state.view = nome;

  qsa('.view').forEach((secao) => secao.classList.add('hidden'));
  el(`view-${nome}`)?.classList.remove('hidden');

  qsa('.nav-item[data-view]').forEach((botao) => {
    botao.classList.toggle('is-active', botao.dataset.view === nome);
  });

  const [titulo, subtitulo] = TITULOS[nome];
  el('header-title').textContent = titulo;
  el('header-subtitle').textContent = subtitulo;
  el('content').scrollTop = 0;

  views.get(nome)?.();
}

/** Redesenha a view ativa depois de uma alteração de dados. */
export function atualizarViewAtual() {
  views.get(state.view)?.();
}

/* ---------- PERFIL NA INTERFACE ---------- */
export function aplicarPerfil(usuario) {
  if (!usuario) return;
  const nome = nomeDoUsuario(usuario);
  const letra = iniciais(nome);

  const nomeSidebar = el('user-name-sidebar');
  if (nomeSidebar) nomeSidebar.textContent = nome;

  [el('user-avatar-sidebar'), el('user-avatar-header')].forEach((avatar) => {
    if (!avatar) return;
    if (usuario.foto_perfil) {
      avatar.style.backgroundImage = `url('${usuario.foto_perfil}')`;
      avatar.textContent = '';
      avatar.classList.remove('avatar-c0', 'avatar-c1', 'avatar-c2', 'avatar-c3', 'avatar-c4', 'avatar-c5');
    } else {
      avatar.style.backgroundImage = '';
      avatar.textContent = letra;
    }
  });

  const setorBadge = el('user-card-setor');
  if (setorBadge) {
    const setores = usuario.setores || [];
    if (usuario.conta_mestre) {
      setorBadge.textContent = 'Conta Mestre';
      setorBadge.className = 'badge badge--gold';
      setorBadge.style.marginTop = '6px';
    } else if (setores.length) {
      const visiveis = setores.slice(0, 2).join(', ');
      setorBadge.textContent = setores.length > 2 ? `${visiveis} +${setores.length - 2}` : visiveis;
      setorBadge.title = setores.join(', ');
      setorBadge.className = 'badge badge--brand';
      setorBadge.style.marginTop = '6px';
    } else {
      setorBadge.textContent = '';
    }
  }
  el('nav-admin-bloco')?.classList.toggle('hidden', !usuario.conta_mestre);

  // Frota & Manutenção: mesma regra do backend (setor Manutenção ou Conta
  // Mestre) — replicada aqui só para decidir o que mostrar no menu; a rota
  // /api/manutencao já barra o acesso de qualquer jeito.
  const podeVerManutencao = usuario.conta_mestre || (usuario.setores || []).includes('Manutenção');
  el('nav-manutencao-bloco')?.classList.toggle('hidden', !podeVerManutencao);
}

export function atualizarDataAtual() {
  const node = el('current-date');
  if (!node) return;
  const texto = new Date().toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: 'short' });
  node.textContent = texto.charAt(0).toUpperCase() + texto.slice(1).replace('.', '');
}

/* ---------- NOTIFICAÇÕES ---------- */
function montarAlertas() {
  const alertas = [];

  todasAsTarefas().forEach((tarefa) => {
    if (tarefa.concluida) return;
    const vencimento = venceEm(tarefa);
    if (!vencimento) return;

    const dias = diasAte(vencimento);
    if (dias === null) return;

    if (dias < 0) {
      const atraso = Math.abs(dias);
      alertas.push({
        peso: 0,
        icone: 'ph-fill ph-warning-circle',
        estilo: 'background:var(--red-soft);color:var(--red)',
        cor: 'color:var(--red)',
        titulo: 'Demanda atrasada',
        texto: tarefa.titulo,
        meta: `${atraso} ${plural(atraso, 'dia', 'dias')} de atraso · ${tarefa.projetoNome}`,
        ordem: dias,
      });
    } else if (dias === 0) {
      alertas.push({
        peso: 1,
        icone: 'ph-fill ph-fire',
        estilo: 'background:var(--gold-soft);color:var(--gold-ink)',
        cor: 'color:var(--gold-ink)',
        titulo: 'Vence hoje',
        texto: tarefa.titulo,
        meta: tarefa.projetoNome,
        ordem: dias,
      });
    } else if (dias <= 2) {
      alertas.push({
        peso: 2,
        icone: 'ph ph-clock',
        estilo: 'background:var(--brand-soft);color:var(--brand)',
        cor: 'color:var(--brand)',
        titulo: `Vence em ${dias} ${plural(dias, 'dia', 'dias')}`,
        texto: tarefa.titulo,
        meta: tarefa.projetoNome,
        ordem: dias,
      });
    }
  });

  (state.usuario?.notificacoes || []).filter((n) => !n.lida).slice(0, 8).forEach((n) => {
    alertas.push({
      peso: 3,
      icone: 'ph ph-user-check',
      estilo: 'background:var(--brand-soft);color:var(--brand)',
      cor: 'color:var(--brand)',
      titulo: 'Atribuída a você',
      texto: n.tarefa_titulo || n.mensagem,
      meta: dataBR(n.data),
      ordem: 99,
    });
  });

  return alertas.sort((a, b) => (a.peso - b.peso) || (a.ordem - b.ordem));
}

export function renderNotificacoes() {
  const lista = el('notif-list');
  if (!lista) return;

  const alertas = montarAlertas();
  const badge = el('notif-badge');
  const contador = el('notif-count');

  if (badge) badge.classList.toggle('hidden', alertas.length === 0);
  if (contador) contador.textContent = String(alertas.length);

  if (!alertas.length) {
    lista.innerHTML = `
      <div class="empty empty--sm">
        <div class="empty__icon"><i class="ph ph-bell-slash"></i></div>
        <p class="empty__title">Tudo em dia</p>
        <p class="empty__text">Nenhum prazo exige atenção agora.</p>
      </div>`;
    return;
  }

  lista.innerHTML = alertas.slice(0, 20).map((alerta) => `
    <div class="notif-item">
      <span class="notif-item__icon" style="${alerta.estilo}"><i class="${alerta.icone}"></i></span>
      <div class="notif-item__body">
        <p class="notif-item__title" style="${alerta.cor}">${escapar(alerta.titulo)}</p>
        <p class="notif-item__text">${escapar(cortar(alerta.texto, 48))}</p>
        <p class="notif-item__meta">${escapar(alerta.meta || '')}</p>
      </div>
    </div>
  `).join('');
}

export function alternarNotificacoes() {
  const painel = el('notif-panel');
  if (!painel) return;

  const abrindo = painel.classList.contains('hidden');
  if (abrindo) {
    renderNotificacoes();
    painel.classList.remove('hidden');
    hooks.aoAbrirNotificacoes?.();
  } else {
    painel.classList.add('hidden');
  }
}

export function fecharNotificacoes() {
  el('notif-panel')?.classList.add('hidden');
}

/* ---------- ESTADO DE CARREGAMENTO EM BOTÕES ---------- */
export function carregando(botao, ativo, textoCarregando = 'Aguarde...') {
  if (!botao) return;
  if (ativo) {
    botao.dataset.htmlOriginal = botao.innerHTML;
    botao.disabled = true;
    botao.innerHTML = `<i class="ph ph-spinner-gap" style="animation:spin .8s linear infinite"></i> <span>${escapar(textoCarregando)}</span>`;
  } else {
    botao.disabled = false;
    if (botao.dataset.htmlOriginal) botao.innerHTML = botao.dataset.htmlOriginal;
    delete botao.dataset.htmlOriginal;
  }
}

/* ---------- ESQUELETOS ---------- */
export function esqueleto(quantidade, altura = 56) {
  return Array.from({ length: quantidade }, () =>
    `<div class="skeleton" style="height:${altura}px;margin:8px 0"></div>`).join('');
}

export { avatarHTML };
