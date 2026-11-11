# Legacy-client / replacement-server compatibility

## What is and is not established

The pinned contract baseline is repository commit
`f30050f65aab1e5582d4cf11e14ad7cb1d4c6bce`, **not an identified installed APK**.
Its Android `clients/android/app/build.gradle.kts` declares:

- `applicationId = "tv.velvet"`
- `versionName = "1.14.0"`
- `versionCode = 40`
- Separate beta flavor: `.beta` application ID suffix and `-beta` version suffix.

**Dad's installed Android version is unknown. These tests do not establish that
his actual build works.** Obtain its package name, version name/code, and preferably
APK/build-source identity before claiming coverage of that release. Do not replace
this unknown with the latest source version. No device was contacted by this test.

The test harness is `backend/tests/legacy_compat.py`. It executes real backend
binaries over HTTP on disposable loopback ports and compares the pinned baseline
with the candidate. It does not build or run an Android APK, emulate client UI, or
contact TMDB, an NNTP provider, deployment infrastructure, or a real device.

## Pinned fixture provenance

The harness retrieves original files with `git show <exact-seed>:<path>` rather
than reading the modified client checkout. Its JSON evidence includes source and
binary SHA-256 hashes, the exact seed identity, stable HTTP request/response
fixtures, phase request counts, and explicit coverage limits.

| Contract | Original source under `clients/android/app/src/main/java/tv/velvet/data/` |
| --- | --- |
| Profiles/progress/watched/My List camelCase rows and envelopes | `sync/SyncDtos.kt`, `sync/SyncEngine.kt` |
| Metadata backend prefix, bearer auth, language/query format | `tmdb/TmdbMetadataRequest.kt` |
| Category Play All / shuffle queue request, nullable coordinates | `categories/CategoryRepository.kt` |
| Legacy handoff post/pending/claim and numeric IDs | `companion/CompanionApi.kt` |
| Disabled live catalog/guide/tune, Android capability headers | `live/LiveRepository.kt` |

Saved-category HTTP persistence and `/catalog/home` / `/catalog/rows/:id` are also
pinned to the original backend `saved_categories.rs` and `catalog_browse.rs`.
**The seed Android category repository stores generated categories locally; its
saved-category persistence is not falsely represented as Android HTTP traffic.**
These additional native-client baseline contracts are tested independently.

## Executed checks

- Exact status codes, JSON field names/value types, and stable response headers
  (`Content-Type`, `Cache-Control`, `Vary`) match the original binary for the
  literal static fixtures. No arbitrary field-stripping masks contract drift.
- Two synthetic profiles have distinct progress for the **same** movie ID.
  `/push` and `/state?since=...` verify profile natural keys, integer IDs/timestamps,
  nullable fields, LWW idempotence, arrival-time cursors, and watched tombstones.
- Nonempty synthetic movie categories are saved, read back with all original
  category/item fields, replaced by the candidate, read after rollback, deleted
  by the baseline, and still absent after re-upgrade. Another profile sees no
  category. Invalid/missing profiles retain the original error contracts.
- Legacy handoff creation returns numeric IDs; pending reads preserve exact
  payloads, profile isolation, single successful claim, and an empty queue after
  claim. No stream is actually played.
- Missing/wrong bearer credentials are rejected for representative legacy sync,
  metadata, catalog, queue, saved-category, handoff, and live requests. `/health`
  remains public. Both binaries are also restarted with an explicitly empty
  token to verify the original open-mode policy.
- The original five `holiday_*` IDs retain inactive-season row envelopes,
  client-specified `date` behavior, page bounds, and original titles. Active
  catalog/home/search/detail requests retain their missing-upstream error
  contracts. Invalid dates, unknown IDs, and invalid page numbers are checked.
- Movie metadata/detail and queue requests use synthetic identifiers and no
  upstream key. Metadata fails closed with the original 503; queue Play All and
  shuffle (`shuffle=false` / `true`) fail with the original 502. **Successful
  metadata retrieval, show expansion, shuffle distribution, and media playback
  are not covered by these credential-free HTTP checks.** No successful upstream
  response is fabricated.
- Disabled Live TV channels, guide, and tune retain original envelopes/statuses,
  including old Android capability-header handling. Enabled live playback is
  outside this test's scope.
- Existing DB/sync/saved-category/queue/metadata/handoff/stream/pool/repair/live/
  resolver/config files must hash identically to the seed. Original production
  portions of catalog browsing, catalog wiring, and startup must also remain
  byte-identical after removing only explicitly permitted additive seasonal
  route/module lines. Test-only additions are allowed. This source guard protects
  legacy success paths and date recipes that cannot be populated without upstream
  data; it is **not** an upstream-success HTTP test.

The additive `/catalog/v1/seasonal` route is probed on the candidate only. New
versioned discovery must not replace `/catalog/home`, reinterpret its date query,
rename old row IDs, or modify legacy auth/fields. The dedicated seasonal test
suite owns the full new-versioned-route contract.

## Actual binary rollback, not only a schema inspection

All durable fixture writes go through HTTP. SQLite is opened read-only afterward
for `PRAGMA integrity_check`.

1. Start the **original seed binary** against a new database; write profiles,
   progress, watched state, My List, and saved categories; stop that process.
2. Start the **candidate binary against that same database**; replay legacy
   fixtures, read original data, write newer progress and a watched tombstone,
   and replace the saved category; stop that process.
3. Restart the **original binary against that same database**; read the candidate's
   writes without conversion or restore, verify stale writes lose, then write a
   newer progress position and delete the category; stop that process.
4. Restart the candidate again and verify the baseline's rollback-era writes.
5. Verify SQLite integrity and separately check explicit empty-token startup.

This establishes local rollback readability and continued writes for the tested
schema/data. It is not a production backup/restore drill or a streaming-worker
rollback test. The baseline and candidate must be distinct executable contents;
passing the candidate as both sides is rejected.

## Reproduce

From the repository worktree root, with the workspace-local Rust installation:

```sh
mkdir -p .tools/legacy-source
# Use a pristine extraction directory; this does not switch branches or create
# another git worktree. The archive includes the original Cargo.lock.
git archive f30050f65aab1e5582d4cf11e14ad7cb1d4c6bce backend \
  | tar -x -C .tools/legacy-source
CARGO_HOME="$PWD/.tools/cargo" RUSTUP_HOME="$PWD/.tools/rustup" \
  .tools/cargo/bin/cargo build --locked --offline \
  --manifest-path .tools/legacy-source/backend/Cargo.toml \
  --target-dir .tools/legacy-target -j 2

# Build the candidate separately, or use the already built current executable.
CARGO_HOME="$PWD/.tools/cargo" RUSTUP_HOME="$PWD/.tools/rustup" \
  .tools/cargo/bin/cargo build --locked --offline \
  --manifest-path backend/Cargo.toml

python3 backend/tests/legacy_compat.py \
  --binary backend/target/debug/velvet-backend \
  --baseline-binary .tools/legacy-target/debug/velvet-backend \
  --report "$TMPDIR/legacy-compatibility-report.json"
```

Python uses only its standard library. A full local git object for the seed is
required. `--offline` requires the dependency cache; failure to build the baseline
is a blocker, not permission to reuse the candidate as the baseline. Separate
Cargo target directories prevent rebuilding the seed over the candidate binary.

The harness creates and removes its own directory under `$TMPDIR`, supplies an
inert `127.0.0.1:9` provider (startup requires a provider entry), disables live/AI/
repair via defaults, and uses an empty TMDB key. It does not inherit secret-bearing
`VELVET_*`, `NNTP_*`, or proxy configuration, nor read the operator's config. No
resolver/media requests are made. Do not point the binary arguments at untrusted
executables. The optional report contains only synthetic fixture data.

## Local verification record

The original backend was compiled successfully from the seed archive with the
workspace Rust toolchain and its cached dependencies. Only existing Rust
`fetch_update` deprecation and dead-code warnings were emitted. The harness passed
against the current replacement binary, including the four-process same-database
upgrade/rollback/re-upgrade sequence and SQLite integrity check. Consult the
machine-readable report generated by the command above for exact hashes, fixture
counts, and request counts for that run; readiness polling can change request
counts between runs. No commits, pushes, or deployment changes are part of this
procedure.
