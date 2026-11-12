# Como funciona o atualizador

Código: `backend/atualizador.py` · rotas: `backend/rotas_atualizacao.py` · tela: `static/js/atualizacao.js` ·
ferramentas de publicação: `tools/atualizacao.py`.

## Fluxo

1. **Verificar** — 8 s depois de abrir (e a cada 6 h, sem travar a tela) o app baixa
   `update-manifest.json` e `update-manifest.json.sig` do **último release** do repositório configurado em
   `backend/versao.py` (`REPO_RELEASES`). Também há "Verificar agora" em *Atualizações*.
2. **Avisar** — se a versão do manifesto for maior que a instalada, aparece o aviso "Uma nova versão está disponível!"
   com **Ver e atualizar** / **Lembrar mais tarde** (adia 24 h) e as novidades.
3. **Baixar** — em segundo plano, com barra de progresso real; confere tamanho e **SHA-256**.
4. **Aplicar** — reconfere o hash, guarda o instalador atual para reversão e executa o instalador silencioso; o app se
   fecha, o instalador troca os arquivos e **reabre o programa**. Dados e configurações não são tocados.
5. **Confirmar** — a versão nova se declara saudável depois de 90 s abertos. Se ela não conseguir subir (4 aberturas
   sem se confirmar), o app **volta sozinho** à versão anterior.

## Segurança

- **Manifesto assinado (Ed25519).** O app só confia em um manifesto cuja assinatura confira com uma das chaves
  públicas embutidas (`CHAVES_PUBLICAS_ATUALIZACAO`). A chave privada nunca está no repositório: fica no segredo
  `UPDATE_SIGNING_KEY` do GitHub e numa cópia offline. Quem controlar só o GitHub não consegue publicar uma
  atualização aceita sem essa chave.
- **A API do GitHub não é confiada**: o que vale é o manifesto assinado.
- **HTTPS e hosts em lista fechada** (`github.com`, `objects.githubusercontent.com`,
  `release-assets.githubusercontent.com`), conferidos também em **cada redirecionamento**.
- A URL do instalador precisa estar em `github.com/<repo>/releases/download/v<versão>/` e o nome precisa ser
  `HGhub-Setup-<versão>.exe`; tamanho e SHA-256 vêm do manifesto assinado.
- **Sem downgrade** automático: só oferece versão estritamente maior.
- Download parcial, maior que o declarado, adulterado ou trocado na pasta local **nunca é executado** (o hash é conferido
  de novo imediatamente antes de rodar).
- Limitação: o instalador **não tem assinatura de código (Authenticode)** — isso exige certificado pago — então o
  SmartScreen pode avisar na instalação. A proteção do canal de atualização não depende disso.

## Voltar à versão anterior

*Atualizações → Voltar à versão anterior*. O instalador deixa uma cópia de si em
`%LOCALAPPDATA%\HGhub\installers`; na abertura seguinte o app faz a rotação `atual → anterior`
(`HGhub-Setup-anterior.exe`, com hash registrado em `installers\estado.json`). Restaurar reinstala essa cópia só se o
hash conferir. Manualmente: execute `HGhub-Setup-anterior.exe` dessa pasta, ou baixe a versão desejada em *Releases*.

## Arquivos locais (`%LOCALAPPDATA%\HGhub`)

`update_state.json` (estado da última atualização) · `config_local.json` (última verificação, "lembrar mais tarde") ·
`installers\` · `updates\` (downloads) · `logs\hghub.log` e `logs\instalador-atualizacao.log`.

## Testes

- `tests/test_atualizador.py` — 53 testes com servidor HTTP local; um **teste de mutação** (quebra de propósito cada
  defesa) foi executado para provar que nenhuma está sem cobertura.
- `tests/e2e_instalado.py <setup_antigo> <setup_novo>` — ponta a ponta com **instaladores reais**.

## Diagnóstico

| Sintoma | Causa provável |
|---|---|
| "Atualização automática indisponível" | `REPO_RELEASES` vazio em `backend/versao.py` |
| "O servidor de atualização respondeu 404" | ainda não há release publicado, ou o repositório é privado |
| "A assinatura do manifesto ... não confere" | release assinado com outra chave; confira `UPDATE_SIGNING_KEY` |
| Atualizou e abriu a versão antiga | veja `logs\instalador-atualizacao.log` |
