"""Tracks live connector WebSocket connections and routes job results.

One hub instance per process. The connector always dials in; the cloud never
opens a connection toward the local machine.
"""
import asyncio
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectorHub:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        # One post-serialization lock per connector. Several vouchers can build
        # their XML in parallel, but their actual Tally writes must happen one at
        # a time and in order (a voucher's masters immediately before its own
        # voucher). Callers hold this around the connector job(s). Keyed by
        # connector id so different Tally machines never block each other.
        self._post_locks: dict[str, asyncio.Lock] = {}

    def post_lock(self, connector_id: str) -> asyncio.Lock:
        """The per-connector lock that serializes Tally writes (see __init__)."""
        lock = self._post_locks.get(connector_id)
        if lock is None:
            lock = asyncio.Lock()
            self._post_locks[connector_id] = lock
        return lock

    # -- connection lifecycle -------------------------------------------------
    async def register(self, connector_id: str, ws: WebSocket) -> None:
        async with self._lock:
            old = self._connections.get(connector_id)
            self._connections[connector_id] = ws
        if old is not None:
            log.info("Connector %s reconnected; replacing the previous connection", connector_id)
            try:
                await old.close()
            except Exception:
                pass
        log.info("Connector online: %s", connector_id)

    async def unregister(self, connector_id: str, ws: WebSocket) -> None:
        removed = False
        async with self._lock:
            if self._connections.get(connector_id) is ws:
                del self._connections[connector_id]
                removed = True
        if removed:
            log.info("Connector offline: %s", connector_id)

    def is_online(self, connector_id: str) -> bool:
        return connector_id in self._connections

    def online_ids(self) -> list[str]:
        return list(self._connections.keys())

    # -- jobs -----------------------------------------------------------------
    async def send_job(self, connector_id: str, job_id: str, action: str, payload: dict) -> None:
        ws = self._connections.get(connector_id)
        if ws is None:
            raise ConnectorOffline(connector_id)
        await ws.send_json({"type": "job", "job_id": job_id, "action": action, "payload": payload})

    async def send_job_and_wait(
        self, connector_id: str, job_id: str, action: str, payload: dict, timeout: float
    ) -> dict[str, Any]:
        """Send a job and wait for its job_result. Raises ConnectorOffline or TimeoutError."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._futures[job_id] = future
        try:
            await self.send_job(connector_id, job_id, action, payload)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._futures.pop(job_id, None)

    def handle_result(self, message: dict) -> bool:
        """Resolve the waiting future for a job_result. Returns True if someone
        was waiting; False means the result must be persisted by the caller
        (e.g. a pending job delivered after a reconnect)."""
        job_id = str(message.get("job_id", ""))
        future = self._futures.get(job_id)
        if future is not None and not future.done():
            future.set_result(message)
            return True
        return False


class ConnectorOffline(Exception):
    def __init__(self, connector_id: str):
        super().__init__(f"Connector {connector_id} is not connected.")
        self.connector_id = connector_id


hub = ConnectorHub()
