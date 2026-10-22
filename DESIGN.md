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

Como ficou (out/2026):

- **Volume nao tem ledger proprio.** Direcao, categoria de cobranca e token
  gasto ja estao em `conversas.Mensagem`, e o resumo e derivado dela. Um
  contador paralelo seria uma segunda versao da mesma verdade, para divergir
  no primeiro bug de escrita.
- **Preco vem de tabela datada (`TabelaPreco`), nao de configuracao.** E a
  unica parte do custo que o historico nao sabe: a mensagem guarda a categoria
  que a Meta cobrou, nunca quanto ela custava naquele mes. Com preco unico em
  env, um reajuste reescreveria o custo de todos os meses ja faturados. Uma
  linha e a tabela inteira (mensagem e token), porque as duas unidades chegam
  no mesmo reajuste.
- **Sem tabela o custo e indefinido, nao zero.** Mes que nao custou nada e mes
  cujo preco ninguem cadastrou sao respostas diferentes.
- **Tres destinos de mensagem enviada, nunca dois:** cobrada, entregue de
  graca (`cobravel=False`, a resposta na janela de 24h) e ainda nao
  precificada pela Meta. Juntar os dois ultimos e o jeito de inventar custo.
- **A tabela de preco e da operacao, nao do cliente.** E o que a plataforma
  paga a Meta e a Anthropic; editavel pelo cliente, o custo faturado seria
  reescrito por quem o paga.
- Pendente: reajuste no meio do periodo nao parte o calculo em dois (o periodo
  usa a tabela vigente no inicio dele), e nao ha teto de gasto por cliente.

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
- **`manage.py check --deploy` e passo de subida**, nao enfeite. As duas
  credenciais do webhook nascem vazias e falham fechado: sem elas o sistema
  sobe, atende o admin, e recusa toda entrega da Meta com 403 e uma linha de
  WARNING, ate a Meta desativar a subscription por acumulo de erro. Falhar
  fechado e certo; o que faltava era alguem dizer o porque. Fica como checagem
  de deploy (`canal.E001`/`E002`) e nao comum porque em dev e na suite as duas
  estao vazias de proposito. O hardening de HTTPS/HSTS/cookie nao tem checagem
  nossa: o Django ja cobre em `security.W004`/`W008`/`W012`/`W016`, no mesmo
  `--deploy` — ali o que faltava nao era a checagem, era rodar.

**O que ainda nao existe:** template de stack por cliente, provisionamento,
migrate e rotacao de secret automatizados. Sem isso, B nao chega no terceiro
cliente — e essa automacao e o trabalho real que a escolha de B cria.

## 9. Reservas e disponibilidade

Decidido em out/2026, junto da app `reservas`.

- **Disponibilidade e o terceiro fato do invariante 2**, ao lado de preco e
  horario. `reservas/services.py` e o dono, com os dois consumidores de
  sempre: o `RuleResponder` e as tools do LLM.
- **Uma tabela so para reserva do bot e reserva do balcao.** Disponibilidade
  so e verdade se a que a equipe anota no back-office contar igual; duas
  tabelas fariam o bot vender mesa ja prometida no telefone.
- **Sobreposicao e recusada pelo banco**, com `ExclusionConstraint` sobre
  `tstzrange(inicio, fim, '[)')` e igualdade de mesa (extensao `btree_gist`).
  Checagem em Python nao resolve: dois workers gravando ao mesmo tempo passam
  por qualquer validacao feita antes do INSERT. A validacao no `clean` fica
  para o formulario ter erro de campo em vez de `IntegrityError`.
- **O fim fica gravado, nao derivado.** `DURACAO_PADRAO` preenche quando
  ninguem informa, e mudar o padrao amanha nao reescreve o que ja foi
  anotado.
- **Cancelada e nao-compareceu liberam a mesa; compareceu continua
  segurando.** Por isso a disponibilidade filtra por `STATUS_OCUPAM` em vez
  de contar toda linha.
- **Fechado e lotado sao respostas separadas** (`Disponibilidade.aberto` ao
  lado de `mesas`), porque sao motivos diferentes para quem perguntou e o
  modelo escolheria um por conta propria.
- **O prompt carrega o agora do estabelecimento.** O cliente escreve "amanha
  as 20h" e a ferramenta recebe data absoluta; sem esse dado o modelo
  converteria a partir do que acha que e hoje, que e o tipo de fato que ele
  nao pode tirar da cabeca.

**O bot fecha reserva** (fatia de out/2026, depois do historico da secao 10).
O tier de regra continua sem poder fechar — casa palavra-chave numa mensagem
isolada, nao coleta dia nem numero de pessoas; quem fecha e o hibrido.

- **A confirmacao e a resposta normal do atendimento, dentro da janela de
  24h**: texto livre, que a Meta nao cobra. Decisao tomada junto da fatia.
  **Nao ha template e nao ha lembrete na vespera** — lembrete seria template,
  e template e cobrado (secao 7). Quando valer a pena, e fatia propria com
  custo proprio.
- **A disponibilidade e reconferida no fechamento, nunca assumida.** O modelo
  pode pedir sem ter consultado, ou ter consultado antes de a mesa ser tomada.
  E o invariante 2 no caminho que mais doi: afirmar reserva feita sem isso
  termina com o cliente na porta no sabado.
- **Fechar duas vezes nao gasta duas mesas.** Reserva do mesmo contato que se
  sobrepoe ao intervalo pedido e devolvida em vez de criada. O criterio e
  sobreposicao e nao igualdade: quem tem 20h e pede 20h30 quer a mesma noite.
  Sem isso, o modelo chamando a ferramenta duas vezes na mesma volta sentaria
  uma pessoa em duas mesas.
- **O contato vem do contexto, nunca do modelo**, como o `tenant_id`: fosse
  argumento, o texto do cliente final poderia pedir reserva no nome de outra
  pessoa. O nome gravado sai do perfil do contato (ou do telefone), porque a
  identidade e o numero e o nome e rotulo para a equipe ler.
- **Menor mesa que serve**, porque queimar a de seis num casal custa a de
  seis.
- **Teto de reservas futuras por contato**, para o bot nao virar a ferramenta
  mais facil de bloquear a agenda da casa.
- **Cancelar e remarcar nao tem ferramenta**, e o prompt manda chamar uma
  pessoa. Agora que o bot fecha, o cliente vai pedir — sem a regra o modelo
  diria que cancelou. E a fatia seguinte obvia.

**Caso deliberado que parece defeito:** entrada que ficou mais de 24h na fila
grava a reserva e **perde a confirmacao**. O `Responder` nao conhece a janela
de proposito (e politica de canal, secao 4) e a ferramenta roda antes de o
envio ser tentado, entao a ordem nao da para inverter sem o atendimento passar
a conhecer o canal. O cliente pediu a reserva, a equipe ve no back-office e
tem o telefone; mandar por template seria gastar para entregar resposta velha.
Esta travado em teste para ser escolha e nao acidente.

**Pedido (`pedido`) nao entrou.** A secao 3 cita "reserva/pedido" na mesma
app; item, quantidade, total e entrega sao outro dominio, com outra fatia.


## 10. Historico de conversa

Decidido em out/2026. Era o limite conhecido desde a fatia do `LLMResponder`:
cada pergunta era respondida sozinha, entao o atendimento nao conseguia
perguntar nada — a resposta do cliente chegaria num turno que nao lembra da
pergunta.

- **O contexto carrega turnos, nao mensagens.** `Turno` tem autor e texto, e
  mais nada: sem status de entrega, sem id da Meta, sem modelo do ORM. E o que
  deixa o atendimento receber historico sem passar a conhecer a tabela
  (invariante 1).
- **Dois limites, porque cada um sozinho falha.** A janela de servico impede
  que a conversa da semana passada volte; o teto de turnos
  (`HISTORICO_MAX_TURNOS`) impede que um dia movimentado vire prompt gigante.
  A janela e ancorada na mensagem que esta sendo respondida, nao em `agora`:
  o fio que importa e o dela. O corte pega as mais recentes e so depois
  reordena, para o teto ficar com o fim da conversa.
- **Saida entra somente nos status que chegaram ao cliente**
  (`ENTREGUES_AO_CLIENTE`). Incluir uma que falhou faria o atendimento
  acreditar que disse algo que o cliente nunca leu, e responder em cima disso.
- **Mensagem sem texto fica fora** (template puro, audio, imagem): nao ha fala
  para reproduzir, e bloco de texto vazio a API recusa.
- **Fala inicial do atendente e descartada na traducao.** A API recusa
  conversa que comeca pelo assistente, e a janela pode abrir numa fala do
  atendente. Perder o comeco do fio e melhor que um 400 que derruba o
  atendimento inteiro.
- **O tier de regra ignora o historico**, por construcao, com teste fixando
  que a resposta dele e identica com e sem. Se passasse a depender, teria
  virado outra coisa sem ninguem pedir.

**Custo:** historico e token de entrada em toda mensagem. Dez turnos curtos
somam algumas centenas de tokens, que no modelo configurado e fracao de
centavo por atendimento — mas cresce com o teto, e e por isso que ele e
configuravel em vez de constante.

**Armadilha para quem ligar cache de prompt:** o prompt de sistema carrega o
agora do estabelecimento (secao 9, para a conversao de data), e ele muda a
cada requisicao. Cache e casamento de prefixo, e `system` vem antes de
`messages` — com a data ali, nada do prefixo casa duas vezes. Hoje nao ha
`cache_control` em lugar nenhum, entao isso nao custa nada; no dia em que
alguem ligar cache, a data tem de sair do `system` e ir para o turno do
cliente, senao o cache nunca acerta.
