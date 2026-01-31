"""
URL configuration for myproject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
# from two_factor.urls import urlpatterns as tf_urls # Importar URLs de 2FA (COMENTADO)

urlpatterns = [
    # CAMBIO DE SEGURIDAD: URL de admin personalizada
    path('gestion-segura/', admin.site.urls),
    
    # Rutas de autenticación de allauth (deben ir PRIMERO)
    path('accounts/', include('allauth.urls')),
    
    # Rutas de 2FA (Ahora bajo 'security/' para evitar conflictos) (COMENTADO)
    # path('', include(tf_urls)),
    
    # PayPal IPN
    path('paypal/', include('paypal.standard.ipn.urls')),

    path('', include('myapp.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
