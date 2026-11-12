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
| Dados do sistema (tarefas, usuários, fotos, anexos, `config.json`) | pasta de rede `Z:`/`Y:` `\SGQ\01 - QUALIDADE\SISTEMA DEMANDAS - Copia` | **nunca** |
| Credenciais do Google, logs, estado do atualizador, cópias dos instaladores | `%LOCALAPPDATA%\HGhub` | **nunca** |
| Sessão do usuário | `%APPDATA%\HGHub\sessao_hg.json` | **nunca** |

Sem a pasta de rede, uma base local nova é criada em `%LOCALAPPDATA%\HGhub\dados`. Para apontar outra base
(testes): variável de ambiente `HGHUB_ARQUIVO_DADOS`.

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
