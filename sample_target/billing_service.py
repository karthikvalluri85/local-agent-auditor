"""
Sample target file for the Local Agent Auditor demo.
Deliberately contains realistic cost-leak, security, and anti-pattern issues
so the audit triad has something real to catch.
DO NOT use this code in production — it's a fixture for the PoC.
"""

import boto3
import requests

# --- Hardcoded credentials (security anti-pattern) ---
AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
DB_PASSWORD = "SuperSecret123!"

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name="us-east-1",
)

ec2 = boto3.client("ec2", region_name="us-east-1")


def get_all_customer_records(customer_ids):
    """
    Cost leak: fires one API call per customer instead of a single
    batched call. At scale this multiplies request costs and latency.
    """
    results = []
    for cid in customer_ids:
        # N+1 network call pattern
        resp = requests.get(f"https://api.internal.example.com/customers/{cid}")
        results.append(resp.json())
    return results


def backup_all_buckets():
    """
    Cost leak: lists and re-uploads every object on every run with no
    incremental/delta check, and never tears down the oversized
    provisioned EC2 instance used to do it.
    """
    buckets = s3.list_buckets()["Buckets"]
    for bucket in buckets:
        objects = s3.list_objects_v2(Bucket=bucket["Name"])
        for obj in objects.get("Contents", []):
            # Re-copies every object every time, full re-upload, no diff check
            s3.copy_object(
                Bucket=f"{bucket['Name']}-backup",
                CopySource={"Bucket": bucket["Name"], "Key": obj["Key"]},
                Key=obj["Key"],
            )


def launch_oversized_worker():
    """
    Cost leak: launches an x1e.32xlarge (largest, most expensive instance
    family) for a lightweight cron job that runs every 5 minutes and
    never gets terminated.
    """
    ec2.run_instances(
        ImageId="ami-0abcdef1234567890",
        InstanceType="x1e.32xlarge",  # ~$26/hr, wildly oversized for a cron job
        MinCount=1,
        MaxCount=1,
    )


def run_query(user_input):
    """
    Security anti-pattern: raw string interpolation into SQL — classic
    SQL injection vector.
    """
    import sqlite3
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    query = "SELECT * FROM users WHERE name = '" + user_input + "'"
    cursor.execute(query)
    return cursor.fetchall()


def log_debug_info(payload):
    """
    Security anti-pattern: logs full request payloads including secrets
    at DEBUG level, which often ends up shipped to a third-party log
    aggregator.
    """
    print(f"DEBUG: full payload dump -> {payload}")
    print(f"DEBUG: using DB_PASSWORD={DB_PASSWORD}")


def fetch_exchange_rates():
    """
    Cost leak: polls a paid third-party API every second in a tight loop
    with no caching, no backoff, and no rate limiting.
    """
    import time
    rates = []
    while True:
        r = requests.get("https://api.exchangerate.example.com/latest")
        rates.append(r.json())
        time.sleep(1)  # hammering a metered API endlessly
