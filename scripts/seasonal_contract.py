#!/usr/bin/env python3
"""Real backend -> production Kotlin/Swift seasonal HTTP contract (no credentials).

Requires JDK 17+, swiftc, and a built backend. --download-tools fetches pinned
Maven compiler/dependency jars to scratch; --tools-dir reuses an offline toolset.
Adapters compile once, stay alive across manifest edits, and never replace client
request/decoding logic. Kotlin's low-level catalog throws on errors (the adapter
reports []); Swift exercises the actual repository's fail-closed rowItems path.
"""
import argparse
import contextlib
import hashlib
import http.server
import json
import os
from pathlib import Path
import selectors
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = [
    ('org/jetbrains/kotlin', 'kotlin-compiler-embeddable', '2.1.20'),
    ('org/jetbrains/kotlin', 'kotlin-stdlib', '2.1.20'),
    ('org/jetbrains/kotlin', 'kotlin-script-runtime', '2.1.20'),
    ('org/jetbrains/kotlin', 'kotlin-reflect', '1.6.10'),
    ('org/jetbrains/intellij/deps', 'trove4j', '1.0.20200330'),
    ('org/jetbrains', 'annotations', '13.0'),
    ('org/jetbrains/kotlinx', 'kotlinx-coroutines-core-jvm', '1.9.0'),
    ('org/jetbrains/kotlinx', 'kotlinx-serialization-core-jvm', '1.7.3'),
    ('org/jetbrains/kotlinx', 'kotlinx-serialization-json-jvm', '1.7.3'),
    ('com/squareup/okhttp3', 'okhttp', '4.12.0'),
    ('com/squareup/okio', 'okio-jvm', '3.6.0'),
    ('junit', 'junit', '4.13.2'),
    ('org/hamcrest', 'hamcrest-core', '1.3'),
]

SHA256 = {
    'annotations': 'ace2a10dc8e2d5fd34925ecac03e4988b2c0f851650c94b8cef49ba1bd111478',
    'hamcrest-core': '66fdef91e9739348df7a096aa384a5685f4e875584cce89386a7a47251c4d8e9',
    'junit': '8e495b634469d64fb8acfa3495a065cbacc8a0fff55ce1e31007be4c16dc57d3',
    'kotlin-compiler-embeddable': 'c54a00718c8c0e3ee858bf42771c7f401c5e3c1738861e18e9536371042fa1b3',
    'kotlin-reflect': '3277ac102ae17aad10a55abec75ff5696c8d109790396434b496e75087854203',
    'kotlin-script-runtime': 'ae4397fbb3aa2a1ada09290e753bba99a4114548977c3115526b9958f4cb1a04',
    'kotlin-stdlib': '1bcc74e8ce84e2c25eaafde10f1248349cce3062b6e36978cbeec610db1e930a',
    'kotlinx-coroutines-core-jvm': 'ad89c2892235e670f222d819cb3d81188143cb19a05b59df9889ae4269f5c70a',
    'kotlinx-serialization-core-jvm': 'f0adde45864144475385cf4aa7e0b7feb27f61fcf9472665ed98cc971b06b1eb',
    'kotlinx-serialization-json-jvm': 'b1e9138499ed8d20375edda3f2b1c95f3103a258eff6af9edc5ea07100f2b29c',
    'okhttp': 'b1050081b14bb7a3a7e55a4d3ef01b5dcfabc453b4573a4fc019767191d5f4e0',
    'okio-jvm': '67543f0736fc422ae927ed0e504b98bc5e269fda0d3500579337cb713da28412',
    'trove4j': 'c5fd725bffab51846bf3c77db1383c60aaaebfe1b7fe2f00d23fe1b7df0a439d',
}


def tools(directory, download):
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for group, artifact, version in ARTIFACTS:
        name = f'{artifact}-{version}.jar'
        target = directory / name
        if not target.exists():
            if not download:
                raise RuntimeError(f'Missing {target}; use --download-tools')
            url = f'https://repo.maven.apache.org/maven2/{group}/{artifact}/{version}/{name}'
            with urllib.request.urlopen(url, timeout=60) as response:
                body = response.read()
            assert hashlib.sha256(body).hexdigest() == SHA256[artifact], name
            target.write_bytes(body)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == SHA256[artifact], name
        paths.append(str(target.resolve()))
    return os.pathsep.join(paths)


def compile_clients(temp, classpath):
    java = str(Path(os.environ['JAVA_HOME']) / 'bin/java') if os.environ.get('JAVA_HOME') else shutil.which('java')
    swiftc = os.environ.get('SWIFTC') or shutil.which('swiftc')
    if not java or not swiftc:
        raise RuntimeError('JDK (JAVA_HOME/java) and Swift (SWIFTC/swiftc) are both required; no checks are skipped')
    main = ROOT / 'clients/android/app/src/main/java/tv/velvet'
    source = (main / 'data/settings/SettingsRepository.kt').read_text()
    settings = source[source.index('data class VelvetSettings('):source.index('\nclass SettingsRepository')]
    assert 'const val DEFAULT_SYNC_URL = ""' in source
    shim = temp / 'VelvetSettings.kt'
    shim.write_text('package tv.velvet.data.settings\nimport tv.velvet.data.model.StreamSourceConfig\n' + settings.replace('SettingsRepository.DEFAULT_SYNC_URL', '""'))
    classes = temp / 'classes'
    classes.mkdir()
    files = [main / p for p in ['data/model/Models.kt', 'data/net/HttpAwait.kt', 'data/tmdb/TmdbMetadataRequest.kt', 'data/tmdb/SeasonalCatalog.kt']]
    subprocess.run([java, '-cp', classpath, 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler', '-no-stdlib', '-no-reflect', '-classpath', classpath, '-d', str(classes), *map(str, files), str(shim), str(ROOT / 'clients/android/tests/SeasonalContractCli.kt')], check=True)
    ios = ROOT / 'clients/ios/Velvet'
    sources = ['Models/Models.swift', 'Data/TmdbDtos.swift', 'Data/TmdbRows.swift', 'Data/TmdbRequest.swift', 'Data/TmdbRepository.swift', 'Data/TmdbClient.swift', 'Data/SeasonalCatalog.swift']
    swift = temp / 'Production.swift'
    swift.write_text('''import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@MainActor final class SettingsStore {
    var backendUrl = ""
    var syncUrl = ""
    var syncToken = ""
    var tmdbApiKey = "never-send-this"
    var activeProfileId: String? = "contract"
}
''' + '\n'.join((ios / p).read_text() for p in sources))
    binary = temp / 'swift-seasonal'
    subprocess.run([swiftc, '-swift-version', '5', '-parse-as-library', str(swift), str(ROOT / 'clients/ios/scripts/SeasonalContractCli.swift'), '-o', str(binary)], check=True)
    return {'Kotlin': [java, '-cp', str(classes) + os.pathsep + classpath, 'tv.velvet.data.tmdb.SeasonalContractCliKt'], 'Swift': [str(binary)]}


class Client:
    def __init__(self, name, command, timezone):
        self.name = f'{name}/{timezone}'
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env={**os.environ, 'TZ': timezone})
        assert self.process.stdin is not None and self.process.stdout is not None
        self.stdin = self.process.stdin
        self.stdout = self.process.stdout

    def request(self, base, kind='discover', row='seasonal_meteor-night', page=1, token='seasonal-fixture'):
        self.stdin.write(f'{kind}\t{base}\t{token}\t{row}\t{page}\n')
        self.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.stdout, selectors.EVENT_READ)
            if not selector.select(30):
                raise AssertionError(f'{self.name}: client response timeout')
        line = self.stdout.readline()
        assert line, (self.name, self.process.poll())
        return json.loads(line)

    def close(self):
        self.stdin.close()
        try:
            assert self.process.wait(timeout=10) == 0, self.name
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise


class FaultServer(http.server.BaseHTTPRequestHandler):
    """An absent old endpoint and transport errors only, never successful catalog data."""
    status = 404
    paths = []

    def do_GET(self):
        type(self).paths.append(self.path)
        self.send_response(type(self).status)
        self.send_header('Content-Type', 'application/json')
        if type(self).status == 302:
            self.send_header('Location', '/must-not-follow')
        self.end_headers()
        self.wfile.write(b'{invalid' if type(self).status == 200 else b'{"error":"fixture"}')

    def log_message(self, format, *args):
        pass


def exercise(temp, binary, commands):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    config = temp / 'velvet.toml'
    config.write_text(f'bind="127.0.0.1"\nport={port}\ndb_path={json.dumps(str(temp / "state.db"))}\ntoken="seasonal-fixture"\n[[providers]]\nhost="127.0.0.1"\nport=9\ntls=false\nusername=""\npassword=""\n')
    manifest = temp / 'seasonal.json'
    def row(id, title, items, enabled=True):
        return {'id': id, 'title': title, 'style': 'landscape', 'artworkUrl': 'https://image.tmdb.org/t/p/original/art.jpg', 'enabled': enabled, 'start': [1, 1], 'end': [12, 31], 'source': {'type': 'items', 'items': items}}
    items = [{'mediaId': n, 'type': 'show' if n % 2 else 'movie', 'title': f'Fixture {n}', 'year': '2099', 'rating': 8.5, 'posterUrl': 'https://image.tmdb.org/t/p/original/poster.jpg', 'backdropUrl': 'https://image.tmdb.org/t/p/original/backdrop.jpg', 'logoUrl': 'https://image.tmdb.org/t/p/original/logo.png', 'overview': 'Server-only collection', 'genres': ['Sci-Fi'], 'runtimeMinutes': 91, 'imdbId': 'tt1234567'} for n in range(1, 26)]
    document = {'schemaVersion': 1, 'rows': [row('seasonal_meteor-night', 'Meteor Night 2099', items), row('seasonal_empty', 'Empty', []), row('seasonal_disabled', 'Disabled', items, False)]}
    def save():
        replacement = temp / 'next.json'
        replacement.write_text(json.dumps(document))
        replacement.replace(manifest)
    save()
    env = {**os.environ, 'VELVET_CONFIG': str(config), 'VELVET_SEASONAL_MANIFEST': str(manifest), 'TZ': 'UTC'}
    for key in ('VELVET_TOKEN', 'VELVET_DB'):
        env.pop(key, None)
    def get(path):
        with urllib.request.urlopen(urllib.request.Request(base + path, headers={'Authorization': 'Bearer seasonal-fixture'}), timeout=5) as response:
            return json.load(response)
    clients = []
    with (temp / 'server.log').open('w') as log, contextlib.ExitStack() as stack:
        server = subprocess.Popen([str(binary), 'serve'], env=env, stdout=log, stderr=log)
        try:
            for _ in range(200):
                try:
                    get('/health')
                    break
                except OSError:
                    assert server.poll() is None, (temp / 'server.log').read_text()
                    time.sleep(.05)
            else:
                raise AssertionError('Backend never became ready')
            faults = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FaultServer)
            stack.callback(faults.server_close)
            thread = threading.Thread(target=faults.serve_forever, daemon=True)
            thread.start()
            stack.callback(faults.shutdown)
            fault_base = f'http://127.0.0.1:{faults.server_port}'
            for name, command in commands.items():
                for timezone in ('Pacific/Kiritimati', 'Etc/GMT+12'):
                    clients.append(Client(name, command, timezone))
            discovery = get('/catalog/v1/seasonal')
            assert discovery['timezone'] == 'UTC'
            # Make activation a single UTC day: at least one client clock is on
            # a different date and must still consume this server-active row.
            month, day = map(int, discovery['effectiveDate'].split('-')[1:])
            for descriptor in document['rows']:
                descriptor['start'] = descriptor['end'] = [month, day]
            save()
            discovery = get('/catalog/v1/seasonal')
            for name in commands:
                device_dates = [c.request(base, 'clock')['deviceDate'] for c in clients if c.name.startswith(name + '/')]
                assert any(date != discovery['effectiveDate'] for date in device_dates), (name, device_dates, discovery)
            assert [r['id'] for r in discovery['rows']] == ['seasonal_meteor-night', 'seasonal_empty']
            for client in clients:
                assert client.request(base) == discovery['rows'], client.name
                for page, expected in ((1, items[:20]), (2, items[20:]), (3, [])):
                    actual = client.request(base, 'items', page=page)
                    normalized = [{**item, 'id': item['mediaId']} for item in expected]
                    for item in normalized:
                        del item['mediaId']
                    assert actual == normalized, (client.name, page, actual)
                assert client.request(base, 'items', row='seasonal_empty') == [], client.name
                assert client.request(base, 'items', row='seasonal_disabled') == [], client.name
                assert client.request(base, token='wrong-token') == [], client.name
            print('PASS: real Kotlin + Swift HTTP discovery, all normalized fields, page 2, empty/disabled/auth failure, opposite device timezones', flush=True)
            old_revision = discovery['revision']
            document['rows'][0]['title'] = 'Server hot edit'
            document['rows'][0]['source']['items'] = [dict(items[-1], title='Replaced live')]
            document['rows'][1]['enabled'] = False
            save()
            changed = get('/catalog/v1/seasonal')
            assert changed['revision'] != old_revision and len(changed['rows']) == 1
            for client in clients:
                assert client.request(base) == changed['rows'], client.name
                actual = client.request(base, 'items')
                assert len(actual) == 1 and actual[0]['title'] == 'Replaced live', (client.name, actual)
                assert client.request(base, 'items', page=2) == [], client.name
            document['rows'][0]['id'] = 'seasonal_added-after-compile'
            document['rows'][0]['style'] = 'poster'
            save()
            added = get('/catalog/v1/seasonal')
            for client in clients:
                assert client.request(base) == added['rows'], client.name
                assert client.request(base, 'items') == [], client.name
                assert client.request(base, 'items', row='seasonal_added-after-compile')[0]['title'] == 'Replaced live', client.name
            document['rows'][0]['enabled'] = False
            save()
            for client in clients:
                assert client.request(base) == [], client.name
                assert client.request(base, 'items', row='seasonal_added-after-compile') == [], client.name
            manifest.write_text('{invalid')
            for client in clients:
                assert client.request(base) == [], client.name
                assert client.request(base, 'items', row='seasonal_added-after-compile') == [], client.name
            document['rows'][0]['enabled'] = True
            save()
            recovered = get('/catalog/v1/seasonal')
            for client in clients:
                assert client.request(base) == recovered['rows'], client.name
                assert client.request(base, 'items', row='seasonal_added-after-compile')[0]['title'] == 'Replaced live', client.name
            print('PASS: same server + same compiled/running clients observe hot metadata/content/ID/activation edits and invalid config recovery without cache or rebuild', flush=True)
            for status in (404, 500, 200, 302):
                FaultServer.status = status
                FaultServer.paths = []
                for client in clients:
                    assert client.request(fault_base) == [], (client.name, status)
                    assert client.request(fault_base, 'items') == [], (client.name, status)
                assert '/must-not-follow' not in FaultServer.paths
                assert len(FaultServer.paths) == 8, FaultServer.paths
            print('PASS: old-backend 404, HTTP 500, malformed JSON, redirects hide seasons; no redirect or legacy fallback', flush=True)
        finally:
            for client in clients:
                client.close()
            server.terminate()
            server.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--tools-dir', type=Path)
    parser.add_argument('--download-tools', action='store_true')
    args = parser.parse_args()
    scratch = os.environ.get('TMPDIR')
    if not scratch:
        parser.error('Set TMPDIR to writable scratch outside the repository')
    directory = args.tools_dir or Path(scratch) / 'seasonal-kotlin-tools'
    classpath = tools(directory, args.download_tools)
    with tempfile.TemporaryDirectory(prefix='seasonal-contract-', dir=scratch) as name:
        temp = Path(name)
        commands = compile_clients(temp, classpath)
        exercise(temp, args.binary.resolve(), commands)


if __name__ == '__main__':
    main()
