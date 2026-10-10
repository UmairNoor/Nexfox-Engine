"""HTTP client for the worker gateway. Only idempotent calls are retried."""
import logging
import time

import httpx

log = logging.getLogger("worker.api")


class LeaseLost(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status: int, detail: str, code: str | None = None):
        super().__init__(f"{status} {code or ''} {detail}")
        self.status, self.detail, self.code = status, detail, code


class WorkerApi:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0, transport: httpx.BaseTransport | None = None):
        self.c = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                              headers={"Authorization": f"Bearer {token}", "User-Agent": "nexfox-worker"})

    def _post(self, path: str, body: dict, retries: int = 0) -> dict:
        delay = 0.5
        for attempt in range(retries + 1):
            try:
                r = self.c.post(path, json=body)
            except httpx.HTTPError as e:
                if attempt >= retries:
                    raise ApiError(0, f"network: {type(e).__name__}") from e
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code >= 500 and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code >= 400:
                try:
                    j = r.json()
                except ValueError:
                    j = {"detail": r.text[:200]}
                if j.get("code") == "LEASE_LOST" or j.get("detail") == "lease_lost":
                    raise LeaseLost(path)
                raise ApiError(r.status_code, str(j.get("detail")), j.get("code"))
            return r.json()
        raise ApiError(0, "unreachable")

    def sync(self, body: dict) -> dict:
        return self._post("/worker/v1/sync", body, retries=1)

    def events(self, instance_id: int, body: dict) -> dict:
        return self._post(f"/worker/v1/instances/{instance_id}/events", body, retries=2)

    def intent(self, instance_id: int, body: dict) -> dict:
        # idempotent server-side by client_order_id: safe to retry with the same body
        return self._post(f"/worker/v1/instances/{instance_id}/intents", body, retries=2)

    def intent_result(self, instance_id: int, client_order_id: str, body: dict) -> dict:
        return self._post(f"/worker/v1/instances/{instance_id}/intents/{client_order_id}/result", body, retries=3)

    def job_result(self, job_id: int, body: dict) -> dict:
        return self._post(f"/worker/v1/jobs/{job_id}/result", body, retries=3)
