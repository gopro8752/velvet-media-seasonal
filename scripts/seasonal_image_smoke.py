#!/usr/bin/env python3
"""Real seasonal HTTP smoke: SHA-only local Docker image or isolated source binary.

No registry operations, production configuration, credentials, or device access.
The --binary mode checks the same HTTP assertions but does NOT verify an image.
"""
import argparse
import ipaddress
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

TOKEN = 'seasonal-image-synthetic-token'
ROW = 'seasonal_image-fixture'


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def atomic_write(path, document):
    replacement = path.with_suffix('.next')
    replacement.write_text(json.dumps(document), encoding='utf-8')
    replacement.replace(path)


def docker(*args):
    result = subprocess.run(['docker', *args], text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'docker {args[0]} failed: {result.stderr.strip()}')
    return result.stdout.strip()


def configuration(root, bind, port, database):
    (root / 'velvet.toml').write_text(
        f'bind={json.dumps(bind)}\nport={port}\n'
        f'db_path={json.dumps(database)}\ntoken={json.dumps(TOKEN)}\n'
        '[[providers]]\nhost="127.0.0.1"\nport=9\ntls=false\n'
        'username=""\npassword=""\nmax_connections=1\n', encoding='utf-8')


@contextmanager
def server(args, root):
    if args.binary:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        configuration(root, '127.0.0.1', port, str(root / 'fixture.db'))
        # Do not inherit operator tokens, upstream keys, NNTP overrides or proxies.
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(root),
               'VELVET_CONFIG': str(root / 'velvet.toml'),
               'VELVET_SEASONAL_MANIFEST': str(root / 'seasonal.json'),
               'VELVET_TMDB_KEY': '', 'RUST_LOG': 'warn'}
        with (root / 'server.log').open('wb') as log:
            process = subprocess.Popen([str(Path(args.binary).resolve()), 'serve'],
                                       env=env, cwd=root, stdout=log, stderr=log)
            try:
                yield f'http://127.0.0.1:{port}'
            except BaseException:
                print((root / 'server.log').read_text(errors='replace'))
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        return

    revision = args.image.removeprefix('velvet-seasonal:sha-')
    docker('info')  # Fail clearly before creating resources when no daemon exists.
    image = json.loads(docker('image', 'inspect', args.image))[0]
    labels = image['Config'].get('Labels') or {}
    require(labels.get('org.opencontainers.image.revision') == revision,
            'Image revision label does not match the exact SHA tag')
    print(f'IMAGE: {args.image} {image["Id"]} revision={revision}')
    name = 'velvet-seasonal-smoke-' + uuid.uuid4().hex[:12]
    configuration(root, '0.0.0.0', 8799, '/data/fixture.db')
    docker('network', 'create', '--internal', name)
    try:
        docker('run', '--detach', '--pull=never', '--name', name,
               '--network', name,
               '--mount', f'type=bind,src={root},dst=/config,readonly',
               '--tmpfs', '/data:rw,nosuid,nodev,size=64m',
               '--env', 'VELVET_CONFIG=/config/velvet.toml',
               '--env', 'VELVET_DB=/data/fixture.db',
               '--env', 'VELVET_SEASONAL_MANIFEST=/config/seasonal.json',
               '--env', 'VELVET_TMDB_KEY=', '--env', 'RUST_LOG=warn', args.image)
        state = json.loads(docker('inspect', name))[0]
        network = json.loads(docker('network', 'inspect', name))[0]
        require(network['Internal'] is True, 'Smoke network must deny external routing')
        require(state['State']['Running'], 'Smoke container exited before HTTP checks')
        settings = state['NetworkSettings']
        require(not any(settings.get('Ports', {}).values()), 'Smoke must not publish host ports')
        require(set(settings['Networks']) == {name}, 'Smoke must use only its internal network')
        endpoint = settings['Networks'][name]
        require(not endpoint.get('Gateway'), 'Internal smoke must not have an external gateway')
        address = ipaddress.ip_address(endpoint['IPAddress'])
        subnets = [ipaddress.ip_network(config['Subnet']) for config in network['IPAM']['Config'] if config.get('Subnet')]
        require(address.is_private and any(address in subnet for subnet in subnets), 'Expected private internal bridge address')
        # Internal-only networks have no default gateway, so Docker cannot
        # publish ports. The Linux Docker host can reach the internal bridge
        # directly; no proxy, non-internal network, or outbound route is needed.
        yield f'http://{address}:8799'
    except BaseException:
        # Keep startup/HTTP failures actionable while the disposable container exists.
        try:
            print(docker('logs', name))
        except RuntimeError as error:
            print(error)
        raise
    finally:
        try:
            docker('rm', '--force', '--volumes', name)
        finally:
            docker('network', 'rm', name)


def exercise(base, root, document):
    # Ignore host proxy configuration even for loopback requests; refuse redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    count = 0

    def get(path, expected=200, token: str | None = TOKEN):
        nonlocal count
        count += 1
        headers = {} if token is None else {'Authorization': 'Bearer ' + token}
        request = urllib.request.Request(base + path, headers=headers)
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = json.load(response)
            require(response.status == expected, (path, expected, response.status, data))
            if expected == 200 and path.startswith('/catalog/v1/'):
                require(response.headers.get('Cache-Control') == 'no-store', path)
            return data

    deadline = time.monotonic() + 30
    while True:
        try:
            get('/health', token=None)
            break
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise RuntimeError('Server did not become healthy within 30 seconds')
            time.sleep(.1)
    paths = ['/catalog/v1/seasonal', '/catalog/v1/seasonal/rows/' + ROW, '/catalog/v1/home']
    for path in paths:
        get(path, 401, None)
        get(path, 401, 'wrong-synthetic-token')
    discovery = get(paths[0])
    require(discovery['schemaVersion'] == 1 and discovery['timezone'] == 'UTC', discovery)
    # Allow UTC midnight to fall between startup and verification.
    effective = datetime.strptime(discovery['effectiveDate'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    require(abs((datetime.now(timezone.utc) - effective).total_seconds()) <= 86401, discovery)
    require([r['id'] for r in discovery['rows']] == [ROW], discovery)
    first = get(paths[1] + '?page=1')
    second = get(paths[1] + '?page=2')
    require([i['mediaId'] for i in first['items']] == list(range(1, 21)), first)
    require([i['mediaId'] for i in second['items']] == list(range(21, 26)), second)
    require(first['totalPages'] == 2 and second['page'] == 2 and not first['stale'], first)
    require(first['revision'] == second['revision'] == discovery['revision'], second)
    require(get(paths[1] + '?page=3')['items'] == [], 'Page beyond content must be empty')
    for page in ['0', '21', '01', '-1', 'abc']:
        get(paths[1] + '?page=' + page, 400)
    get('/catalog/v1/seasonal/rows/seasonal_unknown', 404)
    get(paths[0] + '?date=2000-01-01', 400)
    get(paths[2] + '?date=2000-01-01', 400)
    get(paths[0] + '?profileId=../escape', 400)
    for profile in ['default', 'fixture_a', 'fixture_b']:
        require(get(paths[1] + '?page=1&profileId=' + profile) == first, profile)
    home = get(paths[2])
    require(home['rows'][0]['items'] == first['items'], home)
    require(home['rows'][0]['id'] == ROW and home['failedRows'], home)
    require(all(not r['id'].startswith('holiday_') for r in home['rows']), home)
    print('PASS: bearer auth, UTC discovery, global profile curation, bounded paging, partial inline Home')

    row = document['rows'][0]
    row['title'] = 'Changed while server is running'
    row['style'] = 'landscape'
    row['source']['items'] = [{'mediaId': 999, 'type': 'show', 'title': 'Changed fixture'}]
    atomic_write(root / 'seasonal.json', document)
    changed = get(paths[0])
    require(changed['revision'] != discovery['revision'], changed)
    require(changed['rows'][0]['title'] == row['title'], changed)
    require(changed['rows'][0]['style'] == 'landscape', changed)
    require(get(paths[1])['items'][0]['mediaId'] == 999, 'Hot-reloaded items missing')
    require(get(paths[2])['rows'][0]['items'][0]['mediaId'] == 999, 'Home did not hot reload')
    row['enabled'] = False
    atomic_write(root / 'seasonal.json', document)
    require(get(paths[0])['rows'] == [], 'Disabled row still visible')
    get(paths[1], 404)
    row['enabled'] = True
    atomic_write(root / 'seasonal.json', {'schemaVersion': 99, 'rows': []})
    get(paths[0], 503)
    get(paths[1], 503)
    (root / 'seasonal.json').unlink()
    get(paths[0], 503)  # An explicitly configured missing file must not use defaults.
    (root / 'seasonal.json').write_bytes(b' ' * (128 * 1024 + 1))
    get(paths[0], 503)
    atomic_write(root / 'seasonal.json', document)
    require(get(paths[1])['items'][0]['mediaId'] == 999, 'Recovery requires a restart')
    atomic_write(root / 'seasonal.json', {'schemaVersion': 1, 'rows': []})
    require(get(paths[0])['rows'] == [], 'Removed row still visible')
    get(paths[1], 404)
    print('PASS: atomic directory-mounted metadata/content reload, disable/removal, invalid/missing/oversize fail closed and recover')
    print(f'PASS: {count} actual HTTP requests (including readiness); no upstream success or device playback claimed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--image', help='Existing local velvet-seasonal:sha-<40 lowercase hex> image; never pulled')
    mode.add_argument('--binary', help='Source-built backend; HTTP-only verification, not an image test')
    args = parser.parse_args()
    if args.image and not re.fullmatch(r'velvet-seasonal:sha-[0-9a-f]{40}', args.image):
        parser.error('--image must be an experimental local SHA-only tag')
    with tempfile.TemporaryDirectory(prefix='seasonal-image-smoke-', dir=os.environ.get('TMPDIR')) as directory:
        root = Path(directory)
        document = {'schemaVersion': 1, 'rows': [{
            'id': ROW, 'title': 'Image fixture', 'style': 'poster', 'enabled': True,
            'start': [1, 1], 'end': [12, 31], 'source': {'type': 'items', 'items': [
                {'mediaId': n, 'type': 'movie', 'title': f'Synthetic fixture {n}'}
                for n in range(1, 26)]}}]}
        atomic_write(root / 'seasonal.json', document)
        with server(args, root) as base:
            exercise(base, root, document)
        print('PASS: disposable smoke resources cleaned up')


if __name__ == '__main__':
    main()
