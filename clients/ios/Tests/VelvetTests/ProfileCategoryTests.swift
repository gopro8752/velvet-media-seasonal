import XCTest
@testable import Velvet

@MainActor
final class ProfileCategoryTests: XCTestCase {
    func testRetiredCollectionCannotBeLaunchedByAnyProfile() {
        let legacy = HomeCategory(id: PersonalHalloweenCategory.id, title: "Monstober & Nick Halloween", items: [])
        for profile in ["default", "craig", ""] {
            XCTAssertFalse(PersonalHalloweenCategory.canPlay(legacy, profile: profile))
        }
        let generated = HomeCategory(id: "ai-halloween", title: "Monstober & Nick Halloween", items: [])
        XCTAssertTrue(PersonalHalloweenCategory.canPlay(generated, profile: "default"))
        XCTAssertTrue(PersonalHalloweenCategory.canPlay(generated, profile: "craig"))
        XCTAssertFalse(PersonalHalloweenCategory.canPlay(generated, profile: ""))
    }

    func testMigrationDeletesOnlyTheRetiredIdentityAndPreservesOtherProfiles() throws {
        let suite = "tv.velvet.tests.retired-category." + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let legacy = HomeCategory(id: PersonalHalloweenCategory.id, title: "Monstober & Nick Halloween", items: [])
        let personal = HomeCategory(id: "ai-halloween", title: "Monstober & Nick Halloween", items: [])
        let other = HomeCategory(id: "ai-movies", title: "My movies", items: [])
        let first = "home_generated_categories_v1_default"
        let second = "home_generated_categories_v1_craig"
        defaults.set(try JSONEncoder().encode(legacy), forKey: "home_halloween_category_v1")
        defaults.set(try JSONEncoder().encode([legacy, personal]), forKey: first)
        let originalOther = try JSONEncoder().encode([other])
        defaults.set(originalOther, forKey: second)
        defaults.set("keep", forKey: "unrelated")
        HomeCategoryCache.removeRetiredLegacyCategory(defaults: defaults)
        XCTAssertNil(defaults.object(forKey: "home_halloween_category_v1"))
        let cleaned = try JSONDecoder().decode([HomeCategory].self, from: XCTUnwrap(defaults.data(forKey: first)))
        XCTAssertEqual(cleaned.map(\.id), [personal.id])
        XCTAssertEqual(defaults.data(forKey: second), originalOther)
        XCTAssertEqual(defaults.string(forKey: "unrelated"), "keep")
        HomeCategoryCache.removeRetiredLegacyCategory(defaults: defaults)
        XCTAssertEqual(defaults.data(forKey: second), originalOther)
    }

    func testSeasonalManifestUsesServerMetadataRatherThanDeviceCalendar() throws {
        let data = Data(#"{"schemaVersion":1,"revision":"r1","effectiveDate":"2040-08-01","timezone":"UTC","rows":[{"id":"seasonal_any-new-id","title":"Curated by server","style":"landscape","artworkUrl":null}]}"#.utf8)
        let rows = try JSONDecoder().decode(SeasonalCatalog.Manifest.self, from: data).specs()
        XCTAssertEqual(rows.map(\.id), ["seasonal_any-new-id"])
        XCTAssertEqual(rows.first?.title, "Curated by server")
        XCTAssertEqual(rows.first?.style, .landscape)
    }
}
