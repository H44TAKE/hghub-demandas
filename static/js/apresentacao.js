/**
 * HGhub · Apresentação - linha do tempo cinematográfica, zero interface do app.
 *
 * Tudo roda sobre um relógio próprio movido a requestAnimationFrame: com a
 * aba escondida não há quadro, o relógio para, e a apresentação continua
 * exatamente de onde estava quando a aba volta (nada "acontece" sem ninguém
 * olhando). Cada cena é uma função async; "Pular" e "Assistir de novo"
 * cancelam a sessão atual e todas as esperas pendentes dela.
 */
(() => {
  'use strict';

  const REDUZIDO = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const $ = (seletor, raiz = document) => raiz.querySelector(seletor);
  const $$ = (seletor, raiz = document) => Array.from(raiz.querySelectorAll(seletor));
  const aleatorio = (min, max) => min + Math.random() * (max - min);

  /* ---------- Relógio (pausa com a aba escondida) ---------- */
  const relogio = { t: 0, ultimo: null, ouvintes: new Set() };

  function tique(agora) {
    if (relogio.ultimo !== null) relogio.t += Math.min(agora - relogio.ultimo, 64);
    relogio.ultimo = agora;
    for (const fn of [...relogio.ouvintes]) fn(relogio.t);
    requestAnimationFrame(tique);
  }
  requestAnimationFrame(tique);
  document.addEventListener('visibilitychange', () => { relogio.ultimo = null; });

  function quadro(fn) {
    relogio.ouvintes.add(fn);
    return () => relogio.ouvintes.delete(fn);
  }

  /* ---------- Sessão cancelável ---------- */
  class Cancelado extends Error {}
  let sessao = null;

  function novaSessao() {
    const s = { cancelada: false, limpezas: [] };
    s.cancelar = () => {
      s.cancelada = true;
      s.limpezas.splice(0).forEach((fn) => fn());
    };
    return s;
  }

  function esperar(ms) {
    const s = sessao;
    return new Promise((resolver, rejeitar) => {
      const alvo = relogio.t + ms;
      const desligar = quadro((t) => {
        if (s.cancelada) { desligar(); rejeitar(new Cancelado()); return; }
        if (t >= alvo) { desligar(); resolver(); }
      });
    });
  }

  /** Interpola 0→1 no relógio da apresentação (pausa junto com ele). */
  function animar(duracao, suavizar, passo) {
    const s = sessao;
    return new Promise((resolver, rejeitar) => {
      const inicio = relogio.t;
      const desligar = quadro((t) => {
        if (s.cancelada) { desligar(); rejeitar(new Cancelado()); return; }
        const p = Math.min(1, (t - inicio) / duracao);
        passo(suavizar(p), p);
        if (p >= 1) { desligar(); resolver(); }
      });
    });
  }

  const suave = {
    saidaCubica: (p) => 1 - Math.pow(1 - p, 3),
    meioCubico: (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
    meioSeno: (p) => -(Math.cos(Math.PI * p) - 1) / 2,
  };

  /* ==========================================================
   * Trilha sonora - sintetizada ao vivo (Web Audio), sem arquivo nenhum.
   * Um pad por cena, na mesma tonalidade (Lá menor → Dó maior), e os efeitos
   * passam pelo mesmo reverb e pelo mesmo compressor do pad: soam como parte
   * da música, não como sons colados por cima.
   * ========================================================== */
  const audio = (() => {
    const ACORDES = {
      1: [110.0, 164.81, 220.0, 261.63, 329.63],          // Lá menor
      2: [87.31, 130.81, 174.61, 220.0, 329.63],          // Fá maj7
      3: [73.42, 146.83, 220.0, 261.63, 349.23],          // Ré menor 7
      4: [98.0, 146.83, 196.0, 246.94, 293.66],           // Sol (tensão, antecipa)
      5: [65.41, 130.81, 196.0, 246.94, 293.66, 329.63],  // Dó maj9 (resolução)
    };
    const NOTAS_DADO = [698.46, 880.0, 1046.5, 1318.51];

    let ctx = null;
    let mestre = null;
    let seco = null;
    let reverb = null;
    let filtroPad = null;
    let ruidoBuffer = null;
    let pad = null;
    let ligado = false;
    let cenaAtual = 0;
    let ultimoDado = 0;

    function impulso(duracao, queda) {
      const tamanho = Math.floor(ctx.sampleRate * duracao);
      const buffer = ctx.createBuffer(2, tamanho, ctx.sampleRate);
      for (let canal = 0; canal < 2; canal++) {
        const dados = buffer.getChannelData(canal);
        for (let i = 0; i < tamanho; i++) {
          dados[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / tamanho, queda);
        }
      }
      return buffer;
    }

    function criar() {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      mestre = ctx.createGain();
      mestre.gain.value = 0;
      mestre.connect(ctx.destination);

      const compressor = ctx.createDynamicsCompressor();
      compressor.threshold.value = -20;
      compressor.ratio.value = 3;
      compressor.connect(mestre);

      reverb = ctx.createConvolver();
      reverb.buffer = impulso(3, 2.6);
      const molhado = ctx.createGain();
      molhado.gain.value = 0.5;
      reverb.connect(molhado);
      molhado.connect(compressor);

      seco = ctx.createGain();
      seco.gain.value = 0.75;
      seco.connect(compressor);

      filtroPad = ctx.createBiquadFilter();
      filtroPad.type = 'lowpass';
      filtroPad.frequency.value = 1300;
      filtroPad.Q.value = 0.4;
      filtroPad.connect(seco);
      filtroPad.connect(reverb);

      ruidoBuffer = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
      const ruido = ruidoBuffer.getChannelData(0);
      for (let i = 0; i < ruido.length; i++) ruido[i] = Math.random() * 2 - 1;
    }

    function saida(no) {
      no.connect(seco);
      no.connect(reverb);
    }

    function tocarPad(freqs) {
      const t = ctx.currentTime;
      const grupo = ctx.createGain();
      grupo.gain.setValueAtTime(0, t);
      grupo.gain.linearRampToValueAtTime(1, t + 1.8);
      grupo.connect(filtroPad);

      const osciladores = [];
      freqs.forEach((f, i) => {
        [['sine', 0, 0.028], ['triangle', 5, 0.016], ['sine', -6, 0.024]].forEach(([tipo, cents, ganho]) => {
          const o = ctx.createOscillator();
          o.type = tipo;
          o.frequency.value = f;
          o.detune.value = cents;
          const v = ctx.createGain();
          v.gain.value = ganho * (i === 0 ? 1.35 : 1);
          o.connect(v);
          v.connect(grupo);
          o.start(t);
          osciladores.push(o);
        });
      });

      if (pad) {
        const antigo = pad;
        antigo.grupo.gain.cancelScheduledValues(t);
        antigo.grupo.gain.setValueAtTime(antigo.grupo.gain.value, t);
        antigo.grupo.gain.linearRampToValueAtTime(0, t + 2.2);
        antigo.osciladores.forEach((o) => o.stop(t + 2.4));
      }
      pad = { grupo, osciladores };
    }

    function filtro(freq, duracao) {
      const t = ctx.currentTime;
      filtroPad.frequency.cancelScheduledValues(t);
      filtroPad.frequency.setValueAtTime(filtroPad.frequency.value, t);
      filtroPad.frequency.exponentialRampToValueAtTime(freq, t + duracao);
    }

    function sopro(duracao, de, para, ganho) {
      const t = ctx.currentTime;
      const fonte = ctx.createBufferSource();
      fonte.buffer = ruidoBuffer;
      const banda = ctx.createBiquadFilter();
      banda.type = 'bandpass';
      banda.Q.value = 0.9;
      banda.frequency.setValueAtTime(de, t);
      banda.frequency.exponentialRampToValueAtTime(para, t + duracao);
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.linearRampToValueAtTime(ganho, t + duracao * 0.55);
      g.gain.linearRampToValueAtTime(0.0001, t + duracao);
      fonte.connect(banda);
      banda.connect(g);
      saida(g);
      fonte.start(t);
      fonte.stop(t + duracao + 0.05);
    }

    function sino(freqs, ganho, queda, espaco = 0.07) {
      const t0 = ctx.currentTime;
      freqs.forEach((f, i) => {
        const t = t0 + i * espaco;
        const o = ctx.createOscillator();
        o.type = 'sine';
        o.frequency.value = f;
        const g = ctx.createGain();
        g.gain.setValueAtTime(0.0001, t);
        g.gain.linearRampToValueAtTime(ganho, t + 0.012);
        g.gain.exponentialRampToValueAtTime(0.0001, t + queda);
        o.connect(g);
        saida(g);
        o.start(t);
        o.stop(t + queda + 0.1);
      });
    }

    function batida(atraso, ganho) {
      const t = ctx.currentTime + atraso;
      const o = ctx.createOscillator();
      o.type = 'sine';
      o.frequency.setValueAtTime(72, t);
      o.frequency.exponentialRampToValueAtTime(42, t + 0.28);
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.linearRampToValueAtTime(ganho, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.42);
      o.connect(g);
      g.connect(seco);
      o.start(t);
      o.stop(t + 0.5);
    }

    function entrarNaCena(n, comTransicao) {
      if (!ACORDES[n]) return;
      if (comTransicao && n > 1) sopro(1, 280, 2400, 0.16);
      filtro(n === 4 ? 850 : 1300, 1.2);
      tocarPad(ACORDES[n]);
    }

    return {
      get ligado() { return ligado; },

      alternar() {
        if (!ctx) criar();
        ligado = !ligado;
        const t = ctx.currentTime;
        mestre.gain.cancelScheduledValues(t);
        mestre.gain.setValueAtTime(mestre.gain.value, t);
        if (ligado) {
          ctx.resume();
          mestre.gain.linearRampToValueAtTime(0.55, t + 0.8);
          entrarNaCena(cenaAtual || 1, false);
        } else {
          mestre.gain.linearRampToValueAtTime(0, t + 0.4);
        }
        return ligado;
      },

      cena(n) {
        cenaAtual = n;
        if (ligado) entrarNaCena(n, true);
      },

      batimento() {
        if (!ligado) return;
        batida(0, 0.5);
        batida(0.27, 0.36);
      },

      dado(t) {
        if (!ligado || t - ultimoDado < 420) return;
        ultimoDado = t;
        sino([NOTAS_DADO[Math.floor(Math.random() * NOTAS_DADO.length)]], 0.022, 0.9);
      },

      desintegrar() {
        if (ligado) sopro(1.2, 3400, 240, 0.14);
      },

      anel() {
        if (ligado) filtro(2600, 1.9);
      },

      anelCompleto() {
        if (ligado) sino([880.0, 1318.51], 0.05, 3);
      },

      onda() {
        if (!ligado) return;
        filtro(3600, 2.2);
        sopro(1.9, 220, 3800, 0.12);
      },

      assinatura() {
        if (!ligado) return;
        sino([1046.5, 1567.98, 1318.51, 2093.0], 0.055, 4.5, 0.09);
        filtro(700, 7);
      },
    };
  })();

  /* ---------- Cenas: ativação e reset ---------- */
  const cenas = $$('.cena');

  function mostrarCena(n) {
    document.body.dataset.cena = String(n);
    cenas.forEach((cena) => cena.classList.toggle('is-ativa', Number(cena.dataset.cena) === n));
    audio.cena(n);
  }

  /** Volta tudo ao estado zero sem animar a volta (replay e "pular"). */
  function resetar() {
    document.body.classList.add('sem-transicao');
    $$('.palco [class]').forEach((el) => {
      [...el.classList].filter((c) => c.startsWith('is-')).forEach((c) => el.classList.remove(c));
    });
    ['#pacotes', '#constelacao', '#constelacao-linhas', '#velocimetro-segmentos'].forEach((s) => { $(s).innerHTML = ''; });
    $('#velocimetro-ponteiro').style.transform = '';
    $('#onda').style.transform = '';
    $('#km-valor').textContent = '0';
    $('#dias-valor').textContent = '0';
    void document.body.offsetHeight;
    document.body.classList.remove('sem-transicao');
  }

  /* ==========================================================
   * CENA 1 · O Despertar
   * ========================================================== */
  async function frase(el, segurar) {
    el.classList.add('is-visivel');
    await esperar(750 + segurar);
    el.classList.add('is-fora');
    await esperar(460);
  }

  async function cena1() {
    mostrarCena(1);
    const marca = $('.marca--abertura');
    await esperar(750);
    marca.classList.add('is-visivel');
    await esperar(950);
    marca.classList.add('is-nome');
    await esperar(1700);
    marca.classList.add('is-fora');
    await esperar(650);

    const [um, dois, tres] = $$('.frase');
    await frase(um, 1600);
    await frase(dois, 1600);
    $('.batimento').classList.add('is-pulsando');
    audio.batimento();
    await frase(tres, 2300);
  }

  /* ==========================================================
   * CENA 2 · A Sincronização
   * ========================================================== */
  function valorDeCelula() {
    const r = Math.random();
    if (r < 0.42) return Math.floor(aleatorio(96000, 1260000)).toLocaleString('pt-BR');
    if (r < 0.72) {
      const dia = String(Math.floor(aleatorio(1, 29))).padStart(2, '0');
      const mes = String(Math.floor(aleatorio(1, 13))).padStart(2, '0');
      return `${dia}/${mes}/2026`;
    }
    if (r < 0.88) return `L${Math.floor(aleatorio(8, 190))}:C${Math.floor(aleatorio(2, 60))}`;
    return `0x${Math.floor(Math.random() * 0xffff).toString(16).toUpperCase().padStart(4, '0')}`;
  }

  function lancarPacote(alvo) {
    const largura = alvo.clientWidth;
    const ida = Math.random() < 0.5;
    const el = document.createElement('span');
    el.className = `pacote ${ida ? 'pacote--ida' : 'pacote--volta'}`;
    el.textContent = valorDeCelula();
    alvo.appendChild(el);

    const w = el.offsetWidth;
    const faixa = aleatorio(-26, 26);

    if (REDUZIDO) {
      const x = aleatorio(0, Math.max(0, largura - w));
      el.style.transform = `translate(${x}px, calc(-50% + ${faixa}px))`;
      el.animate([{ opacity: 0 }, { opacity: 1, offset: 0.25 }, { opacity: 1, offset: 0.75 }, { opacity: 0 }], {
        duration: 1600,
      }).onfinish = () => el.remove();
      return;
    }

    const de = ida ? -w * 0.3 : largura - w * 0.7;
    const para = ida ? largura - w * 0.7 : -w * 0.3;
    el.animate([
      { transform: `translate(${de}px, calc(-50% + ${faixa}px))`, opacity: 0 },
      { opacity: 1, offset: 0.16 },
      { opacity: 1, offset: 0.8 },
      { transform: `translate(${para}px, calc(-50% + ${faixa}px))`, opacity: 0 },
    ], {
      duration: aleatorio(1000, 1650),
      easing: 'cubic-bezier(0.45, 0, 0.2, 1)',
    }).onfinish = () => el.remove();
  }

  function iniciarPacotes() {
    const alvo = $('#pacotes');
    const cadencia = REDUZIDO ? 480 : 90;
    let ultimo = -Infinity;
    const desligar = quadro((t) => {
      if (t - ultimo < cadencia) return;
      ultimo = t;
      lancarPacote(alvo);
      audio.dado(t);
    });
    sessao.limpezas.push(desligar);
    return desligar;
  }

  async function cena2() {
    mostrarCena(2);
    const cena = $('.cena-2');
    await esperar(450);
    cena.classList.add('is-orbes');
    await esperar(950);
    cena.classList.add('is-feixe');
    await esperar(450);
    const pararPacotes = iniciarPacotes();
    await esperar(650);
    $('.legenda', cena).classList.add('is-visivel');
    await esperar(4700);
    pararPacotes();
    await esperar(350);
  }

  /* ==========================================================
   * CENA 3 · A Inteligência do Tempo
   * ========================================================== */
  function pontoNoCirculo(graus, raio) {
    const rad = (graus * Math.PI) / 180;
    return [200 + raio * Math.cos(rad), 200 + raio * Math.sin(rad)];
  }

  function arco(de, ate, raio) {
    const [x0, y0] = pontoNoCirculo(de, raio);
    const [x1, y1] = pontoNoCirculo(ate, raio);
    return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${raio} ${raio} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }

  /** 44 segmentos de arco (de 135° a 405°, sentido horário) - cada um vira
   *  uma partícula independente na desintegração. */
  function montarVelocimetro() {
    const grupo = $('#velocimetro-segmentos');
    grupo.innerHTML = '';
    const total = 44;
    const passo = 270 / total;
    const folga = 1.4;
    return Array.from({ length: total }, (_, i) => {
      const de = 135 + passo * i + folga / 2;
      const ate = 135 + passo * (i + 1) - folga / 2;
      const caminho = document.createElementNS(SVG_NS, 'path');
      caminho.setAttribute('d', arco(de, ate, 150));
      caminho.setAttribute('class', 'seg');
      grupo.appendChild(caminho);
      return { el: caminho, meio: (de + ate) / 2 };
    });
  }

  function desintegrar(segmentos) {
    segmentos.forEach((s, i) => {
      if (REDUZIDO) {
        s.el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 500, delay: i * 8, fill: 'forwards' });
        return;
      }
      const rad = (s.meio * Math.PI) / 180;
      const distancia = aleatorio(70, 250);
      const dx = Math.cos(rad) * distancia + aleatorio(-40, 40);
      const dy = Math.sin(rad) * distancia + aleatorio(-40, 40) - 30;
      s.el.animate([
        { transform: 'translate(0, 0) rotate(0deg) scale(1)', opacity: 1 },
        { transform: `translate(${dx}px, ${dy}px) rotate(${aleatorio(-150, 150)}deg) scale(0.4)`, opacity: 0 },
      ], {
        duration: aleatorio(900, 1400),
        delay: (i % 11) * 22 + aleatorio(0, 160),
        easing: 'cubic-bezier(0.2, 0.7, 0.3, 1)',
        fill: 'forwards',
      });
    });
  }

  async function cena3() {
    mostrarCena(3);
    const cena = $('.cena-3');
    const velocimetro = $('#velocimetro');
    const ponteiro = $('#velocimetro-ponteiro');
    const km = $('#km-valor');
    const segmentos = montarVelocimetro();

    await esperar(700);
    velocimetro.classList.add('is-visivel');
    await esperar(700);

    await animar(1500, suave.saidaCubica, (e) => {
      const angulo = -135 + e * 205;
      ponteiro.style.transform = `rotate(${angulo}deg)`;
      km.textContent = Math.round(e * 148095).toLocaleString('pt-BR');
      const ponta = 270 + angulo;
      segmentos.forEach((s) => {
        const acesa = s.meio <= ponta;
        s.el.classList.toggle('is-acesa', acesa);
        s.el.classList.toggle('is-alerta', acesa && s.meio > 300);
      });
    });

    await esperar(350);
    velocimetro.classList.add('is-apagado');
    await esperar(550);
    desintegrar(segmentos);
    velocimetro.classList.add('is-desfeito');
    audio.desintegrar();
    await esperar(750);

    const anel = $('#anel');
    const dias = $('#dias-valor');
    anel.classList.add('is-desenhando');
    audio.anel();
    const contagem = animar(1900, suave.meioCubico, (e) => { dias.textContent = String(Math.round(e * 180)); });
    await esperar(1000);
    $('.legenda', cena).classList.add('is-visivel');
    await contagem;
    anel.classList.add('is-completo');
    audio.anelCompleto();
    await esperar(2500);
  }

  /* ==========================================================
   * CENA 4 · O Status Global
   * ========================================================== */
  function montarConstelacao() {
    const alvo = $('#constelacao');
    const svg = $('#constelacao-linhas');
    alvo.innerHTML = '';
    svg.innerHTML = '';

    const W = window.innerWidth;
    const H = window.innerHeight;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

    const alvoQtd = Math.round(Math.min(170, Math.max(80, (W * H) / 10500)));
    const colunas = Math.max(6, Math.round(Math.sqrt((alvoQtd * W) / H)));
    const linhas = Math.max(4, Math.round(alvoQtd / colunas));
    const cw = W / colunas;
    const lh = H / linhas;
    const cx = W / 2;
    const cy = H / 2;
    const raioMax = Math.hypot(cx, cy);

    const pontos = [];
    const fragmento = document.createDocumentFragment();
    for (let l = 0; l < linhas; l++) {
      for (let c = 0; c < colunas; c++) {
        if (Math.random() < 0.1) continue;
        const x = (c + 0.5 + aleatorio(-0.4, 0.4)) * cw;
        const y = (l + 0.5 + aleatorio(-0.4, 0.4)) * lh;
        // Nunca nasce embaixo dos controles (som / pular) nos cantos de baixo.
        if (y > H - 84 && (x < 190 || x > W - 150)) continue;
        const sorteio = Math.random();
        const tipo = sorteio < 0.24 ? 'urgente' : sorteio < 0.4 ? 'atencao' : 'neutro';
        const el = document.createElement('div');
        el.className = `ponto ponto--${tipo}`;
        el.style.left = `${x}px`;
        el.style.top = `${y}px`;
        el.style.setProperty('--t', `${aleatorio(6, 11).toFixed(1)}px`);
        el.style.setProperty('--d', `${((Math.hypot(x - cx, y - cy) / raioMax) * 0.9 + aleatorio(0, 0.2)).toFixed(2)}s`);
        el.style.setProperty('--p', `${aleatorio(0, 1.5).toFixed(2)}s`);
        el.innerHTML = '<span class="ponto__base"></span><span class="ponto__verde"></span><span class="ponto__halo"></span>';
        fragmento.appendChild(el);
        pontos.push({ el, x, y });
      }
    }
    alvo.appendChild(fragmento);

    // Cada ponto se liga aos 2 vizinhos mais próximos (dentro de um raio) -
    // é isso que transforma pontos soltos numa constelação.
    const segmentos = [];
    const vistos = new Set();
    const alcance = Math.max(cw, lh) * 1.5;
    pontos.forEach((a, i) => {
      pontos
        .map((b, j) => ({ j, d: Math.hypot(a.x - b.x, a.y - b.y) }))
        .filter(({ j, d }) => j !== i && d < alcance)
        .sort((m, n) => m.d - n.d)
        .slice(0, 2)
        .forEach(({ j }) => {
          const chave = i < j ? `${i}-${j}` : `${j}-${i}`;
          if (vistos.has(chave)) return;
          vistos.add(chave);
          const b = pontos[j];
          const linha = document.createElementNS(SVG_NS, 'line');
          linha.setAttribute('x1', a.x.toFixed(1));
          linha.setAttribute('y1', a.y.toFixed(1));
          linha.setAttribute('x2', b.x.toFixed(1));
          linha.setAttribute('y2', b.y.toFixed(1));
          svg.appendChild(linha);
          segmentos.push({ el: linha, x: (a.x + b.x) / 2 });
        });
    });

    return { pontos, segmentos };
  }

  /** A onda varre a tela e converte cada ponto (e cada linha) no instante
   *  exato em que o centro dela passa por cima - nada é "adivinhado" por
   *  atraso fixo, então o verde sempre nasce debaixo da luz. */
  function varrerOnda(pontos, segmentos) {
    const onda = $('#onda');
    const W = window.innerWidth;
    const largura = onda.offsetWidth;
    const fila = [...pontos, ...segmentos].sort((a, b) => a.x - b.x);
    let proximo = 0;

    if (!REDUZIDO) onda.classList.add('is-ativa');
    return animar(REDUZIDO ? 1200 : 2200, suave.meioSeno, (e) => {
      const centro = -largura / 2 + e * (W + largura);
      if (!REDUZIDO) onda.style.transform = `translateX(${centro - largura / 2}px)`;
      while (proximo < fila.length && fila[proximo].x <= centro) {
        fila[proximo].el.classList.add('is-verde');
        proximo++;
      }
    }).then(() => {
      fila.slice(proximo).forEach((item) => item.el.classList.add('is-verde'));
      onda.classList.remove('is-ativa');
    });
  }

  async function cena4() {
    mostrarCena(4);
    const cena = $('.cena-4');
    const { pontos, segmentos } = montarConstelacao();
    await esperar(650);
    cena.classList.add('is-constelacao');
    await esperar(2000);

    audio.onda();
    const onda = varrerOnda(pontos, segmentos);
    await esperar(1150);
    $('.placa', cena).classList.add('is-visivel');
    await onda;
    await esperar(2900);

    cena.classList.add('is-dissolvendo');
    await esperar(900);
  }

  /* ==========================================================
   * CENA 5 · Assinatura
   * ========================================================== */
  async function cena5() {
    mostrarCena(5);
    const cena = $('.cena-5');
    await esperar(350);
    cena.classList.add('is-brilho');
    audio.assinatura();
    await esperar(1100);
    cena.classList.add('is-nome');
    await esperar(700);
    cena.classList.add('is-acao');
  }

  /* ---------- Orquestração ---------- */
  async function executar(aPartirDe = 1) {
    if (sessao) sessao.cancelar();
    sessao = novaSessao();
    resetar();
    try {
      if (aPartirDe <= 1) await cena1();
      if (aPartirDe <= 2) await cena2();
      if (aPartirDe <= 3) await cena3();
      if (aPartirDe <= 4) await cena4();
      await cena5();
    } catch (erro) {
      if (!(erro instanceof Cancelado)) throw erro;
    }
  }

  function pular() {
    if (document.body.dataset.cena !== '5') executar(5);
  }

  /* ---------- Controles ---------- */
  const botaoSom = $('#som');
  const rotuloSom = $('#som-rotulo');

  botaoSom.addEventListener('click', () => {
    const ligado = audio.alternar();
    botaoSom.setAttribute('aria-pressed', String(ligado));
    rotuloSom.textContent = ligado ? 'Som ligado' : 'Ativar som';
  });

  $('#pular').addEventListener('click', pular);
  $('#rever').addEventListener('click', () => executar(1));

  document.addEventListener('keydown', (evento) => {
    if (evento.key === 'Escape') pular();
  });

  /* ---------- Início: espera fontes e logo, depois o breu ---------- */
  const prontos = Promise.all([
    document.fonts ? document.fonts.ready : Promise.resolve(),
    ...$$('.marca__logo').map((img) => (img.decode ? img.decode().catch(() => {}) : Promise.resolve())),
  ]);

  Promise.race([prontos, new Promise((ok) => setTimeout(ok, 1800))]).then(() => {
    setTimeout(() => document.body.classList.add('is-controles'), 1400);
    executar(1);
  });
})();
