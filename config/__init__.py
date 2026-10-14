# Importar o app do Celery aqui garante que ele exista assim que o Django
# carregar, para que o decorator @shared_task encontre o broker configurado.
from config.celery import app as celery_app

__all__ = ("celery_app",)
