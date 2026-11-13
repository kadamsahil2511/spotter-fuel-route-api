from django.apps import AppConfig
from django.db.backends.signals import connection_created


class FuelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fuel"

    def ready(self) -> None:
        def _enable_wal(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA journal_mode=WAL;")

        connection_created.connect(_enable_wal, dispatch_uid="fuel-sqlite-wal")
