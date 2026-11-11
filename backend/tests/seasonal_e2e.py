#!/usr/bin/env python3
"""Loopback black-box seasonal contract; synthetic manifest, no external credentials."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    with tempfile.TemporaryDirectory(prefix='seasonal-', dir=os.environ.get('TMPDIR')) as directory:
        root = Path(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        config = root / 'velvet.toml'
        config.write_text(f'bind="127.0.0.1"\nport={port}\ndb_path={json.dumps(str(root / "state.db"))}\ntoken="seasonal-fixture"\n[[providers]]\nhost="127.0.0.1"\nport=9\ntls=false\nusername=""\npassword=""\n')
        manifest = root / 'seasonal.json'
        document = {'schemaVersion': 1, 'rows': [{'id': 'seasonal_meteor-night', 'title': 'Meteor Night', 'style': 'poster', 'enabled': True, 'start': [1, 1], 'end': [12, 31], 'source': {'type': 'items', 'items': []}}]}
        manifest.write_text(json.dumps(document))
        env = {**os.environ, 'VELVET_CONFIG': str(config), 'VELVET_SEASONAL_MANIFEST': str(manifest)}
        env.pop('VELVET_TOKEN', None)
        env.pop('VELVET_DB', None)
        with (root / 'server.log').open('wb') as log:
            process = subprocess.Popen([binary, 'serve'], env=env, stdout=log, stderr=log)
            try:
                base = f'http://127.0.0.1:{port}'
                def get(path, auth=True):
                    headers = {'Authorization': 'Bearer seasonal-fixture'} if auth else {}
                    try:
                        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=5) as response:
                            return response.status, json.load(response), response.headers
                    except urllib.error.HTTPError as error:
                        return error.code, json.load(error), error.headers
                for _ in range(100):
                    try:
                        get('/health'); break
                    except OSError:
                        if process.poll() is not None:
                            raise AssertionError((root / 'server.log').read_text())
                        time.sleep(.05)
                status, discovery, headers = get('/catalog/v1/seasonal')
                assert status == 200, (status, discovery)
                assert [row['id'] for row in discovery['rows']] == ['seasonal_meteor-night'], discovery
                assert headers['Cache-Control'] == 'no-store'
                assert get('/catalog/v1/seasonal', False)[0] == 401
                status, page, _ = get('/catalog/v1/seasonal/rows/seasonal_meteor-night?page=1')
                assert status == 200 and page['items'] == [] and page['totalPages'] == 0, (status, page)
                assert get('/catalog/v1/seasonal/rows/seasonal_unknown')[0] == 404
                assert get('/catalog/v1/seasonal/rows/seasonal_meteor-night?page=21')[0] == 400
                print('PASS: authenticated arbitrary server-defined seasonal discovery and empty bounded paging')
                # Edit the mounted manifest while this exact server process stays running.
                document['rows'][0]['title'] = 'Renamed from server'
                document['rows'][0]['source']['items'] = [{'mediaId': n, 'type': 'movie', 'title': f'Fixture {n}', 'genres': []} for n in range(1, 26)]
                replacement = root / 'next.json'
                replacement.write_text(json.dumps(document))
                replacement.replace(manifest)
                status, changed, _ = get('/catalog/v1/seasonal')
                assert changed['rows'][0]['title'] == 'Renamed from server' and changed['revision'] != discovery['revision']
                status, first, _ = get('/catalog/v1/seasonal/rows/seasonal_meteor-night?page=1')
                assert status == 200 and [i['mediaId'] for i in first['items']] == list(range(1, 21)) and first['totalPages'] == 2, first
                status, second, _ = get('/catalog/v1/seasonal/rows/seasonal_meteor-night?page=2')
                assert [i['mediaId'] for i in second['items']] == list(range(21, 26)), second
                assert get('/catalog/v1/seasonal/rows/seasonal_meteor-night?page=3')[1]['items'] == []
                print('PASS: hot config reload changes metadata/revision/content without server or client rebuild; paging')
                status, home, _ = get('/catalog/v1/home')
                assert status == 200 and home['rows'][0]['id'] == 'seasonal_meteor-night', (status, home)
                assert home['rows'][0]['items'] == first['items'] and home['effectiveDate'] == first['effectiveDate']
                assert all(not r['id'].startswith('holiday_') for r in home['rows'])
                assert get('/catalog/v1/home?date=2000-01-01')[0] == 400
                assert get('/catalog/v1/home', False)[0] == 401
                print('PASS: Roku versioned home uses same server content without changing legacy Home')
                for profile in ['default', 'profile_a', 'profile_b']:
                    status, scoped, _ = get('/catalog/v1/seasonal/rows/seasonal_meteor-night?page=1&profileId=' + profile)
                    assert status == 200 and scoped == first, (status, scoped)
                    assert get('/catalog/v1/seasonal?profileId=' + profile)[1]['rows'] == changed['rows']
                assert get('/catalog/v1/seasonal?profileId=../escape')[0] == 400
                print('PASS: global curation has identical public content for each profile; private categories remain separate')
            finally:
                process.terminate()
                process.wait(timeout=10)

if __name__ == '__main__':
    main()
