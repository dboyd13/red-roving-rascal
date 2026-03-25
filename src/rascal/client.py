"""Client for the rascal API."""
from __future__ import annotations

from typing import Callable

import httpx

from rascal.models import JobRequest, JobResponse
from rascal.auth import sigv4_headers


# Type alias for auth providers: (method, url, body) -> headers dict
AuthSigner = Callable[[str, str, bytes], dict[str, str]]


def sigv4_auth(region: str | None = None) -> AuthSigner:
    """Create a SigV4 auth signer using environment credentials."""
    def signer(method: str, url: str, body: bytes = b"") -> dict[str, str]:
        return sigv4_headers(method, url, body, region=region)
    return signer


class RascalClient:
    """Client for interacting with a rascal backend.

    Args:
        endpoint: Base URL of the API.
        auth: Optional auth signer. Use sigv4_auth() for SigV4,
              or provide a custom callable for other auth schemes.
        timeout: Request timeout in seconds.

    Examples:
        # No auth (local dev)
        client = RascalClient("http://localhost:8080")

        # SigV4 auth
        client = RascalClient("https://api.example.com/v1", auth=sigv4_auth("us-west-2"))

        # Custom auth (e.g. CloudAuth)
        client = RascalClient("https://api.example.com/v1", auth=my_cloud_auth_signer)
    """

    def __init__(
        self,
        endpoint: str,
        auth: AuthSigner | None = None,
        timeout: float = 30.0,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.auth = auth
        self.timeout = timeout

    def _headers(self, method: str, url: str, body: bytes = b"") -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth:
            headers.update(self.auth(method, url, body))
        return headers

    def run_job(
        self,
        inputs: list[str],
        target: str,
        threshold: float = 0.8,
        tags: list[str] | None = None,
    ) -> JobResponse:
        """Submit inputs for processing."""
        request = JobRequest(
            inputs=inputs,
            target=target,
            threshold=threshold,
            tags=tags or [],
        )
        url = f"{self.endpoint}/jobs"
        body = request.model_dump_json().encode()
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.post(url, content=body, headers=self._headers("POST", url, body))
            resp.raise_for_status()
            return JobResponse.model_validate_json(resp.content)

    def get_job(self, job_id: str) -> JobResponse:
        """Poll for job results."""
        url = f"{self.endpoint}/jobs/{job_id}"
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.get(url, headers=self._headers("GET", url))
            resp.raise_for_status()
            return JobResponse.model_validate_json(resp.content)

    def health(self) -> dict:
        """Check backend health."""
        url = f"{self.endpoint}/health"
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.get(url)
            resp.raise_for_status()
            return resp.json()
