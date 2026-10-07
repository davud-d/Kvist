"""Isolated real HTTP/core-storage fixture; only provider network calls are stubbed."""
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(repo), str(repo / 'frontends/web-local')]


def serve(root):
    from kernel import llm_clients
    from kernel.providers import anymodel_pricing
    from kvist_web.controller import WebController
    from kvist_web.storage import WebStateStore
    from kvist_web.http_api import ControllerHTTPServer

    def verify(key, **kwargs):
        if key != 'qa-valid-key':
            raise anymodel_pricing.AnyModelAuthError('invalid test key')
        return {}

    def catalog(provider, credentials, **kwargs):
        if any(v == 'qa-empty-catalog-key' for v in credentials.values()):
            return [], None
        if not any(v == 'qa-valid-key' for v in credentials.values()):
            return [], 'invalid key'
        return ['example/fast', 'example/smart'], None

    anymodel_pricing.fetch_model_coefficients = verify
    llm_clients.fetch_available_models = catalog
    workspace = root / 'workspace'
    workspace.mkdir()
    controller = WebController(str(workspace), state=WebStateStore(root / 'webstate.json'))
    server = ControllerHTTPServer(('127.0.0.1', 0), controller, admin_token='qa-only')
    print(f'http://127.0.0.1:{server.server_address[1]}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        controller.close()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='kvist-setup-test-') as directory:
        root = Path(directory)
        home = root / 'home'
        home.mkdir()
        Path.home = classmethod(lambda cls: home)
        ssh = home / '.ssh'
        ssh.mkdir()
        (ssh / 'config').write_text('Host build-box gpu-local\n HostName example.invalid\nHost *\n User developer\n')
        serve(root)
