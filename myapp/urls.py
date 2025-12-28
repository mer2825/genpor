from django.urls import path
from . import views

urlpatterns = [
    path('', views.generate_image_view, name='generate_image'),
    path('workspace/', views.workspace_view, name='workspace'),
    path('gallery/', views.gallery_view, name='gallery'),
    path('delete-images/', views.delete_images_view, name='delete_images'),
    # Nueva ruta específica para archivos privados
    path('private-media/<path:path>', views.serve_private_media, name='serve_private_media'),
]
