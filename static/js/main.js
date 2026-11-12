/**
 * Ponto de entrada do frontend.
 * Registra as views, liga a delegação de eventos e restaura a sessão.
 */

import { state, el, qs, escapar } from './state.js';
import {
  toast, hooks, abrirModal, fecharModal, fecharTopo, aplicarTema, alternarTema, temaAtual,
  alternarSidebar, restaurarSidebar, navegar, registrarView, renderNotificacoes,
  alternarNotificacoes, fecharNotificacoes, atualizarDataAtual, modalAberto,
} from './ui.js';
import { api, baixarExportacao } from './api.js';
import {
  iniciarAtualizacoes, abrirAtualizacoes, verificarAtualizacao, baixarAtualizacao, aplicarAtualizacao,
  restaurarVersaoAnterior, lembrarMaisTarde, verNovidades,
} from './atualizacao.js';
import {
  fazerLogin, fazerCadastro, sair, restaurarSessao, abrirRecuperacao, mostrarEtapaRecuperacao,
  solicitarCodigo, validarCodigo, redefinirSenha, abrirEdicaoDeNome, salvarNome, trocarAvatar,
} from './auth.js';
import {
  renderEstrutura, aplicarFiltros, limparFiltros, alternarPasta, alternarProjeto, alternarFavorito,
  abrirModalPasta, salvarPasta, excluirPasta, abrirModalProjeto, salvarProjeto, excluirProjeto,
  abrirNovaTarefa, abrirEdicaoTarefa, salvarTarefa, excluirTarefa, alternarStatusTarefa,
  trocarAba, alternarAvancado, alternarDependencia, enviarAnexo, adicionarComentario,
  filtrarDependencias, filtrarSugestoesVeiculoTarefa, navegarSugestaoVeiculoTarefa,
  confirmarSugestaoAtivaVeiculoTarefa,
} from './structure.js';
import { renderKanban } from './kanban.js';
import { renderCalendario, mudarMes, irParaHoje, abrirDia } from './calendar.js';
import { renderDashboard, mudarSetorDashboard, exportarSetorAtual } from './dashboard.js';
import {
  criarSetorInline, abrirGerenciarUsuarios, selecionarEtiquetaSalva, salvarEtiquetaAtual,
  carregarSetoresParaCadastro, alternarSetorUsuario,
} from './admin.js';
import {
  renderManutencao, filtrarManutencao, alternarCardManutencao, abrirProntuario,
  trocarAbaManutencao, abrirImportarManutencao, enviarArquivoManutencao,
  editarCampoManutencao, salvarCampoManutencao, cancelarEdicaoManutencao,
  filtrarCadastro, mudarPaginaCadastro, abrirNovoVeiculo, selecionarTipoCadastro,
  salvarNovoVeiculo, filtrarResumoAnalitico, usarValorAtualConflito, sobrescreverConflito,
  filtrarSugestoesPlaca, navegarSugestaoPlaca, confirmarSugestaoAtiva,
  filtrarDemandas, garantirProjetoAgendaManutencao, aplicarFiltroTipoFrota,
} from './manutencao.js';
import {
  abrirDetalheSaude, abrirDetalheFrente, fecharDetalheManutencao, filtrarStatusDetalhe, mostrarMaisDetalhe,
} from './manutencao_detalhe.js';

/* ============================================================
   VIEWS
   ============================================================ */
registrarView('dashboard', renderDashboard);
registrarView('pastas', renderEstrutura);
registrarView('quadro', renderKanban);
registrarView('calendario', renderCalendario);
registrarView('manutencao', renderManutencao);

/* ============================================================
   AÇÕES (data-action)
   ============================================================ */
const acoes = {
  noop: () => {},

  /* Login e conta */
  'toggle-password': (alvo) => {
    const campo = el(alvo.dataset.target);
    if (!campo) return;
    const revelando = campo.type === 'password';
    campo.type = revelando ? 'text' : 'password';
    qs('i', alvo).className = revelando ? 'ph ph-eye-slash' : 'ph ph-eye';
    alvo.setAttribute('aria-label', revelando ? 'Ocultar senha' : 'Mostrar senha');
  },
  'open-signup': () => { abrirModal('modal-cadastro'); carregarSetoresParaCadastro(); },
  'add-setor-cadastro': () => criarSetorInline('cad-setor-novo', 'cad-setor'),
  'open-recovery': abrirRecuperacao,
  'rec-request': (alvo) => solicitarCodigo(alvo),
  'rec-validate': (alvo) => validarCodigo(alvo),
  'rec-reset': (alvo) => redefinirSenha(alvo),
  'rec-back': (alvo) => mostrarEtapaRecuperacao(Number(alvo.dataset.step)),
  'edit-name': abrirEdicaoDeNome,
  'save-name': salvarNome,
  logout: sair,

  /* Interface */
  nav: (alvo) => navegar(alvo.dataset.view),
  'toggle-theme': alternarTema,
  'abrir-atualizacoes': abrirAtualizacoes,
  'atualizacao-verificar': verificarAtualizacao,
  'atualizacao-baixar': baixarAtualizacao,
  'atualizacao-aplicar': aplicarAtualizacao,
  'atualizacao-restaurar': restaurarVersaoAnterior,
  'atualizacao-lembrar': lembrarMaisTarde,
  'atualizacao-novidades': verNovidades,
  'toggle-sidebar': alternarSidebar,
  'toggle-notifications': alternarNotificacoes,
  'close-modal': (alvo) => fecharModal(alvo.dataset.modal),
  'close-topmost': fecharTopo,
  export: () => {
    toast('Gerando relatório', 'O arquivo Excel será baixado em instantes.', 'info');
    baixarExportacao().catch((falha) => toast('Não foi possível exportar', falha.message, 'error'));
  },
  'open-admin-usuarios': abrirGerenciarUsuarios,
  'export-setor': exportarSetorAtual,
  'admin-toggle-setor': (alvo) => alternarSetorUsuario(alvo.dataset.id, alvo.dataset.setor),

  /* Estrutura */
  'new-folder': () => abrirModalPasta(),
  'edit-folder': (alvo) => abrirModalPasta(alvo.dataset.id, alvo.dataset.nome, alvo.dataset.cor, alvo.dataset.setor),
  'save-folder': salvarPasta,
  'delete-folder': (alvo) => excluirPasta(alvo.dataset.id),
  'toggle-folder': (alvo) => alternarPasta(alvo.dataset.id),

  'new-project': (alvo) => abrirModalProjeto(alvo.dataset.pasta),
  'edit-project': (alvo) => abrirModalProjeto('', alvo.dataset.id, alvo.dataset.nome),
  'save-project': salvarProjeto,
  'delete-project': (alvo) => excluirProjeto(alvo.dataset.id),
  'toggle-project': (alvo) => alternarProjeto(alvo.dataset.id),

  'toggle-fav': (alvo) => alternarFavorito(alvo.dataset.item),
  'clear-filters': limparFiltros,

  /* Demandas */
  'new-task': (alvo) => abrirNovaTarefa(alvo.dataset.projeto),
  'edit-task': (alvo) => abrirEdicaoTarefa(alvo.dataset.id),
  'save-task': (alvo) => salvarTarefa(alvo),
  'delete-task': (alvo) => excluirTarefa(alvo.dataset.id),
  'toggle-task': (alvo) => alternarStatusTarefa(alvo.dataset.id, alvo.dataset.status),
  'toggle-dep': (alvo) => alternarDependencia(alvo.dataset.id),
  'toggle-advanced': () => alternarAvancado(),
  tab: (alvo) => trocarAba(alvo.dataset.tab),
  'add-comment': adicionarComentario,
  'pick-etiqueta': (alvo) => selecionarEtiquetaSalva(alvo.dataset.nome, alvo.dataset.cor),
  'save-etiqueta': salvarEtiquetaAtual,

  /* Calendário */
  'cal-move': (alvo) => mudarMes(Number(alvo.dataset.delta)),
  'cal-today': irParaHoje,
  'open-day': (alvo) => abrirDia(Number(alvo.dataset.dia)),

  /* Frota & Manutenção */
  'toggle-manut-card': (alvo) => alternarCardManutencao(alvo.dataset.cod),
  'reload-manutencao': () => renderManutencao(true),
  'abrir-prontuario': (alvo) => abrirProntuario(alvo.dataset.placa),
  'manut-tab': (alvo) => trocarAbaManutencao(alvo.dataset.tab),
  'manut-filtro-tipo': (alvo) => aplicarFiltroTipoFrota(alvo.dataset.tipo),
  'abrir-detalhe-saude': abrirDetalheSaude,
  'abrir-detalhe-frente': (alvo) => abrirDetalheFrente(alvo.dataset.categoria),
  'manut-detalhe-voltar': fecharDetalheManutencao,
  'manut-detalhe-chip': (alvo) => filtrarStatusDetalhe(alvo.dataset.chave),
  'manut-detalhe-mais': mostrarMaisDetalhe,
  'abrir-importar-manutencao': abrirImportarManutencao,
  'editar-manut-campo': (alvo) => editarCampoManutencao(alvo.dataset.cod, alvo.dataset.campo),
  'salvar-manut-campo': (alvo) => salvarCampoManutencao(alvo.dataset.cod, alvo.dataset.campo),
  'cancelar-manut-edicao': cancelarEdicaoManutencao,
  'cadastro-pagina': (alvo) => mudarPaginaCadastro(Number(alvo.dataset.delta)),
  'abrir-novo-veiculo': abrirNovoVeiculo,
  'tipo-cadastro': (alvo) => selecionarTipoCadastro(alvo.dataset.tipo),
  'salvar-novo-veiculo': salvarNovoVeiculo,
  'usar-valor-atual-conflito': usarValorAtualConflito,
  'sobrescrever-conflito-manutencao': sobrescreverConflito,
  'nova-demanda': async () => abrirEditorDeDemanda(''),
  'nova-demanda-placa': async (alvo) => abrirEditorDeDemanda(alvo.dataset.placa),
  'editar-demanda': (alvo) => abrirEdicaoTarefa(alvo.dataset.id),
};

/** Garante o Projeto "Agenda de Manutenção" antes de abrir o editor de tarefa
 * compartilhado - só existe porque essa é a ÚNICA tela que abre o modal sem
 * já saber de antemão em qual Projeto a tarefa vai morar. */
async function abrirEditorDeDemanda(veiculoPlaca) {
  const projetoId = await garantirProjetoAgendaManutencao();
  if (!projetoId) {
    toast('Não foi possível abrir o editor', 'Tente novamente em instantes.', 'error');
    return;
  }
  abrirNovaTarefa(projetoId, veiculoPlaca);
}

document.addEventListener('click', (evento) => {
  const alvo = evento.target.closest('[data-action]');
  if (!alvo) return;

  const acao = acoes[alvo.dataset.action];
  if (!acao) return;

  evento.preventDefault();
  acao(alvo, evento);
});

/* Fecha o painel de notificações ao clicar fora dele. */
document.addEventListener('click', (evento) => {
  if (!evento.target.closest('#notif-wrapper')) fecharNotificacoes();
});

/* ============================================================
   FORMULÁRIOS E CAMPOS
   ============================================================ */
function debounce(fn, espera = 220) {
  let temporizador;
  return (...args) => {
    clearTimeout(temporizador);
    temporizador = setTimeout(() => fn(...args), espera);
  };
}

function ligarCampos() {
  el('form-login')?.addEventListener('submit', fazerLogin);
  el('form-cadastro')?.addEventListener('submit', fazerCadastro);

  el('filtro-busca')?.addEventListener('input', debounce(aplicarFiltros));
  ['filtro-status', 'filtro-prioridade', 'filtro-responsavel'].forEach((id) => {
    el(id)?.addEventListener('change', aplicarFiltros);
  });

  document.querySelectorAll('[data-action="change-avatar"]').forEach((campo) => {
    campo.addEventListener('change', trocarAvatar);
  });

  el('tarefa-anexo')?.addEventListener('change', enviarAnexo);
  el('tarefa-dependencias-busca')?.addEventListener('input', debounce(filtrarDependencias, 120));
  el('dash-setor')?.addEventListener('change', mudarSetorDashboard);
  el('manut-busca')?.addEventListener('input', debounce(filtrarManutencao, 150));
  el('manut-filtro-status')?.addEventListener('change', filtrarManutencao);
  el('cadastro-busca')?.addEventListener('input', debounce(filtrarCadastro, 150));
  el('resumo-busca')?.addEventListener('input', debounce(filtrarResumoAnalitico, 150));
  el('prontuario-busca')?.addEventListener('input', debounce(filtrarSugestoesPlaca, 180));
  el('prontuario-busca')?.addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter') {
      evento.preventDefault();
      if (!confirmarSugestaoAtiva()) abrirProntuario();
    } else if (evento.key === 'ArrowDown') {
      evento.preventDefault();
      navegarSugestaoPlaca(1);
    } else if (evento.key === 'ArrowUp') {
      evento.preventDefault();
      navegarSugestaoPlaca(-1);
    }
  });

  el('tarefa-veiculo-placa')?.addEventListener('input', debounce(filtrarSugestoesVeiculoTarefa, 180));
  el('tarefa-veiculo-placa')?.addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter') {
      if (confirmarSugestaoAtivaVeiculoTarefa()) evento.preventDefault();
    } else if (evento.key === 'ArrowDown') {
      evento.preventDefault();
      navegarSugestaoVeiculoTarefa(1);
    } else if (evento.key === 'ArrowUp') {
      evento.preventDefault();
      navegarSugestaoVeiculoTarefa(-1);
    }
  });
  el('demanda-busca')?.addEventListener('input', debounce(filtrarDemandas, 150));

  const dropzoneManut = el('manut-dropzone');
  if (dropzoneManut) {
    ['dragenter', 'dragover'].forEach((tipo) => dropzoneManut.addEventListener(tipo, (evento) => {
      evento.preventDefault();
      dropzoneManut.classList.add('is-dragover');
    }));
    ['dragleave', 'drop'].forEach((tipo) => dropzoneManut.addEventListener(tipo, (evento) => {
      evento.preventDefault();
      dropzoneManut.classList.remove('is-dragover');
    }));
    dropzoneManut.addEventListener('drop', (evento) => {
      const arquivo = evento.dataTransfer.files?.[0];
      if (arquivo) enviarArquivoManutencao(arquivo);
    });
  }
  el('manut-dropzone-input')?.addEventListener('change', (evento) => {
    const arquivo = evento.target.files?.[0];
    if (arquivo) enviarArquivoManutencao(arquivo);
    evento.target.value = '';
  });

  el('tarefa-etiqueta-cor')?.addEventListener('input', (evento) => {
    el('tarefa-etiqueta-preview').style.background = evento.target.value;
  });

  el('novo-comentario')?.addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter') {
      evento.preventDefault();
      adicionarComentario();
    }
  });

  el('rec-codigo')?.addEventListener('input', (evento) => {
    evento.target.value = evento.target.value.replace(/\D/g, '').slice(0, 6);
  });

  // Enter salva nos modais simples de nome único.
  [['pasta-nome', 'save-folder'], ['projeto-nome', 'save-project'], ['input-nome-exibicao', 'save-name']]
    .forEach(([campoId, acao]) => {
      el(campoId)?.addEventListener('keydown', (evento) => {
        if (evento.key === 'Enter') {
          evento.preventDefault();
          acoes[acao](document.querySelector(`[data-action="${acao}"]`));
        }
      });
    });
}

/* ============================================================
   ATALHOS DE TECLADO
   ============================================================ */
const ATALHOS = { 1: 'dashboard', 2: 'pastas', 3: 'quadro', 4: 'calendario' };

document.addEventListener('keydown', (evento) => {
  const digitando = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
  if (digitando || modalAberto() || evento.ctrlKey || evento.altKey || evento.metaKey) return;
  if (el('app-screen')?.classList.contains('hidden')) return;

  if (ATALHOS[evento.key]) {
    evento.preventDefault();
    navegar(ATALHOS[evento.key]);
  }

  if (evento.key === '/' ) {
    evento.preventDefault();
    navegar('pastas');
    setTimeout(() => el('filtro-busca')?.focus(), 80);
  }
});

/* ============================================================
   INICIALIZAÇÃO
   ============================================================ */
hooks.aoAbrirNotificacoes = async () => {
  const naoLidas = (state.usuario?.notificacoes || []).some((n) => !n.lida);
  if (!naoLidas) return;
  try {
    await api.marcarNotificacoesLidas(state.usuario.id);
    state.usuario.notificacoes.forEach((n) => { n.lida = true; });
  } catch (erro) { /* sem impacto na interface */ }
};

async function iniciar() {
  aplicarTema(temaAtual());
  restaurarSidebar();
  atualizarDataAtual();
  ligarCampos();

  const autenticado = await restaurarSessao();
  if (!autenticado) el('login-email')?.focus();

  // Prazos mudam com a passagem do tempo, mesmo sem alteração de dados.
  setInterval(() => {
    if (state.usuario) renderNotificacoes();
  }, 60_000);

  setInterval(atualizarDataAtual, 60_000);
}

iniciar();
