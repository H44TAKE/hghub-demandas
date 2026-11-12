/**
 * Autenticação: login, cadastro, sessão persistente e recuperação de senha.
 */

import { api, sessao, carregarDados } from './api.js';
import { iniciarAtualizacoes } from './atualizacao.js';
import {
  state, el, valor, definirValor, mostrar, nomeDoUsuario, marcarInvalido, limparInvalido,
} from './state.js';
import {
  toast, abrirModal, fecharModal, aplicarPerfil, navegar,
  renderNotificacoes, carregando, atualizarDataAtual,
} from './ui.js';

/* ---------- Entrada e saída ---------- */
async function entrarNaAplicacao(usuario) {
  state.usuario = usuario;
  aplicarPerfil(usuario);
  atualizarDataAtual();

  mostrar('login-screen', false);
  mostrar('app-screen', true);

  await carregarDados(state);
  renderNotificacoes();
  navegar('dashboard');
  iniciarAtualizacoes();   // aviso de nova versao, sem travar a tela
}

export async function fazerLogin(evento) {
  evento.preventDefault();

  const email = valor('login-email');
  const senha = el('login-senha').value;
  const botao = el('btn-login');

  if (!email) {
    toast('Campos obrigatórios', 'Informe e-mail e senha para continuar.', 'warning');
    marcarInvalido('login-email', 'Informe seu e-mail.');
    return;
  }
  if (!senha) {
    toast('Campos obrigatórios', 'Informe e-mail e senha para continuar.', 'warning');
    marcarInvalido('login-senha', 'Informe sua senha.');
    return;
  }
  limparInvalido('login-email');
  limparInvalido('login-senha');

  carregando(botao, true, 'Autenticando...');
  try {
    const resposta = await api.login(email, senha);
    if (resposta.token) sessao.definir(resposta.token);

    await entrarNaAplicacao(resposta.usuario);

    const primeiroNome = nomeDoUsuario(resposta.usuario).split(' ')[0];
    toast('Bem-vindo de volta', `Bom te ver por aqui, ${primeiroNome}.`);
    el('login-senha').value = '';
  } catch (erro) {
    toast('Não foi possível entrar', erro.message, 'error');
    marcarInvalido('login-senha', erro.message);
    el('login-senha').select();
  } finally {
    carregando(botao, false);
  }
}

export async function fazerCadastro(evento) {
  evento.preventDefault();

  const nome = valor('cad-nome');
  const email = valor('cad-email');
  const senha = el('cad-senha').value;
  const setor = valor('cad-setor');
  const botao = evento.submitter || evento.target.querySelector('button[type="submit"]');

  const faltando = [!nome && ['cad-nome', 'Informe seu nome.'],
    !email && ['cad-email', 'Informe seu e-mail.'],
    !senha && ['cad-senha', 'Crie uma senha.'],
    !setor && ['cad-setor', 'Selecione o setor da sua conta.']].find(Boolean);
  if (faltando) {
    toast('Campos obrigatórios', 'Preencha nome, e-mail, senha e setor.', 'warning');
    marcarInvalido(faltando[0], faltando[1]);
    return;
  }
  ['cad-nome', 'cad-email', 'cad-senha', 'cad-setor'].forEach(limparInvalido);

  carregando(botao, true, 'Criando...');
  try {
    await api.cadastrar(nome, email, senha, setor);
    fecharModal('modal-cadastro');
    evento.target.reset();
    toast('Conta criada', 'Já pode entrar com esse e-mail e senha.');
    definirValor('login-email', email);
  } catch (erro) {
    toast('Não foi possível cadastrar', erro.message, 'error');
    marcarInvalido('cad-email', erro.message);
  } finally {
    carregando(botao, false);
  }
}

export async function sair() {
  const token = sessao.obter();
  if (token) {
    try { await api.encerrarSessao(token); } catch (erro) { /* segue o logout local */ }
  }

  sessao.limpar();
  state.usuario = null;
  state.dados = { pastas: [], usuarios: [], status: [], cidades: [], anotacoes: {}, setores: [], etiquetas_salvas: [] };

  mostrar('app-screen', false);
  mostrar('login-screen', true);
  el('form-login')?.reset();
  el('login-email')?.focus();
}

/** Restaura a sessão salva no navegador ou no arquivo local do PC. */
export async function restaurarSessao() {
  let token = sessao.obter();

  if (!token) {
    try {
      const arquivo = await api.sessaoSalva();
      if (arquivo.token) {
        token = arquivo.token;
        sessao.definir(token);
      }
    } catch (erro) { /* sem sessão salva */ }
  }

  if (!token) return false;

  try {
    const resposta = await api.validarSessao(token);
    if (resposta.usuario) {
      await entrarNaAplicacao(resposta.usuario);
      return true;
    }
  } catch (erro) {
    sessao.limpar();
  }
  return false;
}

/* ---------- Recuperação de senha ---------- */
const SUBTITULOS = {
  1: 'Informe seu e-mail cadastrado',
  2: 'Digite o código recebido por e-mail',
  3: 'Crie sua nova senha',
  4: 'Tudo certo',
};

export function mostrarEtapaRecuperacao(etapa) {
  [1, 2, 3, 4].forEach((n) => mostrar(`rec-etapa-${n}`, n === etapa));
  el('subtitulo-recuperacao').textContent = SUBTITULOS[etapa] || '';
}

export function abrirRecuperacao() {
  ['rec-email', 'rec-codigo', 'rec-senha', 'rec-senha-confirma'].forEach((id) => definirValor(id, ''));
  const emailLogin = valor('login-email');
  if (emailLogin) definirValor('rec-email', emailLogin);
  mostrarEtapaRecuperacao(1);
  abrirModal('modal-recuperacao');
}

export async function solicitarCodigo(botao) {
  const email = valor('rec-email');
  if (!email) {
    toast('Informe o e-mail', 'Precisamos do e-mail cadastrado para enviar o código.', 'warning');
    marcarInvalido('rec-email', 'Informe o e-mail cadastrado.');
    return;
  }
  limparInvalido('rec-email');

  carregando(botao, true, 'Enviando...');
  try {
    const resposta = await api.recuperacaoSolicitar(email);
    toast('Código enviado', resposta.message);
    mostrarEtapaRecuperacao(2);
    setTimeout(() => el('rec-codigo')?.focus(), 120);
  } catch (erro) {
    toast('Não foi possível enviar', erro.message, 'error');
  } finally {
    carregando(botao, false);
  }
}

export async function validarCodigo(botao) {
  const email = valor('rec-email');
  const codigo = valor('rec-codigo');

  if (codigo.length !== 6) {
    toast('Código incompleto', 'Digite os 6 dígitos enviados por e-mail.', 'warning');
    marcarInvalido('rec-codigo', 'Digite os 6 dígitos recebidos.');
    return;
  }
  limparInvalido('rec-codigo');

  carregando(botao, true, 'Validando...');
  try {
    await api.recuperacaoValidar(email, codigo);
    mostrarEtapaRecuperacao(3);
    setTimeout(() => el('rec-senha')?.focus(), 120);
  } catch (erro) {
    toast('Código inválido', erro.message, 'error');
    marcarInvalido('rec-codigo', erro.message);
  } finally {
    carregando(botao, false);
  }
}

export async function redefinirSenha(botao) {
  const nova = el('rec-senha').value;
  const confirmacao = el('rec-senha-confirma').value;

  if (!nova || !confirmacao) {
    toast('Campos obrigatórios', 'Preencha e confirme a nova senha.', 'warning');
    marcarInvalido(nova ? 'rec-senha-confirma' : 'rec-senha', 'Este campo é obrigatório.');
    return;
  }
  if (nova.length < 4) {
    toast('Senha curta', 'Use pelo menos 4 caracteres.', 'warning');
    marcarInvalido('rec-senha', 'Use pelo menos 4 caracteres.');
    return;
  }
  if (nova !== confirmacao) {
    toast('Senhas diferentes', 'A confirmação precisa ser igual à nova senha.', 'warning');
    marcarInvalido('rec-senha-confirma', 'Não é igual à nova senha.');
    return;
  }
  limparInvalido('rec-senha');
  limparInvalido('rec-senha-confirma');

  carregando(botao, true, 'Salvando...');
  try {
    await api.recuperacaoRedefinir({
      email: valor('rec-email'),
      codigo: valor('rec-codigo'),
      nova_senha: nova,
      confirmar_senha: confirmacao,
    });
    mostrarEtapaRecuperacao(4);
  } catch (erro) {
    toast('Não foi possível redefinir', erro.message, 'error');
  } finally {
    carregando(botao, false);
  }
}

/* ---------- Perfil ---------- */
export function abrirEdicaoDeNome() {
  definirValor('input-nome-exibicao', nomeDoUsuario(state.usuario));
  abrirModal('modal-nome');
}

export async function salvarNome() {
  const nome = valor('input-nome-exibicao');
  if (!nome) {
    toast('Nome obrigatório', 'O nome não pode ficar vazio.', 'warning');
    marcarInvalido('input-nome-exibicao', 'O nome não pode ficar vazio.');
    return;
  }
  limparInvalido('input-nome-exibicao');

  try {
    const resposta = await api.atualizarPerfil({ usuario_id: state.usuario.id, nome_exibicao: nome });
    state.usuario = resposta.usuario;
    aplicarPerfil(state.usuario);
    fecharModal('modal-nome');
    toast('Nome atualizado', 'A equipe já vê você com esse nome.');
  } catch (erro) { /* toast já exibido */ }
}

/**
 * Redimensiona a imagem no cliente antes de enviar: o backend guarda um JPEG
 * pequeno, e a foto aparece na interface antes mesmo da resposta do servidor.
 */
export function trocarAvatar(evento) {
  const arquivo = evento.target.files?.[0];
  if (!arquivo || !state.usuario) return;

  if (!arquivo.type.startsWith('image/')) {
    toast('Formato inválido', 'Escolha um arquivo de imagem.', 'warning');
    evento.target.value = '';
    return;
  }

  const leitor = new FileReader();
  leitor.onload = (leitura) => {
    const imagem = new Image();
    imagem.onload = async () => {
      const LIMITE = 256;
      let { width: largura, height: altura } = imagem;

      if (largura > altura && largura > LIMITE) {
        altura *= LIMITE / largura;
        largura = LIMITE;
      } else if (altura > LIMITE) {
        largura *= LIMITE / altura;
        altura = LIMITE;
      }

      const canvas = document.createElement('canvas');
      canvas.width = largura;
      canvas.height = altura;
      canvas.getContext('2d').drawImage(imagem, 0, 0, largura, altura);
      const reduzida = canvas.toDataURL('image/jpeg', 0.86);

      aplicarPerfil({ ...state.usuario, foto_perfil: reduzida });

      try {
        const resposta = await api.atualizarPerfil({ usuario_id: state.usuario.id, foto_perfil: reduzida });
        state.usuario = resposta.usuario;
        aplicarPerfil(state.usuario);
        toast('Foto atualizada', 'Sua nova foto de perfil foi salva.');
      } catch (erro) {
        aplicarPerfil(state.usuario);
      }
    };
    imagem.src = leitura.target.result;
  };

  leitor.readAsDataURL(arquivo);
  evento.target.value = '';
}
