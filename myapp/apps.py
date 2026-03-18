from django.apps import AppConfig
import threading
import sys

def start_crypto_monitor():
    from django.core.management import call_command
    call_command('monitor_crypto')

class MyappConfig(AppConfig):
    name = 'myapp'

    def ready(self):
        import myapp.signals
        
        # Iniciar el monitor de cripto en un hilo separado
        # Solo ejecutar si no estamos en comandos como makemigrations o migrate
        # y evitar que se ejecute dos veces por el auto-reloader de Django
        if 'runserver' in sys.argv:
            import os
            # Django runserver arranca dos procesos, uno para el reloader.
            # Solo queremos que el monitor corra en el proceso principal.
            if os.environ.get('RUN_MAIN', None) == 'true':
                monitor_thread = threading.Thread(target=start_crypto_monitor, daemon=True)
                monitor_thread.start()
                print("--- Crypto Monitor started in background thread ---")
