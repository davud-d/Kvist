"""Novice audit stand: the real kvist-web controller on a temporary HOME.

Only loopback traffic is possible: HTTP(S)_PROXY points at a dead port, so a call
to a real provider fails instead of leaving the machine. The AnyModel and DeepSeek
base URLs point at a fake OpenAI-compatible server that accepts only the test key
`qa-valid-key`; AnyModel key verification is replaced by the same check. Prints the
controller URL on its first stdout line. STAND_MODELS overrides the fake catalog.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
root = Path(tempfile.mkdtemp(prefix='kvist-onboarding-stand-'))
home = root / 'home'
workspace = root / 'my-project'
home.mkdir()
workspace.mkdir()
(workspace / 'README.md').write_text('# my-project\n\nA tiny sample project.\n')
for key in list(os.environ):
    if key.startswith(('AGENT_', 'ANYMODEL', 'DEEPSEEK', 'OPENAI', 'ANTHROPIC', 'OPENROUTER')):
        del os.environ[key]
os.environ.update(HOME=str(home), USERPROFILE=str(home), HTTP_PROXY='http://127.0.0.1:9',
                  HTTPS_PROXY='http://127.0.0.1:9', http_proxy='http://127.0.0.1:9',
                  https_proxy='http://127.0.0.1:9', NO_PROXY='127.0.0.1,localhost',
                  no_proxy='127.0.0.1,localhost', AGENT_API_INTERVAL='0', PYTHONUNBUFFERED='1')
MODELS = [m for m in os.environ.get('STAND_MODELS', 'kr/claude-sonnet-4.5,ds/deepseek-v4-pro,gpt-5.6-luna').split(',') if m]
REPLY = 'This is **my-project**: a tiny sample project with one README file.'


def chunk(delta, finish=None):
    return {'id': 'c', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'm',
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}


def completion(message, finish):
    return {'id': 'c', 'object': 'chat.completion', 'created': 0, 'model': 'm',
            'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def _authorized(self):
        if self.headers.get('Authorization') == 'Bearer qa-valid-key':
            return True
        self.send_response(401)
        self.end_headers()
        return False

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
        if self._authorized():
            self._json({'object': 'list', 'data': [{'id': m, 'object': 'model'} for m in MODELS]})

    def do_POST(self):
        if not self._authorized():
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


fake = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=fake.serve_forever, daemon=True).start()
fake_url = f'http://127.0.0.1:{fake.server_address[1]}/v1'

sys.path[:0] = [str(REPO), str(REPO / 'frontends/web-local')]
from kernel import user_config  # noqa: E402
from kernel.providers import anymodel_pricing  # noqa: E402

# The novice never sees these two: they only move the provider endpoints to loopback.
user_config.set_provider_secret('anymodel_base_url', fake_url)
user_config.set_provider_secret('deepseek_base_url', fake_url)


def verify(key, **kwargs):
    if key != 'qa-valid-key':
        raise anymodel_pricing.AnyModelAuthError('invalid test key')
    return {}


anymodel_pricing.fetch_model_coefficients = verify

from kvist_web.controller import WebController  # noqa: E402
from kvist_web.http_api import ControllerHTTPServer  # noqa: E402
from kvist_web.storage import WebStateStore  # noqa: E402

controller = WebController(str(workspace), state=WebStateStore(root / 'webstate.json'))
server = ControllerHTTPServer(('127.0.0.1', 0), controller, admin_token='qa-only')
print(f'http://127.0.0.1:{server.server_address[1]}', flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    controller.close()
