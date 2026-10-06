#!/usr/bin/env python3
"""Render local listeners, upstreams and public URLs into runtime config copies.

Subcommands (all write DESTINATION atomically, never the sub-repo template):
  gateway SOURCE DESTINATION ENTRY FRONT GATEWAY   set the gateway RestConf port
  proxy   SOURCE DESTINATION ENTRY FRONT GATEWAY   fill the nginx @@PORT@@ tokens
  media   SOURCE DESTINATION URL                   set the first PublicBaseURL
"""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile


# Sets the gateway's HTTP listener port.
def render_gateway(text: str, port: int) -> str:
    # Only the direct Port of the RestConf block is the HTTP listener; nested
    # Port keys (e.g. DevServer) must stay untouched.
    section = re.search(r'^RestConf:\s*\n(?P<body>(?:[ \t].*\n|\n)*)', text, re.MULTILINE)
    if section is None:
        raise ValueError('gateway RestConf missing from runtime template')
    body = section.group('body')
    body, count = re.subn(r'^  Port:[^\n]*$', f'  Port: {port}', body, flags=re.MULTILINE)
    if count != 1:
        raise ValueError('gateway RestConf must contain exactly one direct Port')
    return text[:section.start('body')] + body + text[section.end('body'):]


# Fills the nginx template's port tokens; optionally drops IPv6 listeners.
def render_proxy(text: str, entry: int, front: int, gateway: int, ipv6: bool = True) -> str:
    # Every token must be present exactly as a template contract, and no
    # unknown token may survive into the nginx config.
    for name, value in [('ENTRY_PORT', entry), ('FRONT_PORT', front), ('GATEWAY_PORT', gateway)]:
        token = '@@' + name + '@@'
        if token not in text:
            raise ValueError(f'proxy template missing {token}')
        text = text.replace(token, str(value))
    if re.search(r'@@[A-Z_]+@@', text):
        raise ValueError('unknown proxy template token')
    if not ipv6:
        # nginx exits on `listen [::]` when the host has no IPv6 stack.
        text = re.sub(r'^[ \t]*listen[ \t]+\[::\]:[^\n]*\n', '', text, flags=re.MULTILINE)
        if not re.search(r'^[ \t]*listen[ \t]+\S', text, re.MULTILINE):
            raise ValueError('proxy template has no IPv4 listener')
    return text


# Points generated media links at the public base URL.
def render_media_url(text: str, url: str) -> str:
    # Media URLs handed to browsers must point at the same-origin proxy, so the
    # first PublicBaseURL is rewritten as a JSON (hence valid YAML) string.
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.lstrip().startswith('PublicBaseURL:'):
            indent = line[:len(line) - len(line.lstrip())]
            lines[index] = indent + 'PublicBaseURL: ' + json.dumps(url)
            return '\n'.join(lines) + '\n'
    raise ValueError('media PublicBaseURL missing from runtime template')


# Writes CONTENT to DESTINATION through a sibling temp file and rename.
def write_atomically(destination: Path, content: str) -> None:
    # A reader never sees a half-written file: write a sibling temp, then rename.
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + '.', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(content)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


# Subcommand CLI; gateway and proxy share the three port arguments.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    kinds = parser.add_subparsers(dest='kind', required=True)

    # gateway and proxy share the three stack ports.
    ports = argparse.ArgumentParser(add_help=False)
    ports.add_argument('source', type=Path)
    ports.add_argument('destination', type=Path)
    ports.add_argument('entry', type=int)
    ports.add_argument('front', type=int)
    ports.add_argument('gateway', type=int)
    kinds.add_parser('gateway', parents=[ports])
    proxy = kinds.add_parser('proxy', parents=[ports])
    proxy.add_argument('--no-ipv6', action='store_true',
                       help='drop IPv6 proxy listeners for hosts without an IPv6 stack')

    media = kinds.add_parser('media')
    media.add_argument('source', type=Path)
    media.add_argument('destination', type=Path)
    media.add_argument('url')
    return parser


# Validates ports, renders the requested kind and writes it atomically.
def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.kind in ('gateway', 'proxy') and any(
            not 1 <= port <= 65535 for port in (args.entry, args.front, args.gateway)):
        parser.error('ports must be between 1 and 65535')
    try:
        # The proxy template lives in the repo; rendering in place would
        # destroy its @@tokens@@ for every later run.
        if args.kind == 'proxy' and args.source.resolve() == args.destination.resolve():
            raise ValueError('proxy runtime output must differ from its template')
        source = args.source.read_text()
        if args.kind == 'gateway':
            result = render_gateway(source, args.gateway)
        elif args.kind == 'proxy':
            result = render_proxy(source, args.entry, args.front, args.gateway,
                                  ipv6=not args.no_ipv6)
        else:
            result = render_media_url(source, args.url)
        write_atomically(args.destination, result)
    except (OSError, ValueError) as error:
        parser.exit(1, f'cannot render runtime ports: {error}\n')


if __name__ == '__main__':
    main()
