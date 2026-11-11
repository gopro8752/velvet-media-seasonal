import Foundation

/// Local discovery row renames preserve user ordering and enabled preferences.
/// Obsolete seasonal identities are ignored when resolving known row specs.
enum RowIdMigration {
    static let renamed: [String: String] = ["trending_movies": "popular_now"]

    static func apply(_ storedOrder: [String]) -> [String] {
        guard storedOrder.contains(where: { renamed[$0] != nil }) else { return storedOrder }
        var seen = Set<String>()
        var out: [String] = []
        for id in storedOrder {
            let mapped = renamed[id] ?? id
            if seen.insert(mapped).inserted { out.append(mapped) }
        }
        return out
    }
}
