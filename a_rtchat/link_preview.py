from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings


_URL_RE = re.compile(r"(https?://[^\s<>()\"']+)", re.IGNORECASE)


@dataclass(frozen=True)
class LinkPreview:
    url: str
    title: str = ''
    description: str = ''
    image: str = ''
    site_name: str = ''


def extract_first_http_url(text: str) -> str:
    if not text:
        return ''
    m = _URL_RE.search(text)
    if not m:
        return ''

    url = (m.group(1) or '').strip()
    # Trim common trailing punctuation.
    while url and url[-1] in {'.', ',', ';', ':', '!', '?', ')', ']', '}', '"', "'"}:
        url = url[:-1]
    return url


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False

    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return False
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return False
    return True


def _is_safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in {'http', 'https'}:
        return False
    if not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False

    host = parsed.hostname
    if not host:
        return False

    lowered = host.lower()
    if lowered in {'localhost'} or lowered.endswith('.local'):
        return False

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except Exception:
        return False

    # Require at least one public IP and reject if any resolved IP is non-public.
    any_public = False
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0]
        if _is_public_ip(ip):
            any_public = True
        else:
            return False

    return any_public


def fetch_link_preview(url: str) -> Optional[LinkPreview]:
    if not url:
        return None

    if not bool(getattr(settings, 'LINK_PREVIEW_ENABLED', True)):
        return None

    if not _is_safe_public_url(url):
        return None

    timeout = float(getattr(settings, 'LINK_PREVIEW_TIMEOUT_SECONDS', 3.0))
    max_bytes = int(getattr(settings, 'LINK_PREVIEW_MAX_BYTES', 256_000))

    headers = {
        'User-Agent': 'VixoChatLinkPreview/1.0 (+https://example.invalid)',
        'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
    except Exception:
        return None

    final_url = getattr(resp, 'url', url) or url
    if not _is_safe_public_url(final_url):
        return None

    content_type = (resp.headers.get('Content-Type') or '').lower()
    if 'text/html' not in content_type and 'application/xhtml' not in content_type:
        return None

    content = bytearray()
    try:
        for chunk in resp.iter_content(chunk_size=16_384):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > max_bytes:
                break
    except Exception:
        return None

    try:
        html = content.decode(resp.encoding or 'utf-8', errors='ignore')
    except Exception:
        return None

    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None

    soup = BeautifulSoup(html, 'html.parser')

    def meta(prop: str = '', name: str = '') -> str:
        if prop:
            tag = soup.find('meta', attrs={'property': prop})
            if tag and tag.get('content'):
                return str(tag.get('content')).strip()
        if name:
            tag = soup.find('meta', attrs={'name': name})
            if tag and tag.get('content'):
                return str(tag.get('content')).strip()
        return ''

    title = meta(prop='og:title')
    if not title:
        t = soup.find('title')
        if t and t.text:
            title = t.text.strip()

    description = meta(prop='og:description') or meta(name='description')
    image = meta(prop='og:image')
    site_name = meta(prop='og:site_name')

    if image:
        image = urljoin(final_url, image)

    # Normalize lengths so they don't blow up the UI.
    title = title[:300]
    description = description[:500]
    site_name = site_name[:120]

    return LinkPreview(
        url=final_url,
        title=title,
        description=description,
        image=image[:500] if image else '',
        site_name=site_name,
    )
