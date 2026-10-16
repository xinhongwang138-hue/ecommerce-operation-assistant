from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import model_settings as routes
from app.api.routes.auth import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.models import Base, User, UserRole
from app.services import llm, model_settings


def configured():
    return SimpleNamespace(llm_base_url='https://provider.example/v1', llm_api_key='test-secret-do-not-echo', llm_model='course-test-model')


def install_transport(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(llm.httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))


def call():
    return llm.create_json_chat_completion([{'role': 'user', 'content': 'Return JSON'}], settings=configured())


def test_success_posts_expected_request(monkeypatch):
    def handler(request):
        assert str(request.url) == 'https://provider.example/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-secret-do-not-echo'
        assert b'json_object' in request.content
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    install_transport(monkeypatch, handler)
    parsed, raw = call()
    assert parsed == {'ok': True}
    assert 'choices' in raw


@pytest.mark.parametrize('body', ['not-json', '[]', 'null', '"text"'])
def test_model_content_must_be_json_object(monkeypatch, body):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {'content': body}}]}))
    with pytest.raises(llm.LLMResponseError):
        call()


@pytest.mark.parametrize('response', [httpx.Response(200, text='<html>not JSON</html>'), httpx.Response(200, json=[]), httpx.Response(200, json={'choices': []})])
def test_invalid_provider_envelope(monkeypatch, response):
    install_transport(monkeypatch, lambda request: response)
    with pytest.raises(llm.LLMResponseError):
        call()


def test_http_error_does_not_echo_upstream_secret(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(401, json={'error': 'test-secret-do-not-echo'}))
    with pytest.raises(llm.LLMResponseError) as error:
        call()
    assert '401' in str(error.value)
    assert 'test-secret' not in str(error.value)


def test_timeout_has_controlled_message(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout('timeout', request=request)
    install_transport(monkeypatch, handler)
    with pytest.raises(llm.LLMResponseError, match='request failed'):
        call()


def test_unconfigured_never_sends_http(monkeypatch):
    def unexpected(**kwargs):
        pytest.fail('must not send HTTP without configuration')
    monkeypatch.setattr(llm.httpx, 'Client', unexpected)
    with pytest.raises(llm.LLMNotConfiguredError):
        llm.create_json_chat_completion([], settings=SimpleNamespace(llm_base_url='', llm_api_key='', llm_model=''))


@pytest.mark.parametrize('key', ['a', 'abc', 'abcdef'])
def test_short_secret_is_fully_masked(key):
    assert model_settings._mask_secret(key) == '******'


def test_long_secret_always_hides_some_characters():
    for length in range(7, 40):
        key = ''.join(chr(65 + index % 26) for index in range(length))
        masked = model_settings._mask_secret(key)
        assert masked.replace('...', '') != key


@pytest.fixture
def client(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    def override_db():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(id=1, email='course@example.com', display_name='Course', role=UserRole.OWNER)
    # Runtime config opens its own session; it must use the same temporary test DB.
    monkeypatch.setattr(model_settings, 'SessionLocal', lambda: Session(engine))
    clean = get_settings().model_copy(update={'llm_base_url': None, 'llm_api_key': None, 'llm_model': None})
    monkeypatch.setattr(model_settings, 'get_settings', lambda: clean)
    try:
        with TestClient(app) as browser:
            yield browser
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_saved_settings_are_used_by_text_test(client, monkeypatch):
    response = client.patch('/api/v1/model-settings', json={'text': {'base_url': 'https://provider.example/v1', 'api_key': 'test-secret-do-not-echo', 'model': 'course-test-model'}})
    assert response.status_code == 200
    assert 'test-secret-do-not-echo' not in response.text
    def completion(messages, **kwargs):
        assert kwargs['settings'].llm_model == 'course-test-model'
        assert kwargs['settings'].llm_api_key == 'test-secret-do-not-echo'
        return {'ok': True}, {}
    monkeypatch.setattr(routes, 'create_json_chat_completion', completion)
    response = client.post('/api/v1/model-settings/test', json={'target': 'text'})
    assert response.status_code == 200
    assert response.json()['ok'] is True


def test_unconfigured_and_media_boundary(client):
    assert client.post('/api/v1/model-settings/test', json={'target': 'text'}).status_code == 400
    assert client.post('/api/v1/model-settings/test', json={'target': 'image'}).status_code == 501
    assert client.post('/api/v1/model-settings/test', json={'target': 'video'}).status_code == 501
    assert client.post('/api/v1/model-settings/test', json={'target': 'unknown'}).status_code == 422


@pytest.mark.parametrize('payload', [{}, {'ok': False}, {'ok': 'true'}])
def test_test_endpoint_requires_boolean_true(client, monkeypatch, payload):
    monkeypatch.setattr(routes, 'create_json_chat_completion', lambda *args, **kwargs: (payload, {}))
    assert client.post('/api/v1/model-settings/test', json={'target': 'text'}).status_code == 502
