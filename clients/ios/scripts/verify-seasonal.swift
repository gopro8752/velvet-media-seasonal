import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private final class SeasonalFixture: URLProtocol {
    static var requests: [URLRequest] = []
    static var status = 200
    static var body = ""
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requests.append(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: Self.status,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(Self.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@main struct VerifySeasonal {
    @MainActor static func main() async {
        let settings = SettingsStore()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [SeasonalFixture.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let client = TmdbClient(settings: settings, session: session)
        SeasonalFixture.body = #"{"schemaVersion":1,"revision":"r1","effectiveDate":"2040-08-01","timezone":"UTC","id":"seasonal_editor-picks","title":"Server Picks","style":"landscape","artworkUrl":null,"items":[{"mediaId":123,"type":"show","title":"Example","year":"2000","rating":8.2,"posterUrl":"https://images.example.test/p.jpg","backdropUrl":null,"logoUrl":null,"overview":"Story","genres":["Drama"],"runtimeMinutes":42,"imdbId":"tt1234567"}],"page":2,"totalPages":3,"stale":true}"#
        let items = await client.rowItems(rowId: "seasonal_editor-picks", page: 2)
        let validPage = SeasonalFixture.body
        precondition(items.first?.id == 123, "Generic seasonal IDs must fetch normalized server media, not a local recipe")
        precondition(items.first?.type == .show && items.first?.imdbId == "tt1234567")
        precondition(items.first?.genres == ["Drama"] && items.first?.runtimeMinutes == 42)
        let request = SeasonalFixture.requests.last!
        precondition(request.url?.path == "/prefix/catalog/v1/seasonal/rows/seasonal_editor-picks")
        precondition(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "page" }?.value == "2")
        precondition(request.value(forHTTPHeaderField: "Authorization") == "Bearer fixture-token")
        precondition(!request.url!.absoluteString.contains("never-send-this"))
        print("PASS: generic seasonal row endpoint, normalized media, requested page, backend bearer")
        SeasonalFixture.body = #"{"schemaVersion":1,"revision":"r2","effectiveDate":"2040-08-01","timezone":"UTC","rows":[{"id":"seasonal_z","title":"First","style":"landscape","artworkUrl":"https://images.example.test/banner.jpg"},{"id":"seasonal_a","title":"Second","style":"poster","artworkUrl":null}]}"#
        let rows = await client.seasonalRows()
        precondition(rows.map(\.id) == ["seasonal_z", "seasonal_a"], "Manifest is authoritative and preserves server order")
        precondition(rows.first?.style == .landscape)
        precondition(rows.first?.artworkUrl?.absoluteString == "https://images.example.test/banner.jpg")
        precondition(SeasonalFixture.requests.last?.url?.path == "/prefix/catalog/v1/seasonal")
        SeasonalFixture.status = 404
        let unavailable = await client.seasonalRows()
        precondition(unavailable.isEmpty, "Old/unavailable endpoint must hide seasonal rows, not reuse cache")
        precondition(client.allRows.allSatisfy { !$0.id.hasPrefix("holiday_") }, "Local holiday catalog must be retired")
        print("PASS: manifest metadata and server ordering; unavailable endpoint hides rows; no local holidays")
        let local = [RowSpec(id: "comedy", title: "Comedy", style: .poster), RowSpec(id: "action", title: "Action", style: .poster)]
        let resolved = SeasonalCatalog.resolve(local: local, seasonal: rows,
            order: ["seasonal_a", "action", "seasonal_z", "action"], disabled: [])
        precondition(resolved.map(\.id) == ["seasonal_z", "seasonal_a", "action"], "Stored order cannot reorder server rows or duplicate IDs")
        let disabled = SeasonalCatalog.resolve(local: local, seasonal: rows, order: [], disabled: ["seasonal_z", "comedy"])
        precondition(disabled.map(\.id) == ["seasonal_a", "action"])
        precondition(SeasonalCatalog.resolve(local: local, seasonal: [], order: ["holiday_christmas", "action"], disabled: []).map(\.id) == ["action"])
        print("PASS: server order wins, local order and explicit disables preserved, obsolete IDs ignored")
        SeasonalFixture.status = 200
        let validManifest = #"{"schemaVersion":1,"revision":"r3","effectiveDate":"2040-08-01","timezone":"UTC","rows":[]}"#
        for invalid in [
            validManifest.replacingOccurrences(of: "2040-08-01", with: "not-a-date"),
            validManifest.replacingOccurrences(of: "\"schemaVersion\":1", with: "\"schemaVersion\":2"),
            validManifest.replacingOccurrences(of: "UTC", with: "America/New_York")
        ] {
            do {
                _ = try JSONDecoder().decode(SeasonalCatalog.Manifest.self, from: Data(invalid.utf8)).specs()
                preconditionFailure("Invalid manifest must be rejected: \(invalid)")
            } catch { }
        }
        print("PASS: reject incompatible version/timezone/date metadata")
        let connection = TmdbConnection(backendURL: settings.backendUrl, syncURL: settings.syncUrl, token: settings.syncToken, standaloneKey: settings.tmdbApiKey)
        for id in ["seasonal_", "seasonal_A", "seasonal_../x", "seasonal_x?key=y", "seasonal_%2f", "seasonal_é", "seasonal_" + String(repeating: "a", count: 72)] {
            do { _ = try SeasonalCatalog.request(connection: connection, rowID: id); preconditionFailure("Unsafe ID accepted") } catch { }
        }
        for page in [0, -1, 21] {
            do { _ = try SeasonalCatalog.request(connection: connection, rowID: "seasonal_x", page: page); preconditionFailure("Out of range page accepted") } catch { }
        }
        for backend in ["", "file:///catalog", "https://u:p@backend.example.test", "https://backend.example.test?key=x", "https://backend.example.test#fragment"] {
            let invalid = TmdbConnection(backendURL: backend, syncURL: settings.syncUrl, token: "private", standaloneKey: "unused")
            do { _ = try SeasonalCatalog.request(connection: invalid); preconditionFailure("Invalid backend accepted") } catch { }
        }
        let otherOrigin = TmdbConnection(backendURL: settings.backendUrl, syncURL: "https://other.example.test", token: "private", standaloneKey: "unused")
        precondition(try! SeasonalCatalog.request(connection: otherOrigin).value(forHTTPHeaderField: "Authorization") == nil)
        let policy = SeasonalRedirectPolicy()
        for destination in ["https://backend.example.test/prefix/moved", "https://elsewhere.example.test/moved"] {
            var denied = false
            let response = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: "HTTP/1.1", headerFields: nil)!
            policy.urlSession(session, task: session.dataTask(with: request), willPerformHTTPRedirection: response,
                              newRequest: URLRequest(url: URL(string: destination)!)) { denied = $0 == nil }
            precondition(denied, "Every seasonal redirect must be denied")
        }
        print("PASS: ID/page/backend validation, credential origin isolation, same/cross-origin redirects denied")
        let count = SeasonalFixture.requests.count
        SeasonalFixture.status = 404
        let inactiveItems = await client.rowItems(rowId: "seasonal_editor-picks", page: 2)
        precondition(inactiveItems.isEmpty, "Inactive row should be empty")
        precondition(SeasonalFixture.requests.count == count + 1, "Seasonal requests must bypass local row cache")
        SeasonalFixture.status = 503
        let failedItems = await client.rowItems(rowId: "seasonal_editor-picks", page: 2)
        precondition(failedItems.isEmpty)
        print("PASS: inactive/unavailable rows return empty without old successful content")
        SeasonalFixture.status = 200
        SeasonalFixture.body = validPage.replacingOccurrences(of: "\"mediaId\":123", with: "\"mediaId\":456")
        settings.backendUrl = "https://second.example.test/new"
        settings.syncUrl = settings.backendUrl
        settings.syncToken = "changed-token"
        let changed = await client.rowItems(rowId: "seasonal_editor-picks", page: 2)
        precondition(changed.first?.id == 456, "Same row/page must fetch current content")
        precondition(SeasonalFixture.requests.last?.url?.host == "second.example.test")
        precondition(SeasonalFixture.requests.last?.value(forHTTPHeaderField: "Authorization") == "Bearer changed-token")
        var pageJSON = try! JSONSerialization.jsonObject(with: Data(validPage.utf8)) as! [String: Any]
        for (key, value) in [("totalPages", 21 as Any), ("page", 1 as Any), ("id", "seasonal_wrong" as Any), ("schemaVersion", 2 as Any)] {
            var invalid = pageJSON
            invalid[key] = value
            SeasonalFixture.body = String(data: try! JSONSerialization.data(withJSONObject: invalid), encoding: .utf8)!
            let rejected = await client.rowItems(rowId: "seasonal_editor-picks", page: 2)
            precondition(rejected.isEmpty, "Mismatched/out-of-bounds envelope accepted")
        }
        let entry = (pageJSON["items"] as! [[String: Any]])[0]
        pageJSON["items"] = Array(repeating: entry, count: 21)
        SeasonalFixture.body = String(data: try! JSONSerialization.data(withJSONObject: pageJSON), encoding: .utf8)!
        let oversized = await client.rowItems(rowId: "seasonal_editor-picks", page: 2)
        precondition(oversized.isEmpty, "At most 20 items may be accepted")
        SeasonalFixture.body = "not-json"
        let malformed = await client.seasonalRows()
        precondition(malformed.isEmpty)
        print("PASS: changed connection/content read afresh; malformed, mismatched and oversized envelopes rejected")
    }
}
