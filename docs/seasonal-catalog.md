# Server-managed seasonal catalog — experimental only

This experiment is additive. It is not a production release, a device rollout, or
permission to change the shared server. The artifact workflow is restricted to
`gopro8752/velvet-media-seasonal`, pushes to experimental branch `wt/t_fbcd5d8f`.
It never logs in to a registry, pushes an image, uses
production/stable/latest tags, or installs an app. Existing CI is separate.

## Architecture and compatibility

```text
/config/seasonal.json (operator-owned, atomic directory-mounted updates)
        | read + strict validation on each versioned catalog request
        v
backend/src/seasonal.rs -- UTC annual eligibility + manifest order
        |                     |
        | items source        | movies source (fixed TMDB discover recipe)
        | normalized media    v
        |                existing bounded BrowseCache --> runtime TMDB key
        v
 shared existing catalog bearer middleware
        |
        +-- /catalog/v1/seasonal -------- Android / iOS descriptors
        +-- /catalog/v1/seasonal/rows/:id Android / iOS paged items
        +-- /catalog/v1/home ------------ Roku inline items + general rows

old clients --> unchanged /catalog/home, /catalog/rows/:id, holiday_* IDs
personal categories --> existing profile-scoped saved/generate/queue APIs
```

The server owns seasonal IDs, titles, artwork, style, active dates, content and
default ordering. Arbitrary new valid seasonal IDs require no client calendar
update. `profileId` is accepted and validated for client consistency, but seasonal
curation is **global**: it neither selects nor reads private profile data. Saved,
generated and archived categories remain separate; seasonal descriptors are not
saved-category IDs or Play All / Shuffle queue targets. Normal item details and
playback paths are unchanged. No new database schema or migration is introduced.

Old apps keep their original local calendars and legacy request semantics; the
new manifest does not control an old app. New Android/iOS apps hide seasonal
sections on an unsupported/invalid/unavailable endpoint instead of reconstructing
a local calendar. Only an unsupported-version 404 lets Roku fall back to legacy
Home, without a date query and with all legacy seasonal namespaces removed.
Ordinary discovery survives a cold start; auth/transient failures never trigger
that fallback. This is not evidence for every historical backend version.

## Configuration and hot reload

1. `VELVET_SEASONAL_MANIFEST` explicitly selects a JSON file.
2. Otherwise read `seasonal.json` beside the file named by `VELVET_CONFIG`
   (`velvet.toml` when unset; the Docker image sets `/config/velvet.toml`).
3. Only a **missing implicit** file falls back to the compiled
   [`backend/seasonal.default.json`](../backend/seasonal.default.json).
   An explicitly selected missing file, malformed file, oversized file, or read
   error does not fall back to defaults or the last valid manifest.

The file is reread per request, bounded to 128 KiB. Mount its **parent directory**,
not just the file: replacing a host file mounted individually can leave a
container reading the old inode. Keep the directory mount read-only inside the
container; the operator writes on the host. Write a complete sibling file, then
rename/replace it on the same filesystem. No restart or client rebuild is needed.
Separate discovery and row requests may observe different revisions during an
edit; this is not a multi-request snapshot protocol.

The image compiles the fallback with `include_bytes!`; the builder must copy
`seasonal.default.json` before its real Cargo compilation. The deny-all
`backend/.dockerignore` now explicitly allows this single JSON file. The artifact
workflow also stages only source/license/admin inputs plus the manifest into a
fresh runner build context; no runtime secrets/state enter it. Direct builds from
the backend context use the same Dockerfile and embedded manifest.
Runtime edits belong in the mounted file, not in the image or a new DB table.

Minimal synthetic manifest (no upstream credential required):

```json
{
  "schemaVersion": 1,
  "rows": [{
    "id": "seasonal_meteor-night",
    "title": "Meteor Night",
    "style": "poster",
    "enabled": true,
    "start": [1, 1],
    "end": [12, 31],
    "source": {
      "type": "items",
      "items": [{"mediaId": 1, "type": "movie", "title": "Synthetic example"}]
    }
  }]
}
```

Use real catalog identifiers for actual curation. Inline entries are metadata,
not playable URLs, arbitrary external sources or uploaded media.

### Manifest rules

- `schemaVersion` must be `1`; at most 16 rows, in display order. Unknown fields
  are rejected throughout the manifest. `{"schemaVersion":1,"rows":[]}` explicitly
  disables all seasonal rows without changing ordinary discovery.
- Every row requires unique `id`, `title`, `style`, `enabled`, `start`, `end`,
  and `source`. IDs start `seasonal_`, contain only lowercase ASCII letters,
  digits, `_`, `-`, and are 10–80 bytes total. Titles must be nonblank, contain no
  control characters, and be at most 160 characters. Styles: `poster`, `landscape`.
- `start` / `end` are `[month, day]`, inclusive, annually repeated in **server
  UTC**. Start after end wraps across New Year. Feb 29 is valid but matches only
  on leap days; it is not shifted to Feb 28. All active overlapping rows remain
  in manifest order. No client-supplied date override exists in v1.
- Optional `artworkUrl` and item artwork fields must use
  `https://image.tmdb.org/t/p/`, with a nonempty safe ASCII path of at most 300
  bytes, no `..`, and only letters/digits, `/`, `.`, `_`, `-`.
- `source: {"type":"items","items":[...]}` permits at most 400 entries.
  Required: positive `mediaId` no greater than `2147483647`, `type` = `movie` or
  `show`, and a nonblank control-free title of at most 400 characters. Duplicate
  `(type, mediaId)` pairs within a row are rejected.
- Optional normalized item fields: `year` (string, max 20 bytes), `rating`
  (finite 0–10), `posterUrl`, `backdropUrl`, `logoUrl`, `overview` (max 12,000
  characters), `genres` (up to 20 strings, each max 80 characters),
  `runtimeMinutes` (unsigned, max 10,000), `imdbId` (`tt` plus digits, total
  7–12 bytes). Absent nullable fields serialize as null; genres defaults to `[]`.
- `source: {"type":"movies","keyword":207317,"genres":[10751,35,10749]}`
  uses a required positive keyword and up to eight positive genre IDs, each at
  most `2147483647`. The recipe uses US digital releases through the server's
  current day, popularity order, English metadata, no adult content, and a vote
  count floor of 25. Genres are OR-combined. There is no arbitrary upstream URL,
  raw query, secret, or profile selector in the source. Runtime movie discovery
  requires the existing `VELVET_TMDB_KEY`; do not put it in the manifest.

### Embedded annual defaults

| Row | Inclusive UTC window |
| --- | --- |
| `seasonal_halloween` | Aug 1–Oct 31 |
| `seasonal_thanksgiving` | Nov 1–Nov 30 |
| `seasonal_christmas` | Nov 1–Dec 31 |
| `seasonal_newyear` | Dec 26–Jan 5 |
| `seasonal_valentines` | Feb 1–Feb 15 |

The overlap is deliberate: Christmas precedes New Year on Dec 26, Thanksgiving
precedes Christmas in November. These are backend defaults, not client rules.

## HTTP contract, caching and refresh

All three GET endpoints use existing catalog auth: a nonempty configured token
requires the matching `Authorization` bearer header; an empty token retains
legacy open mode. This change adds no authentication policy. `/health` remains
public. Successful versioned responses send `Cache-Control: no-store`.

| Route | Response |
| --- | --- |
| `/catalog/v1/seasonal` | `schemaVersion`, `revision`, `effectiveDate`, `timezone:"UTC"`, active row descriptors (`id`, `title`, `style`, nullable `artworkUrl`) |
| `/catalog/v1/seasonal/rows/:id?page=N` | Descriptor plus the same version/date metadata, `items`, `page`, `totalPages`, `stale` |
| `/catalog/v1/home` | Version/date metadata, active seasonal rows inline at page 1, then general rows, `failedRows`, nullable `message` |

`revision` is an MD5 fingerprint of the **raw manifest bytes**, not an image SHA,
security signature, or monotonically increasing version. Whitespace edits change
it. Pages are canonical decimal integers `1`–`20` (default `1`), 20 items per
curated page; empty/beyond-content pages are empty, not a request error. Unknown,
disabled and inactive row IDs return 404. Invalid queries return 400, including
`date`, unknown parameters, noncanonical pages, or invalid `profileId`.
`profileId` must be 1–128 ASCII letters/digits/underscore/hyphen. Discovery and Home
do not accept `page`.

Invalid configuration makes discovery/row requests return 503. Home instead
records `seasonal_configuration` in `failedRows` and can still return successful
general rows. Individual loading failures are omitted and named in `failedRows`;
a wholly failed Home returns 502. With no upstream key, the synthetic inline row
can succeed while ordinary TMDB rows are reported failed. That partial response
is intentional, not simulated upstream success.

Inline items have no content cache. Movie sources reuse the existing 96-entry
browse cache, four upstream permits, 15-minute fresh results and a four-hour
stale-on-upstream-error window. Day and recipe/query changes create new keys;
row eligibility is checked before cached content is considered. A title/style
edit uses current metadata even if the underlying movie query result is cached.
A content query edit need not rebuild the image. A cached upstream response does
not keep a removed/disabled/inactive manifest row alive.

Android/iOS refresh immediately and every 60 seconds while Home is foregrounded,
plus Home retry/refresh and profile/connection changes. iOS deduplicates backend
URL, sync origin/token and profile publisher values, invalidates synchronously,
and debounces only replacement requests (50 ms). Routine sync-status and unrelated
preferences do not cancel/clear/refetch seasonal rows or hero; row order/visibility
preferences re-resolve existing rows without a network refresh. Pause/background cancels
seasonal tasks and invalidates superseded generations. Neither has a persistent
seasonal cache or local calendar. Backend credentials
are restricted to the validated backend origin and seasonal redirects are
refused. Android Settings marks rows **Managed by server** without local
seasonal reorder/hide controls. Launcher featured metadata uses an isolated
cache namespace, not its own seasonal fetch. Roku refreshes versioned Home on
entry/Back and its 30-second Home sync timer, without overlapping in-flight
requests. Roku preserves explicit profile hides but seasonal ordering is server
managed; saved moves apply only to ordinary rows. Roku renders inline page 1
only, with no extra row paging. Failure removes cached
seasonal rows; failed/stale seasonal rows cannot be resurrected by local rules.

## Experimental image/APK artifacts

[`.github/workflows/seasonal-artifacts.yml`](../.github/workflows/seasonal-artifacts.yml)
uses ordinary `ubuntu-24.04` hosted jobs with read-only contents permission and
commit-pinned checkout/setup-java/upload-artifact actions. It does not dispatch
production workflows, use a deployment environment, load secret configuration,
or publish a registry tag. Actions artifacts expire after seven days.

- **Image:** build target `runtime` from an explicitly staged, allowlisted backend
  context, local tag `velvet-seasonal:sha-${GITHUB_SHA}`, verify the OCI revision label, run the
  isolated HTTP smoke, then `docker save | gzip`. Upload archive,
  `image-inspect.json`, `SHA256SUMS` and `provenance.json` only after success.
- **Android:** JDK 17; runner SDK manager installs `platforms;android-37.2`,
  `build-tools;36.0.0`, and platform-tools. Missing SDK packages fail the job;
  there is no fake build or compile SDK downgrade. Run actual Gradle
  `testSeasonalDebugUnitTest` and `assembleSeasonalDebug`, arm64-v8a only.
  Verify APK badging, upload APK, checksums, provenance and native test reports.
- **Identity:** `tv.velvet.seasonal`, **Velvet Seasonal Lab**,
  `1.14.0-seasonal`; not stable `tv.velvet`, not beta. This is a debug APK with
  an ephemeral runner debug key, not a release-signed update. Do not uninstall
  or clear daily-app data to work around signing conflicts.
- Artifact builds run only for branch pushes; names include the exact branch-head
  `GITHUB_SHA`, never a synthetic PR merge commit. Provenance records
  that SHA, ref, repository, run ID/attempt, image ID or APK hashes. Do not infer
  candidate identity from a branch name alone. A checksum is integrity evidence,
  not a signed supply-chain attestation. Compare the recorded SHA with the
  independently approved candidate.

Rebuild an exact reviewed source commit on an isolated build daemon (never on the production host):

```sh
# Set REVIEWED_SHA to the full independently reviewed commit, not a branch/tag.
REVISION=$(git rev-parse --verify "${REVIEWED_SHA}^{commit}")
test "$(git rev-parse HEAD)" = "$REVISION"
docker build --target runtime --label "org.opencontainers.image.revision=$REVISION" \
  --tag "velvet-seasonal:sha-$REVISION" backend
python3 scripts/seasonal_image_smoke.py --image "velvet-seasonal:sha-$REVISION"
```

After downloading an artifact, use `sha256sum -c SHA256SUMS` in its directory.
Loading a saved image into a **local isolated test daemon** is not a deployment:

```sh
# Use the exact downloaded archive filename, never a stable/latest tag.
gzip -dc velvet-seasonal-sha-<EXACT_SHA>.tar.gz | docker load
python3 scripts/seasonal_image_smoke.py --image velvet-seasonal:sha-<EXACT_SHA>
```

The image smoke creates a unique `--internal` Docker network and **publishes no
host ports**. A helper container on that same internal network drives HTTP over
`docker exec`; the candidate binds a synthetic config directory read-only, uses a
64 MiB tmpfs database and an inert loopback NNTP provider. It never pulls an
image (`--pull=never`), passes upstream secrets, mounts a real database, or calls
media playback. It checks real bearer auth, UTC descriptors, page 1/2/bounds,
profile consistency, inline partial Home, atomic metadata/content changes,
disabling/removing rows, invalid/missing/oversized manifest failure and recovery.
The exact image's revision label is checked before startup. Container/volumes and
network are removed in `finally`; an abrupt runner kill may require cleanup of
resources named `velvet-seasonal-smoke-*` on that disposable daemon.

Local source-binary mode runs the same HTTP assertions without Docker:

```sh
python3 scripts/seasonal_image_smoke.py --binary backend/target/debug/velvet-backend
```

This is **not** an image test. Local source-binary HTTP execution passed, but the
local Docker daemon is unavailable (`Cannot connect to the Docker daemon`).
The image was subsequently built and exercised on a disposable hosted runner:
[artifact run 42962095985](https://github.com/gopro8752/velvet-media-seasonal/actions/runs/42962095985)
at `d9c3f3053d387911a8feee0ae9ea20215332c18c` passed real isolated image HTTP
smoke and native Android build/tests (208 tests, zero failures/errors/skips).
The source run at that head also passed all four required checks. These are
**historical exact-head results**, not approval for a newer commit: each change
requires fresh CI, rebuilt exact-SHA artifacts, and independent tester review.
Use the current PR checks/artifact provenance, not these historical hashes, to
select a candidate. Portable Swift is not an Xcode/iOS application build, and
neither HTTP harness proves device playback. The Apple Combine subscription
regression job separately executes the actual Home subscription and production
seasonal scheduler on a standard public-repository macOS runner; its settings
storage/UI callbacks are fixtures, not a full app or device test.

## Release gate, pilot and rollback (not executed by this change)

1. Obtain independent **PASS for the exact candidate SHA**, including image
   build/smoke/artifact identity and native APK checks, plus the existing legacy
   compatibility and seasonal contract suites. Review failures rather than
   substituting source-only or fabricated results for an image test.
2. Identify Dad's actual installed package, version name/code and preferably APK
   or source-build identity, then establish that actual client's contract against
   the replacement server. **Dad's installed version is currently unknown.** The
   seed `f30050f65aab1e5582d4cf11e14ad7cb1d4c6bce` declares Android 1.14.0 / code
   40, but that is not evidence of what is installed. See
   [legacy-compatibility.md](legacy-compatibility.md) for the real source-pinned
   HTTP/same-database rollback checks and their limits. Dad is not an install
   target for this experiment.
3. Only after those gates and explicit operator approval, replace the
   **confirmed current shared server** while preserving its existing configuration,
   auth and data. Record its actual old image identity and a tested rollback
   path first; do not assume the current registry tag identifies it.
4. Then one Gavin-selected local Shield pilot using the isolated lab app;
   validate real launch, profile selection, seasonal refresh, ordinary playback,
   saved categories, and existing daily app data. Only after pilot approval
   consider the remaining two devices. No automatic or Dad-device rollout.

Before any approved server change, retain the old image/archive and exact image
ID/digest, a private copy of existing runtime config, and a consistent SQLite
backup (SQLite backup API or stop writers; include WAL considerations, not a
casual live-file copy). Preserve existing volumes/permissions and token. Keep
secrets/backups outside source control and workflow artifacts.

Rollback restores the prior server image/config and, only if needed, the
consistent DB backup under an explicit operator plan. The additive seasonal
manifest can remain: an old server ignores it. No seasonal schema migration or
client conversion is required; real seed/candidate same-DB rollback coverage is
documented separately, not a substitute for a production backup/restore drill.
Stop using the lab app without removing or clearing stable/beta data; separate
application IDs preserve their local settings/profiles/cache. A rollback does
not require Dad to install anything.
