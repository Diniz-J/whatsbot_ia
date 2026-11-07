from django.apps import AppConfig


class CanalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.canal"
    verbose_name = "Canal"

    def ready(self):
        # Registra as checagens de credencial do webhook. Import no `ready`
        # porque e o unico momento em que o registro e garantido uma vez.
        from apps.canal import checks  # noqa: F401
