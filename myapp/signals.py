from django.dispatch import receiver
from paypal.standard.models import ST_PP_COMPLETED
from paypal.standard.ipn.signals import valid_ipn_received
from .models import PaymentTransaction, ClientProfile
from django.contrib.auth.models import User
import logging

logger = logging.getLogger(__name__)

@receiver(valid_ipn_received)
def payment_notification(sender, **kwargs):
    ipn_obj = sender
    if ipn_obj.payment_status == ST_PP_COMPLETED:
        # El pago fue exitoso
        try:
            # El 'custom' field debe contener el ID de nuestra PaymentTransaction
            transaction_id = ipn_obj.custom
            transaction = PaymentTransaction.objects.get(id=transaction_id)
            
            # Verificar que el monto coincida
            if transaction.amount == ipn_obj.mc_gross:
                # Marcar transacción como completada
                transaction.status = 'COMPLETED'
                transaction.paypal_transaction_id = ipn_obj.txn_id
                transaction.save()
                
                # Acreditar tokens al usuario
                user_profile = ClientProfile.objects.get(user=transaction.user)
                # Sumamos los tokens como "bonus_tokens" para que no se pierdan en el reset mensual (opcional, depende de tu lógica)
                # O simplemente restamos de 'tokens_used' si quieres que cuenten como cupo normal, 
                # pero lo mejor para compras es aumentar el límite o dar bonus.
                # Aquí asumiremos que se añaden a 'bonus_tokens'.
                
                tokens_to_add = transaction.package.tokens
                user_profile.bonus_tokens += tokens_to_add
                user_profile.save()
                
                logger.info(f"Pago exitoso: {tokens_to_add} tokens añadidos a {transaction.user.username}")
            else:
                logger.warning(f"Pago recibido pero monto incorrecto. Esperado: {transaction.amount}, Recibido: {ipn_obj.mc_gross}")
                
        except PaymentTransaction.DoesNotExist:
            logger.error(f"Transacción no encontrada para IPN: {ipn_obj.custom}")
        except Exception as e:
            logger.error(f"Error procesando pago IPN: {e}")
