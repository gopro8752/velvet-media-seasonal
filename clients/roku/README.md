# Velvet for Roku — ALPHA

**This Roku port is alpha software for private developer-mode sideloading.** It is not a Roku Channel Store release, and the source package does not include a signed channel, an installation password, a server account, or any media subscription. Test it on your target hardware before relying on it.

The native BrightScript / SceneGraph client includes profiles, discovery, search, Continue Watching, saved categories, episode queues, player controls, live TV and guide views, sports source selection, and playback reporting. It connects to a backend that you operate or are explicitly permitted to use.

## Build and test

Requirements: Node.js 20 or newer, npm, and `unzip` on your path.

```sh
npm ci
npm test
npm run build
```

The verified sideload package is written to `dist/velvet-roku.zip`; a SHA-256 checksum is generated beside it. Generated packages and dependencies are local build outputs and should not be committed.

## Private sideload setup

1. On your Roku remote, press Home three times, Up twice, then Right, Left, Right, Left, Right to open developer settings. Review Roku's developer agreement, enable the installer, choose a private password, and restart when prompted.
2. Open the Roku's developer installer address in a browser on the same trusted network. Sign in as `rokudev` with the password you chose and upload your locally built ZIP. Sideloading replaces the Roku's single developer app.
3. Open Velvet and enter your own backend URL and its optional token. The app ships with both unset. Use a reachable HTTPS backend for remote households.
4. Choose or create a profile. Voice input depends on the Roku model, remote, OS, and keyboard-field support.

You can alternatively run the local deployment helper after enabling developer mode:

```sh
ROKU_HOST=roku.example.test npm run deploy
```

Replace that documentation hostname with your Roku's actual hostname or IP. The helper verifies developer mode, builds and tests, then prompts for the password without displaying it. For automation, use a private file via `ROKU_PASSWORD_FILE` with mode `600`. Do not place credentials in source or commit configuration containing them. The helper does not enable developer mode for you.

## Seasonal home catalog

Home uses the additive `/catalog/v1/home` endpoint without a device-date override; the legacy `/catalog/home` endpoint retains its existing date semantics for old clients. Only an unsupported-version HTTP 404 triggers one fallback to `/catalog/home`, also without a date query. That fallback discards every `holiday_` or `seasonal_` row structurally and keeps ordinary discovery plus profile-saved categories, including on a cold start. Its request shares the home concurrency slot and carries a legacy context flag, so timer ticks coalesce and another 404 cannot recurse. Each later refresh probes v1 again; transient failures (including 500) and authentication failures (401) never trigger legacy fallback.

The backend owns seasonal activation, titles, inline items, and the relative order of `seasonal_<id>` rows; Roku has no holiday calendar. Seasonal rows stay in server order after Continue Watching, My List, and profile-saved categories, ahead of general discovery, even when a stored layout tries to move them. Explicit per-profile hide preferences still apply. Home-row settings labels seasonal ordering as server-managed and does not offer or accept seasonal move commands; other rows retain their layout controls. Home revalidates on entry, including Back from details, and on the existing 30-second sync timer while Home is open. An in-flight request is reused until it finishes rather than overlapping timer requests.

A failed home fetch removes cached seasonal rows immediately, but retains nonseasonal cached rows and the normal error message. Successful partial responses omit seasonal rows marked `stale` or named in `failedRows`. Seasonal namespace detection (`seasonal_` and legacy `holiday_`) is only a fail-closed cache rule, not an activation list. A later successful response replaces the catalog; no local rule can bring a removed season back.

Roku currently renders only the inline home items: it does not request additional catalog row pages or resolve row IDs as saved categories. Seasonal items use the existing title-details/playback routes. Saved/generated/archived personal categories remain profile-scoped and keep their existing Play all / Shuffle queue path. Launch-argument deep links are not currently implemented. Device-local dates remain in unrelated title/episode availability paths, not seasonal Home membership.

## Live TV guide

The guide uses a shared two-hour timeline with half-hour markers, channel logos, a live position indicator, and the selected program's synopsis. Listings come from your backend; an absent listing is shown as unavailable and never invented.

- Up / Down changes channels while keeping the selected time. Left / Right browses programs and the available future schedule. At the beginning of the timeline, Left returns to the filters from any channel.
- OK plays a current program. For an upcoming program, OK opens its description; it does not attempt to play a future broadcast. Play always watches the selected channel live.
- Options adds or removes a favorite. Favorites stay on this Roku, separately for each backend and profile. Choose Favorites above the grid to reduce the lineup.
- Search finds channel names, numbers, program titles, and episode titles. A program match opens at its scheduled time. Now or Rewind returns to the current broadcast; Fast Forward moves down a page of channels.
- Back returns from program details or opens Velvet's navigation dock. Schedules refresh every five minutes while the guide is open.

## Playback recovery and reporting

For on-demand playback, the client sends small position heartbeats to its configured backend so verified read-ahead and exact repair can run ahead of the viewer. Admin reports correlate repair results to the same watch and distinguish source attempts, replacements that actually start advancing, manual versus automatic changes, buffering after startup, and intentional seeks. The server performs recovery; the Roku does not download or decode a second movie. Established playback has a bounded 15-second no-progress window before normal source recovery.

## Current limitations

- Developer-mode sideloading is a per-device setup and supports one developer app at a time. This is not an automatic family-and-friends installation or update service.
- Codec support, audio tracks, subtitle behavior, and seeking depend on Roku hardware and firmware. Some sources need the backend's Roku-compatible audio or video path; not every source format is playable.
- Roku cannot embed arbitrary web players. Live sports requires a playable source and the compatible backend route; availability depends on your backend configuration and source.
- Microphone support uses Roku's system keyboard integration. This is not a custom always-listening assistant, and voice availability varies across devices.
- Visual effects are adapted to SceneGraph. They will not exactly match iOS or Android's rendering.
- Unit/build checks do not replace playback, remote navigation, sleep/wake, and long-session testing on real hardware. The current port remains alpha even when these checks pass.

The backend and your own enabled services supply metadata and playback. No provider account, shared API key, private endpoint, copyrighted title artwork, or media library is bundled.

## Assets and licenses

Inter and Outfit font files include their SIL Open Font License notices in `fonts/`; the build includes these notices in the sideload package. Bundled UI imagery consists of Velvet branding, abstract backgrounds, and controls. Runtime title and cast artwork is loaded from your configured backend. Development dependencies and their license notices are installed by npm; review their licenses before distributing modified tooling or a packaged channel.
