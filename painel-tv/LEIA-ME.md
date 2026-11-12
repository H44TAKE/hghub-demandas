# Painel de TV · Manutenção REDE HG

Painel para a TV do setor de Manutenção: a saúde de **cada frente** (chassi, 5ª roda, preventiva,
tanques, revisão e óleo) e uma lista que rola sozinha com **todos os veículos**, cada um na sua cor.
Lê a **mesma planilha do Google Drive** do app principal, **somente leitura**. É um projeto à parte:
não usa a unidade Z: nem nenhum arquivo do app principal.

## A regra das cores dos veículos

"Pendente" é toda frente que **não está em dia** (vencida ou a vencer). Frente "sem dado" não conta.
O painel compara quantas frentes do veículo estão pendentes com quantas estão em dia:

| Situação do veículo | Cor |
|---|---|
| mais pendentes do que em dia | **Vermelho** |
| meio a meio | **Laranja** |
| mais em dia do que pendentes (inclui "só 1 pendente" entre várias e "tudo em dia") | **Verde** |

Exemplos: 4 de 4 pendentes = vermelho · 3 de 4 = vermelho · 2 de 4 = laranja · 1 de 2 = laranja ·
1 de 3 = verde · 1 de 4 = verde · 0 de 3 = verde. A lista começa pelos vermelhos, depois laranja, depois
os verdes; a regra aparece escrita no topo da lista, com quantos veículos há em cada cor. Em cada linha
todas as frentes do veículo aparecem: vermelho cheio = vencida, contorno âmbar = a vencer, contorno verde = em dia.
Veículo sem nenhuma frente com status na planilha não aparece na lista.

## Instalar no mini PC (uma vez)

1. Instale o **Python 3.11 ou mais novo** (marque *Add python.exe to PATH*).
2. Copie esta pasta `painel-tv` inteira para o mini PC (ex.: `C:\painel-tv`).
3. Num terminal dentro da pasta: `pip install -r requirements.txt`
4. **Credencial do Google:** copie à mão o arquivo `credenciais.json` (o mesmo do app principal) para
   dentro da pasta `painel-tv`. É a chave da conta de serviço: **nunca** mande por e-mail, zip ou
   pasta compartilhada. O painel pede só permissão de *leitura* (`drive.readonly`).
5. Abra `config.json` e preencha `planilha_id` com o ID da planilha no Drive (o trecho entre `/d/` e
   `/edit` do link, o mesmo configurado no app principal).
6. Teste sem credencial, com dados fictícios: `python painel.py --demo` (aparece a faixa
   "Modo demonstração"). Para fechar a tela cheia: `Alt+F4`.
7. Teste de verdade: `python painel.py`.
8. Para abrir sozinho ao ligar o PC: dê dois cliques em `instalar_inicio_automatico.bat`.

## Dicas para o PC da TV

- Windows → *Configurações → Sistema → Energia*: tela e suspensão em **Nunca**.
- Ative o login automático do Windows (`netplwiz`) para o painel voltar sozinho depois de uma queda de energia.
- `iniciar_painel.bat` reabre o painel sozinho se ele cair.

## Como funciona

- A cada **5 minutos** (`atualizar_a_cada_segundos`) o servidor confere se a planilha mudou no Drive e,
  só se mudou, baixa e recalcula. A tela lê o último resultado guardado (instantâneo).
- Sem internet ou com o Drive fora do ar, a TV **continua mostrando os últimos dados** e exibe a faixa
  "Sem conexão com a planilha · mostrando dados das HH:MM". Quando volta, a faixa some sozinha.
- Mostra **todos** os veículos, inclusive os que estão ocultos no "Gerenciar" do app principal.
- **Alinhamento não aparece** (a aba da planilha está desatualizada). No `config.json`, `"frentes_ocultas": ["alinhamento"]`
  esconde a frente por inteiro: sem cartão, sem chip na lista, e um veículo que só estava vencido nela deixa de aparecer em
  "Precisam de atenção". Quando a aba for atualizada, deixe a lista vazia (`"frentes_ocultas": []`) e reinicie o painel.
  Chaves possíveis: `revisao_oleo`, `alinhamento`, `quinta_roda`, `integridade_chassi`, `sr_tanques`, `preventiva_carretas`.
- Não mostra "em oficina": a planilha não tem essa informação.
- Log em `painel.log` (na própria pasta).

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| "Painel ainda não configurado" | `planilha_id` vazio, `credenciais.json` ausente ou bibliotecas não instaladas (`pip install -r requirements.txt`). A mensagem na tela diz qual. |
| "Carregando dados da frota…" por muito tempo | A primeira leitura leva alguns segundos; se passar de 1 minuto, veja `painel.log`. |
| Faixa amarela "Sem conexão" | Internet ou Drive fora do ar; some sozinha quando voltar. |
| `config.json` editado e o painel segue "não configurado" | Confira se o arquivo é JSON válido (vírgulas, aspas). O painel aceita o arquivo com ou sem BOM. |
| A conta de serviço não acha o arquivo | A planilha precisa estar compartilhada com o e-mail da conta de serviço (o mesmo do app principal). |

## Manutenção do painel (para quem desenvolve)

- `servidor/leitor.py` é **gerado** a partir de `backend/manutencao.py` do app principal (mesma regra de
  leitura e de status, sem nada de escrita). Se a regra mudar lá, regenere aqui:
  `python ferramentas/gerar_leitor.py` e depois `python -m unittest discover -s testes`.
- `servidor/dados.py` calcula o que a TV mostra (mesma regra de saúde do Dashboard do app principal).
- `web/` é a página da TV (HTML/CSS/JS sem dependências externas, escala pela altura da tela).
- Testes: `python -m unittest discover -s testes -v` (não falam com o Google).
