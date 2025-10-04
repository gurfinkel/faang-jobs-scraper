import os, time, json
from decimal import Decimal
from typing import Dict, Iterable, Set
import boto3
from boto3.dynamodb.conditions import Key

_DDB = boto3.resource("dynamodb")
_TABLE = _DDB.Table(os.environ["TABLE_NAME"])

# -------- Locking to avoid overlap --------
def acquire_lock(lock_key: str, ttl_sec: int = 5400) -> bool:
    now = int(time.time())
    try:
        _TABLE.put_item(
            Item={
                "company": "__lock__",   # PK
                "url": lock_key,         # SK
                "lock_expires_at": Decimal(now + ttl_sec),
            },
            ConditionExpression="attribute_not_exists(company) OR lock_expires_at <= :now",
            ExpressionAttributeValues={":now": Decimal(now)},
        )
        return True
    except _TABLE.meta.client.exceptions.ConditionalCheckFailedException:
        return False

def release_lock(lock_key: str) -> None:
    try:
        _TABLE.delete_item(Key={"company": "__lock__", "url": lock_key})
    except Exception:
        pass

# -------- Read helpers --------
def list_urls(company: str) -> Set[str]:
    urls: Set[str] = set()
    resp = _TABLE.query(
        KeyConditionExpression=Key("company").eq(company),
        ProjectionExpression="#u",
        ExpressionAttributeNames={"#u": "url"},
    )
    urls |= {it["url"] for it in resp.get("Items", []) if "url" in it}
    while "LastEvaluatedKey" in resp:
        resp = _TABLE.query(
            KeyConditionExpression=Key("company").eq(company),
            ProjectionExpression="#u",
            ExpressionAttributeNames={"#u": "url"},
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        urls |= {it["url"] for it in resp.get("Items", []) if "url" in it}
    return urls

# -------- Write helpers --------
def _put_item(company: str, url: str, item: Dict, now_ts: int):
    # Defaults & normalization
    title       = item.get("title") or ""
    desc        = item.get("description") or ""
    category    = item.get("category") or "other"
    posted_at   = int(item.get("posted_at") or now_ts)
    loc_country = (item.get("loc_country") or "").upper()
    loc_admin1  = item.get("loc_admin1") or ""
    loc_city    = item.get("loc_city") or ""
    remote      = int(bool(item.get("remote")))
    _TABLE.put_item(
        Item={
            "company": company,
            "url": url,
            "company_url": url,   # used as a range in the old GSIActive
            "title": title,
            "description": desc,
            "category": category,
            "posted_at": Decimal(posted_at),
            "loc_country": loc_country,
            "loc_admin1": loc_admin1,
            "loc_city": loc_city,
            "remote": remote,
            "active": 1,
            "last_seen_at": Decimal(now_ts),
        }
    )

def batch_upsert_items(company: str, url_to_item: Dict[str, Dict]):
    """Chunk writer using batch_writer (idempotent on PK/SK)."""
    if not url_to_item:
        return
    now_ts = int(time.time())
    with _TABLE.batch_writer(overwrite_by_pkeys=["company", "url"]) as bw:
        for url, item in url_to_item.items():
            title       = item.get("title") or ""
            desc        = item.get("description") or ""
            category    = item.get("category") or "other"
            posted_at   = int(item.get("posted_at") or now_ts)
            loc_country = (item.get("loc_country") or "").upper()
            loc_admin1  = item.get("loc_admin1") or ""
            loc_city    = item.get("loc_city") or ""
            remote      = int(bool(item.get("remote")))
            bw.put_item(Item={
                "company": company,
                "url": url,
                "company_url": url,
                "title": title,
                "description": desc,
                "category": category,
                "posted_at": Decimal(posted_at),
                "loc_country": loc_country,
                "loc_admin1": loc_admin1,
                "loc_city": loc_city,
                "remote": remote,
                "active": 1,
                "last_seen_at": Decimal(now_ts),
            })

def finalize_company(company: str, discovered_urls: Iterable[str]) -> Dict[str, int]:
    """Delete items not discovered in this run."""
    discovered = set(discovered_urls)
    existing = list_urls(company)
    to_delete = existing - discovered
    deleted = 0
    if to_delete:
        with _TABLE.batch_writer() as bw:
            for url in to_delete:
                bw.delete_item(Key={"company": company, "url": url})
                deleted += 1
    return {"deleted": deleted}
