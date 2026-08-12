"""Verify private R2 read/write/delete access without printing credentials."""
from datetime import datetime, timezone

from r2_store import client_and_bucket


def main():
    client, bucket = client_and_bucket()
    key = "_system/connection-test.txt"
    content = ("THIL R2 connection verified " + datetime.now(timezone.utc).isoformat()).encode()
    client.put_object(Bucket=bucket, Key=key, Body=content, ContentType="text/plain")
    result = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if result != content:
        raise RuntimeError("R2 verification content did not match")
    client.delete_object(Bucket=bucket, Key=key)
    print(f"R2 connection verified: private bucket '{bucket}' supports read, write and delete.")


if __name__ == "__main__":
    main()
