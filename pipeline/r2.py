from __future__ import annotations

import boto3

CACHE_IMMUTABLE = "public, max-age=86400, immutable"
CACHE_LATEST = "public, max-age=300"
LATEST_KEY = "gfs/latest.json"


def make_client(account_id: str, access_key_id: str, secret_access_key: str):
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def cycle_key(cycle_id: str, filename: str) -> str:
    return f"gfs/{cycle_id}/{filename}"


def content_type_for(filename: str) -> str:
    return "image/png" if filename.endswith(".png") else "application/json"


def cycles_to_delete(existing_cycle_ids: list[str], keep: int) -> list[str]:
    """cycle_id 格式 YYYYMMDDTHH → 字典序即時間序；保留最新 keep 輪。"""
    return sorted(existing_cycle_ids)[:-keep] if len(existing_cycle_ids) > keep else []


def upload_bytes(client, bucket: str, key: str, data: bytes, cache_control: str) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type_for(key),
        CacheControl=cache_control,
    )


def object_exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


def list_cycle_ids(client, bucket: str) -> list[str]:
    resp = client.list_objects_v2(Bucket=bucket, Prefix="gfs/", Delimiter="/")
    ids = []
    for p in resp.get("CommonPrefixes", []):
        ids.append(p["Prefix"].removeprefix("gfs/").rstrip("/"))
    return ids


def delete_cycle(client, bucket: str, cycle_id: str) -> None:
    resp = client.list_objects_v2(Bucket=bucket, Prefix=f"gfs/{cycle_id}/")
    keys = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
    if keys:
        client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
