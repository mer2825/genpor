import re
from django.conf import settings

class HtmlMinificationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Solo minificar si:
        # 1. Estamos en producción (DEBUG=False)
        # 2. El contenido es HTML
        # 3. No hubo errores (status 200)
        if not settings.DEBUG and 'text/html' in response.get('Content-Type', '') and response.status_code == 200:
            content = response.content.decode('utf-8')
            response.content = self.minify_html(content)

        return response

    def minify_html(self, html):
        # Eliminar comentarios HTML (<!-- ... -->)
        html = re.sub(r'<!--(?!\[if).*?-->', '', html, flags=re.DOTALL)
        
        # Eliminar espacios entre etiquetas (> <)
        html = re.sub(r'>\s+<', '><', html)
        
        # Eliminar espacios extra al inicio y final de líneas
        html = re.sub(r'\n\s+', ' ', html)
        html = re.sub(r'\s+\n', ' ', html)
        
        return html.strip()
