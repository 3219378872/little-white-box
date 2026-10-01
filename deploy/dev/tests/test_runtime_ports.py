from pathlib import Path
import shlex
import tempfile
import unittest

from deploy.dev.tests.stack_support import ROOT, STACK, run_bash


class RuntimePortsTest(unittest.TestCase):
    def test_default_and_overridden_ports_share_gateway_and_proxy_config(self):
        for entry, front, gateway in ((3002, 3003, 8888), (43002, 43003, 48888)):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as td:
                directory = Path(td)
                backend = directory / 'backend'
                config = backend / 'app/gateway/etc/gateway.yaml'
                config.parent.mkdir(parents=True)
                original = 'RestConf:\n  Host: 0.0.0.0\n  Port: 8888\n  DevServer:\n    Host: 0.0.0.0\n    Port: 9180\nAuth:\n  AccessExpire: 1800\n'
                config.write_text(original)
                etc = directory / 'etc'
                run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} BACKEND={shlex.quote(str(backend))}
export ETC_DIR={shlex.quote(str(etc))}
export ENTRY_PORT={entry} FRONT_PORT={front} GATEWAY_PORT={gateway}
export PROXY_IPV6=1
source {shlex.quote(str(STACK))}
prepare_etc
prepare_proxy_conf
''')
                gateway_config = (etc / 'app/gateway/etc/gateway.yaml').read_text()
                proxy = (etc / 'proxy.conf').read_text()
                self.assertIn(f'  Port: {gateway}\n', gateway_config)
                self.assertIn('    Port: 9180\n', gateway_config)
                self.assertIn('  Host: 127.0.0.1\n', gateway_config)
                self.assertIn(f'listen {entry};', proxy)
                self.assertIn(f'listen [::]:{entry};', proxy)
                self.assertIn(f'server 127.0.0.1:{front};', proxy)
                self.assertIn(f'server 127.0.0.1:{gateway};', proxy)
                self.assertNotIn('@@', proxy)
                self.assertIn('$http_host', proxy)
                self.assertEqual(config.read_text(), original)
                self.assertEqual((etc / 'proxy.conf').stat().st_mode & 0o777, 0o600)

    def test_proxy_drops_only_ipv6_listener_without_ipv6(self):
        with tempfile.TemporaryDirectory() as td:
            etc = Path(td) / 'etc'
            run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} ETC_DIR={shlex.quote(str(etc))}
export PROXY_IPV6=0
source {shlex.quote(str(STACK))}
prepare_proxy_conf
''')
            proxy = (etc / 'proxy.conf').read_text()
            self.assertIn('listen 3002;', proxy)
            self.assertNotIn('[::]', proxy)
            self.assertIn('server 127.0.0.1:8888;', proxy)

    def test_proxy_rejects_unknown_ipv6_mode_before_rendering(self):
        with tempfile.TemporaryDirectory() as td:
            etc = Path(td) / 'etc'
            result = run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} ETC_DIR={shlex.quote(str(etc))}
export PROXY_IPV6=yes
source {shlex.quote(str(STACK))}
prepare_proxy_conf
''', check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('PROXY_IPV6 must be auto, 1 or 0', result.stderr)
            self.assertFalse((etc / 'proxy.conf').exists())

    def test_media_default_tracks_port_changed_after_stack_source(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            backend = directory / 'backend'
            config = backend / 'app/media/rpc/etc/media.yaml'
            config.parent.mkdir(parents=True)
            config.write_text('S3Storage:\n  PublicBaseURL: "old"\n')
            etc = directory / 'etc'
            run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} BACKEND={shlex.quote(str(backend))}
export ETC_DIR={shlex.quote(str(etc))}
unset MEDIA_PUBLIC_BASE_URL
source {shlex.quote(str(STACK))}
ENTRY_PORT=04302
prepare_etc
''')
            self.assertIn('http://127.0.0.1:4302/xbh-media',
                          (etc / 'app/media/rpc/etc/media.yaml').read_text())

    def test_invalid_port_fails_before_proxy_or_config_mutation(self):
        for name in ('ENTRY_PORT', 'FRONT_PORT', 'GATEWAY_PORT'):
            for value in ('0', '65536', 'x;bad', '-1'):
                with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as td:
                    etc = Path(td) / 'etc'
                    result = run_bash(f'''
export ROOT={shlex.quote(str(ROOT))} ETC_DIR={shlex.quote(str(etc))}
export {name}={shlex.quote(value)}
source {shlex.quote(str(STACK))}
docker() {{ echo unexpected-docker; return 99; }}
proxy_up
''', check=False)
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn('unexpected-docker', result.stdout)
                    self.assertFalse(etc.exists())


if __name__ == '__main__':
    unittest.main()
