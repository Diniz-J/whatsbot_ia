# whatsbot_ia

Plataforma de atendimento automatizado no WhatsApp para pequenos negocios
(restaurante, escola, clinica). Um produto, dois tiers de atendimento no mesmo
codigo, escolhidos por configuracao de cada cliente (tenant):

- **rules** — bot deterministico por regras. Barato, previsivel, sem alucinacao.
- **llm** — hibrido: LLM entende a mensagem em linguagem natural, mas sempre
  aterrado nos dados reais do negocio (preco, horario, disponibilidade vem da
  base/ferramenta, nunca da cabeca do modelo).

O tier "rules" e o hibrido com o modulo de LLM desligado. Trocar de um para o
outro e virar uma flag na configuracao do tenant — nao ha migracao nem repo
separado. Isso viabiliza o upsell: cliente comeca no rules e sobe para llm.

## Stack

- **Backend:** Python + Django + Django REST Framework
- **Fila:** Celery + Redis (outbound, processamento de inbound, retries)
- **Banco:** PostgreSQL
- **Canal:** WhatsApp Cloud API (oficial da Meta)
- **Admin:** Django Admin + tema Unfold (back-office por cliente)

Decisao de framework e tema registrada em [DESIGN.md](DESIGN.md).

## Status

Fatia 1 (borda de entrada) entregue: o webhook da Cloud API confere a
assinatura HMAC e o numero de destino, grava a entrega crua de forma
idempotente e enfileira o processamento, que normaliza em contato, conversa e
mensagem. Antes dela, a fatia 0 deixou o projeto de pe com `Tenant`,
`Configuracao`, `Usuario` e o escopo por cliente no admin.

Fatia 2a (borda de saida) entregue: envio de texto e de template pela fila,
com a janela de 24h decidindo qual dos dois e permitido, retentativa que
distingue falha transitoria de permanente, e canal em memoria para rodar sem
tocar na Meta.

Fatia 2b entregue: os callbacks de status (`sent`/`delivered`/`read`/`failed`)
atualizam a mensagem, a categoria de cobranca que a Meta informa fica gravada
nela, e uma varredura destrava o que ficou preso (envio reservado por worker
que morreu, entrega gravada cujo enfileiramento se perdeu).

A varredura precisa de um processo `celery beat` no ar; sem ele o agendamento
e inerte.

Catalogo entregue: cardapio por categoria, preco, disponibilidade e horario
de funcionamento (inclusive faixa que passa da meia-noite), com back-office
em que o dono do negocio edita tudo. `catalogo/services.py` e a fonte unica
dessas respostas — e o que o invariante 2 exige.

`RuleResponder` entregue: o bot atende de verdade. Cinco funcoes — saudacao,
cardapio, preco de item, horario de funcionamento e fallback que oferece
gente. Tudo que ele afirma sai de `catalogo/services.py`; nada e inventado.
Item sem preco cadastrado recebe "nao tenho o preco aqui", nao um numero.

`LLMResponder` entregue: o tier hibrido. O modelo entende a pergunta em
linguagem natural, mas preco, horario e disponibilidade saem de ferramenta — e
as ferramentas SAO as funcoes de `catalogo/services.py`, com teste conferindo
identidade objeto a objeto. O `tenant_id` nao aparece em schema nenhum: e
injetado do contexto, para texto de cliente final nao alcancar catalogo
alheio. Falha do modelo cai no tier de regra em vez de deixar sem resposta.

Trocar de tier e mudar um campo no admin: `Configuracao.responder`.

Os apps `reservas` e `metricas` ainda estao vazios. Token gasto fica na
propria mensagem (`tokens_entrada`/`tokens_saida`), ao lado da categoria de
cobranca — agora ha duas fontes de custo numa consulta, que e o que o
`metricas` precisava para existir.

Webhook em `POST /webhook/whatsapp/` (e `GET` para a verificacao da Meta).

## Estrutura

```
config/              settings, urls, celery, wsgi/asgi
apps/
  common/            base models, mixins de escopo (tenant), permissions
  tenants/           Tenant + Configuracao (responder, credenciais WABA, plano)
  canal/             adaptador de canal (a borda); whatsapp/ com HMAC, templates
  conversas/         Contato, Conversa, Mensagem, EventoWebhook, janela de 24h
  core/              orquestracao: evento -> tenant -> responder -> canal (+ tasks)
  responder/         o plugavel: base, rules, llm/, registry
  catalogo/          cardapio, itens, preco, horario (a fonte da verdade)
  reservas/          reserva/pedido + disponibilidade
  metricas/          custo por mensagem e por token, por tenant
tests/
```

## Desenvolvimento

Requer Python 3.13 e [uv](https://docs.astral.sh/uv/). Configuracao via
variaveis de ambiente — copie `.env.example` para `.env` e preencha. As
credenciais WABA de cada cliente ficam no banco (por tenant), nao no `.env`.

```bash
uv sync                                  # cria .venv e instala do uv.lock
cp .env.example .env                     # e preencha DJANGO_SECRET_KEY
docker compose up -d                     # postgres + redis
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver        # admin em /painel/ (ver ADMIN_URL)
uv run celery -A config worker -l info   # em outro terminal
```

Qualidade, o mesmo que o CI roda:

```bash
uv run ruff check . && uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest
```

Antes de subir um cliente, com o ambiente de producao carregado:

```bash
uv run python manage.py check --deploy
```

Nao esta no CI porque o ambiente de CI nao e de producao: a checagem recusa
exatamente a configuracao vazia que o CI usa. Ela cobre as credenciais do
webhook (`canal.E001`/`E002`, que falham fechado e em silencio) e o hardening
que o proprio Django confere (`SECURE_SSL`, HSTS, cookie `secure`).
