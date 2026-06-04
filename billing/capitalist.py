"""Capitalist.net merchant payment integration."""
import hashlib
import hmac
from decimal import Decimal
from django.conf import settings


class CapitalistService:
    CHECKOUT_URL = 'https://capitalist.net/merchant/pay'

    @classmethod
    def _sign(cls, params: dict, secret: str) -> str:
        data = {k: str(v) for k, v in params.items() if not k.startswith('opt_')}
        sorted_values = [data[k] for k in sorted(data.keys())]
        raw = ':'.join(sorted_values)
        return hmac.new(secret.encode('utf-8'), raw.encode('utf-8'), hashlib.md5).hexdigest()

    @classmethod
    def build_checkout(cls, amount: Decimal, currency: str, order_id: str, description: str = ''):
        merchant_id = settings.CAPITALIST_MERCHANT_ID
        secret = settings.CAPITALIST_SECRET
        amount_str = f'{Decimal(amount):.2f}'
        description_str = description or f'Deposit {amount_str} {currency.upper()}'

        params = {
            'merchantid': str(merchant_id),
            'number': str(order_id),
            'amount': amount_str,
            'currency': currency.upper(),
            'description': description_str,
        }
        params['sign'] = cls._sign(params, secret)

        query = '&'.join(f'{k}={v}' for k, v in params.items())
        return f'{cls.CHECKOUT_URL}?lang=en&{query}', params

    @classmethod
    def verify_callback(cls, data: dict) -> bool:
        secret = settings.CAPITALIST_SECRET
        received_sig = data.get('sign', '')
        verify_params = {
            'merchant_id': data.get('merchant_id', ''),
            'order_number': data.get('order_number', ''),
            'payment_amount': data.get('payment_amount', ''),
            'payment_currency': data.get('payment_currency', ''),
            'payment_state': data.get('payment_state', ''),
        }
        sorted_values = [verify_params[k] for k in sorted(verify_params.keys())]
        raw = ':'.join(sorted_values)
        expected_sig = hmac.new(secret.encode('utf-8'), raw.encode('utf-8'), hashlib.md5).hexdigest()
        return received_sig.lower() == expected_sig.lower()
