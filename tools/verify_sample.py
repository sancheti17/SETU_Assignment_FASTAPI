"""Verify the bundled real sample in a temporary DB: python -m tools.verify_sample."""
import hashlib
import json
import logging
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path
from fastapi.testclient import TestClient
from payments import create_app
from payments.config import Settings
from payments.db import connect
from tools.seed import load


def main():
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('payments.api').setLevel(logging.WARNING)
    path = Path(__file__).resolve().parents[1] / 'sample_events.json'
    raw = path.read_bytes()
    events = json.loads(raw, parse_float=Decimal)
    unique_events = len({e['event_id'] for e in events})
    tx_count = len({e['transaction_id'] for e in events})
    merchant_count = len({e['merchant_id'] for e in events})
    with tempfile.TemporaryDirectory() as directory:
        database = str(Path(directory) / 'verification.db')
        first = load(events, database=database)
        assert first == {201: unique_events, 200: len(events) - unique_events}, first
        replay = load(events, database=database)
        assert replay == {200: len(events)}, replay
        db = connect(database)
        try:
            counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                for table in ['merchants', 'transactions', 'payment_events', 'ingestion_conflicts']}
            assert counts == {'merchants': merchant_count, 'transactions': tx_count,
                'payment_events': unique_events, 'ingestion_conflicts': 0}, counts
            integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
            foreign_keys = [tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
            assert integrity == 'ok' and foreign_keys == []
            statuses = dict(db.execute('SELECT status,COUNT(*) FROM transactions GROUP BY status'))
            total_minor = db.execute('SELECT SUM(amount_minor) FROM transactions').fetchone()[0]
            plans = [tuple(r) for r in db.execute('EXPLAIN QUERY PLAN SELECT * FROM transactions '
                'WHERE merchant_id=? AND status=? ORDER BY created_at,transaction_id LIMIT 10',
                ('merchant_2', 'settled'))]
            assert any('idx_transactions_merchant_status_created' in str(r) for r in plans), plans
        finally:
            db.close()
        with TestClient(create_app(Settings(database_path=database))) as client:
            response = client.get('/transactions', params={'page_size': 10})
            assert response.status_code == 200, response.text
            assert response.json()['pagination']['total'] == tx_count
            summary = client.get('/reconciliation/summary', params={
                'group_by': 'merchant,status', 'page_size': 200, 'as_of': '2026-05-01T00:00:00Z'})
            assert summary.status_code == 200, summary.text
            rows = summary.json()['data']
            assert sum(r['transaction_count'] for r in rows) == tx_count
            assert sum(r['total_amount_minor'] for r in rows) == total_minor
            discrepancies = client.get('/reconciliation/discrepancies', params={
                'page_size': 200, 'as_of': '2026-05-01T00:00:00Z'})
            assert discrepancies.status_code == 200, discrepancies.text
            reasons = {}
            for reason in ['processed_not_settled', 'settled_failed_payment', 'settled_without_processed',
                'conflicting_payment_outcomes', 'multiple_settlement_events', 'ingestion_conflict']:
                response = client.get('/reconciliation/discrepancies', params={
                    'reason': reason, 'as_of': '2026-05-01T00:00:00Z'})
                assert response.status_code == 200, response.text
                reasons[reason] = response.json()['pagination']['total']
        result = {'sample_sha256': hashlib.sha256(raw).hexdigest(),
            'deliveries': len(events), 'input_event_types': dict(Counter(e['event_type'] for e in events)),
            'first_ingestion_status_counts': first, 'replay_status_counts': replay,
            'database_counts': counts, 'transaction_status_counts': statuses,
            'total_amount_minor': total_minor, 'discrepant_transactions': discrepancies.json()['pagination']['total'],
            'discrepancy_as_of': '2026-05-01T00:00:00Z', 'grace_hours': 24,
            'discrepancy_reason_counts_overlap': reasons, 'integrity_check': integrity,
            'foreign_key_violations': foreign_keys, 'query_plan': plans,
            'transport': 'direct domain ingestion; queries through FastAPI TestClient'}
        print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
