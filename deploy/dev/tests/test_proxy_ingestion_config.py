"""Keep ordinary ingestion streaming independent from media and SSE budgets."""
from pathlib import Path
import unittest


class ProxyIngestionConfigTest(unittest.TestCase):
    def test_ordinary_api_streams_to_gateway_absolute_deadline(self):
        config = (Path(__file__).resolve().parents[1] / 'proxy.conf').read_text()
        api = config.split('location /api/ {', 1)[1].split('}', 1)[0]
        for directive in ('proxy_request_buffering off;', 'client_body_timeout 3s;',
                          'proxy_send_timeout 5s;'):
            self.assertIn(directive, api)
        video = config.split('location = /api/v1/media/video {', 1)[1].split('}', 1)[0]
        self.assertIn('client_body_timeout 330s;', video)
        self.assertIn('proxy_request_buffering off;', video)
        self.assertIn('proxy_read_timeout 310s;', video)
        self.assertIn('proxy_read_timeout 10m;', config)


if __name__ == '__main__':
    unittest.main()
