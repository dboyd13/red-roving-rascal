"""SigV4 request signing for API Gateway IAM auth."""
from __future__ import annotations

import hashlib
import hmac
import datetime
import os
from urllib.parse import urlparse, quote

import httpx


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def sigv4_headers(
    method: str,
    url: str,
    body: bytes = b"",
    region: str | None = None,
    service: str = "execute-api",
    access_key: str | None = None,
    secret_key: str | None = None,
    session_token: str | None = None,
) -> dict[str, str]:
    """Generate SigV4 authorization headers for a request."""
    access_key = access_key or os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret_key = secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    session_token = session_token or os.environ.get("AWS_SESSION_TOKEN")
    region = region or os.environ.get("AWS_REGION", "us-east-1")

    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = quote(parsed.path or "/", safe="/")

    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_querystring = parsed.query
    payload_hash = hashlib.sha256(body).hexdigest()

    headers_to_sign = {"host": host, "x-amz-date": amz_date}
    if session_token:
        headers_to_sign["x-amz-security-token"] = session_token

    signed_header_keys = sorted(headers_to_sign.keys())
    signed_headers = ";".join(signed_header_keys)
    canonical_headers = "".join(f"{k}:{headers_to_sign[k]}\n" for k in signed_header_keys)

    canonical_request = "\n".join([
        method.upper(),
        path,
        canonical_querystring,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = _get_signature_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    result = {
        "Authorization": authorization,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }
    if session_token:
        result["x-amz-security-token"] = session_token
    return result
