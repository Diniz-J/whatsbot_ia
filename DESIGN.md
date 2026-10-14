# DESIGN — whatsbot_ia

Documento de arquitetura. Explica as decisoes e os invariantes que o codigo
precisa respeitar. Leitura obrigatoria antes de escrever qualquer app.

## 1. Objetivo e forma do produto

Atendimento automatizado no WhatsApp para pequenos negocios, vendido como
servico com mensalidade por cliente. O valor esta no **inbound** (cliente
inicia a conversa): responder cardapio/horario, confirmar reserva/pedido. Isso
e barato e legitimo na API oficial. Broadcast de marketing diario fica fora do
nucleo do produto — e caro e arriscado (ver secao 6).

**Um produto, dois tiers, um repo.** A diferenca entre "bot de regra" e
"hibrido com IA" e um unico componente trocavel (o `Responder`), selecionado
pela configuracao do tenant. Nao existe repo separado nem fork.

## 2. Decisoes de stack

- **Django + DRF** (e nao FastAPI). O criterio e o back-office: o dono do
  negocio precisa mexer em cardapio, preco, horario, mesas e nas respostas de
  regra. O Django Admin entrega esse CRUD em horas; construir painel proprio
  queimaria a margem (mensalidade de R$80-200/cliente). Async nao e requisito:
  o webhook valida HMAC, persiste o evento cru e devolve 200 em milissegundos;
  todo o resto vai para a fila, entao nenhuma request fica pendurada em I/O.
- **Celery + Redis** para outbound e processamento de inbound — e onde moram
  retry, rate limit da Meta e registro de custo.
- **PostgreSQL**.
- **Tema Unfold** no admin (o unico apresentavel para o cliente).
- **LLM:** camada isolada em `apps/responder/llm/`. O sinal de MLOps do
  portfolio vem daqui (eval, tracing, custo por tenant/token, fila com retry),
  nao do framework HTTP.

Se um dia virar FastAPI, so a **borda** muda (`config/`, admin, ORM). Os apps
`responder`, `core`, `catalogo` e `reservas` ficam identicos — por isso eles
nao podem depender de Django alem do ORM.

## 3. Mapa de camadas

```
config/              settings (via python-decouple), urls, celery.py, asgi/wsgi
apps/
  common/            TimeStampedModel, BaseModelTenant, mixins de escopo, permissions
  tenants/           Tenant + Configuracao (responder=rules|llm, credenciais WABA, plano)
  canal/             adaptador de canal (a borda externa)
    base.py          interface: enviar_texto, enviar_template, normalizar_evento
    whatsapp/        client.py, webhook.py (HMAC), templates.py, status.py
    fake.py          canal em memoria para dev/teste (nao toca na Meta)
  conversas/         Contato, Conversa, Mensagem, EventoWebhook, janela de 24h
  core/              orquestracao
    pipeline.py      evento -> tenant -> responder -> canal
    tasks.py         celery: processar_inbound, enviar_outbound
  responder/         o plugavel
    base.py          Responder.handle(msg, ctx) -> Resposta
    rules.py         RuleResponder
    llm/             prompt.py, tools.py, client.py, guardrails.py
    registry.py      resolve o Responder pela Configuracao do tenant
  catalogo/          cardapio, itens, preco, horario de funcionamento (a verdade)
  reservas/          reserva/pedido + disponibilidade
  metricas/          custo por mensagem e por token, por tenant
tests/
```

## 4. O contrato do Responder

```
MensagemEntrada + Contexto  ->  Responder.handle  ->  Resposta
```

- **Entrada:** `MensagemEntrada` (texto do cliente) + `Contexto` (tenant,
  historico da conversa, acesso ao catalogo/servicos).
- **Saida:** `Resposta` (texto a enviar + acoes pedidas, ex: criar reserva).
- `responder/` **nao conhece canal nem HTTP**. Recebe objeto, devolve objeto.
  E isso que torna "bot de regra = hibrido com LLM desligado" verdade no
  codigo, nao so no slide.

## 5. Invariantes (o que o codigo nao pode violar)

1. **`responder/` sem canal nem HTTP.** So objetos de dominio entram e saem.
2. **Tools do LLM = funcoes de servico que o RuleResponder tambem chama.**
   `catalogo/services.py` e `reservas/services.py` tem um dono e dois
   consumidores. Se o LLM e a regra tiverem caminhos proprios para consultar
   preco, o anti-alucinacao vira teatro. Preco, horario e disponibilidade
   **nunca** saem da cabeca do modelo — sempre de servico/dado. Isso precisa
   de teste travando.
3. **Uma instancia por cliente, e o `phone_number_id` confere o payload.**
   Cada cliente roda a propria stack com o proprio banco (secao 8), entao o
   webhook dele tem URL propria e nao ha tenant a resolver. O
   `phone_number_id` do payload serve de conferencia: se nao for o do cliente
   daquela instancia, a entrega e recusada em vez de gravada. Sem essa
   conferencia, quem descobrisse a URL conseguiria injetar conversa de outro
   numero.
4. **Webhook idempotente.** A Meta reenvia; a chave e o `message id` dela, com
   unique no banco. Persiste cru primeiro, processa depois — se o LLM cair, o
   evento nao se perde.
5. **Outbound sempre pela fila** (Celery). E la que moram retry, rate limit da
   Meta e o registro de custo por mensagem. A decisao de template (janela
   fechada) vs texto livre e regra de canal, mora em `canal/whatsapp`,
   consultando `Conversa.janela_aberta()`.

## 6. WhatsApp Cloud API (oficial) — mecanica que o codigo assume

- **Janela de 24h:** texto livre so dentro de 24h desde a ultima mensagem do
  cliente. Fora disso, so **template** pre-aprovado (HSM).
- **Categorias de template:** utility (transacional, barato) vs marketing
  (promo, caro). A Meta decide a categoria no review — nao da para rotular
  promo como utility para pagar menos.
- **Webhook:** recebe status (sent/delivered/read/failed) e mensagens de
  entrada. Validar `X-Hub-Signature-256` (HMAC com o app secret) antes de
  confiar no payload.
- **Via oficial apenas.** Nada de bibliotecas nao-oficiais (Baileys/whatsmeow):
  risco de ban e de suporte infinito.
- **Status/stories nao tem API oficial** — fora do escopo do produto.

## 7. Custo (modelo mental, para a camada de metricas)

- Cobranca por mensagem entregue. BR: utility barato, marketing ~9x mais caro,
  service (resposta inbound na janela de 24h) gratis.
- Token de LLM e baixo e **repassavel ao cliente** (embutido no plano).
- Base de conhecimento pequena (ex: cardapio) **nao precisa de vector DB** —
  vai no proprio prompt. So justifica retrieval quando a base crescer.
- `apps/metricas/` registra custo por mensagem e por token, por tenant — base
  do faturamento e do controle de margem.

## 8. Topologia de deploy e isolamento

Decidido em out/2026: **uma stack e um banco por cliente, na mesma VPS**. O
dado sensivel do cliente mora no Postgres, entao o isolamento que importa e o
de banco, nao o de kernel.

| | Isolamento | Infra | Operacao |
|---|---|---|---|
| A — instancia compartilhada, escopo por linha | fraco: um bug de escopo vaza todos | 1 VPS | 1 host, 1 banco, 1 deploy |
| **B — 1 stack + 1 banco por cliente, mesma VPS** | **forte no dado** | **1 VPS** | **1 host, N bancos, 1 script** |
| C — 1 VPS por cliente | maximo | N VPS | N hosts, N backups, N deploys |

**A esta fora** porque um bug de escopo custaria o dado de todos os clientes
de uma vez. **C nao e o default** porque operacao e o recurso escasso deste
projeto, e nao dinheiro: N VPS sao N sistemas para atualizar, N certificados e
N restores para testar, e uma VPS esquecida sem patch e pior que um banco
separado num host mantido. Em B ha um proxy reverso, um certificado wildcard e
N upstreams. C fica como tier premium para quem exigir servidor dedicado
(clinica, escola: dado de saude ou de menor) — ai e preco, nao arquitetura.

Whitelabel nao entra nessa conta: marca por cliente (nome, logo, cor no admin)
e ortogonal a topologia e funciona em qualquer uma das tres.

**O FK de tenant continua, com uma linha por instancia.** Parece
contraintuitivo em single-tenant e e deliberado:

- Remover agora sai barato; readicionar depois exige tocar todo modelo de
  dominio, com migration. O risco e de mao unica.
- Com a linha no banco, a `Configuracao` fica no banco, e trocar `rules` por
  `llm` segue sendo um campo no admin. Como variavel de ambiente, voltaria a
  ser deploy.
- Em desenvolvimento, varios clientes falsos no mesmo banco facilitam o teste.

O `TenantScopedModelAdmin` segue valendo pelo mesmo motivo: com uma linha ele
nao faz nada, e no dia em que uma instancia tiver duas ele e a diferenca entre
separacao e vazamento.

**Consequencias que o codigo assume:**

- Cada cliente tem URL de webhook propria; o `phone_number_id` e conferencia,
  nao roteamento (secao 5, invariante 3).
- Credenciais WABA ficam no banco do cliente, nunca num `.env` compartilhado.
- Backup e por banco, com restore testado por cliente.
- Rota do admin configuravel no `urls.py` (nao deixar no `/admin/` padrao);
  proteger com 2FA, rate limit e, se possivel, allowlist de IP.

**O que ainda nao existe:** template de stack por cliente, provisionamento,
migrate e rotacao de secret automatizados. Sem isso, B nao chega no terceiro
cliente — e essa automacao e o trabalho real que a escolha de B cria.
