#!/usr/bin/env python3
"""Pinned seed-client HTTP contracts and same-database binary rollback.

Standard library only; starts disposable loopback servers, never deployment hosts.
See docs/legacy-compatibility.md for scope and intentionally untested behavior.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

SEED = "f30050f65aab1e5582d4cf11e14ad7cb1d4c6bce"
ROOT = Path(__file__).resolve().parents[2]
ANDROID = "clients/android/app/"
# These production subsystems must remain byte-identical, not merely pass samples.
UNCHANGED = [f"backend/src/{name}.rs" for name in (
    "db", "sync", "saved_categories", "category_queue", "metadata", "handoff",
    "stream", "pool", "repair", "live", "resolver", "config",
)]
SOURCES = {
    "sync": ANDROID + "src/main/java/tv/velvet/data/sync/SyncDtos.kt",
    "sync_requests": ANDROID + "src/main/java/tv/velvet/data/sync/SyncEngine.kt",
    "queue": ANDROID + "src/main/java/tv/velvet/data/categories/CategoryRepository.kt",
    "metadata": ANDROID + "src/main/java/tv/velvet/data/tmdb/TmdbMetadataRequest.kt",
    "handoff": ANDROID + "src/main/java/tv/velvet/data/companion/CompanionApi.kt",
    "live": ANDROID + "src/main/java/tv/velvet/data/live/LiveRepository.kt",
    # Saved-category HTTP APIs are native-client baseline contracts; this Android
    # seed stores generated categories locally, and must not be mislabeled.
    "saved": "backend/src/saved_categories.rs",
    "home": "backend/src/catalog_browse.rs",
}
TOKEN = "legacy-compat-synthetic-token"
PROFILE = "compat-alice"
OTHER = "compat-bob"
CATEGORY_FIELDS = ("mediaId type title year rating posterUrl backdropUrl imdbId "
                   "season episode episodeTitle stillUrl").split()
CATEGORY = {
    "id": "ai-compat-movies", "title": "Synthetic movies",
    "items": [dict.fromkeys(CATEGORY_FIELDS) | {
        "mediaId": number, "type": "movie", "title": f"Synthetic movie {number}",
        "year": "2000", "rating": 7.0, "imdbId": f"tt{number:07d}",
    } for number in (900001, 900002)],
}
PUSH = {
    "profiles": [{"id": profile, "name": name, "color": "blue", "createdAt": 1000,
                  "updatedAt": 1000, "deleted": False}
                 for profile, name in ((PROFILE, "Fixture Alice"), (OTHER, "Fixture Bob"))],
    "progress": [{"profileId": PROFILE, "mediaId": 900001, "type": "movie",
                  "imdbId": "tt0900001", "title": "Synthetic movie 900001",
                  "posterUrl": None, "backdropUrl": None, "season": None,
                  "episode": None, "episodeTitle": None, "positionMs": 42000,
                  "durationMs": 120000, "year": "2000", "rating": 7.0,
                  "updatedAt": 1000, "deleted": False}],
    "watched": [{"profileId": PROFILE, "mediaId": 900010, "season": 2,
                 "episode": 3, "watchedAt": 1000, "updatedAt": 1000, "deleted": False}],
    "mylist": [{"profileId": PROFILE, "mediaId": 900002, "type": "movie",
                "title": "Synthetic movie 900002", "posterUrl": None, "backdropUrl": None,
                "year": "2000", "rating": 7.0, "addedAt": 1000,
                "updatedAt": 1000, "deleted": False}],
}
# The same media ID under a second profile must remain a separate natural key.
PUSH["progress"].append(PUSH["progress"][0] | {"profileId": OTHER, "positionMs": 21000})
HANDOFF = {"profileId": PROFILE, "mediaId": 900001, "type": "movie",
           "season": None, "episode": None, "positionMs": 42000,
           "title": "Synthetic movie 900001"}
ERROR_CATALOG = {"error": "The catalog could not be loaded. Try again."}
ERROR_INVALID = {"error": "Invalid catalog request."}
ERROR_QUEUE = {"error": "Some episode metadata could not load. Try again; your queue has not started."}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def seed_file(path):
    return subprocess.check_output(["git", "show", f"{SEED}:{path}"], cwd=ROOT)


def provenance():
    build = seed_file(ANDROID + "build.gradle.kts").decode()
    require(re.search(r'versionCode\s*=\s*40\b', build), "Seed Android versionCode changed")
    require('versionName = "1.14.0"' in build, "Seed Android versionName changed")
    evidence = {name: {"path": path, "sha256": hashlib.sha256(seed_file(path)).hexdigest()}
                for name, path in SOURCES.items()}
    # Guard against accidentally deriving fixtures from the edited/latest clients.
    for name, tokens in {
        "sync": ["val profiles: List<SyncProfileRow>", "val profileId: String", "val serverNow: Long"],
        "queue": ['"/categories/queue"', "val shuffle: Boolean", "val season: Int?"],
        "metadata": ['addPathSegments("metadata/tmdb")', '"Authorization", "Bearer'],
        "handoff": ['"/handoff"', '"/handoff/claim"', '"${cfg.baseUrl}/handoff/pending?profileId='],
        "live": ['"/live/channels"', '"/live/tune"', '"X-Velvet-Client", "android"'],
    }.items():
        text = seed_file(SOURCES[name]).decode()
        require(all(token in text for token in tokens), f"Pinned {name} request evidence absent")
    hashes = {}
    for path in UNCHANGED:
        before = hashlib.sha256(seed_file(path)).hexdigest()
        after = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        require(before == after, f"Protected legacy subsystem modified: {path}")
        hashes[path] = before
    # Additions in these shared modules must not silently rewrite legacy handlers.
    # Ignore test-only additions, but compare all existing production bytes.
    for path in ("backend/src/catalog_browse.rs", "backend/src/catalog.rs", "backend/src/main.rs"):
        before = seed_file(path).split(b"#[cfg(test)]", 1)[0]
        after = (ROOT / path).read_bytes().split(b"#[cfg(test)]", 1)[0]
        if path.endswith("catalog.rs"):
            allowed = {
                b'        .route("/catalog/v1/seasonal", get(crate::seasonal::discovery))\n',
                b'        .route("/catalog/v1/seasonal/rows/:id", get(crate::seasonal::row))\n',
                b'        .route("/catalog/v1/home", get(crate::seasonal::home))\n',
            }
            after = b"".join(line for line in after.splitlines(keepends=True) if line not in allowed)
        elif path.endswith("main.rs"):
            after = after.replace(b"mod seasonal;\n", b"")
        elif path.endswith("catalog_browse.rs"):
            # Remove only the new, disjoint v1 helpers; all existing handler bytes
            # still compare exactly with the seed (including legacy recipes/date).
            for start_marker, end_marker in [
                (b"fn seasonal_recipe(", b"async fn load_row("),
                (b"pub(crate) async fn general_home_rows(", b"pub async fn home("),
            ]:
                require(after.count(start_marker) == 1 and start_marker not in before,
                        "Unexpected additive helper boundaries")
                start = after.index(start_marker)
                end = after.index(end_marker, start)
                after = after[:start] + after[end:]
        require(before == after, f"Legacy production code changed beyond permitted additive wiring: {path}")
        hashes[path + "#legacy-production"] = hashlib.sha256(before).hexdigest()
    return {"seed": SEED, "android": {"versionName": "1.14.0", "versionCode": 40,
            "applicationId": "tv.velvet", "dad_installed_build": "unknown; not verified"},
            "fixture_sources": evidence, "unchanged_sha256": hashes}


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.requests = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, method="GET", body=None, auth=TOKEN, headers=None):
        self.requests += 1
        request_headers = {"Accept": "application/json", **(headers or {})}
        if auth is not None:
            request_headers["Authorization"] = "Bearer " + auth
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=request_headers, method=method)
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            try:
                value = json.loads(raw)
            except ValueError:
                value = raw.decode()
            # Compare stable protocol headers, not Date/Content-Length/connection.
            selected = {key: response.headers.get(key) for key in
                        ("Content-Type", "Cache-Control", "Vary")}
            return {"status": response.status, "body": value, "headers": selected}

    def expect(self, path, status=200, expected=None, **kwargs):
        response = self.call(path, **kwargs)
        require(response["status"] == status, f"{path}: expected HTTP {status}, got {response}")
        if expected is not None:
            require(response["body"] == expected, f"{path}: body mismatch {response['body']!r} != {expected!r}")
        return response["body"]


@contextmanager
def server(binary, directory, label, token=TOKEN):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = directory / "velvet.toml"
    config.write_text(f'bind="127.0.0.1"\nport={port}\ndb_path={json.dumps(str(directory / "state.db"))}\n'
                      f'token={json.dumps(token)}\n[[providers]]\nname="inert-fixture"\n'
                      'host="127.0.0.1"\nport=9\ntls=false\nusername=""\npassword=""\nmax_connections=1\n')
    # Do not inherit secret-bearing configuration, proxy or upstream environment.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LD_LIBRARY_PATH") if key in os.environ}
    env.update(VELVET_CONFIG=str(config), VELVET_TMDB_KEY="", RUST_LOG="error")
    manifest = directory / "seasonal.json"
    manifest.write_text(json.dumps({"schemaVersion": 1, "rows": []}))
    env["VELVET_SEASONAL_MANIFEST"] = str(manifest)
    client = Client(port)
    with (directory / f"{label}.log").open("wb") as log:
        process = subprocess.Popen([str(binary), "serve"], cwd=directory, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while True:
                require(process.poll() is None, f"{label} exited; see {directory / (label + '.log')}")
                try:
                    client.expect("/health", expected={"ok": True}, auth=None)
                    break
                except (OSError, urllib.error.URLError):
                    require(time.monotonic() < deadline, f"{label} did not start")
                    time.sleep(.05)
            yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def contract(client):
    """Literal seed-era request fixtures; baseline is the exact-body/header oracle."""
    cases = []
    def case(path, status, expected=None, **kwargs):
        response = client.call(path, **kwargs)
        require(response["status"] == status, f"Fixture {path}: {response}")
        if expected is not None:
            require(response["body"] == expected, f"Fixture {path}: {response['body']} != {expected}")
        cases.append({"request": {"path": path, **kwargs}, "response": response})

    case("/health", 200, {"ok": True}, auth=None)
    protected = [("/state?since=0", "GET", None), ("/push", "POST", {}),
                 ("/metadata/tmdb/movie/900001?language=en-US", "GET", None),
                 ("/categories/saved?profileId=" + PROFILE, "GET", None),
                 ("/categories/queue", "POST", {"title": "Test", "items": []}),
                 ("/catalog/home?date=2026-10-31", "GET", None),
                 ("/catalog/rows/holiday_halloween?date=2026-07-31", "GET", None),
                 ("/handoff", "POST", HANDOFF), ("/handoff/pending?profileId=" + PROFILE, "GET", None),
                 ("/handoff/claim", "POST", {"id": 1}),
                 ("/live/channels", "GET", None), ("/live/tune", "POST", {"channelId": "fixture"})]
    for path, method, body in protected:
        for auth in (None, "wrong-fixture-token"):
            case(path, 401, method=method, body=body, auth=auth)

    # Metadata request shape exactly follows seed TmdbMetadataRequest.kt; no key
    # means fail-closed 503, never a fabricated successful TMDB response.
    message = "Metadata is not configured. Set VELVET_TMDB_KEY on the backend."
    for path in ("movie/900001?language=en-US&append_to_response=external_ids",
                 "tv/900010/season/2?language=en-US", "discover/movie?language=en-US&page=1"):
        case("/metadata/tmdb/" + path, 503, {"success": False, "error": message, "status_message": message})
    case("/metadata/tmdb/account?language=en-US", 400)
    case("/metadata/tmdb/movie/900001?api_key=must-not-forward", 400)
    case("/catalog/movie/900001", 502, ERROR_CATALOG)
    case("/catalog/show/900010/seasons/2", 502, ERROR_CATALOG)
    case("/catalog/search?q=Synthetic&page=1", 502, ERROR_CATALOG)
    # Existing native API date query, row IDs, page bounds and inactive row shape.
    for day in ("2026-01-05", "2026-01-06", "2026-02-01", "2026-02-15", "2026-02-16",
                "2026-07-31", "2026-08-01", "2026-10-31", "2026-11-01", "2026-11-30",
                "2026-12-01", "2026-12-25", "2026-12-26", "2026-12-31"):
        case("/catalog/home?date=" + day, 502, ERROR_CATALOG)
    for row, title, inactive in (
        ("holiday_halloween", "Halloween Movies", "2026-07-31"),
        ("holiday_thanksgiving", "Thanksgiving Movies", "2026-12-01"),
        ("holiday_christmas", "Christmas Movies", "2026-10-31"),
        ("holiday_newyear", "New Year's Movies", "2026-01-06"),
        ("holiday_valentines", "Valentine's Movies", "2026-02-16"),
    ):
        case(f"/catalog/rows/{row}?date={inactive}&page=2", 200,
             {"id": row, "title": title, "style": "poster", "items": [],
              "page": 2, "totalPages": 0, "stale": False})
    for row in ("popular_now", "trending_shows", "new_to_stream", "bingeworthy", "acclaimed", "action", "animation_family"):
        case(f"/catalog/rows/{row}?date=2026-10-31&page=1", 502, ERROR_CATALOG)
    for suffix in ("date=2026-02-30", "page=0", "page=21"):
        case("/catalog/rows/holiday_halloween?" + suffix, 400, ERROR_INVALID)
    case("/catalog/home?date=2026-02-30", 400, ERROR_INVALID)
    case("/catalog/rows/not-a-legacy-row", 404, {"error": "This catalog item is unavailable."})
    for shuffle in (False, True):
        case("/categories/queue", 502, ERROR_QUEUE, method="POST", body={
            "title": CATEGORY["title"], "items": [{"mediaId": i["mediaId"], "type": "movie",
            "season": None, "episode": None} for i in CATEGORY["items"]], "shuffle": shuffle})
    case("/categories/queue", 400, method="POST", body={"title": "Invalid", "items": []})
    case("/categories/saved?profileId=not-synced", 404,
         {"error": "Choose an available profile first."})
    case("/categories/saved", 400, {"error": "Invalid saved category request."})
    case("/live/channels", 200, {"enabled": False, "sourceName": "Spectrum", "channels": [],
         "message": "Live TV is not configured yet"})
    case("/live/guide", 200, {"enabled": False, "sourceName": "Spectrum", "updatedAt": None,
         "channels": [], "message": "Program guide is not configured yet"})
    case("/live/tune", 503, {"error": "Live TV is not configured yet"}, method="POST", body={"channelId": "fixture"}, headers={
        "X-Velvet-Client": "android", "X-Velvet-Playback-Capabilities": "hls-clear,dash-widevine,hls-fmp4-widevine"})
    case("/handoff", 400, {"error": "profileId required"}, method="POST", body={})
    case("/handoff/claim", 400, {"error": "id required"}, method="POST", body={})
    return cases


def read_state(client, expected):
    response = client.expect("/state?since=0")
    require(set(response) == {"serverNow", *PUSH}, f"State envelope drift: {response}")
    require(type(response["serverNow"]) is int, "serverNow is not integer milliseconds")
    for table, rows in expected.items():
        sort = lambda row: json.dumps(row, sort_keys=True)
        require(sorted(response[table], key=sort) == sorted(rows, key=sort), f"{table} state mismatch: {response[table]} != {rows}")
    return response


def push(client, body, count):
    result = client.expect("/push", method="POST", body=body)
    require(set(result) == {"applied", "serverNow"} and result["applied"] == count
            and type(result["serverNow"]) is int, f"Push result drift: {result}")


def saved(client, profile, categories):
    client.expect("/categories/saved?profileId=" + profile, expected={"categories": categories})


def handoff(client):
    created = client.expect("/handoff", method="POST", body=HANDOFF)
    require(set(created) == {"id"} and type(created["id"]) is int, f"Legacy numeric ID drift: {created}")
    client.expect("/handoff/pending?profileId=" + OTHER, expected={"handoff": None})
    client.expect("/handoff/pending?profileId=" + PROFILE, expected={"handoff": HANDOFF | created})
    client.expect("/handoff/claim", method="POST", body=created, expected={"claimed": True})
    client.expect("/handoff/claim", method="POST", body=created, expected={"claimed": False})
    client.expect("/handoff/pending?profileId=" + PROFILE, expected={"handoff": None})


def run(args, directory):
    report = provenance()
    binary = Path(args.binary).resolve()
    baseline = Path(args.baseline_binary).resolve()
    for path in (binary, baseline):
        require(path.is_file(), f"Missing server binary: {path}")
    require(binary != baseline and binary.read_bytes() != baseline.read_bytes(),
            "Rollback must use a distinct baseline executable, not the candidate twice")
    report["binaries"] = {label: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                          for label, path in (("candidate", binary), ("baseline", baseline))}
    report["phases"] = []
    with server(baseline, directory, "baseline-create") as client:
        fixtures = contract(client)
        push(client, PUSH, 6)
        read_state(client, PUSH)
        client.expect("/categories/saved", method="POST", body={"profileId": PROFILE, "category": CATEGORY}, expected=CATEGORY)
        saved(client, PROFILE, [CATEGORY])
        saved(client, OTHER, [])
        handoff(client)
        report["phases"].append({"name": "baseline-create", "http_requests": client.requests})

    expected = deepcopy(PUSH)
    expected["progress"][0].update(positionMs=84000, updatedAt=2000)
    expected["watched"][0].update(updatedAt=2000, deleted=True)
    revised = deepcopy(CATEGORY)
    revised["title"] = "Written by replacement server"
    with server(binary, directory, "candidate-upgrade") as client:
        require(contract(client) == fixtures, "Candidate changed a pinned legacy status/body/header fixture")
        read_state(client, PUSH)
        saved(client, PROFILE, [CATEGORY])
        push(client, PUSH, 0)  # Same timestamp loses; replay is idempotent.
        cursor = read_state(client, PUSH)["serverNow"]
        time.sleep(.02)
        push(client, {"progress": expected["progress"], "watched": expected["watched"]}, 2)
        read_state(client, expected)
        delta = client.expect(f"/state?since={cursor}")
        require(delta["progress"] == expected["progress"][:1] and delta["watched"] == expected["watched"]
                and delta["profiles"] == [] and delta["mylist"] == [], f"Arrival cursor semantics changed: {delta}")
        client.expect("/categories/saved", method="POST", body={"profileId": PROFILE, "category": revised}, expected=revised)
        saved(client, PROFILE, [revised])
        # Additive route probe is intentionally not part of the legacy oracle.
        client.expect("/catalog/v1/seasonal")
        handoff(client)
        report["phases"].append({"name": "candidate-upgrade-and-write", "http_requests": client.requests})

    with server(baseline, directory, "baseline-rollback") as client:
        read_state(client, expected)
        saved(client, PROFILE, [revised])
        saved(client, OTHER, [])
        push(client, PUSH, 0)  # Baseline also honors newer candidate LWW values.
        handoff(client)
        expected["progress"][0].update(positionMs=96000, updatedAt=3000)
        push(client, {"progress": expected["progress"]}, 1)
        read_state(client, expected)
        client.expect("/categories/saved/ai-compat-movies?profileId=" + PROFILE,
                      method="DELETE", expected={"ok": True})
        saved(client, PROFILE, [])
        report["phases"].append({"name": "baseline-rollback-read-and-write", "http_requests": client.requests})

    with server(binary, directory, "candidate-reupgrade") as client:
        read_state(client, expected)
        saved(client, PROFILE, [])
        report["phases"].append({"name": "candidate-reupgrade", "http_requests": client.requests})
    # SQLite verification is read-only; all fixture data was written via HTTP.
    with sqlite3.connect(f"file:{directory / 'state.db'}?mode=ro", uri=True) as db:
        require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "Rollback damaged DB integrity")
    # Empty token remains an explicit baseline policy; do not accidentally tighten
    # auth for users who run the existing backend in open mode.
    for label, path in (("baseline-open", baseline), ("candidate-open", binary)):
        with server(path, directory, label, token="") as client:
            client.expect("/state?since=0", auth=None)
            client.expect("/categories/saved?profileId=" + OTHER, auth=None, expected={"categories": []})
            report["phases"].append({"name": label, "http_requests": client.requests})
    report.update(result="PASS", static_fixture_count=len(fixtures), static_fixtures=fixtures,
                  total_http_requests=sum(phase["http_requests"] for phase in report["phases"]),
                  same_database_binary_sequence=["baseline", "candidate", "baseline", "candidate"],
                  sqlite_integrity="ok", limits=["Unknown dad Android build not verified",
                  "No APK/device UI or successful provider/media playback tested",
                  "Metadata/detail/queue Play All/shuffle validate fail-closed missing-key contracts, not successful expansion",
                  "Legacy home upstream-success/date ordering is protected by source evidence, not populated HTTP responses"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, help="Current replacement backend executable")
    parser.add_argument("--baseline-binary", required=True, help=f"Backend built unmodified from {SEED}")
    parser.add_argument("--report", type=Path, help="Write machine-readable evidence outside the repository")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="velvet-legacy-compat-", dir=os.environ.get("TMPDIR")) as temp:
        report = run(args, Path(temp))
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({key: value for key, value in report.items() if key != "static_fixtures"}, indent=2))


if __name__ == "__main__":
    main()
