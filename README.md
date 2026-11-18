# HGhub — REDE HG · Sistema de Demandas

Aplicativo desktop para Windows de gestão de demandas (pastas → projetos → tarefas, quadro Kanban, calendário,
dashboard) e de **Frota & Manutenção** (lê a planilha de preventivas no Google Drive). Backend Flask,
interface HTML/CSS/JS e janela nativa (pywebview), empacotado com PyInstaller e instalado com Inno Setup.

## Instalar (usuário final)

1. Baixe o `HGhub-Setup-X.Y.Z.exe` em **[Releases](https://github.com/H44TAKE/hghub-demandas/releases/latest)**.
2. Execute. Não pede administrador: instala em `%LOCALAPPDATA%\Programs\HGhub`.
3. O Windows pode mostrar o aviso **"O Windows protegeu o computador" (SmartScreen)**: o instalador ainda não tem
   assinatura de código paga. Clique em **Mais informações → Executar assim mesmo**. A integridade das
   atualizações é garantida por assinatura digital própria (veja [docs/ATUALIZADOR.md](docs/ATUALIZADOR.md)).
4. Se usa o módulo de Manutenção, na instalação aponte o arquivo `credenciais.json` (opcional; pode ser feito depois
   copiando-o para `%LOCALAPPDATA%\HGhub\credenciais.json`).

Depois disso o programa **se atualiza sozinho**: avisa quando há versão nova, baixa, confere e instala, sem tocar nos
seus dados. Em **Atualizações** (menu lateral) dá para verificar na hora e **voltar à versão anterior**.

## Onde ficam as coisas

| O quê | Onde | Apagado ao atualizar? |
|---|---|---|
| Programa | `%LOCALAPPDATA%\Programs\HGhub` | substituído |
| **Dados do sistema (base atual)** | pasta de rede `Z:`/`Y:` `\SGQ - QUALIDADE\SISTEMA DEMANDAS - Copia\HGhub-dados` | **nunca** |
| Arquivos antigos (`dados_sistema_hg.json`, `usuarios.json`, `config.json`...) | a pasta acima (um nível) — **somente leitura**, copia de segurança visual | **nunca** |
| Credenciais do Google, logs, estado do atualizador, cópias dos instaladores | `%LOCALAPPDATA%\HGhub` | **nunca** |
| Sessão do usuário | `%APPDATA%\HGHub\sessao_hg.json` | **nunca** |

### A pasta `HGhub-dados` (uma coisa em cada arquivo)

| Arquivo | Conteúdo |
|---|---|
| `usuarios.json` | contas: nome, e-mail, setores, conta mestre, senha (**hash**). Editável à mão: veja `LEIA-ME.txt` na pasta (campo `nova_senha` troca a senha de alguém) |
| `preferencias_usuarios.json` | notificações e favoritos de cada conta |
| `demandas.json` | pastas → projetos → tarefas |
| `catalogos.json` | setores, cidades, etiquetas salvas, anotações |
| `sessoes.json` | sessões abertas e códigos de recuperação — **só hashes** (ler o arquivo não dá acesso a ninguém) |
| `configuracao.json` | e-mail (SMTP) e planilha do Google |
| `fotos\`, `anexos\` | arquivos enviados (os antigos continuam sendo servidos da pasta antiga) |
| `copias\` | cópias de segurança feitas antes de reimportar do legado |

Na **primeira abertura** da 1.1.0 o programa importa tudo o que existe nos arquivos antigos para `HGhub-dados`
(sem alterá-los). Se algum PC ainda usar uma versão antiga e gravar nos arquivos antigos, a Conta Mestre vê o aviso
e pode **somar** o que falta (`/api/armazenamento/reimportar`; nunca remove nem sobrescreve nada da base nova).
Sem a pasta de rede o programa **não** abre uma base local vazia: mostra o motivo e não grava nada
(para uso offline deliberado: `%LOCALAPPDATA%\HGhub\config_local.json` com `{"permitir_base_local": true}`).

## Novidades da 1.2.0

- **Meu dia** (tela inicial): só o que é seu e está atrasado ou vence em até 7 dias, com "Concluir" em um clique.
- **Concluídas ocultas** na lista (Estrutura) e no Quadro, com aviso "N concluídas ocultas — mostrar". As concluídas há
  mais de 90 dias viram *arquivadas* (nada é apagado; "Incluir arquivadas" as mostra).
- **Busca e filtros melhores**: ignora acento e maiúsculas, vale para título, descrição, responsável, cidade, projeto,
  pasta e etiqueta; filtros por prazo e etiqueta; os filtros escolhidos ficam lembrados.
- **Importar planilha** (Estrutura): lê a aba do mês de uma planilha de vencimento de exames, mostra uma prévia e cria
  só as demandas que ainda não existem (pasta/projeto à sua escolha). O arquivo nunca é alterado.
- **E-mail semanal de vencimentos**: toda segunda, às 8h, cada responsável recebe um e-mail (atrasadas e vencimentos dos
  próximos 7 dias). Desligar/ajustar em `configuracao.json`: `"avisos_semanais": {"ativo": false, "dia_semana": 0, "hora": 8}`.
- **Tema** (claro/escuro) lembrado por conta e por PC; o padrão é claro.
- Manutenção: 5ª roda voltou a aparecer, o Cadastro de Frota mostra a união Frota + Revisões e óleo, e a página não
  "pula" mais ao ocultar um veículo. Removido o "Exportar Excel".

## Desenvolvimento

```powershell
pip install -r requirements.txt pytest pyflakes
$env:HGHUB_ARQUIVO_DADOS = "$env:TEMP\hghub_dev\dados_sistema_hg.json"   # NUNCA rode sem isto: o padrão é a base real
$env:HGHUB_ADMIN_SENHA = "troque-me"                                     # senha da conta admin de uma base nova
python app.py            # janela nativa     |   python app.py --web   # só o servidor, no navegador
python -m pytest tests   # testes
```

Primeiro acesso numa base nova: a conta `admin@redehg.com` nasce com senha **aleatória** gravada em
`SENHA_INICIAL_ADMIN.txt` (na pasta de dados) — leia e apague o arquivo.

## Publicar uma versão

Veja [docs/PUBLICAR.md](docs/PUBLICAR.md). Resumo: edite `backend/versao.py`, commite,
`git tag vX.Y.Z && git push origin main vX.Y.Z` — o GitHub Actions testa, compila, assina e publica.

## Documentação

- [docs/ATUALIZADOR.md](docs/ATUALIZADOR.md) — como a atualização funciona, segurança e recuperação
- [docs/PUBLICAR.md](docs/PUBLICAR.md) — gerar e publicar versões, chaves e segredos
- [docs/AUDITORIA.md](docs/AUDITORIA.md) — relatório de auditoria, correções e pendências
