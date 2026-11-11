"""CH-665: customers buy tracking numbers only from our stock (Sir, 11 Nov).

High Risk accounts see and buy only High Risk numbers, Low Risk only Low; $20
or $10 a number (one time), $0.040 or $0.035 a minute by the exact second;
never Twilio; a bought number is the customer's alone, and retired goes to
trash for good - never to anyone else, never to our account.

Run:  python manage.py test phone_numbers.test_tfn_stock
"""
import json
from decimal import Decimal
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from billing.models import BillingAccount, Transaction
from billing.services import BillingService
from phone_numbers import pools, stock
from phone_numbers.models import Carrier, PhoneNumber
from phone_numbers.services import PhoneNumberService

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(MAIN_ORGANIZATION_SLUG='interns', TFN_STOCK_SLUG='tfn-stock', ALLOWED_HOSTS=['*'], CACHES=CACHE)
class StockTests(TestCase):
    def setUp(self):
        cache.clear()
        self.main = Organization.objects.create(name='Avortyx', slug='interns')
        self.stock_org, _ = Organization.objects.get_or_create(slug='tfn-stock', defaults={'name': 'TFN Stock'})
        self.xhr = Carrier.objects.create(organization=self.main, name='XOLO High Risk', code='XHR',
                                          risk_override='high', number_monthly_cost=Decimal('15'))
        self.xlr = Carrier.objects.create(organization=self.main, name='XLR', code='XLR', risk_override='low')
        self.high = self.customer('High Co', 'high-co', 'high')
        self.low = self.customer('Low Co', 'low-co', 'low')
        self.h1 = stock.put_in_stock(self.xhr, '+18660000001')
        self.h2 = stock.put_in_stock(self.xhr, '+18660000002')
        self.l1 = stock.put_in_stock(self.xlr, '+18660000003')

    def customer(self, name, slug, risk, balance='100'):
        org = Organization.objects.create(name=name, slug=slug)
        BillingAccount.objects.create(organization=org, balance=Decimal(balance))
        stock.set_type(org, risk)
        org.refresh_from_db()
        org.admin = User.objects.create(username=f'a@{slug}.io', email=f'a@{slug}.io', organization=org,
                                        role='admin', is_active=True)
        return org

    def client_for(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def offer(self, org):
        r = self.client_for(org.admin).get('/api/numbers/stock')
        return r.status_code, r.json()

    def buy(self, org, number):
        return self.client_for(org.admin).post(f'/api/numbers/stock/{number.id}/buy')

    # ── putting numbers in the stock ─────────────────────────────────────────
    def test_stock_numbers_are_held_by_the_stock_account_with_their_type(self):
        self.assertEqual((self.h1.organization_id, self.h1.pool, self.h1.sale_risk),
                         (self.stock_org.id, pools.STOCK, 'high'))
        self.assertEqual(self.l1.sale_risk, 'low')

    def test_import_tfns_puts_numbers_in_the_stock(self):
        out = StringIO()
        call_command('import_tfns', '--org', 'Avortyx', '--carrier', 'XHR', '--numbers', '18660000009',
                     '--stock', stdout=out)
        n = PhoneNumber.objects.get(number='+18660000009')
        self.assertEqual((n.organization_id, n.pool, n.sale_risk), (self.stock_org.id, pools.STOCK, 'high'))
        self.assertIn('STOCK for HIGH RISK', out.getvalue())

    def test_the_stock_account_holds_only_stock(self):
        with self.assertRaisesMessage(pools.PoolError, 'stock account holds stock TFNs only'):
            PhoneNumber.objects.create(organization=self.stock_org, number='+18660000010',
                                       number_type='toll_free', pool=pools.INVENTORY)

    # ── what each account sees ───────────────────────────────────────────────
    def test_each_account_sees_only_its_own_type_and_price(self):
        code, d = self.offer(self.high)
        self.assertEqual(code, 200, d)
        self.assertEqual((d['risk'], d['price'], d['minute'], d['total']), ('high', '20.00', '0.0400', 2))
        self.assertEqual({i['number'] for i in d['items']}, {'+18660000001', '+18660000002'})
        code, d = self.offer(self.low)
        self.assertEqual((d['risk'], d['price'], [i['number'] for i in d['items']]), ('low', '10.00', ['+18660000003']))

    def test_our_account_and_an_account_without_a_type_do_not_buy_from_the_stock(self):
        boss = User.objects.create(username='m@x.io', email='m@x.io', organization=self.main, role='admin')
        self.assertEqual(self.client_for(boss).get('/api/numbers/stock').status_code, 400)
        plain = Organization.objects.create(name='Plain', slug='plain')
        plain.admin = User.objects.create(username='p@x.io', email='p@x.io', organization=plain, role='admin')
        code, d = self.offer(plain)
        self.assertEqual(code, 400)
        self.assertIn('not set', d['detail'])

    # ── buying ───────────────────────────────────────────────────────────────
    def test_buying_moves_the_number_once_and_takes_the_price(self):
        r = self.buy(self.high, self.h1)
        self.assertEqual(r.status_code, 201, r.content)
        n = PhoneNumber.objects.get(pk=self.h1.pk)
        self.assertEqual((n.organization_id, n.pool, n.created_by_id), (self.high.id, pools.INVENTORY, self.high.admin.id))
        self.assertEqual(BillingService.get_balance(self.high), Decimal('80'))
        t = Transaction.objects.get(organization=self.high, reference_id=f'tfn-buy:{n.id}')
        self.assertEqual(t.amount, Decimal('20'))
        # Gone from everyone's stock; nobody can buy it again.
        self.assertEqual(self.offer(self.high)[1]['total'], 1)
        other = self.customer('High Two', 'high-two', 'high')
        self.assertEqual(self.buy(other, self.h1).status_code, 400)

    def test_an_account_cannot_buy_the_other_type(self):
        r = self.buy(self.low, self.h1)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PhoneNumber.objects.get(pk=self.h1.pk).organization_id, self.stock_org.id)

    def test_not_enough_balance_buys_nothing(self):
        poor = self.customer('Poor', 'poor', 'high', balance='5')
        r = self.buy(poor, self.h1)
        self.assertEqual(r.status_code, 400)
        self.assertIn('Insufficient balance', r.json()['detail'])
        n = PhoneNumber.objects.get(pk=self.h1.pk)
        self.assertEqual((n.organization_id, n.pool), (self.stock_org.id, pools.STOCK))
        self.assertEqual(BillingService.get_balance(poor), Decimal('5'))

    def test_a_bought_number_is_the_customers_alone_and_retired_goes_to_trash(self):
        self.buy(self.high, self.h1)
        n = PhoneNumber.objects.get(pk=self.h1.pk)
        for org, pool in ((self.low, pools.INVENTORY), (self.main, pools.MAIN), (self.stock_org, pools.STOCK)):
            n = PhoneNumber.objects.get(pk=n.pk)
            n.organization, n.pool = org, pool
            with self.assertRaises(pools.PoolError):
                n.save()
        PhoneNumberService.release_number(str(n.id), self.high.admin)
        n = PhoneNumber.objects.get(pk=n.pk)
        self.assertEqual((n.pool, n.organization_id), (pools.TRASH, self.high.id))
        self.assertNotIn('+18660000001', [i['number'] for i in self.offer(self.high)[1]['items']])

    def test_the_customer_never_sees_our_carrier(self):
        d = self.buy(self.high, self.h1).json()
        self.assertEqual((d['carrier_name'], d['carrier_code'], d['carrier_id'], d['risk']), ('', '', None, 'high'))

    def test_customers_cannot_buy_from_twilio_or_add_their_own_numbers(self):
        c = self.client_for(self.high.admin)
        body = json.dumps({'phone_number': '+18005550100', 'number_type': 'toll_free'})
        for path in ('/api/numbers/search', '/api/numbers/purchase', '/api/numbers/import'):
            r = c.post(path, body, content_type='application/json')
            self.assertEqual(r.status_code, 400, path)
            self.assertIn('Avortyx stock', r.json()['detail'])

    # ── the account's type and its billing ───────────────────────────────────
    def test_the_type_fills_sirs_prices_and_exact_second_billing(self):
        a = BillingAccount.objects.get(organization=self.high)
        self.assertEqual((a.tfn_purchase_fee, a.per_minute_rate, a.bill_exact_seconds),
                         (Decimal('20.00'), Decimal('0.0400'), True))
        a = BillingAccount.objects.get(organization=self.low)
        self.assertEqual((a.tfn_purchase_fee, a.per_minute_rate), (Decimal('10.00'), Decimal('0.0350')))
        BillingAccount.objects.filter(organization=self.high).update(recording_fee_per_minute=0,
                                                                     voip_shield_fee_per_call=0)
        self.assertEqual(BillingService.call_cost(self.high, 90), Decimal('0.0600'))      # 1.5 min, not 2
        BillingAccount.objects.filter(organization=self.high).update(bill_exact_seconds=False)
        self.assertEqual(BillingService.call_cost(self.high, 90), Decimal('0.0800'))      # as before
        with self.assertRaises(stock.StockError):
            stock.set_type(self.main, 'high')

    def test_the_owner_sets_the_type_and_sees_the_stock(self):
        sir = User.objects.create(username='sir', email='sir@x.io', organization=self.main, role='admin',
                                  is_superuser=True, is_active=True)
        c = self.client_for(sir)
        new = Organization.objects.create(name='New Co', slug='new-co')
        r = c.patch(f'/api/owner/organizations/{new.id}', json.dumps({'tfn_risk': 'low'}),
                    content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        d = c.get(f'/api/owner/organizations/{new.id}').json()
        self.assertEqual((d['tfn_risk'], d['bill_exact_seconds'], d['billing']['tfn_purchase_fee']),
                         ('low', True, '10.00'))
        self.assertEqual(c.patch(f'/api/owner/organizations/{self.main.id}', json.dumps({'tfn_risk': 'high'}),
                                 content_type='application/json').status_code, 400)
        names = [o['slug'] for o in c.get('/api/owner/organizations').json()['items']]
        self.assertNotIn('tfn-stock', names)
        self.buy(self.high, self.h1)
        rows = {(r['risk'], r['carrier']): r for r in c.get('/api/owner/tfn-stock').json()['rows']}
        self.assertEqual((rows[('high', 'XHR')]['for_sale'], rows[('high', 'XHR')]['sold']), (1, 1))
        self.assertEqual(rows[('low', 'XLR')]['for_sale'], 1)
