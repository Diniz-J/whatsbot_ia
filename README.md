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

Os apps `responder`, `catalogo`, `reservas` e `metricas` ainda estao vazios. A
proxima fatia e a 2b: callbacks de status (`sent`/`delivered`/`read`/`failed`),
que hoje sao aceitos e descartados, e a categoria de cobranca que vem neles.

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
