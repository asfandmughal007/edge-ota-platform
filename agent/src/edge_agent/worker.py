"""Deployment worker: polls the backend for work and drives the updater."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from edge_agent.backend_client import BackendClient, BackendError
from edge_agent.models import ReportStatus
from edge_agent.updater import Updater

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PendingReport:
    deployment_id: str
    status: ReportStatus
    message: str


class DeploymentWorker:
    def __init__(
        self,
        device_id: str,
        client: BackendClient,
        updater: Updater,
        poll_interval_seconds: float,
    ) -> None:
        self._device_id = device_id
        self._client = client
        self._updater = updater
        self._interval = poll_interval_seconds
        # A final result the backend has not acknowledged yet. The update has
        # already happened on the device, so the report must not be lost.
        self._unreported: _PendingReport | None = None

    def poll_once(self) -> None:
        """One polling cycle. Never raises."""
        try:
            self._poll()
        except BackendError as exc:
            logger.warning(
                "deployment poll failed", extra={"device_id": self._device_id, "error": str(exc)}
            )
        except Exception:
            logger.exception("deployment worker error", extra={"device_id": self._device_id})

    def _poll(self) -> None:
        if self._unreported is not None and not self._flush_report():
            return  # still cannot reach the backend; try again next cycle

        command = self._client.next_deployment(self._device_id)
        if command is None:
            return
        logger.info(
            "deployment received",
            extra={
                "device_id": self._device_id,
                "deployment_id": command.id,
                "target_version": command.target_version,
                "image": command.image,
            },
        )
        # Tell the backend before touching anything. If this fails we have
        # changed nothing and simply pick the deployment up again next cycle.
        self._client.report_status(command.id, "in_progress")

        result = self._updater.apply(command)
        self._unreported = _PendingReport(command.id, result.outcome, result.message)
        self._flush_report()

    def _flush_report(self) -> bool:
        report = self._unreported
        assert report is not None
        log = {"deployment_id": report.deployment_id, "status": report.status}
        try:
            self._client.report_status(report.deployment_id, report.status, report.message)
        except BackendError as exc:
            if exc.is_rejection:
                # The backend refused it (e.g. already finalised). Retrying
                # forever would block all future deployments.
                logger.error("deployment report rejected", extra={**log, "error": str(exc)})
                self._unreported = None
                return True
            logger.warning("deployment report failed, will retry", extra={**log, "error": str(exc)})
            return False
        logger.info("deployment reported", extra=log)
        self._unreported = None
        return True

    def run(self, stop: threading.Event) -> None:
        logger.info(
            "deployment worker started",
            extra={"device_id": self._device_id, "interval_seconds": self._interval},
        )
        while not stop.is_set():
            self.poll_once()
            stop.wait(self._interval)
