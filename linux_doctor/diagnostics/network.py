from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

import psutil

from ..config import Settings
from ..core import runner
from ..core.models import Confidence, Finding, Severity


def _resolve(name: str, timeout: float) -> bool:
    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            return bool(ex.submit(socket.getaddrinfo, name, None).result(timeout=timeout))
        except (OSError, FutTimeout):
            return False


def _tcp(host: str, port: int, timeout: float = 3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_connectivity(s: Settings, **_) -> list[Finding]:
    stats, addrs = psutil.net_if_stats(), psutil.net_if_addrs()
    up = [n for n, st in stats.items() if st.isup and n != "lo"]
    if not up:
        return [Finding("NET_NO_IFACE", "No active network interface", Severity.ACTION,
                        "Every non-loopback interface is down or absent.", {"interfaces": list(stats)},
                        "Check Wi-Fi/cable and whether NetworkManager is running.", Confidence.HIGH, "ip")]
    with_ip = [n for n in up if any(a.family == socket.AF_INET for a in addrs.get(n, []))]
    if not with_ip:
        return [Finding("NET_NO_IP", "Interface is up but has no IPv4 address", Severity.ACTION,
                        "DHCP may have failed.", {"up": up}, "Reconnect to the network and re-run.", Confidence.MEDIUM, "ip")]
    r = runner.run("ip_route", timeout=5)
    default = next((l for l in r.stdout.splitlines() if l.startswith("default")), "") if r.ok else ""
    if r.ok and not default:
        return [Finding("NET_NO_ROUTE", "No default route", Severity.ACTION,
                        "The machine has an address but no gateway to reach the internet.",
                        {"routes": r.stdout.splitlines()[:10]}, "Reconnect; check router/DHCP.", Confidence.HIGH, "ip")]
    tcp_ok = _tcp(s.network_test_host, s.network_test_port)
    dns_ok = _resolve(s.dns_test_name, 4)
    ev = {"interfaces_up": up, "default_route": default, "tcp_to_test_host": tcp_ok, "dns_resolves": dns_ok}
    if tcp_ok and dns_ok:
        return [Finding("NET_OK", "Connectivity and DNS look healthy", Severity.OK, evidence=ev, confidence=Confidence.HIGH)]
    if tcp_ok and not dns_ok:
        return [Finding("NET_DNS_FAIL", "Internet reachable by IP but DNS lookup failed", Severity.ATTENTION,
                        "Routing works; name resolution does not.", ev,
                        "Check DNS servers (resolvectl status) or try another resolver.", Confidence.HIGH, "ip")]
    if not tcp_ok and dns_ok:
        return [Finding("NET_TCP_FAIL", "DNS works but the test host is unreachable", Severity.INFO,
                        "The test host/port may be blocked by a firewall or be down; this is not proof the internet is down.",
                        ev, "Change the test host in Settings and retry.", Confidence.LOW, "ip")]
    return [Finding("NET_NO_INTERNET", "Neither TCP nor DNS checks succeeded", Severity.ATTENTION,
                    "Local config looks fine but nothing external is reachable. Could be ISP, router, or firewall.",
                    ev, "Restart the router/connection and retry.", Confidence.MEDIUM, "ip")]


def check_listening(s: Settings, **_) -> list[Finding]:
    r = runner.run("ss_listen", timeout=5)
    if not r.ok:
        return [Finding("NET_SS_UNAVAILABLE", "Could not list listening ports", Severity.INFO, r.stderr[:200])]
    exposed = []
    for line in r.stdout.splitlines():
        cols = line.split()
        if len(cols) >= 5 and cols[1] in ("LISTEN", "UNCONN"):
            local = cols[4]
            if local.startswith(("0.0.0.0:", "*:", "[::]:")):
                exposed.append(f"{cols[0]} {local}")
    if exposed:
        return [Finding("NET_LISTEN_ALL", f"{len(exposed)} service(s) listen on all interfaces", Severity.INFO,
                        "An open port is not automatically a vulnerability; what matters is the service, its configuration and firewall.",
                        {"listening": exposed[:30]}, "Confirm you recognise each service.", Confidence.MEDIUM, "ss")]
    return [Finding("NET_LISTEN_OK", "No services listening on all interfaces", Severity.OK)]
