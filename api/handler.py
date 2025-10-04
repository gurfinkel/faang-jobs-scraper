import os, json, base64, time
import boto3
from boto3.dynamodb.conditions import Key, Attr
from datetime import datetime, timezone
from dateutil import parser as dateparser

DDB = boto3.resource("dynamodb")
TABLE = DDB.Table(os.environ["TABLE_NAME"])

def _ok(body):  # HTTP API v2
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }

def _parse_since(since: str) -> int:
    s = (since or "").strip().lower()
    now = int(time.time())
    if not s:
        return now - 30*86400
    if s.endswith("d"):
        try: return now - int(s[:-1]) * 86400
        except: pass
    if s.endswith("h"):
        try: return now - int(s[:-1]) * 3600
        except: pass
    # try absolute date
    try:
        dt = dateparser.parse(s)
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return now - 30*86400

def _b64e(d): return base64.urlsafe_b64encode(json.dumps(d).encode()).decode()
def _b64d(s): return json.loads(base64.urlsafe_b64decode(s.encode()))

def lambda_handler(event, _ctx):
    qs = (event.get("queryStringParameters") or {}) or {}
    company  = qs.get("company")
    category = (qs.get("category") or "it").lower()
    country  = (qs.get("country") or "").upper()
    remote_q = qs.get("remote")
    remote   = None
    if remote_q is not None:
        remote = 1 if str(remote_q).lower() in ("1","true","yes","y") else 0
    since    = _parse_since(qs.get("since", "30d"))
    limit    = min(max(int(qs.get("limit", "50")), 1), 200)
    cursor_s = qs.get("cursor")

    scan_index = None
    keycond = None
    filt = Attr("active").eq(1)

    # Choose the best index
    if company:
        scan_index = "GSICompanyPosted"
        keycond = Key("company").eq(company) & Key("posted_at").gte(since)
        # Additional filters
        if category:
            filt = filt & Attr("category").eq(category)
        if country:
            filt = filt & Attr("loc_country").eq(country)
        if remote is not None:
            filt = filt & Attr("remote").eq(remote)
    elif country:
        scan_index = "GSICountryPosted"
        keycond = Key("loc_country").eq(country) & Key("posted_at").gte(since)
        if category:
            filt = filt & Attr("category").eq(category)
        if remote is not None:
            filt = filt & Attr("remote").eq(remote)
    else:
        # default: IT across all companies
        scan_index = "GSICategoryPosted"
        keycond = Key("category").eq(category) & Key("posted_at").gte(since)
        if remote is not None:
            filt = filt & Attr("remote").eq(remote)

    kwargs = {
        "IndexName": scan_index,
        "KeyConditionExpression": keycond,
        "FilterExpression": filt,
        "Limit": limit,
        "ScanIndexForward": False,  # newest first
    }

    if cursor_s:
        try:
            kwargs["ExclusiveStartKey"] = _b64d(cursor_s)
        except Exception:
            pass

    resp = TABLE.query(**kwargs)
    items = resp.get("Items", [])
    next_cursor = _b64e(resp["LastEvaluatedKey"]) if "LastEvaluatedKey" in resp else None

    # Return only the essentials
    out = []
    for it in items:
        out.append({
            "company": it.get("company"),
            "url": it.get("url"),
            "title": it.get("title"),
            "description": it.get("description"),
            "category": it.get("category"),
            "posted_at": int(it.get("posted_at", 0)),
            "loc_country": it.get("loc_country"),
            "loc_admin1": it.get("loc_admin1"),
            "loc_city": it.get("loc_city"),
            "remote": 1 if int(it.get("remote", 0)) else 0,
        })

    return _ok({"items": out, "next_cursor": next_cursor})
