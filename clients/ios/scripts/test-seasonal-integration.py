"""Structural integration guards; these are not substitutes for Xcode UI tests."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "Velvet"

class SeasonalIntegration(unittest.TestCase):
    def test_checked_in_xcode_project_builds_seasonal_source(self):
        project = (ROOT.parent / "Velvet.xcodeproj/project.pbxproj").read_text()
        self.assertIn("SeasonalCatalog.swift in Sources", project)
        self.assertIn("path = SeasonalCatalog.swift", project)

    def test_home_refresh_fetches_manifest_and_discards_previous_seasonal_content(self):
        home = (ROOT / "UI/Home/HomeViewModel.swift").read_text()
        self.assertIn("await tmdb.seasonalRows()", home)
        self.assertIn("await refreshSeasonalRows()", home)
        self.assertIn("SeasonalCatalog.resolve(", home)
        self.assertIn("seasonalGeneration == generation", home)
        self.assertIn('rawRows = rawRows.filter { !$0.key.hasPrefix("seasonal_") }', home)
        self.assertNotIn("Calendar.current.startOfDay", home)
        self.assertNotIn("keepExistingOnFailure: true)", home)

    def test_foreground_profile_connection_and_settings_changes_refresh(self):
        view = (ROOT / "UI/Home/HomeView.swift").read_text()
        self.assertIn("scenePhase == .active", view)
        profile = view.split(".onChange(of: activeProfileId)")[1].split("}\n")[0]
        self.assertIn("onHomeAppear()", profile)
        home = (ROOT / "UI/Home/HomeViewModel.swift").read_text()
        self.assertNotIn("settings.objectWillChange", home)
        for publisher in ["settings.$streamSources", "settings.$syncUrl", "settings.$syncToken", "settings.$activeProfileId"]:
            self.assertIn(publisher, home)
        changes = home.split("// Invalidate synchronously")[1].split("// Idle featured")[0]
        self.assertLess(changes.index(".removeDuplicates()"), changes.index(".handleEvents("))
        self.assertLess(changes.index(".handleEvents("), changes.index(".debounce("))
        self.assertIn("refreshSeasonalRows()", home)

    def test_home_visibility_and_scene_gate_scheduler_and_sync(self):
        view = (ROOT / "UI/Home/HomeView.swift").read_text()
        self.assertIn("@State private var homeVisible = false", view)
        self.assertIn("homeVisible = true", view)
        self.assertIn("homeVisible = false", view)
        self.assertIn("visible: homeVisible, active: scenePhase == .active", view)
        self.assertIn("container.sync.setHomeVisible(homeVisible && scenePhase == .active)", view)
        # VM creation must also update lifecycle, not rely on onAppear ordering.
        task = view.split(".task {")[1].split("// Spec")[0]
        self.assertIn("updateHomeForeground()", task)

    def test_scheduler_owns_manifest_and_row_tasks_with_synchronous_invalidation(self):
        home = (ROOT / "UI/Home/HomeViewModel.swift").read_text()
        self.assertIn("SeasonalForegroundRefresh(", home)
        self.assertIn("seasonalRefresh.setForeground(visible: visible, active: active)", home)
        self.assertIn("seasonalRefresh.trackItems(task)", home)
        self.assertIn("seasonalRefresh.isCurrent(generation)", home)
        changes = home.split("// Invalidate synchronously")[1].split("// Idle featured")[0]
        self.assertIn("seasonalRefresh.invalidate()", changes)
        self.assertIn(".debounce(", changes)
        self.assertNotIn("scheduleSeasonalRefresh()\n\n", changes)

    def test_no_local_calendar_recipes_and_settings_uses_same_server_order(self):
        sources = [(ROOT / p).read_text() for p in ["Data/TmdbRows.swift", "Data/TmdbRepository.swift", "Data/TmdbClient.swift", "UI/Settings/SettingsView.swift"]]
        for source in sources:
            self.assertNotIn("SeasonalRow", source)
            self.assertNotIn("isSeasonalRowActive", source)
            self.assertNotIn('("with_keywords", String(seasonal.keywordId))', source)
        self.assertIn("await container.tmdb.seasonalRows()", sources[-1])
        self.assertIn("SeasonalCatalog.resolve(", sources[-1])

if __name__ == "__main__":
    unittest.main()
