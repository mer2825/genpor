from django.dispatch import receiver
from paypal.standard.models import ST_PP_COMPLETED
from paypal.standard.ipn.signals import valid_ipn_received
from .models import PaymentTransaction, ClientProfile, UserSubscription, SubscriptionPlan, TokenSettings
from django.contrib.auth.models import User
import logging
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

@receiver(valid_ipn_received)
def payment_notification(sender, **kwargs):
    ipn_obj = sender
    
    # --- LOGICA PARA SUSCRIPCIONES ---
    if ipn_obj.txn_type in ['subscr_signup', 'subscr_payment', 'subscr_cancel', 'subscr_eot', 'subscr_failed']:
        handle_subscription_ipn(ipn_obj)
        return

    # --- LOGICA PARA PAGOS UNICOS (TOKENS) ---
    if ipn_obj.payment_status == ST_PP_COMPLETED:
        # El pago fue exitoso
        try:
            # El 'custom' field debe contener el ID de nuestra PaymentTransaction
            transaction_id = ipn_obj.custom
            
            # Verificar si es un UUID válido (para evitar errores si llega basura)
            try:
                transaction = PaymentTransaction.objects.get(id=transaction_id)
            except (ValueError, PaymentTransaction.DoesNotExist):
                # Si no es una transacción de tokens, podría ser otra cosa, ignoramos
                return

            # Verificar que el monto coincida
            if transaction.amount == ipn_obj.mc_gross:
                # Marcar transacción como completada
                transaction.status = 'COMPLETED'
                transaction.paypal_transaction_id = ipn_obj.txn_id
                transaction.save()
                
                # Acreditar tokens al usuario
                user_profile, _ = ClientProfile.objects.get_or_create(user=transaction.user)
                
                tokens_to_add = transaction.package.tokens
                user_profile.bonus_tokens += tokens_to_add
                user_profile.save()
                
                logger.info(f"Pago exitoso: {tokens_to_add} tokens añadidos a {transaction.user.username}")
            else:
                logger.warning(f"Pago recibido pero monto incorrecto. Esperado: {transaction.amount}, Recibido: {ipn_obj.mc_gross}")
                
        except Exception as e:
            logger.error(f"Error procesando pago IPN (Tokens): {e}")

def handle_subscription_ipn(ipn_obj):
    # El custom field trae el user_id
    user_id = ipn_obj.custom
    try:
        # Asegurarse de que user_id sea un entero válido
        user_id = int(user_id)
        user = User.objects.get(id=user_id)
        sub, created = UserSubscription.objects.get_or_create(user=user)
    except (ValueError, User.DoesNotExist):
        logger.error(f"Usuario no encontrado o ID inválido para suscripción IPN: {ipn_obj.custom}")
        return

    if ipn_obj.txn_type == 'subscr_signup':
        # Suscripción iniciada (pero el pago real viene en subscr_payment)
        sub.paypal_sub_id = ipn_obj.subscr_id
        sub.status = 'PENDING' # Esperamos el primer pago
        sub.save()
        logger.info(f"Suscripción iniciada para {user.username}")

    elif ipn_obj.txn_type == 'subscr_payment':
        # Pago recurrente recibido (o el primero)
        if ipn_obj.payment_status == ST_PP_COMPLETED:
            sub.paypal_sub_id = ipn_obj.subscr_id # Asegurar ID
            sub.status = 'ACTIVE'
            sub.last_payment_date = timezone.now()
            
            # Intentamos deducir el plan por el monto si no lo tenemos vinculado aún
            if not sub.plan:
                try:
                    plan = SubscriptionPlan.objects.get(price=ipn_obj.mc_gross)
                    sub.plan = plan
                except SubscriptionPlan.DoesNotExist:
                    logger.error(f"No se encontró plan para el monto {ipn_obj.mc_gross}")
            
            if sub.plan:
                # Otorgar beneficios (Tokens mensuales)
                profile, _ = ClientProfile.objects.get_or_create(user=user)
                
                # --- CORRECCIÓN: SUMAR TOKENS DIRECTAMENTE ---
                # Antes se reseteaba, ahora se acumula.
                tokens_to_add = sub.plan.tokens_per_period
                profile.bonus_tokens += tokens_to_add
                
                # Opcional: Resetear el uso si es un nuevo ciclo, o dejarlo.
                # Generalmente en suscripciones se resetea el uso mensual, pero si quieres acumular todo:
                # profile.tokens_used = 0  <-- Descomenta si quieres que el contador de uso vuelva a 0 cada mes
                
                profile.last_reset_date = timezone.now()
                profile.save()
                
                # Calcular siguiente pago
                if sub.plan.billing_period_unit == 'M':
                    sub.next_payment_date = timezone.now() + timedelta(days=30 * sub.plan.billing_period)
                elif sub.plan.billing_period_unit == 'D':
                    sub.next_payment_date = timezone.now() + timedelta(days=sub.plan.billing_period)
                elif sub.plan.billing_period_unit == 'W':
                    sub.next_payment_date = timezone.now() + timedelta(weeks=sub.plan.billing_period)
                elif sub.plan.billing_period_unit == 'Y':
                    sub.next_payment_date = timezone.now() + timedelta(days=365 * sub.plan.billing_period)
            
            sub.save()
            logger.info(f"Pago de suscripción procesado para {user.username}. Se añadieron {tokens_to_add} tokens.")

    elif ipn_obj.txn_type == 'subscr_cancel':
        sub.status = 'CANCELLED'
        sub.save()
        logger.info(f"Suscripción cancelada para {user.username}")

    elif ipn_obj.txn_type == 'subscr_eot':
        sub.status = 'EXPIRED'
        # Quitar beneficios (volver a free tier)
        # NOTA: Si quieres que mantengan los tokens que ya pagaron, comenta estas líneas.
        # Si quieres que al expirar pierdan el "bonus", déjalas.
        # Por ahora, asumo que si pagaron, se quedan con los tokens hasta gastarlos.
        # profile, _ = ClientProfile.objects.get_or_create(user=user)
        # profile.bonus_tokens = 0 
        # profile.save()
        
        sub.save()
        logger.info(f"Suscripción expirada para {user.username}")

    elif ipn_obj.txn_type == 'subscr_failed':
        logger.warning(f"Pago de suscripción fallido para {user.username}")
