# Relatório de auditoria — HGhub

Escopo e limites: esta auditoria foi parcial. Os resultados abaixo vêm de leitura do código **e** de execução em base
descartável. Onde algo não foi verificado, está dito.

## O que foi auditado

| Área | Situação |
|---|---|
| Rotas da API (`routes.py`) — lidas por inteiro e exercitadas com o cliente de teste do Flask | **concluída** |
| Núcleo do backend (`models.py`, `utils.py`, persistência, sessões, SMTP) | **concluída** |
| Módulo de Manutenção — backend (`manutencao.py`) | **concluída** |
| Frontend — núcleo (`main/ui/api/auth/admin.js`, `index.html`) | **concluída** |
| Frontend — telas (estrutura, kanban, calendário, dashboard) | **concluída** |
| Frontend — Manutenção (`manutencao*.js`) | **não concluída** (auditor interrompido por limite de uso) |
| Design (CSS, hierarquia, acessibilidade) | **não concluída** (idem) |
| Segurança transversal e dependências | **não concluída** (idem; as falhas de segurança vieram das outras áreas) |
| Baseline automatizado da bateria de testes | **não concluída** como auditoria; os testes foram rodados à mão (tabela abaixo) |

## Falhas críticas e altas — corrigidas

| Problema | Correção | Teste |
|---|---|---|
| **Base ilegível/ausente era sobrescrita por uma base vazia** (perda total) | arquivo existente mas ilegível bloqueia qualquer gravação (503, nada é alterado); BOM passa a ser lido | `test_base_ilegivel_nunca_e_sobrescrita`, `test_base_com_bom_e_lida` |
| **Erro de gravação engolido** (API dizia "sucesso" e a alteração se perdia) | o erro sobe; `os.replace` tenta de novo em falhas transitórias (WinError 5) | `test_falha_de_gravacao_nao_responde_sucesso` |
| **Conta `admin@redehg.com` com senha fixa `123456`** | senha **aleatória** em `SENHA_INICIAL_ADMIN.txt` (ou `HGHUB_ADMIN_SENHA`) | `test_admin_inicial_nao_usa_a_senha_fixa_123456` |
| **Exportar Excel entregava todos os setores a qualquer um** (botão sem token) | rota exige login; o botão baixa com token | `test_exportar_com_token_respeita_o_setor` |
| **Rotas sem login** (`/api/dados` vazava e-mails; perfil/notificações/favoritos de qualquer conta; setores/etiquetas/cidades/anotação anônimos) | toda a API exige sessão, exceto login/cadastro/recuperação/`GET /setores`; perfil só do dono ou Conta Mestre | 15 casos parametrizados + `test_perfil_e_notificacoes_so_do_proprio_dono` |
| **XSS armazenado**: anexo `.html`/`.svg` abria na origem do app (roubava o token) | só imagens/PDF/txt abrem na tela; o resto é baixado, com `CSP: sandbox` e `nosniff` | `test_anexo_html_e_baixado_nunca_executado` |
| **Sem limite de tentativas** (login e código de recuperação) | 5 falhas/15 min por conta (429); 3 pedidos de código/15 min | `test_login_bloqueia_forca_bruta`, `test_codigo_de_recuperacao_bloqueia_forca_bruta` |
| Código de recuperação gerado com `random` | `secrets` | `test_codigo_de_recuperacao_usa_secrets` |
| **Sessões nunca expiravam** | expiram em 30 dias; as vencidas são removidas | `test_sessao_expira_depois_de_30_dias` |
| STARTTLS sem validar o certificado | `ssl.create_default_context()` | `test_smtp_valida_certificado` |
| Segredo no código (senha de app e e-mail remetente em `utils.py`) | removidos; vêm do `config.json` ou do ambiente | `tools/verificar_segredos.py` (CI) |
| App falhava se a pasta de dados não existia (instalação nova) | cria a pasta | `test_admin_inicial_...` |
| Cadastro e busca de placa vazios por célula vazia (`NaN` no JSON) — corrigido antes desta missão | — | `testar_celulas_vazias.py` |

## Falhas altas — **pendentes** (não corrigidas)

1. **Cadastro aberto com setor autodeclarado**: qualquer pessoa cria conta no setor "Manutenção" e vê o módulo. Exige
   decisão de produto (aprovação pela Conta Mestre) — não mudei para não quebrar o onboarding.
2. **Tokens de sessão e códigos de recuperação em texto puro** no JSON da pasta compartilhada.
3. **Atualização perdida entre PCs** em `recarregar()` e no construtor (gravam antes da trava) e **trava de arquivo sem
   dono** (considerada órfã após 30 s).
4. **Campos desconhecidos descartados ao regravar** e **sem versão do formato** dos dados.
5. **Queda silenciosa para uma base local vazia** quando a unidade de rede não está montada (o app abre "vazio").
6. **Manutenção**: escrita no `.xlsx` sem trava (lê-altera-reenvia o arquivo inteiro), reescrita de namespaces no XML,
   e valores de fórmula que "voltam" ao valor antigo após editar.
7. **Frontend**: dados compartilhados nunca são recarregados sozinhos (cada PC trabalha com estado envelhecido) e o
   editor regrava a demanda inteira a partir da cópia antiga.
8. Médias/baixas: injeção de fórmula no Excel exportado, entradas de tipo errado gerando 500, bloqueio por dependência
   contornável na edição, desempenho (o JSON inteiro é relido a cada requisição).

## Matriz de testes (o que foi **efetivamente executado**)

| Teste | Resultado |
|---|---|
| `pytest tests` — atualizador (53) + segurança/dados (29) | **APROVADO 82/82** |
| Teste de mutação do atualizador (12 defesas quebradas de propósito) | **APROVADO** — todas detectadas |
| **E2E com instaladores reais** (`tests/e2e_instalado.py`): instalar, abrir, login, dados, atualizar 1.0.0→1.0.1, dados intactos, voltar à anterior, sem internet, assinatura falsa, pacote adulterado, desinstalar | **APROVADO 33/33** |
| Navegador — `verificar_manutencao.js` (dashboard, análises, cadastro/Gerenciar, contas) | **APROVADO 108/108** |
| Permissões de contas / saúde-detalhes / células vazias / BOM / e-mail (SMTP falso) / unidade Z:Y: | **APROVADO** 32, 18, 16, 4, 18, 12 |
| Painel de TV (`painel-tv`) — 34 testes | executado antes desta missão; **não reexecutado** nesta rodada |
| Login real no Gmail (`diagnosticar_email.py`, sem enviar e-mail) | **APROVADO** |
| Envio real de e-mail de recuperação ponta a ponta | **NÃO TESTADO** (só o login SMTP e o fluxo com servidor falso) |
| Fluxo completo do Kanban (arrastar/soltar), calendário, anexos, comentários **no navegador** | **NÃO TESTADO** (só leitura de código; sem suíte) |
| Escrita real na planilha do Google (editar KM, cadastrar veículo) | **NÃO TESTADO** (só leitura foi validada) |
| Tela "Atualizações" no app instalado, visualmente | verificada em captura com release simulado; **não** no `.exe` instalado |
| GitHub Actions (`ci.yml`, `release.yml`) | ver o status do primeiro run no repositório |

## Não realizado nesta rodada

- **Redesign visual completo** (Etapa 3): **não foi feito.** A tela nova de Atualizações segue o sistema visual atual.
  O auditor de design foi interrompido, então não há ainda um diagnóstico formal de design.
- Reorganização dos scripts de teste legados (`brag-output/work`, fora do repositório, com caminhos da máquina de
  desenvolvimento) para `tests/`: apenas os testes novos estão em `tests/`.
- Assinatura de código (Authenticode) do instalador: exige certificado pago; o SmartScreen pode avisar.

## Riscos e observações

- O repositório é **público**: tudo o que está nele é visível. Segredos, dados, credenciais, legado e a base do
  Obsidian ficam fora (ver `.gitignore` e `tools/verificar_segredos.py`). O caminho da pasta de rede da empresa
  aparece em `backend/utils.py`.
- A chave privada de assinatura das atualizações está em `~/.hghub-signing/` (cópia offline recomendada) e no segredo
  `UPDATE_SIGNING_KEY` do GitHub.
