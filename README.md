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
nela, e uma varredura destrava o que ficou preso: envio reservado por worker
que morreu, saida gravada que nunca foi reclamada, entrega gravada cujo
enfileiramento se perdeu e entrada que nunca foi atendida.

A varredura precisa de um processo `celery beat` no ar; sem ele o agendamento
e inerte.

Catalogo entregue: cardapio por categoria, preco, disponibilidade e horario
de funcionamento (inclusive faixa que passa da meia-noite), com back-office
em que o dono do negocio edita tudo. `catalogo/services.py` e a fonte unica
dessas respostas — e o que o invariante 2 exige.

`RuleResponder` entregue: o bot atende de verdade. Saudacao, cardapio, preco
de item, horario de funcionamento, assunto de reserva (que ele reconhece e
passa para gente, sem afirmar disponibilidade) e fallback que oferece uma
pessoa. Tudo que ele afirma sai de `catalogo/services.py`; nada e inventado.
Item sem preco cadastrado recebe "nao tenho o preco aqui", nao um numero.

`LLMResponder` entregue: o tier hibrido. O modelo entende a pergunta em
linguagem natural, mas preco, horario e disponibilidade saem de ferramenta — e
as ferramentas SAO as funcoes de `catalogo/services.py` e
`reservas/services.py`, com teste conferindo identidade objeto a objeto. O
`tenant_id` e o contato nao aparecem em schema nenhum: sao injetados do
contexto, para texto de cliente final nao alcancar catalogo alheio nem
reservar no nome de outra pessoa. Falha do modelo cai no tier de regra em vez
de deixar sem resposta.

Trocar de tier e mudar um campo no admin: `Configuracao.responder`.

Historico de conversa entregue: o atendimento recebe as falas anteriores do
fio, entao consegue perguntar o que falta em vez de responder cada mensagem
sozinha. Dois limites, porque cada um sozinho falha: a janela de servico, para
a conversa da semana passada nao voltar, e o teto de turnos
(`HISTORICO_MAX_TURNOS`), para um dia movimentado nao virar prompt gigante. O
tier de regra ignora o historico por construcao, com teste fixando que a
resposta dele e identica com e sem.

`reservas` entregue: mesa, reserva e disponibilidade, numa tabela so para o
que o bot fecha e o que a equipe anota no balcao — disponibilidade so e
verdade se as duas contarem igual. Sobreposicao de horario na mesma mesa e
recusada pelo banco (`ExclusionConstraint`), e nao por checagem em Python, que
dois workers atravessam ao mesmo tempo. O bot fecha reserva pela ferramenta,
reconferindo a disponibilidade na hora da escrita e com teto de reservas
futuras por contato; cancelar e remarcar ainda chamam uma pessoa.

`metricas` entregue: custo por mensagem entregue e por token, por cliente,
derivado do historico de mensagens em vez de um contador paralelo — token
gasto fica na propria mensagem (`tokens_entrada`/`tokens_saida`), ao lado da
categoria de cobranca que a Meta reportou. O preco vem de tabela datada
(`TabelaPreco`), para um reajuste nao reescrever o custo de meses ja
faturados; sem tabela cadastrada o custo e indefinido, nao zero. A leitura e o
comando `metricas_resumo`.

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
uv run celery -A config beat -l info     # sem ele a varredura nao roda
```

Qualidade, o mesmo que o CI roda:

```bash
uv run ruff check . && uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest
```
