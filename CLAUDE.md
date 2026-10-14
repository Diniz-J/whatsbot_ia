# CLAUDE.md — whatsbot_ia

Instrucoes para sessoes de IA que forem trabalhar neste repo. Versionado de
proposito (como no Diario-Escolar) para que qualquer sessao siga os mesmos
padroes. Repo pode vir a ser publico: nada de senha, token, email pessoal ou
dado sensivel aqui.

## O que e

Plataforma de atendimento no WhatsApp para pequenos negocios. Um produto, dois
tiers (bot de regra e hibrido com IA) no mesmo codigo, via `Responder`
plugavel por tenant. Leia [DESIGN.md](DESIGN.md) antes de escrever qualquer app
— as decisoes e os invariantes estao la.

## Stack

- Django + DRF, Celery + Redis, PostgreSQL
- WhatsApp Cloud API (oficial)
- Django Admin + tema Unfold
- Config via variaveis de ambiente (python-decouple); ver `.env.example`

## Invariantes (nao violar — detalhe em DESIGN.md secao 5)

1. `apps/responder/` nao conhece canal nem HTTP: so objetos de dominio.
2. Preco, horario e disponibilidade vem sempre de servico/dado, nunca da cabeca
   do LLM. As tools do LLM sao as mesmas funcoes de servico que o RuleResponder
   chama. Isso tem que ter teste travando.
3. Tenant resolvido pelo `phone_number_id` do payload (webhook e URL unica).
4. Webhook idempotente: unique no `message id` da Meta, persiste cru antes de
   processar.
5. Outbound sempre pela fila (Celery).

## Convencoes de codigo

- Comentarios sempre em portugues.
- Sem emojis em codigo ou resposta.
- Separar em camadas (handler/view, service, repository/model). Logica de
  negocio em `services.py`, nao na view nem no model.
- Erros explicitos; nunca silenciar excecao. Nada de `except: pass`.
- Queries sempre pelo ORM ou parametrizadas — nunca interpolacao de string.
- `apps/responder`, `core`, `catalogo`, `reservas` nao podem depender de Django
  alem do ORM (mantem a opcao de trocar a borda sem reescrever o nucleo).
- Mudanca de schema: sempre migration versionada.
- Ao adicionar funcionalidade, considerar o teste junto; nunca remover teste
  existente sem justificativa.

## O que NAO fazer

- Nao criar repo/fork separado para o "bot de regra": e o hibrido com a flag
  em `rules`.
- Nao usar biblioteca nao-oficial de WhatsApp (Baileys/whatsmeow). So Cloud API.
- Nao construir painel admin do zero para CRUD interno: usar Django Admin +
  Unfold. Painel custom so se virar diferencial do cliente.
- Nao deixar o LLM afirmar preco/disponibilidade por conta propria.
- Nao atribuir autoria de IA em nada: README, docs, comentarios, mensagens de
  commit, corpo de PR. Sem trailer `Co-Authored-By`. O credito e do Diniz.

## Git

- Mensagens de commit em ingles, descritivas.
- Um commit por mudanca logica, nao por arquivo.
- Confirmar antes de cada push.
