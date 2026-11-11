#!/usr/bin/env python3
"""Exercise Home's actual subscription with Apple Combine and production task scope.

Requires macOS Swift; this is not an Xcode app build. Only the settings storage
boundary and UI display callback are fixtures. The subscription is extracted
verbatim so a detached reimplementation cannot hide a wiring regression.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
home = (root / 'Velvet/UI/Home/HomeViewModel.swift').read_text()
subscription = home.split('        // Invalidate synchronously', 1)[1].split('        // Idle featured', 1)[0]
subscription = subscription[subscription.index('\n') + 1:]
catalog = (root / 'Velvet/Data/SeasonalCatalog.swift').read_text()
scheduler = catalog[catalog.index('@MainActor\nfinal class SeasonalForegroundRefresh'):catalog.index('/// Seasonal API redirects')]
source = '''import Foundation
import Combine

struct StreamSourceConfig { var baseUrl: String }
@MainActor final class SettingsStore: ObservableObject {
    @Published var streamSources = [StreamSourceConfig(baseUrl: "https://fixture.test")]
    @Published var syncUrl = "https://fixture.test"
    @Published var syncToken = "fixture-only"
    @Published var activeProfileId: String? = "a"
    @Published var lastSyncStatus = ""
    @Published var hardwareDecoding = true
}
''' + scheduler + '''
@MainActor final class HomeSubscriptionFixture {
    let settings: SettingsStore
    var seasonalRefresh: SeasonalForegroundRefresh!
    var cancellables = Set<AnyCancellable>()
    var rows = ["seasonal_arbitrary"]
    var hero: String? = "seasonal_arbitrary"
    var requests = 0
    init(settings: SettingsStore) {
        self.settings = settings
        seasonalRefresh = SeasonalForegroundRefresh(invalidate: { [weak self] in
            self?.rows = []; self?.hero = nil
        }, action: { [weak self] _ in
            self?.requests += 1
            self?.rows = ["seasonal_arbitrary"]; self?.hero = "seasonal_arbitrary"
        })
''' + subscription + '''
    }
    func scheduleSeasonalRefresh() { seasonalRefresh.refresh() }
}

@main struct VerifySettings {
    @MainActor static func settle() {
        RunLoop.main.run(until: Date().addingTimeInterval(0.2))
    }
    @MainActor static func main() {
        let settings = SettingsStore()
        let home = HomeSubscriptionFixture(settings: settings)
        home.seasonalRefresh.setForeground(visible: true, active: true)
        settle()
        precondition(home.requests == 1 && !home.rows.isEmpty)
        var generation = home.seasonalRefresh.generation
        var item = Task { _ = try? await Task.sleep(nanoseconds: 60_000_000_000) }
        home.seasonalRefresh.trackItems(item)
        for status in ["Syncing", "Synced", "Sync failed"] {
            settings.lastSyncStatus = status
            precondition(home.rows == ["seasonal_arbitrary"] && home.hero != nil,
                         "Status-only sync update cleared seasonal rail/hero")
            precondition(home.seasonalRefresh.generation == generation && !item.isCancelled,
                         "Status-only sync update cancelled seasonal tasks")
            settle()
            precondition(home.requests == 1, "Status-only update redundantly refetched")
        }
        settings.hardwareDecoding = false
        settle()
        precondition(home.requests == 1 && !item.isCancelled, "Unrelated preference triggered discovery")
        let changes: [() -> Void] = [
            { settings.syncToken = "changed-fixture" },
            { settings.activeProfileId = "b" },
            { settings.syncUrl = "https://other.test" },
            { settings.streamSources = [StreamSourceConfig(baseUrl: "https://other.test")] }
        ]
        for (index, change) in changes.enumerated() {
            change()
            precondition(home.rows.isEmpty && home.hero == nil && item.isCancelled,
                         "Real credential/profile/connection change must clear and cancel immediately")
            precondition(home.seasonalRefresh.generation != generation)
            settle()
            precondition(home.requests == index + 2, "Real change must refresh once after debounce")
            generation = home.seasonalRefresh.generation
            item = Task { _ = try? await Task.sleep(nanoseconds: 60_000_000_000) }
            home.seasonalRefresh.trackItems(item)
        }
        settings.syncToken = settings.syncToken
        settings.activeProfileId = settings.activeProfileId
        settings.syncUrl = settings.syncUrl
        settings.streamSources = settings.streamSources
        settle()
        precondition(home.requests == 5 && home.seasonalRefresh.generation == generation && !item.isCancelled,
                     "Equal relevant settings must be deduplicated")
        home.seasonalRefresh.setForeground(visible: false, active: false)
        settings.syncToken = "hidden-fixture"
        settle()
        precondition(home.requests == 5 && item.isCancelled, "Hidden Home must not fetch")
        print("PASS: real Apple Combine Home subscription preserves rows/hero/tasks on sync status, deduplicates settings, synchronously invalidates credentials/profile/connection, and gates hidden refresh")
    }
}
'''
compiler = os.environ.get('SWIFTC') or shutil.which('swiftc')
if not compiler:
    raise SystemExit('swiftc unavailable; run on macOS with Apple Combine')
with tempfile.TemporaryDirectory(prefix='seasonal-settings-', dir=os.environ.get('TMPDIR')) as tmp:
    swift = Path(tmp) / 'SettingsRegression.swift'
    swift.write_text(source)
    binary = Path(tmp) / 'settings-regression'
    subprocess.run([compiler, '-swift-version', '5', '-parse-as-library', str(swift), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
