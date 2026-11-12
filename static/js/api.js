/**
 * Única porta de entrada para o servidor.
 * Anexa o token de sessão, normaliza erros e avisa o usuário via toast.
 */

import { toast, iniciarCarregamentoGlobal, concluirCarregamentoGlobal } from './ui.js';

const CHAVE_TOKEN = 'hg_session_token';

export const sessao = {
  obter() {
    try { return localStorage.getItem(CHAVE_TOKEN); } catch (e) { return null; }
  },
  definir(token) {
    try { localStorage.setItem(CHAVE_TOKEN, token); } catch (e) { /* modo privado */ }
  },
  limpar() {
    try { localStorage.removeItem(CHAVE_TOKEN); } catch (e) { /* modo privado */ }
  },
};

/**
 * @param {string} caminho  rota absoluta, ex.: '/api/dados'
 * @param {object} opcoes   { metodo, corpo, silencioso }
 *   `silencioso` evita o toast automático quando quem chama trata o erro.
 */
async function requisicao(caminho, { metodo = 'GET', corpo = null, silencioso = false } = {}) {
  const opcoes = { method: metodo, headers: { 'Content-Type': 'application/json' } };

  const token = sessao.obter();
  if (token) opcoes.headers.Authorization = `Bearer ${token}`;
  if (corpo !== null) opcoes.body = JSON.stringify(corpo);

  // Toda chamada ao servidor liga a barra de progresso global (debounced,
  // some sozinha se a resposta for rápida) - a tela nunca deve parecer
  // travada, mesmo numa tela que ainda não tem seu próprio skeleton.
  iniciarCarregamentoGlobal();
  try {
    let resposta;
    try {
      resposta = await fetch(caminho, opcoes);
    } catch (falha) {
      const erro = new Error('Não foi possível falar com o servidor.');
      if (!silencioso) toast('Sem conexão', erro.message, 'error');
      throw erro;
    }

    let dados = {};
    try {
      dados = await resposta.json();
    } catch (falha) {
      dados = {};
    }

    if (!resposta.ok || dados.success === false) {
      const erro = new Error(dados.message || 'Não foi possível concluir a operação.');
      erro.status = resposta.status;
      erro.dados = dados;
      if (!silencioso) toast('Ops', erro.message, 'error');
      throw erro;
    }

    return dados;
  } finally {
    concluirCarregamentoGlobal();
  }
}

export const api = {
  /* Sessão e conta */
  login: (email, senha) => requisicao('/api/login', { metodo: 'POST', corpo: { email, senha }, silencioso: true }),
  validarSessao: (token) => requisicao('/api/sessao/validar', { metodo: 'POST', corpo: { token }, silencioso: true }),
  sessaoSalva: () => requisicao('/api/sessao/arquivo', { silencioso: true }),
  encerrarSessao: (token) => requisicao('/api/sessao/encerrar', { metodo: 'POST', corpo: { token }, silencioso: true }),
  cadastrar: (nome, email, senha, setor) =>
    requisicao('/api/cadastro', { metodo: 'POST', corpo: { nome, email, senha, setor }, silencioso: true }),

  /* Recuperação de senha */
  recuperacaoSolicitar: (email) => requisicao('/api/recuperacao/solicitar', { metodo: 'POST', corpo: { email }, silencioso: true }),
  recuperacaoValidar: (email, codigo) => requisicao('/api/recuperacao/validar', { metodo: 'POST', corpo: { email, codigo }, silencioso: true }),
  recuperacaoRedefinir: (dados) => requisicao('/api/recuperacao/redefinir', { metodo: 'POST', corpo: dados, silencioso: true }),

  /* Perfil */
  atualizarPerfil: (dados) => requisicao('/api/perfil/atualizar', { metodo: 'POST', corpo: dados }),
  gerenciarUsuario: (usuarioId, dados) => requisicao(`/api/usuario/${usuarioId}/gerenciar`, { metodo: 'POST', corpo: dados }),

  /* Dados */
  dados: () => requisicao('/api/dados'),
  dashboard: (setor) => requisicao(setor ? `/api/dashboard?setor=${encodeURIComponent(setor)}` : '/api/dashboard'),

  /* Setores */
  setores: () => requisicao('/api/setores', { silencioso: true }),
  adicionarSetor: (nome) => requisicao('/api/setores/adicionar', { metodo: 'POST', corpo: { nome } }),

  /* Etiquetas pré-salvas */
  etiquetas: () => requisicao('/api/etiquetas', { silencioso: true }),
  salvarEtiqueta: (nome, cor) => requisicao('/api/etiquetas', { metodo: 'POST', corpo: { nome, cor } }),
  excluirEtiqueta: (id) => requisicao(`/api/etiquetas/${id}`, { metodo: 'DELETE' }),

  /* Atualização automática */
  atualizacao: (caminho, metodo = 'GET') => requisicao(`/api/atualizacao${caminho}`, { metodo, silencioso: true }),

  /* Frota & Manutenção (Google Sheets) */
  manutencao: (forcar = false) => requisicao(`/api/manutencao${forcar ? '?forcar=1' : ''}`),
  prontuario: (placa) => requisicao(`/api/manutencao/prontuario/${encodeURIComponent(placa)}`, { silencioso: true }),
  categoriasManutencao: (chaves) => requisicao(`/api/manutencao/categoria/${chaves.map(encodeURIComponent).join(',')}`, { silencioso: true }),
  atualizarManutencao: (dados) => requisicao('/api/manutencao/atualizar', { metodo: 'POST', corpo: dados, silencioso: true }),
  importarManutencao: (dados) => requisicao('/api/manutencao/importar', { metodo: 'POST', corpo: dados, silencioso: true }),
  cadastroFrota: () => requisicao('/api/manutencao/cadastro', { silencioso: true }),
  cadastrarVeiculo: (dados) => requisicao('/api/manutencao/cadastro', { metodo: 'POST', corpo: dados, silencioso: true }),
  definirVisibilidadeVeiculo: (placa, visivel) => requisicao(
    `/api/manutencao/cadastro/${encodeURIComponent(placa)}/visibilidade`,
    { metodo: 'POST', corpo: { visivel }, silencioso: true },
  ),
  resumoAnalitico: () => requisicao('/api/manutencao/resumo-analitico', { silencioso: true }),
  demandasManutencao: (placa) => requisicao(`/api/manutencao/demandas${placa ? `?placa=${encodeURIComponent(placa)}` : ''}`, { silencioso: true }),
  saudeFrota: () => requisicao('/api/manutencao/saude-frota', { silencioso: true }),

  /* Estrutura */
  salvarPasta: (dados) => requisicao('/api/pasta', { metodo: 'POST', corpo: dados }),
  excluirPasta: (id) => requisicao('/api/pasta', { metodo: 'DELETE', corpo: { id } }),
  salvarProjeto: (dados) => requisicao('/api/projeto', { metodo: 'POST', corpo: dados }),
  excluirProjeto: (id) => requisicao('/api/projeto', { metodo: 'DELETE', corpo: { id } }),

  /* Tarefas */
  salvarTarefa: (dados) => requisicao('/api/tarefa', { metodo: 'POST', corpo: dados }),
  alterarStatus: (dados) => requisicao('/api/tarefa', { metodo: 'PATCH', corpo: dados }),
  excluirTarefa: (id) => requisicao('/api/tarefa', { metodo: 'DELETE', corpo: { id } }),
  anexar: (tarefaId, dados) => requisicao(`/api/tarefa/${tarefaId}/anexo`, { metodo: 'POST', corpo: dados }),
  comentar: (tarefaId, dados) => requisicao(`/api/tarefa/${tarefaId}/comentario`, { metodo: 'POST', corpo: dados }),

  /* Extras */
  alternarFavorito: (usuarioId, itemId) =>
    requisicao(`/api/usuario/${usuarioId}/favoritos`, { metodo: 'POST', corpo: { item_id: itemId } }),
  marcarNotificacoesLidas: (usuarioId) =>
    requisicao(`/api/usuario/${usuarioId}/notificacoes/lidas`, { metodo: 'POST', corpo: {}, silencioso: true }),
  adicionarCidade: (cidade) => requisicao('/api/cidades/adicionar', { metodo: 'POST', corpo: { cidade }, silencioso: true }),
  salvarAnotacao: (chave, texto) => requisicao('/api/anotacao', { metodo: 'POST', corpo: { chave, texto }, silencioso: true }),
};

/** Recarrega o estado global a partir do servidor. */
export async function carregarDados(state) {
  const dados = await api.dados();
  state.dados = {
    pastas: dados.pastas || [],
    usuarios: dados.usuarios || [],
    status: dados.status || [],
    cidades: dados.cidades || [],
    anotacoes: dados.anotacoes || {},
    setores: dados.setores || [],
    etiquetas_salvas: dados.etiquetas_salvas || [],
  };
  return state.dados;
}

/**
 * Baixa o relatório Excel ENVIANDO o token (antes: window.open sem token, e o servidor entregava
 * os dados de todos os setores a qualquer pessoa).
 */
export async function baixarExportacao(setor = null) {
  const url = '/api/exportar' + (setor ? `?setor=${encodeURIComponent(setor)}` : '');
  const token = sessao.obter();
  iniciarCarregamentoGlobal();
  try {
    const resposta = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!resposta.ok) {
      let mensagem = 'Não foi possível gerar o relatório.';
      try { mensagem = (await resposta.json()).message || mensagem; } catch (falha) { /* corpo não é JSON */ }
      throw new Error(mensagem);
    }
    const blob = await resposta.blob();
    const origem = (resposta.headers.get('Content-Disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = origem ? decodeURIComponent(origem[1]) : 'relatorio_demandas.xlsx';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 15000);
  } finally {
    concluirCarregamentoGlobal();
  }
}
