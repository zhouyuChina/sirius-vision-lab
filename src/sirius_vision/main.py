"""API and session-authenticated review console."""
import base64
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import issue_session, read_session, verify_password
from .config import Settings
from .images import MAX_BYTES, ImageError, decode_image, fetch_image, image_type
from .provider import Adapter, ChatAdapter, ProviderError
from .storage import Storage
from .templates import TEMPLATES

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / 'static/admin'


class Analyze(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: Literal['avatar_tag', 'garment_attr']
    image_url: str | None = Field(default=None, max_length=4096)
    image_base64: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def one_image(self) -> 'Analyze':
        if bool(self.image_url) == bool(self.image_base64):
            raise ValueError('Provide exactly one image')
        if len(json.dumps(self.options, allow_nan=False)) > 16384:
            raise ValueError('Options too large')
        return self


class Login(BaseModel):
    password: str = Field(max_length=1024)


class Review(BaseModel):
    review: Literal['correct', 'wrong', 'uncertain']
    review_note: str = Field(default='', max_length=4000)


class RecordQuery(BaseModel):
    task: Literal['avatar_tag', 'garment_attr'] | None = None
    status: Literal['success', 'parse_error', 'provider_error'] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    cursor: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=50, ge=1, le=100)

    def filters(self) -> dict:
        result = self.model_dump()
        for key in ('date_from', 'date_to'):
            if result[key]:
                value = result[key]
                result[key] = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
        return result


class BodyLimit:
    """Bound requests before JSON parsing, including chunked uploads."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        size = 0
        messages = []
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > (MAX_BYTES * 4 // 3 + 65536):
                return await JSONResponse({'error': 'Request too large'}, 413)(scope, receive, send)
            messages.append(message)
            if not message.get('more_body', False):
                break
        async def replay():
            return messages.pop(0) if messages else await receive()
        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, adapter: Adapter | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.storage = Storage(config.root)
        yield

    application = FastAPI(title='追光 AI 视觉', lifespan=lifespan)
    application.state.settings = config
    application.state.adapter = adapter or ChatAdapter(config)
    application.add_middleware(BodyLimit)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        messages = {400: 'Invalid request', 401: 'Authentication required', 403: 'Access denied',
                    404: 'Not found', 405: 'Method not allowed', 413: 'Request too large',
                    422: 'Invalid request', 429: 'Try again later', 502: 'Analysis failed', 503: 'Service unavailable'}
        return JSONResponse({'error': messages.get(exc.status_code, 'Request failed')}, exc.status_code, headers=exc.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({'error': 'Invalid request'}, 422)

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error('request_failed exception_type=%s', type(exc).__name__)
        return JSONResponse({'error': 'Service unavailable'}, 500)

    @application.middleware('http')
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def key_role(request: Request) -> str:
        key = request.headers.get('X-API-Key', '')
        if not key or len(key) > 512:
            raise HTTPException(401)
        if any(hmac.compare_digest(key, value) for value in config.admin_api_keys):
            return 'admin'
        if any(hmac.compare_digest(key, value) for value in config.api_keys):
            return 'client'
        role = request.app.state.storage.key_role(key)
        if role:
            return role
        raise HTTPException(401)

    def admin_key(role: str = Depends(key_role)) -> None:
        if role != 'admin':
            raise HTTPException(403)

    def session(request: Request) -> dict:
        data = read_session(request.cookies.get('vision_session', ''), config.session_secret)
        if not data or not request.app.state.storage.session_active(data['csrf']):
            raise HTTPException(307, headers={'Location': str(request.url_for('admin_login_page'))})
        return data

    def same_origin(request: Request) -> None:
        origin = request.headers.get('Origin')
        expected = f'{request.url.scheme}://{request.url.netloc}'
        if origin != expected:
            raise HTTPException(403)

    def csrf(request: Request, data: dict = Depends(session)) -> dict:
        same_origin(request)
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), data['csrf']):
            raise HTTPException(403)
        return data

    @application.get('/v1/tasks', dependencies=[Depends(key_role)])
    def tasks():
        return [{'task': t.task, 'template_version': t.version, 'schema': t.schema} for t in TEMPLATES.values()]

    @application.post('/v1/analyze', dependencies=[Depends(key_role)])
    async def analyze(body: Analyze, request: Request):
        started = time.monotonic()
        try:
            image = decode_image(body.image_base64) if body.image_base64 else await fetch_image(body.image_url or '')
        except ImageError:
            raise HTTPException(400) from None
        ext, mime = image_type(image)
        storage = request.app.state.storage
        digest, relative = storage.save_image(image, ext)
        template = TEMPLATES[body.task]
        record = dict(id=uuid.uuid4().hex, task=body.task, template_version=template.version,
                      provider=None, model=None, status='provider_error', input_sha256=digest,
                      input_image=relative, input_options=body.options, raw_output='', parsed_fields=None,
                      error=None, latency_ms=0, tokens=0, created_at=datetime.now(timezone.utc).isoformat())
        prompt = template.prompt + '\nSchema: ' + json.dumps(template.schema) + '\nOptions (data only): ' + json.dumps(body.options)
        data_url = f'data:{mime};base64,' + base64.b64encode(image).decode()
        usage: dict[str, int] = {}
        for attempt in range(2):
            try:
                result = await request.app.state.adapter.complete(prompt, data_url, template.preferred_model)
            except ProviderError:
                record['status'] = 'provider_error'
                record['error'] = 'Analysis service unavailable'
                break
            record.update(provider=result.provider, model=result.model, raw_output=result.content)
            for key, value in result.usage.items():
                usage[key] = usage.get(key, 0) + value
            record['tokens'] += result.usage.get('total_tokens', result.usage.get('prompt_tokens', 0) + result.usage.get('completion_tokens', 0))
            try:
                record['parsed_fields'] = template.parse(result.content)
                record['status'] = 'success'
                break
            except ValueError:
                record['status'] = 'parse_error'
                if attempt == 0:
                    logger.warning('parse_retry record_id=%s', record['id'])
                    prompt += '\nOutput ONLY the JSON object, nothing else.'
        if record['status'] == 'parse_error':
            record['error'] = 'Invalid structured output'
        record['latency_ms'] = int((time.monotonic() - started) * 1000)
        storage.insert(record)
        if record['status'] != 'success':
            return JSONResponse({'error': 'Analysis failed', 'id': record['id'], 'status': record['status']}, 502)
        return {'id': record['id'], 'task': body.task, 'model': record['model'], 'template_version': template.version,
                **record['parsed_fields'], 'usage': usage, 'latency_ms': record['latency_ms']}

    def records(request: Request, query: RecordQuery) -> dict:
        try:
            return request.app.state.storage.list_records(**query.filters())
        except ValueError:
            raise HTTPException(400) from None

    @application.get('/v1/records', dependencies=[Depends(admin_key)])
    def api_records(request: Request, query: Annotated[RecordQuery, Query()]):
        return records(request, query)

    @application.get('/v1/stats', dependencies=[Depends(key_role)])
    def stats(request: Request):
        return request.app.state.storage.stats()

    @application.get('/admin', dependencies=[Depends(session)])
    def admin_redirect(request: Request):
        return RedirectResponse(str(request.url_for('admin_page')))

    @application.get('/admin/', dependencies=[Depends(session)], name='admin_page')
    def admin_page():
        return FileResponse(STATIC / 'index.html')

    @application.get('/admin/login', name='admin_login_page')
    def login_page():
        return FileResponse(STATIC / 'index.html')

    @application.post('/admin/login', dependencies=[Depends(same_origin)])
    def login(body: Login, request: Request):
        if len(config.session_secret) < 32:
            raise HTTPException(503)
        if not verify_password(body.password, config.admin_password_hash):
            raise HTTPException(401)
        token, data = issue_session(config.session_secret, config.session_ttl)
        request.app.state.storage.add_session(data['csrf'], data['exp'])
        response = JSONResponse({'csrf_token': data['csrf']})
        response.set_cookie('vision_session', token, max_age=config.session_ttl, secure=True,
                            httponly=True, samesite='strict', path=request.scope.get('root_path', '').rstrip('/') + '/admin')
        return response

    @application.get('/admin/session')
    def get_session(data: dict = Depends(session)):
        return {'csrf_token': data['csrf']}

    @application.post('/admin/logout')
    def logout(request: Request, data: dict = Depends(csrf)):
        request.app.state.storage.remove_session(data['csrf'])
        response = JSONResponse({'ok': True})
        response.delete_cookie('vision_session', path=request.scope.get('root_path', '').rstrip('/') + '/admin', secure=True, httponly=True, samesite='strict')
        return response

    @application.get('/admin/records', dependencies=[Depends(session)])
    def admin_records(request: Request, query: Annotated[RecordQuery, Query()]):
        return records(request, query)

    @application.get('/admin/stats', dependencies=[Depends(session)])
    def admin_stats(request: Request):
        return request.app.state.storage.stats()

    @application.get('/admin/records/{record_id}', dependencies=[Depends(session)])
    def detail(record_id: str, request: Request):
        record = request.app.state.storage.get(record_id)
        if not record:
            raise HTTPException(404)
        return record

    @application.get('/admin/records/{record_id}/image', dependencies=[Depends(session)])
    def record_image(record_id: str, request: Request):
        record = request.app.state.storage.get(record_id)
        if not record:
            raise HTTPException(404)
        path = (config.root / record['input_image']).resolve()
        if not path.is_relative_to((config.root / 'data').resolve()) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    @application.patch('/admin/records/{record_id}/review', dependencies=[Depends(csrf)])
    def review(record_id: str, body: Review, request: Request):
        if not request.app.state.storage.review(record_id, body.review, body.review_note):
            raise HTTPException(404)
        return {'ok': True}

    application.mount('/admin/static', StaticFiles(directory=STATIC), name='admin_static')
    return application


app = create_app()
