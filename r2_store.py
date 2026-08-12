"""Private Cloudflare R2 access for the THIL archive."""
from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


HERE = Path(__file__).resolve().parent


def load_env(path: Path | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = path or HERE / "r2.env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    for key in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    missing = [k for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET")
               if not values.get(k)]
    if missing:
        raise RuntimeError("Missing R2 settings: " + ", ".join(missing))
    return values


def client_and_bucket():
    cfg = load_env()
    client = boto3.client(
        "s3",
        endpoint_url=cfg["R2_ENDPOINT"].rstrip("/"),
        aws_access_key_id=cfg["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )
    return client, cfg["R2_BUCKET"]


def object_key(storage_name: str) -> str:
    """Stable, immutable key for an MHRA document version."""
    if not storage_name.isalnum():
        raise ValueError("Invalid MHRA storage name")
    return f"mhra/documents/{storage_name[:2]}/{storage_name}.pdf"


def upload_file(storage_name: str, path: str) -> tuple[str, int]:
    """Upload a PDF privately, returning its R2 key and byte count."""
    client, bucket = client_and_bucket()
    key = object_key(storage_name)
    client.upload_file(
        path,
        bucket,
        key,
        ExtraArgs={"ContentType": "application/pdf"},
    )
    return key, os.path.getsize(path)


def open_object(storage_name: str, byte_range: str | None = None):
    """Open a private object. The caller must close response['Body']."""
    client, bucket = client_and_bucket()
    kwargs = {"Bucket": bucket, "Key": object_key(storage_name)}
    if byte_range:
        kwargs["Range"] = byte_range
    return client.get_object(**kwargs)


def object_exists(storage_name: str) -> bool:
    client, bucket = client_and_bucket()
    try:
        client.head_object(Bucket=bucket, Key=object_key(storage_name))
        return True
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            return False
        raise
