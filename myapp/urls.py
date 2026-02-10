from django.urls import path
from . import views

urlpatterns = [
    path('', views.generate_image_view, name='generate_image'),
    path('workspace/', views.workspace_view, name='workspace'),
    path('gallery/', views.gallery_view, name='gallery'),
    path('profile/', views.profile_view, name='profile'), # NUEVA RUTA
    path('delete-images/', views.delete_images_view, name='delete_images'),
    # Nueva ruta específica para archivos privados
    path('private-media/<path:path>', views.serve_private_media, name='serve_private_media'),
    
    # Rutas para gestión de chat
    path('delete-message/', views.delete_message_view, name='delete_message'),
    path('clear-chat/', views.clear_chat_history_view, name='clear_chat'),
    
    # Ruta para canjear cupones
    path('redeem-coupon/', views.redeem_coupon_view, name='redeem_coupon'),
    
    # Ruta para actualizar username
    path('update-username/', views.update_username_view, name='update_username'),

    # Rutas de Pagos (PayPal)
    path('tokens/', views.token_packages, name='token_packages'),
    path('payment/process/<int:package_id>/', views.payment_process, name='payment_process'),
    path('payment/done/', views.payment_done, name='payment_done'),
    path('payment/canceled/', views.payment_canceled, name='payment_canceled'),

    # Rutas de Stripe (Pagos Únicos)
    path('payment/stripe/create-checkout-session/<int:package_id>/', views.create_checkout_session, name='create_checkout_session'),
    
    # Rutas de Stripe (Suscripciones)
    path('subscription/stripe/create-checkout-session/<int:plan_id>/', views.create_subscription_checkout_session, name='create_subscription_checkout_session'),

    # Rutas de Suscripciones (PayPal)
    path('subscriptions/', views.subscription_plans, name='subscription_plans'),
    path('subscription/process/<int:plan_id>/', views.subscription_process, name='subscription_process'),
    path('subscription/done/', views.subscription_done, name='subscription_done'),
    path('subscription/canceled/', views.subscription_canceled, name='subscription_canceled'),
]
