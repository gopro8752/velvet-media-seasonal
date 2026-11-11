# Velvet for Android TV

Velvet is a remote-first media client built with Kotlin, Compose for TV, Media3, and GeckoView. It supports profiles, Continue Watching, lists, episode navigation, source and track selection, category queues, shuffle, playback handoff, and optional backend-powered live TV, sports, and AI categories.

This repository contains the client software, not a media library, provider subscription, API credential, or hosted backend. Configure services you are entitled to use. See the [repository setup](../../README.md) for the other components.

## First launch

Open **Settings** from the dock. Configure:

1. **Backend → Backend URL**: the base URL of your Velvet backend, for example `https://backend.example.com`. This configures metadata, streaming, and sync. TMDB requests go through your backend, so its TMDB key stays on the server.
2. **Backend → Token (optional)**: the backend's bearer token, if your server requires one. Use HTTPS for a backend reached over the internet.

Without a backend, **Metadata → Standalone TMDB API key** allows browsing with your own key. Connected clients never fall back to a local TMDB key.

Live TV, sports, AI categories, cross-device sync, and reporting depend on the matching backend capabilities and configuration. Configure upstream credentials on your own server. Neither the client nor this source distribution grants access to any third-party service.

The playback reporter sends title/profile/device information and measured playing/buffering time to the **configured Velvet backend**. It uses an app-install identifier and the backend token, sends asynchronously, and does not report media URLs or upstream credentials. Clearing the backend configuration disables reporting. See the server documentation before sharing a deployment with other people.

## Build

Use JDK 17 and the Android command-line tools or Android Studio. Install Android SDK Platform 37.2 and the build tools requested by Gradle. Set `ANDROID_HOME` to your SDK directory, or create an untracked `local.properties` containing `sdk.dir=/path/to/android-sdk`.

```sh
export JAVA_HOME=/path/to/jdk-17
export ANDROID_HOME=/path/to/android-sdk
./gradlew :app:testStableDebugUnitTest :app:assembleStableRelease :app:lintStableRelease
node --test tests/sports-startup.test.cjs
```

Gradle downloads its pinned toolchain dependencies from the configured upstream repositories. The client supports Android 8.0/API 26 or newer. Release builds produce **unsigned** APKs for `arm64-v8a`, `armeabi-v7a`, and `x86_64` under `app/build/outputs/apk/stable/release/`. Choose the ABI supported by your device.

Sign and align release APKs using your own Android signing key and the Android SDK's `zipalign`/`apksigner` tools. Keep signing material and passwords outside the repository. Debug builds (`./gradlew :app:assembleStableDebug`) use your local Android debug key. No release signing key or publisher credentials are included.

To use [Velour](../../launcher/README.md), sign **both applications with the same certificate**. Velvet protects its launcher data provider and launch entry point with a signature permission. Independently signed copies cannot read that data. An existing installation signed with a different key cannot be updated by your build; use your own test device or app identifier while developing.

## Stream recovery

The mainline app reports playback position, measured duration, buffered position,
a temporary session ID, and a seek generation to the configured backend's
`/stream/:id/playback` endpoint while a native stream is open; leaving playback
sends a stop. This lets the server verify upcoming data, repair missing articles
before playback reaches them when possible, and attribute results to the watch
in the admin dashboard. Other sources and older servers keep normal fallback
behavior. Position reports contain no title, profile, or provider secret.

**Playback options → Stream recovery** shows server status and verified seconds ahead
only when the backend has a trustworthy media-time index, plus PAR2 phase and charged repair
download budget when available (including conservative charges for partial failures). A missing index displays
an explicit unavailable value. Repairs do not add a toast or overlay to playback.
Fresh recovery work can defer speculative source changes; actual player errors and
the ordinary stall deadline still fail over. A different release is not guaranteed
to preserve the same scene, audio, or encoding boundaries.

### Isolated development install

Build `./gradlew :app:testBetaDebugUnitTest :app:assembleBetaDebug` for **Velvet Beta**
(`tv.velvet.beta`, version suffix `-beta`). It has the same recovery functionality
as mainline, with separate preferences, profiles, launcher provider, and playback
receiver identity. Configure its backend at runtime. Stable Velour continues
opening stable Velvet. To build for one device architecture and save disk space,
add `-PvelvetAbis=arm64-v8a` (or another supported ABI) to a Gradle command.

## Server-managed seasonal discovery

Home fetches `/catalog/v1/seasonal` on foreground and every 60 seconds while Home
remains resumed, plus retry, settings and profile changes while visible. Pausing or
leaving Home stops the timer, cancels seasonal manifest/item requests and invalidates
late responses; background changes defer seasonal requests until the next resume.
Each refresh removes previous seasonal rows before fetching the fresh manifest;
generation guards prevent older responses from restoring them. No device-local date
filtering decides which seasonal rows are active.
The server supplies the active rows, titles, styles, artwork metadata and ordering;
stored Home row order cannot override them. Every `seasonal_` item request uses
`/catalog/v1/seasonal/rows/:id?page=N` directly, with no local rotation or seasonal
cache. The configured backend bearer token is used only on its validated origin,
redirects are refused, and standalone TMDB credentials are never sent. An old,
unconfigured, invalid or unavailable backend hides seasonal sections; ordinary
TMDB discovery and personal categories remain available as before.

Settings lists active seasonal rows as **Managed by server**, without local toggle
or reorder controls. Seasonal rows do not become saved AI categories, Play All /
Shuffle queues, or category deep-link targets. Cards still open normal movie/show
details and playback. The signed launcher receives ordinary discovery metadata
only: its featured cache uses a new namespace so old locally curated seasonal
picks cannot reappear. Its Continue Watching, My List, saved categories and media
launch intents are unchanged; the launcher performs no seasonal network requests.

### Isolated seasonal experiment

```sh
./gradlew :app:testSeasonalDebugUnitTest :app:assembleSeasonalDebug -PvelvetAbis=arm64-v8a
node --test tests/*.test.cjs
```

This builds **Velvet Seasonal Lab**, `tv.velvet.seasonal`, version suffix
`-seasonal`, separately from both stable and beta. Preferences, profiles, launcher
provider and signature permission use that separate application identity. Configure
its backend at runtime. Stable Velour still opens stable Velvet; no device install
or deployment is part of these commands.

`HomeSeasonalLifecycleTest` runs the real HomeViewModel with virtual coroutine time
in the native Gradle unit-test suite. It covers the 60-second foreground bound,
server removal/reordering/metadata and item changes, background suppression, request
cancellation, duplicate resume/pause events, and stale-generation responses. Android
storage and sync boundaries are mocked; this is not a Compose or device test.

### SDK-free seasonal JVM slice

`tests/seasonal-jvm.py` compiles the actual request builder, seasonal catalog parser,
media models and row-order policy with JUnit. It extracts the existing settings
value type from its DataStore source, substituting only its empty-string default
constant; it does **not** test Android DataStore, ViewModel lifecycle, Compose,
resources, APK packaging or device behavior. Node migration tests are source
integration guards, not native UI tests. Run the Gradle commands above before
installing on a device.

Supply a JDK via `JAVA_HOME`, set `TMPDIR` to scratch storage outside the repo, and
put the following Maven jars in a tool directory: Kotlin compiler-embeddable,
stdlib and script-runtime 2.1.20; kotlin-reflect 1.6.10; trove4j 1.0.20200330;
JetBrains annotations 13.0; kotlinx-coroutines-core-jvm 1.9.0;
kotlinx-serialization core/json-jvm 1.7.3; OkHttp 4.12.0; okio-jvm 3.6.0;
JUnit 4.13.2; hamcrest-core 1.3. This portable compiler is separate from the
project's pinned Android Kotlin plugin; it is a fallback check, not a native build.

```sh
python3 tests/seasonal-jvm.py --tools-dir /path/to/jvm-test-jars
```

## Source layout

| Directory | Purpose |
| --- | --- |
| `app/src/main/java/tv/velvet/data` | Catalog, backend, sync, profiles, telemetry, and launcher integration |
| `app/src/main/java/tv/velvet/ui` | TV navigation, browsing, settings, native and browser players |
| `app/src/main/assets/sports-startup` | Browser playback-start coordination |
| `app/src/test` | Unit tests and reduced metadata fixtures |
| `tests` | Browser startup logic tests |

The source is available under the repository's [custom license](../../LICENSE). It permits personal, noncommercial use subject to its terms; it is not an MIT/Apache license for the application. Third-party code and assets retain their own terms, described in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
