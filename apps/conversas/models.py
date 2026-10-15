"""Quem fala com o cliente, o fio da conversa e a entrega crua da Meta."""

from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.common.models import BaseModelTenant

# A Cloud API so aceita texto livre dentro de 24h desde a ultima mensagem do
# cliente. Fora disso, somente template aprovado (DESIGN.md secao 6).
JANELA_SERVICO = timedelta(hours=24)


class Contato(BaseModelTenant):
    """Pessoa do outro lado. Identidade vem do telefone, que a Meta chama wa_id."""

    telefone = models.CharField("telefone", max_length=20)
    nome_perfil = models.CharField("nome no perfil", max_length=120, blank=True)

    class Meta:
        verbose_name = "contato"
        verbose_name_plural = "contatos"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "telefone"], name="contato_unico_por_telefone"
            )
        ]

    def __str__(self):
        return self.nome_perfil or self.telefone


class Conversa(BaseModelTenant):
    """Fio de conversa com um contato.

    Guarda `ultima_entrada_em` porque e dela que sai a janela de 24h, e a
    janela decide se o envio pode ser texto livre ou precisa de template.
    """

    contato = models.ForeignKey(
        Contato,
        on_delete=models.PROTECT,
        related_name="conversas",
        verbose_name="contato",
    )
    ultima_entrada_em = models.DateTimeField("ultima mensagem do cliente", null=True, blank=True)

    class Meta:
        verbose_name = "conversa"
        verbose_name_plural = "conversas"
        ordering = ["-ultima_entrada_em"]
        constraints = [
            # Uma conversa por contato. Sem isto, duas tarefas da mesma
            # entrega inserem duplicata e todo `get_or_create` seguinte passa
            # a levantar MultipleObjectsReturned, para sempre.
            models.UniqueConstraint(fields=["tenant", "contato"], name="conversa_unica_por_contato")
        ]

    def __str__(self):
        return f"Conversa com {self.contato}"

    def janela_aberta(self, agora=None):
        """Verdadeiro quando ainda cabe texto livre, sem template."""
        if self.ultima_entrada_em is None:
            return False
        return (agora or timezone.now()) - self.ultima_entrada_em < JANELA_SERVICO


class Mensagem(BaseModelTenant):
    """Uma mensagem do fio, em qualquer direcao."""

    class Direcao(models.TextChoices):
        ENTRADA = "entrada", "Recebida"
        SAIDA = "saida", "Enviada"

    class Status(models.TextChoices):
        """Ciclo de vida de uma mensagem de saida.

        Mensagem de entrada nasce `recebida` e fica. A de saida nasce
        `enfileirada` antes de qualquer chamada de rede, para que uma queda no
        meio do envio deixe rastro em vez de desaparecer.
        """

        RECEBIDA = "recebida", "Recebida"
        ENFILEIRADA = "enfileirada", "Enfileirada"
        # Reservada entre o claim e a resposta da Meta. Existe para duas
        # execucoes da mesma tarefa nao mandarem a mesma mensagem duas vezes:
        # template e cobrado, e o cliente receberia em dobro.
        ENVIANDO = "enviando", "Enviando"
        ENVIADA = "enviada", "Enviada"
        FALHOU = "falhou", "Falhou"

    # CASCADE por design: a mensagem e filha da conversa e nao faz sentido
    # sozinha. E a mesma excecao que o guardrail de PROTECT prevê.
    conversa = models.ForeignKey(
        Conversa,
        on_delete=models.CASCADE,
        related_name="mensagens",
        verbose_name="conversa",
    )
    direcao = models.CharField("direcao", max_length=10, choices=Direcao.choices)
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.RECEBIDA
    )
    id_externo = models.CharField("id na Meta", max_length=128, blank=True)
    tipo = models.CharField("tipo", max_length=30, default="text")
    texto = models.TextField("texto", blank=True)
    template = models.CharField("template", max_length=120, blank=True)
    erro = models.TextField("erro do envio", blank=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["criado_em"]
        constraints = [
            # Garantia de nao duplicar no banco, e nao so no servico. Parcial
            # porque mensagem de saida nasce sem id da Meta, e varias string
            # vazia colidiriam num unique comum.
            models.UniqueConstraint(
                fields=["tenant", "id_externo"],
                condition=~models.Q(id_externo=""),
                name="mensagem_unica_por_id_externo",
            )
        ]

    def __str__(self):
        return f"{self.get_direcao_display()}: {self.texto[:40]}"


class EventoWebhook(BaseModelTenant):
    """Entrega da Meta gravada crua, antes de qualquer processamento.

    Existe por causa do invariante 4: a Meta reenvia a mesma mensagem quando
    nao recebe 200 rapido, e o `id_mensagem` unico faz o reenvio virar no-op
    em vez de mensagem duplicada. Gravar antes de processar tambem garante que
    uma falha no processamento nao perca a entrega.
    """

    id_mensagem = models.CharField("id da mensagem", max_length=128)
    payload = models.JSONField("payload cru")
    processado_em = models.DateTimeField("processado em", null=True, blank=True)

    class Meta:
        verbose_name = "evento de webhook"
        verbose_name_plural = "eventos de webhook"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "id_mensagem"], name="evento_unico_por_id_mensagem"
            )
        ]

    def __str__(self):
        return self.id_mensagem
