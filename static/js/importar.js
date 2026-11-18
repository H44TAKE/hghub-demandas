/**
 * Importar planilha de exames (aba do mes -> demandas). Duas etapas: previa (nada e gravado) e confirmacao.
 * O arquivo so e lido; demandas que ja existem na pasta sao puladas.
 */

import { api } from './api.js';
import { state, el, escapar, dataBR, nomeDoUsuario } from './state.js';
import { abrirModal, fecharModal, toast, carregando } from './ui.js';
import { sincronizar } from './structure.js';

let arquivoBase64 = null;
let nomeArquivo = '';
let previa = null;
let escolhas = { aba: null, pasta_id: null, projetos: {}, responsavel_id: 'auto' };

function opcoes(lista, selecionado) {
  return lista.map(([valor, texto]) => `<option value="${escapar(valor)}" ${valor === selecionado ? 'selected' : ''}>${escapar(texto)}</option>`).join('');
}

function renderPrevia() {
  const corpo = el('importar-corpo');
  if (!corpo) return;
  if (!previa) {
    corpo.innerHTML = '<p class="muted-note">Escolha o arquivo .xlsx para ver a prévia. Nada é gravado até você confirmar.</p>';
    el('importar-confirmar').disabled = true;
    return;
  }
  const pastas = state.dados.pastas.map((p) => [p.id, p.nome]);
  const pasta = state.dados.pastas.find((p) => p.id === escolhas.pasta_id);
  const regioes = (previa.regioes || []).map((r) => {
    const projetos = (pasta?.projetos || []).map((p) => [p.id, p.nome]);
    const atual = escolhas.projetos[r.regiao] ?? r.projeto_id ?? '';
    return `<label class="field"><span class="field__label">${escapar(r.regiao || 'Sem região')} → projeto</span>
      <select class="select" data-importar-projeto="${escapar(r.regiao)}">
        ${opcoes([['', `Criar “${r.projeto_nome}”`], ...projetos], atual)}
      </select></label>`;
  }).join('');
  const usuarios = state.dados.usuarios.map((u) => [u.id, nomeDoUsuario(u)]);
  const { lidas, novas, existentes } = previa.resumo;

  const linhas = previa.linhas.slice(0, 80).map((l) => `
    <tr class="${l.estado === 'existente' ? 'is-oculto' : ''}">
      <td>${escapar(l.nome)}</td><td>${escapar(l.exame)}</td><td>${escapar(l.regiao)}</td>
      <td>${dataBR(l.vencimento)}</td><td>${escapar(l.situacao)}</td>
      <td>${l.estado === 'nova' ? '<span class="badge badge--green">Nova</span>' : `<span class="badge badge--neutral" title="${escapar(l.motivo || '')}">Já existe</span>`}</td>
    </tr>`).join('');

  corpo.innerHTML = `
    <div class="form-grid">
      <label class="field"><span class="field__label">Aba (mês)</span>
        <select class="select" id="importar-aba">${opcoes(previa.abas.map((a) => [a, a]), previa.aba)}</select></label>
      <label class="field"><span class="field__label">Pasta de destino</span>
        <select class="select" id="importar-pasta">${opcoes([['', 'Escolha a pasta…'], ...pastas], escolhas.pasta_id || '')}</select></label>
      ${regioes}
      <label class="field"><span class="field__label">Responsável pelas novas demandas</span>
        <select class="select" id="importar-responsavel">
          ${opcoes([['auto', 'Automático (quem já cuida do projeto)'], ['', 'Ninguém (cada um assume)'], ...usuarios], escolhas.responsavel_id)}
        </select></label>
    </div>
    <p class="importar-resumo"><b>${lidas}</b> linhas lidas · <b>${novas}</b> novas · <b>${existentes}</b> já existiam (não serão duplicadas)</p>
    <div class="data-table-wrap importar-tabela">
      <table class="data-table"><thead><tr><th>Nome</th><th>Exame</th><th>UF</th><th>Vencimento</th><th>Situação</th><th></th></tr></thead>
      <tbody>${linhas}</tbody></table>
    </div>
    ${previa.linhas.length > 80 ? `<p class="muted-note">Mostrando 80 de ${previa.linhas.length} linhas.</p>` : ''}`;

  const botao = el('importar-confirmar');
  botao.disabled = !escolhas.pasta_id || novas === 0;
  botao.querySelector('span').textContent = novas ? `Importar ${novas} demanda${novas > 1 ? 's' : ''}` : 'Nada novo para importar';
}

async function analisar() {
  if (!arquivoBase64) return;
  try {
    previa = await api.importarPlanilha({
      arquivo: arquivoBase64, aba: escolhas.aba, pasta_id: escolhas.pasta_id || undefined, confirmar: false,
    });
    escolhas.aba = previa.aba;
    escolhas.pasta_id = escolhas.pasta_id || previa.pasta_id || '';
    renderPrevia();
  } catch (erro) {
    previa = null;
    renderPrevia();
    toast('Não foi possível ler a planilha', erro.message, 'error');
  }
}

export function abrirImportarDemandas() {
  arquivoBase64 = null; nomeArquivo = ''; previa = null;
  escolhas = { aba: null, pasta_id: null, projetos: {}, responsavel_id: 'auto' };
  const campo = el('importar-arquivo');
  if (campo) campo.value = '';
  el('importar-nome-arquivo').textContent = 'Nenhum arquivo escolhido';
  renderPrevia();
  abrirModal('modal-importar-demandas');
}

function lerArquivo(arquivo) {
  if (!arquivo) return;
  nomeArquivo = arquivo.name;
  el('importar-nome-arquivo').textContent = arquivo.name;
  const leitor = new FileReader();
  leitor.onload = () => {
    arquivoBase64 = String(leitor.result).split(',')[1] || '';
    escolhas.aba = null;
    analisar();
  };
  leitor.readAsDataURL(arquivo);
}

export async function confirmarImportacao() {
  const botao = el('importar-confirmar');
  carregando(botao, true, 'Importando...');
  try {
    const r = await api.importarPlanilha({
      arquivo: arquivoBase64, aba: escolhas.aba, pasta_id: escolhas.pasta_id, projetos: escolhas.projetos,
      responsavel_id: escolhas.responsavel_id, confirmar: true,
    });
    fecharModal('modal-importar-demandas');
    const extra = r.projetos_criados?.length ? ` Projeto novo: ${r.projetos_criados.join(', ')}.` : '';
    toast('Importação concluída', `${r.criadas} nova${r.criadas === 1 ? '' : 's'}, ${r.existentes} já existia${r.existentes === 1 ? '' : 'm'}.${extra}`);
    await sincronizar();
  } catch (erro) {
    toast('Não foi possível importar', erro.message, 'error');
  } finally {
    carregando(botao, false);
  }
}

document.addEventListener('change', (evento) => {
  const alvo = evento.target;
  if (!alvo.closest?.('#modal-importar-demandas')) return;
  if (alvo.id === 'importar-arquivo') lerArquivo(alvo.files?.[0]);
  else if (alvo.id === 'importar-aba') { escolhas.aba = alvo.value; escolhas.projetos = {}; analisar(); }
  else if (alvo.id === 'importar-pasta') { escolhas.pasta_id = alvo.value; escolhas.projetos = {}; analisar(); }
  else if (alvo.id === 'importar-responsavel') escolhas.responsavel_id = alvo.value;
  else if (alvo.dataset.importarProjeto !== undefined) escolhas.projetos[alvo.dataset.importarProjeto] = alvo.value;
});
