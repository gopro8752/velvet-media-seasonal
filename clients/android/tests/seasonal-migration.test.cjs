const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../app/src/main/java/tv/velvet');
const read = p => fs.readFileSync(path.join(root, p), 'utf8');

test('seasonal experimental install is separate from stable and beta', () => {
  const gradle = fs.readFileSync(path.join(__dirname, '../app/build.gradle.kts'), 'utf8');
  assert.ok(gradle.includes('applicationIdSuffix = ".seasonal"'), 'missing isolated seasonal flavor');
  const manifest = fs.readFileSync(path.join(__dirname, '../app/src/main/AndroidManifest.xml'), 'utf8');
  assert.ok(manifest.includes('${applicationId}.launcher'));
  assert.ok(manifest.includes('${applicationId}.permission.LAUNCHER_ACCESS'));
});

test('repository routes any server seasonal id before local row rotation/cache', () => {
  const code = read('data/tmdb/TmdbRepositoryImpl.kt');
  assert.match(code, /override suspend fun seasonalRows\(\)/);
  assert.match(code, /if \(rowId\.startsWith\("seasonal_"\)\) return seasonalCatalog\.items\(rowId, page\)/);
  assert.ok(code.indexOf('return seasonalCatalog.items(rowId, page)') < code.indexOf('val fetchPage = rotatedPage'));
  assert.doesNotMatch(code, /SeasonalWindow|seasonalWindows|KEYWORD_(HALLOWEEN|THANKSGIVING|CHRISTMAS|NEW_YEAR|VALENTINES)|holiday_halloween|holiday_christmas|CHRISTMAS_GENRES|private suspend fun seasonalRow/);
});

test('home refreshes server discovery on settings/profile, foreground and retry without a device calendar', () => {
  const code = read('ui/home/HomeViewModel.kt');
  assert.match(code, /combine\(settingsRepository\.settings, activeProfileId\)/);
  assert.match(code, /tmdb\.seasonalRows\(\)/);
  assert.match(code, /fun onHomeResumed\(\)[\s\S]*?refreshSeasonalRows\(\)/);
  assert.match(code, /fun retryFailedRows\(\)[\s\S]*?refreshSeasonalRows\(\)/);
  assert.match(code, /seasonalGeneration/);
  assert.doesNotMatch(code, /LocalDate|isSeasonalRowActive|seasonalRowIds|window opened|date rollover/);
});

test('launcher never persists seasonal row titles and settings labels them read only', () => {
  const home = read('ui/home/HomeViewModel.kt');
  assert.match(home, /specs\.filterNot \{ it\.id\.startsWith\("seasonal_"\) \}/);
  const cache = read('data/launcher/LauncherFeaturedCache.kt');
  assert.ok(cache.includes('ordinary_profile_'), 'legacy featured cache could retain local seasonal picks');
  const settings = read('ui/settings/SettingsScreen.kt');
  assert.match(settings, /entry\.serverManaged/);
  assert.match(settings, /Managed by server/);
});

test('settings exposes server managed seasonal entries but cannot persist/reorder them', () => {
  const code = read('ui/settings/SettingsViewModel.kt');
  assert.match(code, /tmdb\.seasonalRows\(\)/);
  assert.match(code, /rowId\.startsWith\("seasonal_"\).*return/);
  assert.doesNotMatch(code, /isSeasonalRowActive|seasonalRowIds|calendar window|out-of-season/);
});
