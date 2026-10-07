"""Real controller and real core sessions on a temporary HOME; only the model provider is fake.

The AnyModel and DeepSeek base URLs point at a loopback OpenAI-compatible server and
HTTP(S)_PROXY at a dead port, so nothing leaves the machine. Keys starting with
`qa-valid` work, `qa-empty-catalog-key` works with an empty model list, `qa-offline`
cannot reach the provider, anything else is refused. KVIST_FIXTURE_SETUP=configured
saves AnyModel with `qa-valid-key` and the first catalog model before the controller
starts; the default is a blank start. Prints the controller URL, then the HOME path.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
MODELS = ['kr/claude-sonnet-4.5', 'ds-web/deepseek-v4-pro', 'gpt-5.6-luna']
REPLY = 'This is my-project, a sample project with one README file.'


def chunk(delta, finish=None):
    return {'id': 'c', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'm',
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}


def completion(message, finish):
    return {'id': 'c', 'object': 'chat.completion', 'created': 0, 'model': 'm',
            'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}


class FakeProvider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def _key(self):
        key = self.headers.get('Authorization', '').removeprefix('Bearer ')
        if key.startswith('qa-valid') or key == 'qa-empty-catalog-key':
            return key
        self.send_response(503 if key == 'qa-offline' else 401)
        self.end_headers()
        return None

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, chunks):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for item in chunks:
            self.wfile.write(f'data: {json.dumps(item)}\n\n'.encode())
        self.wfile.write(b'data: [DONE]\n\n')

    def do_GET(self):
        key = self._key()
        if key:
            models = [] if key == 'qa-empty-catalog-key' else MODELS
            self._json({'object': 'list', 'data': [{'id': m, 'object': 'model'} for m in models]})

    def do_POST(self):
        if not self._key():
            return
        request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
        tools = {t['function']['name'] for t in request.get('tools') or []}
        if tools == {'ping'}:
            call = {'id': 'call_ping', 'type': 'function', 'function': {'name': 'ping', 'arguments': '{"ok": true}'}}
            if request.get('stream'):
                return self._sse([chunk({'role': 'assistant', 'tool_calls': [dict(call, index=0)]}), chunk({}, 'tool_calls')])
            return self._json(completion({'role': 'assistant', 'content': None, 'tool_calls': [call]}, 'tool_calls'))
        if request.get('stream'):
            return self._sse([chunk({'role': 'assistant', 'content': REPLY}), chunk({}, 'stop')])
        self._json(completion({'role': 'assistant', 'content': REPLY}, 'stop'))


def isolate(root: Path) -> Path:
    home = root / 'home'
    home.mkdir()
    for key in list(os.environ):
        if key.startswith(('AGENT_', 'ANYMODEL', 'DEEPSEEK', 'OPENAI', 'ANTHROPIC', 'OPENROUTER')):
            del os.environ[key]
    dead = 'http://127.0.0.1:9'
    os.environ.update(HOME=str(home), USERPROFILE=str(home), HTTP_PROXY=dead, HTTPS_PROXY=dead,
                      http_proxy=dead, https_proxy=dead, NO_PROXY='127.0.0.1,localhost',
                      no_proxy='127.0.0.1,localhost', AGENT_API_INTERVAL='0', PYTHONUNBUFFERED='1')
    return home


def serve(root: Path) -> None:
    fake = ThreadingHTTPServer(('127.0.0.1', 0), FakeProvider)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    fake_url = f'http://127.0.0.1:{fake.server_address[1]}/v1'
    sys.path[:0] = [str(REPO), str(REPO / 'frontends/web-local')]
    from kernel import user_config
    from kernel.providers import anymodel_pricing

    user_config.set_provider_secret('anymodel_base_url', fake_url)
    user_config.set_provider_secret('deepseek_base_url', fake_url)
    if os.environ.get('KVIST_FIXTURE_SETUP') == 'configured':
        user_config.set_provider_secret('anymodel_api_key', 'qa-valid-key')
        user_config.save_models_for_provider('anymodel', {'fast': MODELS[0], 'smart': MODELS[0]})
        user_config.set_provider_secret(user_config.LLM_PROVIDER_KEY, 'anymodel')

    # AnyModel's key check calls anymodel.org itself, not the base URL; the same rule as the fake provider.
    def verify(key, **kwargs):
        if key == 'qa-offline':
            raise anymodel_pricing.AnyModelUnavailableError('test provider is offline')
        if not (key.startswith('qa-valid') or key == 'qa-empty-catalog-key'):
            raise anymodel_pricing.AnyModelAuthError('invalid test key')
        return {}

    anymodel_pricing.fetch_model_coefficients = verify
    from kvist_web.controller import WebController
    from kvist_web.http_api import ControllerHTTPServer
    from kvist_web.storage import WebStateStore

    workspace = root / 'my-project'
    workspace.mkdir()
    (workspace / 'README.md').write_text('# my-project\n\nA sample project.\n')
    controller = WebController(str(workspace), state=WebStateStore(root / 'webstate.json'))
    server = ControllerHTTPServer(('127.0.0.1', 0), controller, admin_token='qa-only')
    print(f'http://127.0.0.1:{server.server_address[1]}', flush=True)
    print(os.environ['HOME'], flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        controller.close()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='kvist-core-test-') as directory:
        isolate(Path(directory))
        serve(Path(directory))
