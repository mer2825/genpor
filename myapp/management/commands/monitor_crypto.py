import time
import requests
from decimal import Decimal
from django.core.management.base import BaseCommand
from myapp.models import PaymentTransaction, CompanySettings, ClientProfile, UserSubscription
from django.db import transaction as db_transaction
from django.utils import timezone
from datetime import timedelta

class Command(BaseCommand):
    help = 'Monitor TRC20 for USDT deposits and confirm pending payments'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting TRC20 Crypto Monitor..."))
        self.stdout.write("Waiting for USDT deposits...")

        # Guardar transacciones ya vistas en memoria
        seen_tx = set()

        while True:
            try:
                settings = CompanySettings.objects.last()

                if not settings or not settings.crypto_usdt_address or not settings.crypto_trongrid_api_key:
                    time.sleep(30)
                    continue

                API_KEY = settings.crypto_trongrid_api_key
                ADDRESS = settings.crypto_usdt_address
                MIN_CONFIRMATIONS = settings.crypto_min_confirmations

                HEADERS = {"TRON-PRO-API-KEY": API_KEY}

                # 1. Obtener bloque actual
                block_res = requests.post("https://api.trongrid.io/wallet/getnowblock", headers=HEADERS, timeout=10)
                if block_res.status_code != 200:
                    time.sleep(5)
                    continue

                block = block_res.json()
                current_block = block["block_header"]["raw_data"]["number"]

                # 2. Obtener transacciones
                url = f"https://api.trongrid.io/v1/accounts/{ADDRESS}/transactions/trc20?only_to=true&limit=50"
                r = requests.get(url, headers=HEADERS, timeout=10)
                if r.status_code != 200:
                    time.sleep(5)
                    continue

                data = r.json()
                txs = data.get("data", [])

                for tx in txs:
                    if tx["token_info"]["symbol"] != "USDT":
                        continue

                    txid = tx["transaction_id"]
                    if txid in seen_tx:
                        continue

                    if PaymentTransaction.objects.filter(crypto_tx_id=txid).exists():
                        seen_tx.add(txid)
                        continue

                    # FIX: Use Decimal for precision from the start
                    amount_decimal = Decimal(tx["value"]) / Decimal('1000000')

                    # DEBUG VISUAL PARA LA CONSOLA
                    self.stdout.write(self.style.WARNING(f"\n[!] Detectado depósito en la blockchain: {amount_decimal} USDT (TX: {txid[:8]}...)"))

                    # Buscar en la BD orden PENDIENTE con este MONTO EXACTO
                    pending_tx = PaymentTransaction.objects.filter(
                        status='PENDING',
                        crypto_amount=amount_decimal
                    ).first()

                    # DEBUG LOG for exact match
                    if pending_tx:
                        self.stdout.write(self.style.SUCCESS(f"DEBUG: Coincidencia EXACTA encontrada para {amount_decimal} USDT. ID: {pending_tx.id}"))
                    else:
                        self.stdout.write(self.style.WARNING(f"DEBUG: No se encontró coincidencia exacta para {amount_decimal} USDT. Buscando con tolerancia..."))


                    # Lógica de tolerancia: Diferentes billeteras/exchanges descuentan comisiones distintas
                    if not pending_tx:
                        decimal_part_received = amount_decimal % 1
                        all_pending = PaymentTransaction.objects.filter(status='PENDING')

                        for p_tx in all_pending:
                            if not p_tx.crypto_amount:
                                continue

                            expected_decimal_part = p_tx.crypto_amount % 1
                            
                            # DEBUG LOG for tolerance check
                            self.stdout.write(self.style.WARNING(f"DEBUG: Comparando recibido {amount_decimal} (decimal: {decimal_part_received}) con {p_tx.id} (esperado: {p_tx.crypto_amount}, decimal: {expected_decimal_part})"))

                            if expected_decimal_part == decimal_part_received:
                                difference = p_tx.crypto_amount - amount_decimal

                                # SEGURIDAD ADICIONAL SOLICITADA:
                                # Asegurarse de que el usuario no mande "2.003" para un plan de "10.003"
                                # y se apruebe. Vamos a ser estrictos con la diferencia.
                                # Por lo general, las comisiones de red no superan los 2 o 3 dólares.
                                # Si la diferencia es un número entero PERO es demasiado grande, rechazamos.
                                if difference >= 0 and difference % 1 == 0:
                                    # Límite máximo de comisión aceptable (ej. 3 USDT).
                                    # Si falta más de eso, el usuario intentó pagar menos a propósito.
                                    MAX_ALLOWED_FEE = Decimal('3.00')

                                    if difference <= MAX_ALLOWED_FEE:
                                        pending_tx = p_tx
                                        self.stdout.write(self.style.WARNING(f"    -> Coincide con orden de {p_tx.crypto_amount} (Tolerancia: Faltan {difference} USDT asumidos como fee)."))
                                        break
                                    else:
                                        self.stdout.write(self.style.ERROR(f"    -> Alerta de Pago Incompleto: La orden era de {p_tx.crypto_amount}, pero solo llegaron {amount_decimal} (Faltan {difference} USDT. Esto supera el fee máximo permitido de {MAX_ALLOWED_FEE} USDT)."))

                    if not pending_tx:
                        self.stdout.write(self.style.ERROR(f"    -> Ignorado: No hay orden válida para {amount_decimal} USDT."))
                        seen_tx.add(txid)
                        continue

                    # Comprobar confirmaciones
                    tx_info_res = requests.post(
                        "https://api.trongrid.io/wallet/gettransactioninfobyid",
                        json={"value": txid},
                        headers=HEADERS,
                        timeout=10
                    )
                    if tx_info_res.status_code != 200:
                        continue

                    tx_info = tx_info_res.json()
                    if "blockNumber" not in tx_info:
                        continue

                    block_number = tx_info["blockNumber"]
                    confirmations = current_block - block_number

                    if confirmations >= MIN_CONFIRMATIONS:
                        self.stdout.write(self.style.SUCCESS(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Payment Confirmed! TXID: {txid}"))

                        with db_transaction.atomic():
                            pending_tx.refresh_from_db()
                            if pending_tx.status == 'PENDING':
                                pending_tx.status = 'COMPLETED'
                                pending_tx.crypto_tx_id = txid
                                pending_tx.save()

                                if pending_tx.package:
                                    profile, _ = ClientProfile.objects.get_or_create(user=pending_tx.user)
                                    profile.bonus_tokens += pending_tx.package.tokens
                                    profile.save()
                                    self.stdout.write(self.style.SUCCESS(f" => Granted {pending_tx.package.tokens} tokens to {pending_tx.user.username}."))
                                else:
                                    try:
                                        sub = UserSubscription.objects.get(user=pending_tx.user, status='PENDING')
                                        sub.status = 'ACTIVE'
                                        sub.last_payment_date = timezone.now()

                                        now = timezone.now()
                                        if sub.plan.billing_period_unit == 'M':
                                            sub.next_payment_date = now + timedelta(days=30 * sub.plan.billing_period)
                                        elif sub.plan.billing_period_unit == 'Y':
                                            sub.next_payment_date = now + timedelta(days=365 * sub.plan.billing_period)
                                        elif sub.plan.billing_period_unit == 'W':
                                            sub.next_payment_date = now + timedelta(weeks=sub.plan.billing_period)
                                        elif sub.plan.billing_period_unit == 'D':
                                            sub.next_payment_date = now + timedelta(days=sub.plan.billing_period)

                                        sub.save()

                                        if sub.plan.tokens_per_period > 0:
                                            profile, _ = ClientProfile.objects.get_or_create(user=pending_tx.user)
                                            profile.bonus_tokens += sub.plan.tokens_per_period
                                            profile.save()

                                        self.stdout.write(self.style.SUCCESS(f" => Activated Subscription '{sub.plan.name}' for {pending_tx.user.username}."))
                                    except UserSubscription.DoesNotExist:
                                        self.stdout.write(self.style.WARNING(f" => Payment completed but no pending subscription found for {pending_tx.user.username}."))

                        seen_tx.add(txid)
                    else:
                        self.stdout.write(self.style.WARNING(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Pending confirmations for {amount_decimal} USDT... ({confirmations}/{MIN_CONFIRMATIONS})"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error in monitor loop: {e}"))

            time.sleep(5)
