"""Collects host metrics for the heartbeat."""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass

import psutil

from edge_agent.models import NetworkStatus

logger = logging.getLogger(__name__)

_LOOPBACK_PREFIXES = ("lo", "loopback")


@dataclass(frozen=True, slots=True)
class SystemMetrics:
    hostname: str
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    uptime_seconds: float
    network: NetworkStatus


def check_tcp(host: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection to host:port can be opened."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def primary_ip_address() -> str | None:
    """Address of the interface holding the default route.

    Connecting a UDP socket sends no packets; it only asks the kernel which
    local address it would route from.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            address: str = sock.getsockname()[0]
            return address
    except OSError:
        return None


class SystemCollector:
    def __init__(
        self,
        disk_path: str = "/",
        connectivity_target: tuple[str, int] | None = None,
        connectivity_timeout: float = 3.0,
        initial_cpu_sample_seconds: float = 0.2,
    ) -> None:
        self._disk_path = disk_path
        self._connectivity_target = connectivity_target
        self._connectivity_timeout = connectivity_timeout
        self._initial_cpu_sample_seconds = initial_cpu_sample_seconds
        self._cpu_primed = False

    def _cpu_percent(self) -> float:
        # psutil measures CPU since its previous call, so the very first
        # reading has no baseline and comes back as 0.0. Block briefly once to
        # get a real sample; every later call is non-blocking and reports usage
        # over the preceding heartbeat interval.
        if not self._cpu_primed:
            self._cpu_primed = True
            return psutil.cpu_percent(interval=self._initial_cpu_sample_seconds)
        return psutil.cpu_percent(interval=None)

    def collect(self) -> SystemMetrics:
        return SystemMetrics(
            hostname=socket.gethostname(),
            cpu_percent=self._cpu_percent(),
            memory_percent=psutil.virtual_memory().percent,
            disk_percent=psutil.disk_usage(self._disk_path).percent,
            uptime_seconds=max(0.0, time.time() - psutil.boot_time()),
            network=self._network_status(),
        )

    def _network_status(self) -> NetworkStatus:
        interfaces_up = sorted(
            name
            for name, stats in psutil.net_if_stats().items()
            if stats.isup and not name.lower().startswith(_LOOPBACK_PREFIXES)
        )
        reachable: bool | None = None
        if self._connectivity_target is not None:
            host, port = self._connectivity_target
            reachable = check_tcp(host, port, self._connectivity_timeout)
        return NetworkStatus(
            interfaces_up=interfaces_up,
            ip_address=primary_ip_address(),
            backend_reachable=reachable,
        )
