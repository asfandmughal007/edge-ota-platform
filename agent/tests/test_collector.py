from __future__ import annotations

import socket

from edge_agent.collector import SystemCollector, check_tcp


def test_collect_returns_sane_values() -> None:
    metrics = SystemCollector().collect()

    assert metrics.hostname == socket.gethostname()
    assert 0 <= metrics.cpu_percent <= 100
    assert 0 < metrics.memory_percent <= 100
    assert 0 <= metrics.disk_percent <= 100
    assert metrics.uptime_seconds > 0
    assert metrics.network.backend_reachable is None
    assert not any(name.lower().startswith("lo") for name in metrics.network.interfaces_up)


def test_check_tcp_against_local_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert check_tcp("127.0.0.1", port, timeout=2.0) is True
    # Listener is closed now, so the same port must refuse.
    assert check_tcp("127.0.0.1", port, timeout=0.5) is False


def test_reachability_reported_when_target_configured() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        target = ("127.0.0.1", server.getsockname()[1])
        metrics = SystemCollector(connectivity_target=target).collect()
    assert metrics.network.backend_reachable is True
