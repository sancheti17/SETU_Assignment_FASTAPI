"""FastAPI HTTP layer; domain state and parameterized SQL live separately."""
import hmac
import json
import logging
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException
from .config import Settings
from .db import initialize, session
from .domain import APIError, ingest, normalize_event
from .queries import discrepancies, list_transactions, summary, transaction_details
from .schemas import (
    DiscrepancyList, DiscrepancyQuery, ErrorResponse, EventIn, EventReceipt,
    HistoryQuery, ListQuery, SummaryOut, SummaryQuery, TransactionDetail, TransactionList,
)

logger = logging.getLogger('payments.api')
api_key_header = APIKeyHeader(name='X-API-Key', auto_error=False)


def settings_for(request: Request) -> Settings:
    return request.app.state.settings


Config = Annotated[Settings, Depends(settings_for)]


def authorize(settings: Config, supplied: Annotated[str | None, Security(api_key_header)] = None):
    if settings.api_key and not hmac.compare_digest(settings.api_key.encode(), (supplied or '').encode()):
        raise APIError(401, 'unauthorized', 'Missing or invalid X-API-Key')


def error_response(request, status, code, message, **extras):
    body = {'error': {'code': code, 'message': message,
        'request_id': getattr(request.state, 'request_id', ''), **extras}}
    return JSONResponse(body, status_code=status)


class StrictJSONRoute(APIRoute):
    """Bounded exact-Decimal parsing before FastAPI/Pydantic validates EventIn."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def strict_handler(request: Request):
            # Check the key before consuming any body; the dependency documents it too.
            settings = settings_for(request)
            authorize(settings, request.headers.get('X-API-Key'))
            media = request.headers.get('content-type', '').split(';')[0].strip().lower()
            if media != 'application/json' and not (media.startswith('application/') and media.endswith('+json')):
                raise APIError(415, 'unsupported_media_type', 'Use Content-Type: application/json')
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_body_bytes:
                    raise APIError(413, 'payload_too_large', 'Event payload must not exceed 16 KiB')
                chunks.append(chunk)
            raw = b''.join(chunks)

            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('Duplicate JSON field')
                    result[key] = value
                return result

            def invalid_constant(_value):
                raise ValueError('Non-finite JSON number')

            try:
                parsed = json.loads(raw, parse_float=Decimal, parse_constant=invalid_constant,
                    object_pairs_hook=unique_object)
            except (ValueError, UnicodeError, RecursionError):
                raise APIError(400, 'invalid_json', 'Use valid JSON with unique keys and finite numbers') from None
            # Cache exact parsed values so FastAPI does not first round JSON money to floats.
            request._body = raw
            request._json = parsed
            return await handler(request)
        return strict_handler


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(initialize, settings.database_path)
        yield

    app = FastAPI(
        title='Setu Payment Reconciliation API', version='2.0.0', lifespan=lifespan,
        description='Idempotent payment event ingestion and SQL reconciliation. '
        'Amounts are exact decimal strings. See README for projection and aging semantics.',
    )
    app.state.settings = settings

    @app.middleware('http')
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception('Unhandled request error')
            response = error_response(request, 500, 'internal_error', 'Unexpected server error')
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        route = request.scope.get('route')
        logger.info(json.dumps({'request_id': request.state.request_id, 'method': request.method,
            'route': getattr(route, 'path', 'unmatched'), 'status': response.status_code,
            'duration_ms': round((time.perf_counter() - started) * 1000, 2)}))
        return response

    @app.exception_handler(APIError)
    async def domain_error(request, error):
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        details = [{'loc': list(e['loc']), 'message': e['msg'], 'type': e['type']} for e in error.errors()]
        return error_response(request, 422, 'validation_error', 'Request validation failed', details=details)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        response = error_response(request, error.status_code,
            {404: 'not_found', 405: 'method_not_allowed'}.get(error.status_code, 'http_error'), str(error.detail))
        if error.headers:
            response.headers.update(error.headers)
        return response

    @app.exception_handler(sqlite3.OperationalError)
    async def db_error(request, error):
        if (getattr(error, 'sqlite_errorcode', 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            response = error_response(request, 503, 'database_busy', 'Retry with backoff and the same event_id')
            response.headers['Retry-After'] = '1'
            return response
        logger.error('Database operation failed', exc_info=(type(error), error, error.__traceback__))
        return error_response(request, 500, 'internal_error', 'Database operation failed')

    @app.get('/', include_in_schema=False)
    def index():
        return {'service': app.title, 'docs': '/docs', 'health': '/health'}

    @app.get('/health', tags=['operations'])
    def health(config: Config):
        with session(config) as db:
            db.execute('SELECT transaction_id FROM transactions LIMIT 1').fetchone()
            version = db.execute('PRAGMA user_version').fetchone()[0]
        return {'status': 'ok', 'schema_version': version}

    common_errors = {code: {'model': ErrorResponse} for code in (401, 404, 422, 503)}
    router = APIRouter(dependencies=[Depends(authorize)], responses=common_errors)
    events = APIRouter(route_class=StrictJSONRoute, dependencies=[Depends(authorize)], responses=common_errors)

    @events.post('/events', status_code=201, response_model=EventReceipt, tags=['events'],
        responses={200: {'model': EventReceipt, 'description': 'Canonical duplicate; no new event'},
                   **{code: {'model': ErrorResponse} for code in (400, 409, 413, 415)}})
    def post_event(event: EventIn, request: Request, response: Response, config: Config):
        with session(config, write=True) as db:
            result, status = ingest(db, normalize_event(event.model_dump(mode='json')))
        if status == 409:
            result['error']['request_id'] = request.state.request_id
            return JSONResponse(result, status_code=409)
        response.status_code = status
        response.headers['Location'] = '/transactions/' + result['transaction_id']
        return result

    @router.get('/transactions', response_model=TransactionList, tags=['transactions'])
    def transactions(request: Request, config: Config, filters: Annotated[ListQuery, Query()]):
        with session(config) as db:
            return list_transactions(db, request.query_params)

    @router.get('/transactions/{transaction_id}', response_model=TransactionDetail, tags=['transactions'])
    def detail(transaction_id: str, request: Request, config: Config, filters: Annotated[HistoryQuery, Query()]):
        with session(config) as db:
            return transaction_details(db, transaction_id, request.query_params)

    @router.get('/reconciliation/summary', response_model=SummaryOut, response_model_exclude_none=True, tags=['reconciliation'])
    def reconciliation_summary(request: Request, config: Config, filters: Annotated[SummaryQuery, Query()]):
        with session(config) as db:
            return summary(db, request.query_params, config.settlement_grace_hours)

    @router.get('/reconciliation/discrepancies', response_model=DiscrepancyList, tags=['reconciliation'])
    def reconciliation_discrepancies(request: Request, config: Config, filters: Annotated[DiscrepancyQuery, Query()]):
        with session(config) as db:
            return discrepancies(db, request.query_params, config.settlement_grace_hours)

    app.include_router(events)
    app.include_router(router)
    return app
