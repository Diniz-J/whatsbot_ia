"""Bases de admin compartilhadas: escopo por cliente e area so da operacao."""

from django.core.exceptions import FieldDoesNotExist, PermissionDenied
from unfold.admin import ModelAdmin


class OperacaoModelAdmin(ModelAdmin):
    """Admin visivel apenas para a operacao da plataforma (superusuario).

    Nega no codigo em vez de so deixar de conceder a permissao no grupo: tier
    de atendimento e token de WhatsApp nao podem vazar para o cliente por
    descuido de configuracao.
    """

    def has_module_permission(self, request):
        return bool(request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_add_permission(self, request):
        return bool(request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_superuser)


class TenantScopedModelAdmin(ModelAdmin):
    """Admin de modelo escopado por cliente.

    O usuario de um cliente so le e escreve dentro do proprio tenant. O campo
    `tenant` sai do formulario dele e e preenchido no servidor: se ficasse
    editavel, bastaria trocar o select para mover o registro para outro
    cliente.
    """

    campo_tenant = "tenant"

    def get_form(self, request, obj=None, change=False, **kwargs):
        """Preenche o cliente na instancia ANTES da validacao.

        O `save_model` abaixo tambem grava o campo, mas ele roda depois de o
        formulario validar — e tarde para qualquer modelo que confira escopo no
        proprio `clean`. Com o campo fora do formulario, a instancia chega a
        validacao com `tenant` nulo, e uma checagem como "esta mesa pertence a
        outro cliente?" compara contra nulo e recusa sempre: o cliente nao
        conseguia gravar nada. Preenchido aqui, o `clean` enxerga o dono certo
        e continua pegando a FK de verdade cruzada.
        """
        formulario = super().get_form(request, obj, change=change, **kwargs)
        if request.user.is_superuser or not request.user.tenant_id:
            # Sem cliente definido nao se adivinha um: o `save_model` recusa a
            # gravacao e o `get_queryset` nao mostra nada. Falha fechada.
            return formulario

        campo = f"{self.campo_tenant}_id"
        tenant_id = request.user.tenant_id

        class FormularioComCliente(formulario):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                if not getattr(self.instance, campo, None):
                    setattr(self.instance, campo, tenant_id)

        return FormularioComCliente

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if not request.user.tenant_id:
            # Falha fechada: sem cliente definido, nao ha nada para ver.
            return qs.none()
        return qs.filter(**{self.campo_tenant: request.user.tenant_id})

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if not request.user.is_superuser and self.campo_tenant not in exclude:
            exclude.append(self.campo_tenant)
        return exclude

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            if not request.user.tenant_id:
                raise PermissionDenied("Usuario sem cliente nao pode gravar.")
            setattr(obj, f"{self.campo_tenant}_id", request.user.tenant_id)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Restringe toda FK para modelo escopado ao cliente do usuario.

        O `save_model` acima resolve o escopo do registro que se grava, mas
        nao o do que ele referencia: a FK vem do formulario, e um POST
        forjado com o id de uma linha do vizinho criaria um registro que
        atravessa o escopo. Limitar o queryset do campo recusa isso na
        validacao e, de quebra, deixa o autocomplete so com o que e do
        cliente.

        Generico de proposito. Resolvido campo a campo, cada admin novo com
        FK nasce com o furo outra vez e a protecao passa a depender de
        alguem lembrar.
        """
        if not request.user.is_superuser and _escopado_por_tenant(db_field.remote_field.model):
            # Sem cliente definido o queryset fica vazio, nao global: mesma
            # falha fechada do `get_queryset`.
            kwargs["queryset"] = db_field.remote_field.model._default_manager.filter(
                tenant_id=request.user.tenant_id
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


def _escopado_por_tenant(modelo) -> bool:
    """Se o modelo tem dono, e portanto nao pode ser referenciado cruzando escopo.

    Checa o campo e nao a heranca de `BaseModelTenant` de proposito: o
    `Usuario` tambem tem `tenant` sem herdar dela, e uma FK para ele sofre do
    mesmo problema.
    """
    try:
        modelo._meta.get_field("tenant")
    except FieldDoesNotExist:
        return False
    return True
