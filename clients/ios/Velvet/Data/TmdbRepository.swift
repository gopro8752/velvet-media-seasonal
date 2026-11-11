import Foundation

/// Backend seasonal collections are loaded afresh. Standalone repositories hide them.
protocol TmdbRepository: Sendable {
    var allRows: [RowSpec] { get }
    func seasonalRows() async -> [RowSpec]
    func rowItems(rowId: String, page: Int) async -> [MediaItem]
    func details(id: Int, type: MediaType) async throws -> MediaDetails
    func episodes(showId: Int, season: Int) async -> [Episode]
    func episode(showId: Int, season: Int, number: Int) async -> Episode?
    func search(query: String) async -> [MediaItem]
}

extension TmdbRepository {
    func seasonalRows() async -> [RowSpec] { [] }
    func episode(showId: Int, season: Int, number: Int) async -> Episode? {
        await episodes(showId: showId, season: season).first { $0.covers(season: season, episode: number) }
    }
}
