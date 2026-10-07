# bluesky-agent

Agente autônomo que opera um perfil Bluesky 24/7 como se fosse o dono.
GitHub Actions é o relógio, GitHub Models é o cérebro, o próprio repo é o banco.
Sem servidor, sem fatura, sem caixa pra babysitar.

O agente **lê o timeline, curte, comenta, segue, reposta e posta** sozinho,
respeitando um governador anti-ban e um filtro anti-spam em cada ação.

## Arquitetura

```
a cada 15min  agent.yml  -> src.act_run    -> sessão autônoma (ler + agir)
a cada 3h     compose.yml-> src.compose_run-> estoca rascunhos na fila
diário        health.yml -> src.health     -> abre issue se parar
após cada run site.yml   -> src.site       -> docs/index.html -> GitHub Pages
```

Cada mudança de estado é um commit. O histórico do git é o audit log.

## O que ele faz

| ação | gate |
|---|---|
| postar | cota/dia, gap mínimo, autocrítica do modelo, dedupe por shingle |
| comentar | cota/dia, relevância mínima, taxa por autor, filtro de spam |
| curtir | cota/dia, gap de 45s, rajada máx 4 + cooldown, filtro de spam |
| seguir | cota/dia, modelo decide, filtro de spam no perfil |
| repostar | cota/dia, relevância mínima 0.5, gap |

## Notícias reais (`src/news.py` -> `src/ground.py`)

O bot não inventa nada. Pipeline:

```
GDELT (keyless, 100+ idiomas, reindexa a cada 15min)
Wikipedia Current Events (humano-curado)
Hacker News Algolia (sinal tech)
Google News RSS (desligado por padrão — os termos do feed limitam a uso pessoal)
        ↓
cluster  -> agrupa manchetes do mesmo evento
        ↓
corrobora -> exige >= 2 domínios distintos (ou 1 confiável + 2 menções)
        ↓
escolhe  -> modelo aponta o melhor ângulo
        ↓
rascunho -> escreve SÓ com as manchetes fornecidas
        ↓
factcheck -> número/nome fora da fonte = rejeitado
```

Fato de uma fonte só não vira post. É o que separa "crescendo com conteúdo
real" de espalhar boato.

## Anti-ban (`src/policy.py`)

Nenhuma ação é ilimitada. Toda ação passa por `policy.allow()`:

- **cota diária** por ação, escalada pelo horário local
- **ritmo humano** — escala 0.25 de atividade de madrugada, janela 7h–23h
- **jitter** de ±35% em todo intervalo — nada de metrônomo perfeito
- **skip aleatório** de 18% — nem toda oportunidade vira ação
- **controle de rajada** — 4 curtidas e pausa de 35min
- **cap por autor** — no máximo 1 comentário e 2 curtidas por pessoa/dia
- **circuit breaker** — 3 erros seguidos abre o circuito por 30min
- **máx 6 ações por execução**

## Multi-modelo (`src/models.py`)

GitHub Models é gratuito mas escalonado:

| tier | modelos | limite/dia |
|---|---|---|
| high | gpt-4.1, gpt-4o, llama-3.3-70b | ~50 |
| low | gpt-4.1-mini, gpt-4o-mini, phi-4 | ~150 |
| reasoning | deepseek-r1, o3-mini | ~8-15 |

Então: modelo barato filtra e pontua, modelo forte só escreve o rascunho
final, modelo de raciocínio quase nunca. Orçamento contado por dia no ledger,
com downgrade automático em 429.

## Anti-spam (`src/spam.py`)

Camada determinística, roda sempre, sem custo de modelo:

blocklist de termos · mais de 3 hashtags · emoji wall (>5) · link spam (>2) ·
texto repetitivo (unicidade <40%) · CAIXA ALTA · blocos duplicados ·
idade do post · autor já tratado

O modelo só é chamado depois que o barato passa.

## Crescimento orgânico (`src/growth.py` + `src/timing.py`)

Sem anúncio, sem follow-churn, sem follow-back farming.

- **timing** — observa likes/replies dos próprios posts por hora local e
  aprende quando o perfil é visto. Posta pesado no pico, explora fora dele.
- **growth** — descobre contas ativas na nossa área (300 a 120k seguidores:
  grandes o suficiente pra valer, pequenas o suficiente pra ver a gente),
  o modelo veta, e o bot aparece onde o público dele já lê.

O que faz crescer é utilidade em reply, não volume.

## Kill switch

Crie `state/STOP` no repo. Com esse arquivo presente **nada executa**,
nenhuma ação, nenhum post. Apague para retomar. Funciona em segundos,
sem deploy, sem acesso ao Actions.

## Painel (GitHub Pages)

`Settings → Pages → Deploy from branch → main → /docs`

O `src/site.py` gera um HTML estático com: status do agente, barras de cota
do dia, volume de posts/fila/rejeições/seguindo, feed de atividade e o
estado do kill switch. Atualiza sozinho a cada execução. Auto-refresh 5min.

## Status operacional

Sete workflows no ar:

| workflow | quando | faz |
|---|---|---|
| `agent-act` | a cada 10 min | sessao autonoma: curte, comenta, segue, reposta, posta |
| `agent-news` | a cada 2 h | busca noticia real, corrobora, enfileira |
| `agent-compose` | a cada 3 h | mantem a fila estocada |
| `audit` | a cada hora | 15 invariantes; abre issue se alguma quebrar |
| `preflight` | push + manual | valida credenciais e escreve diagnostico em `state/preflight.log` |
| `agent-health` | 06:11 UTC | abre issue se a fila secar |
| `site-build` | apos cada sessao | painel em `docs/`, publica no Pages |

Kill switch: crie `state/STOP` e nada executa. Apague e retoma.

## Por que o modelo nao fala (e como resolver)

Diagnosticado de dentro do runner, com o token do usuario:

```
models.github.ai/qualquer/caminho  200  "OK"      <- stub
gh models list                     erro de parse ("O")
gh copilot -- -p "oi"              erro: classic PAT nao suportado
api.githubcopilot.com              400: integration id nao corresponde
```

**Causa:** o token em uso e um **classic PAT (`ghp_`)**. GitHub Models e Copilot
aceitam apenas **fine-grained PAT** com permissao `models: read`. Classic PAT
recebe um `200 OK` de texto puro que nao e resposta de modelo nenhum.

**Como resolver:** crie em
<https://github.com/settings/personal-access-tokens>
um fine-grained token com `Models: read`, e grave como secret `GH_MODELS_TOKEN`.
Nada mais precisa mudar — `src/llm.py` detecta e usa automaticamente.

## Setup

### 0. Valide antes de tudo (30 segundos)

```bash
export BSKY_HANDLE="lh434534.github.io"
export BSKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"   # App Password, nao a senha da conta
export GITHUB_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxx"                   # fine-grained, escopo models:read

python tools/preflight.py --write-config
```

Isso mascara as credenciais na saida, nunca grava segredo em arquivo, e confere
tres coisas: o handle resolve pra um DID, a App Password abre sessao, e o token
do GitHub Models responde. So libere o bot quando os tres derem PASS.

Se `createSession` falhar mas a senha estiver certa, o handle provavelmente nao
e o principal da conta — use o DID (`did:plc:...`) no lugar.

1. Sobe o repo no GitHub
2. `Settings → Secrets and variables → Actions → Secrets`
   - `BSKY_HANDLE`
   - `BSKY_APP_PASSWORD` (App Password, não a senha da conta)
3. `Variables`
   - `AUTONOMY` = `2`
   - `DRY_RUN` = `1` por um dia, depois `0`
   - `PDS` = `https://bsky.social` (opcional)
4. Actions → habilita workflows
5. Pages → branch `main`, pasta `/docs`

**Rode com `DRY_RUN=1` por 24h antes de liberar.** O agente decide tudo,
loga tudo, não executa nada.

## Local

```bash
pip install -r requirements.txt
set -a && . ./.env && set +a
python -m src.act_run        # sessão autônoma
DRY_RUN=1 python -m src.act_run
python -m src.compose_run
python -m src.site
```

## Ajustando o agente

- `state/voice.md` — personalidade e frases banidas. Edite primeiro.
- `config.yml` — cotas, gaps, horários, blocklist, termos de descoberta.
- `src/spam.py` → `LEXICON` — vocabulário de interesse.
- `state/ledger.json` — histórico de ações, base das cotas.

## Se algo travar

- muitas rejeições → `state/rejected.json`, provável `voice.md` restritivo
- circuito aberto → veja `state/ledger.json` em `errors`
- fila vazia → `agent-compose` falhando, veja os logs
- quero parar tudo agora → cria `state/STOP`

## Rotação de token

Se uma credencial aparecer em chat, log ou paste: revoga na hora.
Bluesky: Settings → App Passwords. GitHub: Settings → Developer settings → Tokens.

## Rodando local (sem tocar na conta)

O harness em `tools/` falsifica o socket, não o código. Todo `src/` executa de
verdade — `news.py` parseia payload real, `social.py` monta registro atproto
real, `policy.py` limita de verdade. Só o fio é falso.

```bash
python tools/run_local.py --cycles 8 --advance-minutes 45
python tools/run_local.py --cycles 3 --dry 1      # decide, nao executa
```

Flags: `--cycles N` execuções · `--advance-minutes M` envelhece o ledger entre
ciclos pra soltar as cotas (simula o intervalo de 15min real) · `--dry 1`
decide tudo e posta nada · `--seed N` reprodutível.
