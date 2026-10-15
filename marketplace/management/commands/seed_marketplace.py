"""Seed the Services directory with the reference list - names only.

Every seeded service starts hidden (is_active False) with no link and no
logo: staff fill those in from the portal. Links are never invented here.
Safe to re-run: existing names are skipped.
"""
from django.core.management.base import BaseCommand

from marketplace.models import MarketplaceService, ServiceCategory

CATEGORIES = [
    ('Ad networks', {'ru': 'Рекламные сети', 'zh': '广告网络', 'ja': '広告ネットワーク'}, 10, [
        'Adskeeper', 'Wizads', 'Youtarget', 'Convertadv', 'Adcash', 'TrafficStars',
        'HilltopAds', 'MGID', 'Kaleido Media', 'ClickAdilla', 'RichAds', 'Adsterra',
        'Pushub', 'EZmob', 'MyBid', 'Popcash', 'AdsBravo', 'ReachEffect',
        'EpicAds.Net', 'Kadam', 'Power Push', 'AdProfex', 'hClicks.com',
        'Pushground', 'Popunder', 'OnClickA', 'Mobivion', 'River Traffic',
        'DAO Advertising', 'AdOperator', 'ADxAD', 'Noviclick', 'Mondiad',
        'RollerAds', 'Bidvertiser', 'Activerevenue', 'Traffic Nomads',
        'AdsCompass', 'Mixadvert', 'Yandex Direct', 'Google Ads', 'TacoLoco',
        'Evadav', 'Adsy.Tech', 'ROIads', 'AdSpyglass', 'Traforama', 'Retarg',
        'CliqBetter Ad Network', 'Afront DSP', 'myTarget', 'Directadvert',
        'OctoClick', 'BuyMedia',
    ]),
    ('Servers, hosting, proxies',
     {'ru': 'Серверы, хостинги, прокси', 'zh': '服务器、主机、代理', 'ja': 'サーバー・ホスティング・プロキシ'}, 20, [
        'CyberYozh APP', 'PROXYS.IO', 'SX.ORG', 'DLine Media', 'Asocks',
        'Z-PROXY.COM', 'HostZealot', 'Inferno Solutions', 'iProxy.online',
        'Mobile Proxies (mobileproxy.space)', 'Froxy', 'ProHoster.info',
        'HostMeNow', 'VPN.how', 'LTEBOOST', 'proxy-solutions.net', 'ModJus Shop',
        'Luxury Socks', 'snowgate cloud', 'PrivateFlare',
    ]),
]


class Command(BaseCommand):
    help = 'Seed marketplace categories and service names (hidden until staff fill them in)'

    def handle(self, *args, **options):
        made = skipped = 0
        for name, translations, order, services in CATEGORIES:
            cat, _ = ServiceCategory.objects.get_or_create(
                name=name,
                defaults={'name_translations': translations, 'sort_order': order})
            for i, svc in enumerate(services):
                if MarketplaceService.objects.filter(name__iexact=svc).exists():
                    skipped += 1
                    continue
                MarketplaceService.objects.create(
                    category=cat, name=svc, website_url='',
                    is_active=False, sort_order=i)
                made += 1
        self.stdout.write(f'{made} services seeded hidden, {skipped} already present')
