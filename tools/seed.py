"""Replay a JSON array through the shared domain function or POST /events."""
import argparse
import json
import os
import sys
import time
from collections import Counter
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from payments.db import connect, initialize, transaction
from payments.domain import APIError, ingest, normalize_event


def http_ingest(base_url, event, api_key):
    request = Request(base_url.rstrip('/') + '/events', data=json.dumps(event, default=str).encode(),
        headers={'Content-Type': 'application/json', 'X-API-Key': api_key}, method='POST')
    for attempt in range(5):
        try:
            with urlopen(request, timeout=20) as response:
                return response.status
        except HTTPError as error:
            status = error.code
            error.close()
            if status != 503:
                return status
        except (URLError, TimeoutError):
            if attempt == 4:
                raise
        time.sleep(0.1 * 2 ** attempt)
    return 503


def load(events, database=None, url=None, api_key='', batch_size=500):
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    counts = Counter()
    if url:
        for event in events:
            counts[http_ingest(url, event, api_key)] += 1
    else:
        initialize(database)
        db = connect(database)
        try:
            for offset in range(0, len(events), batch_size):
                with transaction(db, write=True):
                    for event in events[offset:offset + batch_size]:
                        _, status = ingest(db, normalize_event(event))
                        counts[status] += 1
        finally:
            db.close()
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', default='sample_events.json')
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--database')
    target.add_argument('--url', help='Send events through the running API instead of writing SQLite directly')
    parser.add_argument('--expect-conflicts', type=int, default=0)
    args = parser.parse_args()
    try:
        with open(args.file, encoding='utf-8') as f:
            events = json.load(f, parse_float=Decimal)
        if not isinstance(events, list):
            parser.error('Input must be a JSON array')
        started = time.perf_counter()
        counts = load(events, database=args.database or os.environ.get('DATABASE_PATH', 'data/payments.db'),
            url=args.url, api_key=os.environ.get('API_KEY', ''))
    except (OSError, ValueError, APIError) as error:
        print(f'Seed failed: {error}. Previously committed batches remain; retry is idempotent.', file=sys.stderr)
        return 1
    print(json.dumps({'deliveries': len(events), 'status_counts': counts,
        'seconds': round(time.perf_counter() - started, 3)}, indent=2))
    if any(status not in (200, 201, 409) for status in counts) or counts.get(409, 0) != args.expect_conflicts:
        print(f'Unexpected response counts; expected exactly {args.expect_conflicts} conflicts (409).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
