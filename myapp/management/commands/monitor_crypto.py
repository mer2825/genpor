import time
import requests
from decimal import Decimal
from django.core.management.base import BaseCommand
from myapp.models import PaymentTransaction, CompanySettings, ClientProfile, UserSubscription
from django.db import transaction as db_transaction
from django.utils import timezone
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════════════════
#  CONFIGURACIÓN ADAPTADA A DJANGO
# ═══════════════════════════════════════════════════════════
BASE_URL = "https://apilist.tronscanapi.com"
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

class Command(BaseCommand):
    help = 'Monitor TRC20 for USDT deposits using TronScan API, with full history support.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--history',
            action='store_true',
            help='Process all historical transactions before starting the real-time monitor.',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_tx = set()
        self.settings = None
        self.api_key = None
        self.wallet_address = None
        self.min_confirmations = 10

    # ───────────────────────────────────────────────────────────
    #  UTILIDADES (Adaptadas)
    # ───────────────────────────────────────────────────────────
    def timestamp_a_fecha(self, ts_ms) -> str:
        try:
            return datetime.fromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except:
            return "?"

    def imprimir_usdt(self, monto: Decimal, desde: str, hacia: str, fecha: str, txhash: str, etiqueta: str):
        direccion = "⬇ RECIBIDO" if hacia == self.wallet_address else "⬆ ENVIADO"
        self.stdout.write(self.style.SUCCESS(f"[{etiqueta}] {direccion} | {monto:.6f} USDT | From: {desde[:6]}... | TX: {txhash[:8]}..."))

    # ───────────────────────────────────────────────────────────
    #  FETCH API (Adaptado del script original)
    # ───────────────────────────────────────────────────────────
    def obtener_usdt(self, limit: int = 50, start: int = 0) -> dict:
        params = {
            "limit": limit,
            "start": start,
            "relatedAddress": self.wallet_address,
            "contract_address": USDT_CONTRACT,
        }
        try:
            resp = requests.get(f"{BASE_URL}/api/token_trc20/transfers", params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  [ERROR] USDT fetch: {e}"))
            return {}

    def obtener_todas_usdt(self) -> list:
        limite, inicio, total = 50, 0, []
        while True:
            data = self.obtener_usdt(limit=limite, start=inicio)
            pagina = data.get("token_transfers", [])
            total.extend(pagina)
            rango = int(data.get("rangeTotal", 0))
            inicio += limite
            self.stdout.write(f"    USDT: {len(total)}/{rango} transactions downloaded...", end="\r")
            if inicio >= rango or not pagina:
                break
        self.stdout.write("")
        return total

    # ───────────────────────────────────────────────────────────
    #  PROCESAMIENTO DE TRANSACCIONES (Lógica de negocio integrada)
    # ───────────────────────────────────────────────────────────
    def procesar_transaccion(self, tx: dict, etiqueta: str = "🔔 NUEVA TX"):
        txid = tx.get("transaction_id")
        if not txid or txid in self.seen_tx:
            return

        # Solo procesar transacciones RECIBIDAS
        if tx.get("to_address") != self.wallet_address:
            return
            
        self.seen_tx.add(txid)

        if PaymentTransaction.objects.filter(crypto_tx_id=txid).exists():
            return

        amount_decimal = Decimal(tx.get("quant", 0)) / Decimal('1000000')
        fecha = self.timestamp_a_fecha(tx.get("block_ts", 0))
        self.imprimir_usdt(amount_decimal, tx.get("from_address"), tx.get("to_address"), fecha, txid, etiqueta)

        # --- BÚSQUEDA DE ORDEN EN LA BASE DE DATOS ---
        pending_tx = PaymentTransaction.objects.filter(status='PENDING', crypto_amount=amount_decimal).first()

        if not pending_tx:
            # Lógica de tolerancia a comisiones
            decimal_part_received = amount_decimal % 1
            all_pending = PaymentTransaction.objects.filter(status='PENDING', crypto_amount__isnull=False)
            for p_tx in all_pending:
                if p_tx.crypto_amount % 1 == decimal_part_received:
                    difference = p_tx.crypto_amount - amount_decimal
                    if 0 <= difference <= Decimal('2.00'): # Max fee de 2 USDT
                        pending_tx = p_tx
                        self.stdout.write(self.style.WARNING(f"    -> Match with tolerance for order {p_tx.crypto_amount} (Fee: {difference} USDT)."))
                        break
        
        if not pending_tx:
            self.stdout.write(self.style.ERROR(f"    -> Ignored: No valid order found for {amount_decimal} USDT."))
            return

        # --- VERIFICACIÓN DE CONFIRMACIONES (Paso de seguridad crítico) ---
        try:
            headers = {"TRON-PRO-API-KEY": self.api_key}
            block_res = requests.post("https://api.trongrid.io/wallet/getnowblock", headers=headers, timeout=10)
            current_block = block_res.json()["block_header"]["raw_data"]["number"]
            
            tx_info_res = requests.post("https://api.trongrid.io/wallet/gettransactioninfobyid", json={"value": txid}, headers=headers, timeout=10)
            tx_info = tx_info_res.json()

            if "blockNumber" not in tx_info:
                self.stdout.write(self.style.WARNING(f"    -> TX {txid[:8]} not yet in a block. Will re-check."))
                self.seen_tx.remove(txid) # Permitir que se vuelva a procesar
                return

            confirmations = current_block - tx_info["blockNumber"]
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"    -> Error checking confirmations: {e}"))
            self.seen_tx.remove(txid) # Permitir que se vuelva a procesar
            return

        if confirmations >= self.min_confirmations:
            self.stdout.write(self.style.SUCCESS(f"    -> CONFIRMED ({confirmations}/{self.min_confirmations}). Processing payment."))
            self.completar_pago(pending_tx, txid)
        else:
            self.stdout.write(self.style.WARNING(f"    -> PENDING CONFIRMATIONS ({confirmations}/{self.min_confirmations}). Will re-check."))
            self.seen_tx.remove(txid) # Permitir que se vuelva a procesar

    def completar_pago(self, pending_tx, txid):
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
                    self.stdout.write(self.style.SUCCESS(f"     => Granted {pending_tx.package.tokens} tokens to {pending_tx.user.username}."))
                elif pending_tx.paypal_transaction_id and "SUB_PLAN" in pending_tx.paypal_transaction_id:
                    try:
                        sub = UserSubscription.objects.get(user=pending_tx.user, status='PENDING')
                        sub.status = 'ACTIVE'
                        sub.last_payment_date = timezone.now()
                        # Lógica de fecha de expiración
                        now = timezone.now()
                        if sub.plan.billing_period_unit == 'M':
                            sub.next_payment_date = now + timedelta(days=30 * sub.plan.billing_period)
                        elif sub.plan.billing_period_unit == 'Y':
                            sub.next_payment_date = now + timedelta(days=365 * sub.plan.billing_period)
                        # ... (otros periodos)
                        sub.save()
                        self.stdout.write(self.style.SUCCESS(f"     => Activated Subscription '{sub.plan.name}' for {pending_tx.user.username}."))
                    except UserSubscription.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"     => Payment completed but no pending subscription found for {pending_tx.user.username}."))

    # ═══════════════════════════════════════════════════════════
    #  MAIN (Adaptado a Django Command)
    # ═══════════════════════════════════════════════════════════
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("="*60))
        self.stdout.write(self.style.SUCCESS("  💵 MONITOR USDT (TRC20) — TRONSCAN API"))
        self.stdout.write(self.style.SUCCESS("="*60))

        self.settings = CompanySettings.objects.last()
        if not self.settings or not self.settings.crypto_usdt_address or not self.settings.crypto_trongrid_api_key:
            self.stdout.write(self.style.ERROR("Crypto settings are not configured in the Admin Panel. Exiting."))
            return

        self.api_key = self.settings.crypto_trongrid_api_key
        self.wallet_address = self.settings.crypto_usdt_address
        self.min_confirmations = self.settings.crypto_min_confirmations

        self.stdout.write(f"  Wallet           : {self.wallet_address}")
        self.stdout.write(f"  Contrato         : {USDT_CONTRACT}")
        self.stdout.write(f"  Min Confirmations: {self.min_confirmations}")
        self.stdout.write(f"  Process History  : {'✅ SÍ' if options['history'] else '❌ NO'}")
        self.stdout.write(self.style.SUCCESS("="*60))

        if options['history']:
            self.stdout.write("\n  🔄 Downloading full USDT transaction history...")
            txs = self.obtener_todas_usdt()
            txs_ord = sorted(txs, key=lambda x: int(x.get("block_ts", 0)))
            total = len(txs_ord)
            self.stdout.write(f"\n  📋 HISTORY — Found {total} transactions. Processing...")
            for i, tx in enumerate(txs_ord, 1):
                self.procesar_transaccion(tx, etiqueta=f"📜 {i}/{total}")
            self.stdout.write("\n  ✅ History processing complete. Starting real-time monitor...\n")
        else:
            # Cargar transacciones recientes para evitar procesarlas al inicio
            self.stdout.write("\n  🔄 Initializing real-time monitor...")
            for tx in self.obtener_usdt(limit=50).get("token_transfers", []):
                self.seen_tx.add(tx.get("transaction_id"))
            self.stdout.write(f"  ✅ Ready. Ignoring last {len(self.seen_tx)} transactions.\n")

        # --- Loop de Monitoreo ---
        ciclo = 0
        while True:
            ciclo += 1
            recientes = self.obtener_usdt(limit=50).get("token_transfers", [])
            # Procesar de la más antigua a la más nueva para evitar errores de confirmación
            for tx in sorted(recientes, key=lambda x: int(x.get("block_ts", 0))):
                self.procesar_transaccion(tx)
            
            if ciclo % 6 == 0: # Cada minuto aprox.
                self.stdout.write(self.style.HTTP_INFO(f"  ⏳ [{datetime.now().strftime('%H:%M:%S')}] Monitoring... (Cycle #{ciclo})"))
            
            time.sleep(10)
