import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private final class ForegroundManifestFixture: URLProtocol {
    static var rows = #"[{"id":"seasonal_server-picks","title":"Server Picks","style":"poster","artworkUrl":null}]"#
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let data = Data("{\"schemaVersion\":1,\"revision\":\"fixture\",\"effectiveDate\":\"2040-08-01\",\"timezone\":\"UTC\",\"rows\":\(Self.rows)}".utf8)
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

/// Virtual monotonic sleep; the scheduler/action under test are real production Swift.
@MainActor private final class Clock {
    var sleeps: [UInt64] = []
    var waiters: [CheckedContinuation<Void, Error>] = []
    func sleep(_ nanos: UInt64) async throws {
        sleeps.append(nanos)
        try await withCheckedThrowingContinuation { waiters.append($0) }
    }
    func tick() { let pending = waiters; waiters = []; pending.forEach { $0.resume() } }
}

@main struct VerifySeasonalForeground {
    @MainActor static func settle() async { for _ in 0..<50 { await Task.yield() } }

    @MainActor static func main() async {
        let clock = Clock()
        var requests = 0
        var invalidations = 0
        let scheduler = SeasonalForegroundRefresh(sleep: clock.sleep, invalidate: { invalidations += 1 }) { _ in
            requests += 1
        }
        scheduler.refresh()
        await settle()
        precondition(requests == 0, "Initialization/settings while hidden must not request")
        scheduler.setForeground(visible: false, active: true)
        scheduler.setForeground(visible: true, active: false)
        await settle()
        precondition(requests == 0 && clock.sleeps.isEmpty, "Both Home visibility and active scene are required")
        scheduler.setForeground(visible: true, active: true)
        await settle()
        precondition(requests == 1, "Entering foreground must immediately revalidate")
        precondition(clock.sleeps == [60_000_000_000], "Use bounded 60-second monotonic cadence, not device calendar")
        scheduler.setForeground(visible: true, active: true)
        await settle()
        precondition(requests == 1 && clock.sleeps.count == 1, "Repeated appearance must not duplicate loops")
        clock.tick()
        await settle()
        precondition(requests == 2 && clock.sleeps.count == 2, "Still-visible Home must fetch the changed manifest at next tick")
        scheduler.setForeground(visible: true, active: false)
        let stopped = requests
        scheduler.refresh()
        clock.tick() // Even a non-cooperative sleep must not issue a background request.
        await settle()
        precondition(requests == stopped, "Background/settings callbacks must never fetch")
        scheduler.setForeground(visible: true, active: true)
        await settle()
        precondition(requests == stopped + 1, "Resume immediately refreshes")
        scheduler.setForeground(visible: false, active: true)
        scheduler.refresh()
        clock.tick()
        await settle()
        precondition(requests == stopped + 1, "Hidden Home must remain paused even when scene is active")
        precondition(invalidations > 0)
        print("PASS: 60-second foreground cadence, immediate resume, duplicate-start suppression, hidden/background settings guard")

        // Non-cooperative responses finish after cancellation: neither manifests
        // nor item pages may publish into a newer profile/settings/visibility scope.
        var manifestWaiters: [CheckedContinuation<Void, Never>] = []
        var manifestCancelled: [Bool] = []
        var published: [UUID] = []
        var scope: SeasonalForegroundRefresh!
        scope = SeasonalForegroundRefresh(sleep: { _ in try await Task.sleep(nanoseconds: 1_000_000_000) }, invalidate: {}) { generation in
            await withCheckedContinuation { manifestWaiters.append($0) }
            manifestCancelled.append(Task.isCancelled)
            if scope.isCurrent(generation) { published.append(generation) }
        }
        scope.setForeground(visible: true, active: true)
        await settle()
        let first = scope.generation
        var itemWaiter: CheckedContinuation<Void, Never>?
        var itemCancelled = false
        let item = Task { @MainActor in
            await withCheckedContinuation { itemWaiter = $0 }
            itemCancelled = Task.isCancelled
            if scope.isCurrent(first) { published.append(first) }
        }
        scope.trackItems(item)
        await settle()
        scope.refresh() // Profile/backend/token/settings supersession.
        await settle()
        precondition(!scope.isCurrent(first), "Superseded generation must be rejected synchronously")
        manifestWaiters.removeFirst().resume()
        itemWaiter!.resume()
        await settle()
        precondition(manifestCancelled == [true] && itemCancelled && published.isEmpty,
                     "Supersession must cancel manifest AND row pages and reject late responses")
        let second = scope.generation
        scope.setForeground(visible: false, active: true)
        manifestWaiters.removeFirst().resume()
        await settle()
        precondition(manifestCancelled == [true, true] && published.isEmpty && !scope.isCurrent(second),
                     "Disappear must cancel outstanding work and reject late response")
        scope.setForeground(visible: true, active: true)
        await settle()
        let current = scope.generation
        manifestWaiters.removeFirst().resume()
        await settle()
        precondition(published == [current], "Only current foreground responses may publish")
        scope.setForeground(visible: true, active: false)
        print("PASS: manifest/item cancellation, superseded/disappeared late-response rejection, current-response acceptance")

        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [ForegroundManifestFixture.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let client = TmdbClient(settings: SettingsStore(), session: session)
        let serverClock = Clock()
        var displayed: [RowSpec] = []
        var completed = 0
        var live: SeasonalForegroundRefresh!
        live = SeasonalForegroundRefresh(sleep: serverClock.sleep, invalidate: { displayed = [] }) { generation in
            let rows = await client.seasonalRows()
            guard !Task.isCancelled, live.isCurrent(generation) else { return }
            displayed = rows
            completed += 1
        }
        live.setForeground(visible: true, active: true)
        for _ in 0..<2_000 {
            if completed == 1 { break }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        precondition(completed == 1 && displayed.map(\.id) == ["seasonal_server-picks"])
        ForegroundManifestFixture.rows = "[]" // Backend expires a collection while Home stays visible.
        serverClock.tick()
        for _ in 0..<2_000 {
            if completed == 2 { break }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        precondition(completed == 2 && displayed.isEmpty, "Next foreground tick must reflect server expiration without a scene/profile change")
        live.setForeground(visible: false, active: false)
        serverClock.tick()
        await settle()
        print("PASS: real TmdbClient + scheduler observes server manifest expiration while Home remains visible")
    }
}
