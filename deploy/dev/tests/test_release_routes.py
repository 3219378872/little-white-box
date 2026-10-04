import functools
import http.server
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from deploy.dev.tests.stack_support import ROOT

spec = importlib.util.spec_from_file_location('serve_release', ROOT / 'deploy/dev/serve_release.py')
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)


class ReleaseRoutesTest(unittest.TestCase):
    def test_query_never_changes_spa_or_static_resource_routing(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / 'web'
            directory.mkdir()
            (directory / 'index.html').write_text('SYNTHETIC_SPA')
            (Path(td) / 'outside.txt').write_text('SYNTHETIC_OUTSIDE')
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(serve.ReleaseHandler, directory=str(directory)))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for path in ('/post/123?x=normal', '/post/123?x=v1.2', '/post/123?x=a/b.js',
                             '/post/%31%32%33?x=a.b/c.js', '/post/123?file=.css'):
                    with self.subTest(path=path), urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{path}') as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.read(), b'SYNTHETIC_SPA')
                for path in ('/missing.js', '/missing.css?x=foo', '/missing%2Ejs?x=foo', '/%2e%2e/outside.txt'):
                    with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{path}')
                    self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
