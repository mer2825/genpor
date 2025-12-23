from django.urls import path
from . import views

urlpatterns = [
    path('', views.generate_image_view, name='generate_image'),
    # Nueva ruta específica para archivos privados
    path('private-media/<path:path>', views.serve_private_media, name='serve_private_media'),
]
