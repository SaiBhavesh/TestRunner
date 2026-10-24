"""SSRF guard (spec 11): only public http(s) URLs on standard ports, checked after DNS resolution."""
import ipaddress
import socket
from urllib.parse import urlparse


def resolve_host(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def validate_public_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("URL has no host")
    if parsed.port not in (None, 80, 443):
        raise ValueError("Only standard ports (80, 443) are allowed")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("localhost is not allowed")
    try:
        addresses = resolve_host(host)
    except socket.gaierror:
        raise ValueError("Could not resolve that domain")
    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if not ip.is_global:
            raise ValueError("That address is private or reserved")
    return url
