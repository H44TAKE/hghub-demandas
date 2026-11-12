/* Painel de TV · Manutenção REDE HG
 * Busca /api/painel (resultado já pronto no servidor), desenha e mantém a lista rolando.
 * Nunca fica em branco: se a busca falhar, continua com o último dado e avisa. */

const INTERVALO_BUSCA_MS = 30 * 1000;       // lê o servidor local; ele mesmo atualiza o Drive a cada 5 min
const VELOCIDADE_ROLAGEM = 38;              // pixels (de 1080p) por segundo
const PAUSA_NO_TOPO_MS = 5000;              // para um instante antes de recomeçar o giro

const el = (id) => document.getElementById(id);
const escapar = (texto) => String(texto ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let assinaturaLista = '';
let ultimoDado = null;
let falhasSeguidas = 0;

/* ---------- relógio ---------- */
function tocarRelogio() {
  const agora = new Date();
  el('hora').textContent = agora.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const data = agora.toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long' });
  el('data').textContent = data.charAt(0).toUpperCase() + data.slice(1);
}

/* ---------- saúde ---------- */
const faixa = (pct) => (pct >= 80 ? 'boa' : pct >= 50 ? 'media' : 'ruim');   // mesma regra de cores do app
const pctTexto = (pct) => (pct == null ? '—' : `${pct}%`);

/** Um cartão por frente (Alinhamento, Chassi, 5ª roda...), cada um com a PRÓPRIA saúde: % dos
 * veículos que têm aquela frente e estão em dia. Não há saúde geral: uma frente muito vencida
 * (ex.: Alinhamento) escondia as demais num número único. */
function desenharSaude(d) {
  el('saude-total').textContent = `${d.saude.total} veículos`;
  el('frentes').innerHTML = d.frentes.map((f) => `
    <li class="frente frente--${f.pct == null ? 'boa' : faixa(f.pct)}">
      <span class="frente__nome">${escapar(f.titulo)}</span>
      <span class="frente__pct">${pctTexto(f.pct)}</span>
      <span class="frente__contagem">
        <b class="n n--urgente">${f.parar_urgente}</b> vencidos
        <b class="n n--programar">${f.programar_parada}</b> a vencer
        <b class="n n--emdia">${f.em_dia}</b> em dia
      </span>
      <span class="frente__barra"><i style="width:${f.pct ?? 0}%"></i></span>
    </li>`).join('');
}

/* ---------- lista que rola ---------- */
const NOME_TIPO = { cavalo: 'Cavalo', carreta: 'Carreta' };
const CLASSE_STATUS = { 'PARAR URGENTE': 'urgente', 'PROGRAMAR PARADA': 'programar', 'EM DIA': 'emdia' };

/** Uma linha por veículo, na cor da regra (vermelho / laranja / verde) e com TODAS as frentes dele:
 * vencida (vermelho cheio), a vencer (contorno âmbar) ou em dia (contorno verde). */
function htmlLinha(v) {
  const chips = v.frentes.map((f) => `<span class="chip chip--${CLASSE_STATUS[f.status]}">${escapar(f.titulo)}</span>`).join('');
  return `<div class="linha linha--${v.cor}">
    <span class="linha__id">
      <span class="linha__placa">${escapar(v.placa)}</span>
      <span class="linha__tipo">${NOME_TIPO[v.tipo] || ''} · ${v.pendentes} pendente${v.pendentes === 1 ? '' : 's'} de ${v.total_frentes}</span>
    </span>
    <span class="chips">${chips}</span>
  </div>`;
}

let animacao = null;

function pararRolagem() {
  if (animacao) cancelAnimationFrame(animacao);
  animacao = null;
}

/** Rola em laço contínuo: se a lista não cabe na janela, duplica o conteúdo e volta ao
 * início sem salto. Se cabe, fica parada. Velocidade em px por segundo, independente do FPS. */
function iniciarRolagem() {
  pararRolagem();
  const janela = el('lista-janela');
  const trilho = el('lista-trilho');
  trilho.style.transform = 'translateY(0)';
  const altura = trilho.scrollHeight;
  if (altura <= janela.clientHeight) return;

  trilho.insertAdjacentHTML('beforeend', trilho.innerHTML);
  const escala = window.innerHeight / 1080;
  let posicao = 0;
  let anterior = null;
  let pausadoAte = performance.now() + PAUSA_NO_TOPO_MS;

  const passo = (agora) => {
    if (anterior == null) anterior = agora;
    const dt = Math.min(0.1, (agora - anterior) / 1000);
    anterior = agora;
    if (agora >= pausadoAte) {
      posicao += VELOCIDADE_ROLAGEM * escala * dt;
      if (posicao >= altura) { posicao -= altura; pausadoAte = agora + PAUSA_NO_TOPO_MS; }
      trilho.style.transform = `translateY(${-posicao}px)`;
    }
    animacao = requestAnimationFrame(passo);
  };
  animacao = requestAnimationFrame(passo);
}

function desenharLista(d) {
  const total = d.lista.length;
  el('atencao-total').textContent = total ? `${total} veículo${total > 1 ? 's' : ''}` : '';
  for (const cor of ['vermelho', 'laranja', 'verde']) el(`n-${cor}`).textContent = d.resumo_lista[cor];

  const assinatura = JSON.stringify(d.lista);
  if (assinatura === assinaturaLista) return;   // nada mudou: não reinicia a rolagem
  assinaturaLista = assinatura;

  const trilho = el('lista-trilho');
  pararRolagem();
  if (!total) {
    trilho.innerHTML = `<div class="vazio"><p class="vazio__titulo">Nenhum veículo com status</p>
      <p class="vazio__texto">A planilha não trouxe status de manutenção para nenhum veículo.</p></div>`;
    trilho.style.transform = 'none';
    trilho.style.padding = '0';
    trilho.style.height = '100%';
    return;
  }
  trilho.style.height = '';
  trilho.style.padding = '';
  trilho.innerHTML = d.lista.map(htmlLinha).join('');
  iniciarRolagem();
}

/* ---------- estados ---------- */
function mostrarEstado(titulo, texto) {
  el('estado-titulo').textContent = titulo;
  el('estado-texto').textContent = texto;
  el('tela-estado').classList.remove('hidden');
}

function horaDe(iso) {
  return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

function aplicar(d, falhouBusca) {
  el('aviso-demo').classList.toggle('hidden', d.modo !== 'demo');

  if (d.nao_configurado) {
    mostrarEstado('Painel ainda não configurado', d.mensagem || 'Falta configurar a planilha e a credencial do Google.');
    return;
  }
  if (d.carregando || !d.saude) {
    mostrarEstado('Carregando dados da frota…', 'A primeira leitura da planilha leva alguns segundos.');
    return;
  }

  el('tela-estado').classList.add('hidden');
  desenharSaude(d);
  desenharLista(d);

  const velho = falhouBusca || d.desatualizado;
  const aviso = el('aviso-velho');
  aviso.classList.toggle('hidden', !velho);
  if (velho) aviso.textContent = `Sem conexão com a planilha · mostrando dados das ${horaDe(d.atualizado_em)}`;
}

async function buscar() {
  try {
    const resposta = await fetch('/api/painel', { cache: 'no-store' });
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    const d = await resposta.json();
    falhasSeguidas = 0;
    if (d.saude) ultimoDado = d;
    aplicar(d, false);
  } catch (erro) {
    falhasSeguidas += 1;
    if (ultimoDado) aplicar(ultimoDado, true);
    else mostrarEstado('Iniciando o painel…', 'Aguardando o servidor local responder.');
  }
}

tocarRelogio();
setInterval(tocarRelogio, 1000);
buscar();
setInterval(buscar, INTERVALO_BUSCA_MS);

// Rede de proteção para uma TV que fica dias ligada: se o servidor local ficar fora do ar
// por muito tempo, recarrega a página inteira para recomeçar do zero.
setInterval(() => { if (falhasSeguidas >= 10) location.reload(); }, INTERVALO_BUSCA_MS);
