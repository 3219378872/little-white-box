#!/usr/bin/env python3
"""Render local listeners and upstreams from the stack's three port settings."""
import argparse
import os
from pathlib import Path
import re
import tempfile


def render_gateway(text: str, port: int) -> str:
    section = re.search(r'^RestConf:\s*\n(?P<body>(?:[ \t].*\n|\n)*)', text, re.MULTILINE)
    if section is None:
        raise ValueError('gateway RestConf missing from runtime template')
    body = section.group('body')
    body, count = re.subn(r'^  Port:[^\n]*$', f'  Port: {port}', body, flags=re.MULTILINE)
    if count != 1:
        raise ValueError('gateway RestConf must contain exactly one direct Port')
    return text[:section.start('body')] + body + text[section.end('body'):]


def render_proxy(text: str, entry: int, front: int, gateway: int) -> str:
    for name, value in [('ENTRY_PORT', entry), ('FRONT_PORT', front), ('GATEWAY_PORT', gateway)]:
        token = '@@' + name + '@@'
        if token not in text:
            raise ValueError(f'proxy template missing {token}')
        text = text.replace(token, str(value))
    if re.search(r'@@[A-Z_]+@@', text):
        raise ValueError('unknown proxy template token')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('gateway', 'proxy'))
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('entry', type=int)
    parser.add_argument('front', type=int)
    parser.add_argument('gateway', type=int)
    args = parser.parse_args()
    if any(not 1 <= port <= 65535 for port in (args.entry, args.front, args.gateway)):
        parser.error('ports must be between 1 and 65535')
    try:
        if args.kind == 'proxy' and args.source.resolve() == args.destination.resolve():
            raise ValueError('proxy runtime output must differ from its template')
        source = args.source.read_text()
        result = (render_gateway(source, args.gateway) if args.kind == 'gateway'
                  else render_proxy(source, args.entry, args.front, args.gateway))
        args.destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=args.destination.name + '.', dir=args.destination.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                output.write(result)
            os.replace(temporary, args.destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    except (OSError, ValueError) as error:
        parser.exit(1, f'cannot render runtime ports: {error}\n')


if __name__ == '__main__':
    main()
