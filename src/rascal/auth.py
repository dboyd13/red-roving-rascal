"""SigV4 request signing for API Gateway IAM auth using botocore."""
from __future__ import annotations

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def sigv4_headers(
    method: str,
    url: str,
    body: bytes = b"",
    region: str | None = None,
    service: str = "execute-api",
) -> dict[str, str]:
    """Generate SigV4 authorization headers for a request.

    Uses the default boto3 credential chain (env vars, ~/.aws/credentials,
    instance profile, etc.).
    """
    session = boto3.Session(region_name=region)
    credentials = session.get_credentials().get_frozen_credentials()
    region = region or session.region_name or "us-east-1"

    headers = {"Content-Type": "application/json"}
    request = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(credentials, service, region).add_auth(request)

    return dict(request.headers)
