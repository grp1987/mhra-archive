"""Private Cloudflare R2 access for the THIL archive."""
from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.config import Config


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
