"""Real ASGI requests via FastAPI TestClient; isolated file-backed DB per test."""
import itertools
import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from payments import create_app
from payments.config import Settings
from payments.db import connect

logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('payments.api').setLevel(logging.CRITICAL)


def event(kind='payment_initiated', event_id='e1', tx='t1', **changes):
    return {'event_id': event_id, 'event_type': kind, 'transaction_id': tx,
        'merchant_id': 'm1', 'merchant_name': 'Test Merchant', 'amount': '100.25',
        'currency': 'INR', 'timestamp': '2026-01-01T00:00:00Z', **changes}


class APITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'payments.db')
        self.settings = Settings(database_path=self.path)
        self.app = create_app(self.settings)
        self.client = self.enterContext(TestClient(self.app))

    def post(self, body, status=201):
        response = self.client.post('/events', json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def details(self, tx='t1', **params):
        response = self.client.get('/transactions/' + tx, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def reasons(self, **params):
        response = self.client.get('/reconciliation/discrepancies', params={
            'as_of': '2026-01-03T00:00:00Z', **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_health_empty_endpoints_and_headers(self):
        for url in ['/health', '/transactions', '/reconciliation/summary', '/reconciliation/discrepancies']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.headers['X-Request-ID'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_swagger_redoc_and_openapi(self):
        for url in ['/docs', '/redoc', '/openapi.json']:
            self.assertEqual(self.client.get(url).status_code, 200)
        spec = self.client.get('/openapi.json').json()
        self.assertEqual(len(spec['paths']), 6)
        self.assertIn('EventIn', spec['components']['schemas'])
        self.assertIn('APIKeyHeader', spec['components']['securitySchemes'])
        parameters = spec['paths']['/transactions']['get']['parameters']
        self.assertIn('from', [p['name'] for p in parameters])
        self.assertIn('requestBody', spec['paths']['/events']['post'])

    def test_complete_lifecycle(self):
        for i, kind in enumerate(['payment_initiated', 'payment_processed', 'settled']):
            self.post(event(kind, f'e{i}'))
        data = self.details()
        self.assertEqual((data['status'], data['payment_status'], data['settlement_status']), ('settled', 'processed', 'settled'))
        self.assertEqual(data['amount_minor'], 10025)
        self.assertEqual(data['amount'], '100.25')
        self.assertEqual(data['event_count'], 3)
        self.assertEqual(self.reasons()['data'], [])

    def test_exact_duplicate(self):
        self.post(event())
        for _ in range(3):
            self.assertTrue(self.post(event(), 200)['duplicate'])
        self.assertEqual(self.details()['event_count'], 1)

    def test_canonical_duplicate(self):
        self.post(event())
        self.post(event(amount=100.25, currency='inr', timestamp='2026-01-01T05:30:00+05:30'), 200)
        self.assertEqual(self.details()['conflict_count'], 0)

    def test_same_id_different_payload_is_audited_once(self):
        self.post(event())
        for _ in range(3):
            result = self.post(event(amount='200.25'), 409)
            self.assertEqual(result['error']['code'], 'idempotency_key_reuse')
            self.assertTrue(result['error']['request_id'])
        data = self.details()
        self.assertEqual((data['amount'], data['event_count'], data['conflict_count']), ('100.25', 1, 1))
        self.assertEqual(data['conflicts'][0]['reason'], 'idempotency_key_reuse')

    def test_cross_transaction_id_reuse(self):
        self.post(event())
        self.post(event(tx='other'), 409)
        self.assertEqual(self.client.get('/transactions/other').status_code, 404)
        self.assertEqual(self.details()['conflicts'][0]['requested_transaction_id'], 'other')

    def test_transaction_invariants(self):
        self.post(event())
        for i, change in enumerate([{'amount': '1.25'}, {'merchant_id': 'm2'}, {'currency': 'USD'}]):
            with self.subTest(change=change):
                result = self.post(event('payment_processed', f'new{i}', **change), 409)
                self.assertEqual(result['error']['code'], 'transaction_mismatch')
        self.assertEqual(self.details()['event_count'], 1)
        self.assertEqual(self.details()['conflict_count'], 3)

    def test_out_of_order_all_permutations(self):
        kinds = ['payment_initiated', 'payment_processed', 'settled']
        for i, sequence in enumerate(itertools.permutations(kinds)):
            for kind in sequence:
                self.post(event(kind, f'{i}-{kind}', tx=f'tx{i}'))
            data = self.details(f'tx{i}')
            self.assertEqual((data['status'], data['event_count']), ('settled', 3))

    def test_conflicting_outcomes_are_preserved(self):
        self.post(event('payment_processed'))
        self.post(event('payment_failed', 'e2'))
        self.post(event('settled', 'e3'))
        data = self.reasons()['data'][0]
        self.assertEqual(data['status'], 'conflicted')
        self.assertIn('conflicting_payment_outcomes', data['reasons'])
        self.assertIn('settled_failed_payment', data['reasons'])

    def test_failed_then_settled(self):
        self.post(event('payment_failed'))
        self.post(event('settled', 'e2'))
        data = self.reasons()['data'][0]
        self.assertEqual(data['status'], 'failed')
        self.assertEqual(data['settlement_status'], 'settled')
        self.assertIn('settled_without_processed', data['reasons'])

    def test_settlement_before_processed_is_reconciled_when_processed_arrives(self):
        self.post(event('settled'))
        self.assertIn('settled_without_processed', self.reasons()['data'][0]['reasons'])
        self.post(event('payment_processed', 'e2'))
        self.assertEqual(self.reasons()['data'], [])

    def test_distinct_settlements_are_flagged_but_exact_retries_are_not(self):
        self.post(event('payment_processed'))
        self.post(event('settled', 'e2'))
        self.post(event('settled', 'e2'), 200)
        self.assertEqual(self.reasons()['data'], [])
        self.post(event('settled', 'e3'))
        self.assertIn('multiple_settlement_events', self.reasons()['data'][0]['reasons'])

    def test_grace_boundary_and_aging_clock(self):
        self.post(event('payment_processed'))
        self.assertEqual(self.reasons(as_of='2026-01-01T23:59:59Z')['data'], [])
        self.assertEqual(self.reasons(as_of='2026-01-02T00:00:00Z')['pagination']['total'], 1)
        self.post(event('settled', 'e2', timestamp='2026-01-04T00:00:00Z'))
        # as_of is the aging clock, not an event-history cutoff.
        self.assertEqual(self.reasons(as_of='2026-01-02T00:00:00Z')['data'], [])

    def test_filters_dates_sort_and_pagination(self):
        for i in range(5):
            self.post(event(event_id=f'e{i}', tx=f't{i}', amount=str(i + 1),
                merchant_id='m1' if i < 3 else 'm2', timestamp=f'2026-01-0{i+1}T00:00:00Z'))
        response = self.client.get('/transactions', params={'merchant_id': 'm1', 'status': 'initiated',
            'from': '2026-01-01T00:00:00Z', 'to': '2026-01-04T00:00:00Z',
            'sort_by': 'amount', 'sort_order': 'desc', 'page': 2, 'page_size': 1})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['pagination']['total'], 3)
        self.assertEqual(data['data'][0]['amount'], '2.00')

    def test_summary_keeps_currencies_separate(self):
        self.post(event())
        self.post(event(event_id='e2', tx='t2', currency='USD', amount='2.25'))
        response = self.client.get('/reconciliation/summary', params={'group_by': 'merchant'})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['group_by'], ['merchant', 'currency'])
        self.assertEqual(data['pagination']['total_groups'], 2)
        self.assertEqual({r['currency']: r['total_amount'] for r in data['data']}, {'INR': '100.25', 'USD': '2.25'})
        self.assertNotIn('status', data['data'][0])

    def test_summary_counts_transactions_not_event_join_rows(self):
        for i, kind in enumerate(['payment_initiated', 'payment_processed', 'settled']):
            self.post(event(kind, f'e{i}'))
        data = self.client.get('/reconciliation/summary').json()['data'][0]
        self.assertEqual(data['transaction_count'], 1)
        self.assertEqual(data['total_amount'], '100.25')
        self.assertEqual(data['settled_amount'], '100.25')

    def test_paged_history_and_conflicts(self):
        for i, kind in enumerate(['payment_initiated', 'payment_processed', 'settled']):
            self.post(event(kind, f'e{i}'))
        self.post(event(event_id='e0', amount='200'), 409)
        data = self.details(history_page=2, history_page_size=1, conflict_page_size=1)
        self.assertEqual(data['history_pagination']['total'], 3)
        self.assertEqual(len(data['events']), 1)
        self.assertEqual(len(data['conflicts']), 1)

    def test_invalid_query_values(self):
        urls = ['/transactions?page=0', '/transactions?page_size=201', '/transactions?status=bad',
            '/transactions?sort_by=amount;DROP', '/transactions?sort_order=up', '/transactions?unknown=1',
            '/transactions?page=1&page=2', '/transactions?from=2026-01-01',
            '/transactions?from=2026-01-02T00:00:00Z&to=2026-01-01T00:00:00Z',
            '/reconciliation/summary?group_by=merchant,merchant', '/reconciliation/summary?group_by=evil',
            '/reconciliation/discrepancies?reason=evil', '/reconciliation/discrepancies?grace_hours=-1']
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()['error']['code'], 'validation_error')

    def test_invalid_event_fields(self):
        for change in [{'amount': '1.001'}, {'amount': 0}, {'amount': -1}, {'amount': True},
            {'amount': 'NaN'}, {'amount': 'Infinity'}, {'amount': '10000000000'},
            {'amount': '1.00000000000000000000000000000000001'}, {'amount': '1e-10000000'},
            {'event_type': 'refund'}, {'timestamp': '2026-01-01'}, {'timestamp': '2026-01-01T00:00:00'},
            {'timestamp': '1900-01-01T00:00:00Z'}, {'currency': 'JPY'}, {'event_id': '../oops'},
            {'merchant_name': ''}, {'merchant_name': 'x\n'}, {'extra_field': 1}]:
            with self.subTest(change=change):
                self.post(event(**change), 422)
        for body in [[], None, {}, {'event_id': 'only'}]:
            response = self.client.post('/events', content=json.dumps(body), headers={'content-type': 'application/json'})
            self.assertEqual(response.status_code, 422, response.text)

    def test_strict_json_and_content_type(self):
        for content in ['{', '{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}']:
            response = self.client.post('/events', content=content, headers={'content-type': 'application/json'})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.post('/events', content='{}').status_code, 415)

    def test_exact_json_precision_is_not_lost(self):
        raw = json.dumps(event()).replace('"100.25"', '0.10000000000000001')
        response = self.client.post('/events', content=raw, headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 422, response.text)

    def test_body_limit(self):
        response = self.client.post('/events', content=' ' * 16385, headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 413)

    def test_api_key_and_public_docs(self):
        key = 'local-test-key-more-than-24-characters'
        app = create_app(Settings(database_path=self.path, api_key=key, require_api_key=True))
        with TestClient(app) as client:
            for url in ['/transactions', '/reconciliation/summary', '/reconciliation/discrepancies']:
                self.assertEqual(client.get(url).status_code, 401)
                self.assertEqual(client.get(url, headers={'X-API-Key': key}).status_code, 200)
            self.assertEqual(client.post('/events', json=event()).status_code, 401)
            for url in ['/health', '/docs', '/openapi.json']:
                self.assertEqual(client.get(url).status_code, 200)

    def test_production_key_is_required(self):
        with self.assertRaises(ValueError):
            Settings(require_api_key=True, api_key='short')

    def test_concurrent_identical_posts(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(lambda _: self.client.post('/events', json=event()).status_code, range(24)))
        self.assertEqual(codes.count(201), 1)
        self.assertEqual(codes.count(200), 23)
        self.assertEqual(self.details()['event_count'], 1)

    def test_busy_database_returns_retryable_error(self):
        db = connect(self.path)
        try:
            db.execute('BEGIN IMMEDIATE')
            app = create_app(Settings(database_path=self.path, db_timeout_ms=10))
            # Schema already initialized; use ASGI client without another lifespan migration.
            with TestClient(app) as client:
                response = client.post('/events', json=event())
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.headers['Retry-After'], '1')
        finally:
            db.rollback()
            db.close()

    def test_persistence_after_app_restart(self):
        self.post(event())
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get('/transactions/t1').json()['event_count'], 1)

    def test_not_found_and_method_not_allowed(self):
        for method, url, expected in [('get', '/nope', 404), ('get', '/transactions/missing', 404), ('delete', '/events', 405)]:
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, expected)
            self.assertEqual(response.json()['error']['request_id'], response.headers['X-Request-ID'])

    def test_sql_integrity_and_index_plan(self):
        self.post(event())
        db = connect(self.path)
        try:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            plan = db.execute('EXPLAIN QUERY PLAN SELECT * FROM transactions WHERE merchant_id=? AND status=? ORDER BY created_at,transaction_id LIMIT 10', ('m1', 'initiated')).fetchall()
            self.assertIn('idx_transactions_merchant_status_created', str([tuple(r) for r in plan]))
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
