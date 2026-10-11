from __future__ import annotations

import abc
import ipaddress
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from ..config import Settings


class AIUnavailable(RuntimeError):
    pass


class Provider(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def generate(self, system: str, prompt: str, timeout: float = 60) -> str: ...


class NullProvider(Provider):
    name = "none"

    def generate(self, system: str, prompt: str, timeout: float = 60) -> str:
        raise AIUnavailable("AI is disabled in Settings.")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            return all(ipaddress.ip_address(i[4][0]).is_loopback for i in socket.getaddrinfo(host, None))
        except OSError:
            return False


class OllamaProvider(Provider):
    name = "ollama"
    MAX_RESPONSE = 1_000_000

    def __init__(self, endpoint: str, model: str, allow_remote: bool = False):
        u = urlparse(endpoint)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise AIUnavailable("Invalid AI endpoint.")
        if not allow_remote and not _is_loopback(u.hostname):
            raise AIUnavailable("Remote AI endpoints are blocked. Enable 'allow remote' in Settings to override.")
        self.url = f"{u.scheme}://{u.netloc}/api/generate"
        self.model = model

    def generate(self, system: str, prompt: str, timeout: float = 60) -> str:
        body = json.dumps({"model": self.model, "system": system, "prompt": prompt, "stream": False,
                           "format": "json", "options": {"temperature": 0.2}}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        try:
            # The endpoint scheme and host are validated when the provider is created.
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read(self.MAX_RESPONSE))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AIUnavailable(f"AI provider unreachable: {exc}") from exc
        return str(data.get("response", ""))


def make_provider(s: Settings) -> Provider:
    if s.ai_provider == "ollama":
        return OllamaProvider(s.ai_endpoint, s.ai_model, s.ai_allow_remote)
    return NullProvider()
