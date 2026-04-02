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
from django_ratelimit.decorators import ratelimit
from allauth.account import views as allauth_views

# 🛡️ Proteger vistas críticas (Fuerza Bruta y Spam)
# Limitar a 5 intentos de login cada 5 minutos por IP
login_ratelimited = ratelimit(key='ip', rate='5/5m', block=True)(allauth_views.login)
# Limitar a 3 registros cada 10 minutos por IP
signup_ratelimited = ratelimit(key='ip', rate='3/10m', block=True)(allauth_views.signup)

urlpatterns = [
    # CAMBIO DE SEGURIDAD: URL de admin personalizada
    path('gestion-segura/', admin.site.urls),
    
    # Interceptar URLs de login y registro de allauth con nuestro rate limit ANTES del include
    path('accounts/login/', login_ratelimited, name='account_login'),
    path('accounts/signup/', signup_ratelimited, name='account_signup'),

    # Rutas de autenticación de allauth
    path('accounts/', include('allauth.urls')),
    
    # Rutas de 2FA (Ahora bajo 'security/' para evitar conflictos) (COMENTADO)
    # path('', include(tf_urls)),
    
    # PayPal IPN
    path('paypal/', include('paypal.standard.ipn.urls')),

    path('', include('myapp.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
