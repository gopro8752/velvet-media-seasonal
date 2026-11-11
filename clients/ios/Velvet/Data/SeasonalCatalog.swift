import Foundation

/// Backend-owned collections. No local calendar, recipes, or persisted content.
enum SeasonalCatalog {
    /// Server rows keep their relative order; saved ordering applies only to local rows.
    static func resolve(local: [RowSpec], seasonal: [RowSpec], order: [String], disabled: [String]) -> [RowSpec] {
        let disabled = Set(disabled)
        let byID = Dictionary(local.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let rest = order.isEmpty ? local : order.compactMap { byID[$0] }
        var seen = Set<String>()
        return (seasonal + rest).filter { !disabled.contains($0.id) && seen.insert($0.id).inserted }
    }
    static func isRowID(_ id: String) -> Bool {
        id.hasPrefix("seasonal_") && id.count > 9 && id.utf8.count <= 80
            && id.utf8.allSatisfy { (97...122).contains($0) || (48...57).contains($0) || $0 == 95 || $0 == 45 }
    }

    /// Validate syntax only; never compare against a device date or timezone.
    private static func validDate(_ date: String) -> Bool {
        date.range(of: #"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"#, options: .regularExpression) != nil
    }

    static func request(connection: TmdbConnection, rowID: String? = nil, page: Int = 1) throws -> URLRequest {
        guard (1...20).contains(page), rowID.map(isRowID) ?? true,
              var url = URLComponents(string: connection.backendURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.host?.isEmpty == false, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil, let base = url.url else { throw URLError(.badURL) }
        while url.path.hasSuffix("/") { url.path.removeLast() }
        url.path += "/catalog/v1/seasonal"
        if let rowID {
            url.path += "/rows/\(rowID)"
            url.queryItems = [URLQueryItem(name: "page", value: String(page))]
        }
        guard let target = url.url else { throw URLError(.badURL) }
        var request = URLRequest(url: target, cachePolicy: .reloadIgnoringLocalCacheData)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let token = connection.token.trimmingCharacters(in: .whitespacesAndNewlines)
        if !token.isEmpty, let sync = URL(string: connection.syncURL.trimmingCharacters(in: .whitespacesAndNewlines)),
           TmdbConnection.sameOrigin(base, sync) {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    struct Manifest: Decodable {
        let schemaVersion: Int
        let revision: String
        let effectiveDate: String
        let timezone: String
        let rows: [Descriptor]

        func specs() throws -> [RowSpec] {
            guard schemaVersion == 1, timezone == "UTC", !revision.isEmpty, validDate(effectiveDate),
                  Set(rows.map(\.id)).count == rows.count,
                  rows.allSatisfy({ isRowID($0.id) && ($0.style == .poster || $0.style == .landscape) })
            else { throw URLError(.cannotParseResponse) }
            return rows.map { RowSpec(id: $0.id, title: $0.title, style: $0.style, artworkUrl: $0.artworkUrl) }
        }
    }

    struct Descriptor: Decodable {
        let id: String
        let title: String
        let style: RowStyle
        let artworkUrl: URL?
    }

    struct RowPage: Decodable {
        let schemaVersion: Int
        let revision: String
        let effectiveDate: String
        let timezone: String
        let id: String
        let title: String
        let style: RowStyle
        let artworkUrl: URL?
        let items: [Item]
        let page: Int
        let totalPages: Int
        let stale: Bool

        func media(rowID: String, requestedPage: Int) throws -> [MediaItem] {
            guard schemaVersion == 1, timezone == "UTC", id == rowID, isRowID(id),
                  !revision.isEmpty, validDate(effectiveDate), page == requestedPage, (1...20).contains(page),
                  (0...20).contains(totalPages), items.count <= 20,
                  style == .poster || style == .landscape else { throw URLError(.cannotParseResponse) }
            return items.map(\.media)
        }
    }

    struct Item: Decodable {
        let mediaId: Int
        let type: MediaType
        let title: String
        let year: String?
        let rating: Double?
        let posterUrl: URL?
        let backdropUrl: URL?
        let logoUrl: URL?
        let overview: String?
        let genres: [String]
        let runtimeMinutes: Int?
        let imdbId: String?
        var media: MediaItem {
            MediaItem(id: mediaId, type: type, title: title, year: year,
                      posterUrl: posterUrl, backdropUrl: backdropUrl, logoUrl: logoUrl,
                      rating: rating, overview: overview, genres: genres,
                      runtimeMinutes: runtimeMinutes, imdbId: imdbId)
        }
    }
}

/// Foreground-only monotonic revalidation. Foundation-only so the real task and
/// cancellation policy can be exercised without a SwiftUI/Combine runtime.
@MainActor
final class SeasonalForegroundRefresh {
    static let intervalNanos: UInt64 = 60_000_000_000
    private(set) var generation = UUID()
    private(set) var isForeground = false
    private var timer: Task<Void, Never>?
    private var request: Task<Void, Never>?
    private var itemTasks: [Task<Void, Never>] = []
    private let sleep: @MainActor (UInt64) async throws -> Void
    private let onInvalidate: @MainActor () -> Void
    private let action: @MainActor (UUID) async -> Void

    init(sleep: @escaping @MainActor (UInt64) async throws -> Void = { try await Task.sleep(nanoseconds: $0) },
         invalidate: @escaping @MainActor () -> Void,
         action: @escaping @MainActor (UUID) async -> Void) {
        self.sleep = sleep
        self.onInvalidate = invalidate
        self.action = action
    }

    deinit { timer?.cancel(); request?.cancel(); itemTasks.forEach { $0.cancel() } }

    func setForeground(visible: Bool, active: Bool) {
        let foreground = visible && active
        guard foreground != isForeground else { return }
        isForeground = foreground
        timer?.cancel()
        timer = nil
        guard foreground else { invalidate(); return }
        refresh()
        let sleep = self.sleep
        timer = Task { [weak self] in
            while !Task.isCancelled {
                do { try await sleep(Self.intervalNanos) } catch { return }
                guard !Task.isCancelled else { return }
                self?.refresh()
            }
        }
    }

    func invalidate() {
        generation = UUID()
        request?.cancel()
        request = nil
        itemTasks.forEach { $0.cancel() }
        itemTasks = []
        onInvalidate()
    }

    func trackItems(_ task: Task<Void, Never>) {
        guard isForeground else { task.cancel(); return }
        itemTasks.append(task)
    }

    @discardableResult
    func refresh() -> Task<Void, Never>? {
        invalidate()
        guard isForeground else { return nil }
        let generation = self.generation
        let action = self.action
        let task = Task {
            guard !Task.isCancelled else { return }
            await action(generation)
        }
        request = task
        return task
    }

    func isCurrent(_ generation: UUID) -> Bool {
        isForeground && self.generation == generation
    }
}

/// Seasonal API redirects are denied even within the same origin.
final class SeasonalRedirectPolicy: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
