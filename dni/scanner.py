"""Read a landing page and find its phone numbers (CH-460, "We scan the page
and find every phone number to swap").

Only public http(s) pages: every address the page (and each redirect) points
to must resolve to a public IP, so the scanner cannot be used to reach our
own servers or private networks. 6 seconds and 2 MB at most.
"""
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

PHONE = re.compile(r'(?<!\d)(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})(?!\d)')
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 6
MAX_REDIRECTS = 3

LABELS = {
    'header': 'Header', 'footer': 'Footer', 'nav': 'Menu',
    'h1': 'Headline', 'h2': 'Headline', 'h3': 'Heading', 'button': 'Call button',
}


class ScanError(Exception):
    pass


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ScanError("That website address could not be found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def fetch(url: str) -> str:
    import requests
    url = (url or '').strip()
    if not re.match(r'^https?://', url, re.I):
        url = 'https://' + url
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ScanError("Enter a full page address, e.g. https://example.com/offer")
        if not _public(parts.hostname):
            raise ScanError("Only public websites can be scanned.")
        try:
            r = requests.get(url, timeout=TIMEOUT, allow_redirects=False, stream=True,
                             headers={'User-Agent': 'AvortyxNumberPoolScanner/1.0'})
        except requests.RequestException:
            raise ScanError("The page did not answer. Check the address and try again.")
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get('Location', ''))
            r.close()
            continue
        if r.status_code >= 400:
            raise ScanError(f"The page answered with an error ({r.status_code}).")
        body = b''
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > MAX_BYTES:
                break
        r.close()
        return body.decode(r.encoding or 'utf-8', errors='replace')
    raise ScanError("The page redirected too many times.")


def _fmt(d: str) -> str:
    return f'1-{d[:3]}-{d[3:6]}-{d[6:]}'


class _Finder(HTMLParser):
    VOID = {'br', 'img', 'input', 'meta', 'link', 'hr', 'source', 'area', 'base', 'col', 'embed', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.found = []
        self.skip = 0

    def _where(self):
        parts = []
        for tag, attrs in self.stack[-3:]:
            cls = (attrs.get('class') or '').split()
            ident = attrs.get('id')
            parts.append(tag + (f'#{ident}' if ident else '') + (f'.{cls[0]}' if cls else ''))
        return ' '.join(parts)

    def _label(self):
        for tag, attrs in reversed(self.stack):
            cls = (attrs.get('class') or '').lower()
            if tag == 'a' and ('btn' in cls or 'button' in cls or 'call' in cls):
                return 'Call button'
            if tag in LABELS:
                return LABELS[tag]
            if 'hero' in cls:
                return 'Hero'
        return 'Page text'

    def _add(self, d: str):
        self.found.append({'number': d, 'display': _fmt(d), 'where': self._where(), 'label': self._label()})

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        if tag not in self.VOID:
            self.stack.append((tag, a))
        href = a.get('href') or ''
        if tag == 'a' and href.lower().startswith('tel:'):
            m = PHONE.search(href[4:])
            if m:
                self._add(''.join(m.groups()))

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.skip = max(0, self.skip - 1)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.skip:
            return
        for m in PHONE.finditer(data):
            self._add(''.join(m.groups()))


def find_numbers(html: str) -> list:
    """Each phone number on the page once per place it appears."""
    f = _Finder()
    try:
        f.feed(html or '')
    except Exception:
        pass
    seen, out = set(), []
    for item in f.found:
        key = (item['number'], item['where'])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out[:50]


def scan(url: str) -> list:
    return find_numbers(fetch(url))
