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

Esqueleto. Estrutura de pastas e documentacao de arquitetura prontas; o codigo
dos apps ainda nao foi escrito.

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

Configuracao via variaveis de ambiente — copie `.env.example` para `.env` e
preencha. As credenciais WABA de cada cliente ficam no banco (por tenant), nao
no `.env`.
