import XCTest
@testable import Velvet

final class EpisodeNavigationTests: XCTestCase {
    private func episode(_ season: Int, _ number: Int) -> Episode {
        Episode(season: season, number: number, title: "S\(season) E\(number)")
    }

    func testFirstRegularEpisodeDoesNotEnterSpecials() async throws {
        var calls = [Int]()
        let previous = try await findPreviousEpisode(season: 1, episode: 1, seasonNumbers: [0, 1]) {
            calls.append($0)
            return [self.episode(0, 8)]
        }
        XCTAssertNil(previous)
        XCTAssertTrue(calls.isEmpty)
    }

    func testNearestExistingEpisodeIgnoresGapsAndWrongSeason() async throws {
        let previous = try await findPreviousEpisode(season: 2, episode: 6, seasonNumbers: [1, 2]) { _ in
            [self.episode(2, 1), self.episode(1, 5), self.episode(2, 4), self.episode(2, 6), self.episode(2, 0)]
        }
        XCTAssertEqual(previous, episode(2, 4))
    }

    func testSeasonBoundaryChoosesLastActualEpisode() async throws {
        var calls = [Int]()
        let previous = try await findPreviousEpisode(season: 3, episode: 1, seasonNumbers: [0, 1, 2, 3]) {
            calls.append($0)
            return [self.episode(2, 4), self.episode(2, 10), self.episode(2, 1)]
        }
        XCTAssertEqual(previous, episode(2, 10))
        XCTAssertEqual(calls, [2])
    }

    func testEmptySeasonsAreSkippedOnceInDescendingOrder() async throws {
        var calls = [Int]()
        let previous = try await findPreviousEpisode(season: 4, episode: 1, seasonNumbers: [2, 0, 3, 3, 1, 4]) {
            calls.append($0)
            return $0 == 1 ? [self.episode(1, 7)] : []
        }
        XCTAssertEqual(previous, episode(1, 7))
        XCTAssertEqual(calls, [3, 2, 1])
    }

    func testNoEarlierActualEpisodeReturnsNil() async throws {
        let previous = try await findPreviousEpisode(season: 2, episode: 1, seasonNumbers: [0, 1, 2]) { _ in [] }
        XCTAssertNil(previous)
    }

    func testSpecialsNavigateOnlyWithinTheirSeason() async throws {
        let previous = try await findPreviousEpisode(season: 0, episode: 4, seasonNumbers: [0, 1]) { _ in
            [self.episode(0, 1), self.episode(0, 3)]
        }
        XCTAssertEqual(previous, episode(0, 3))
        let first = try await findPreviousEpisode(season: 0, episode: 1, seasonNumbers: [0, 1]) { _ in
            XCTFail("S0 E1 must not fetch another season")
            return []
        }
        XCTAssertNil(first)
    }

    func testInvalidCoordinatesNeverFetch() async throws {
        for (season, number) in [(-1, 2), (1, 0), (1, -1)] {
            let previous = try await findPreviousEpisode(season: season, episode: number, seasonNumbers: [1]) { _ in
                XCTFail("Invalid coordinates must not fetch metadata")
                return []
            }
            XCTAssertNil(previous)
        }
    }

    func testMissingCurrentSeasonFailsWithoutSkippingIt() async {
        enum Offline: Error { case failed }
        var calls = [Int]()
        do {
            _ = try await findPreviousEpisode(season: 3, episode: 4, seasonNumbers: [1, 2, 3]) {
                calls.append($0)
                throw Offline.failed
            }
            XCTFail("Expected metadata error")
        } catch { XCTAssertTrue(error is Offline) }
        XCTAssertEqual(calls, [3])
    }

    func testMissingEarlierSeasonFailsWithoutSkippingIt() async {
        enum Offline: Error { case failed }
        var calls = [Int]()
        do {
            _ = try await findPreviousEpisode(season: 3, episode: 1, seasonNumbers: [1, 2, 3]) {
                calls.append($0)
                throw Offline.failed
            }
            XCTFail("Expected metadata error")
        } catch { XCTAssertTrue(error is Offline) }
        XCTAssertEqual(calls, [2])
    }

    func testCancellationPropagatesWithoutTryingAnotherSeason() async {
        var calls = [Int]()
        do {
            _ = try await findPreviousEpisode(season: 3, episode: 1, seasonNumbers: [1, 2, 3]) {
                calls.append($0)
                throw CancellationError()
            }
            XCTFail("Expected cancellation")
        } catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertEqual(calls, [2])
    }
    func testJoinedPilotCoverageAndPairedReleaseNumbering() async throws {
        let pilot = Episode(season: 1, number: 1, title: "Help Wanted / Reef Blower / Tea at the Treedome",
            parts: [EpisodePart(season: 1, episode: 1, name: "Help Wanted"),
                    EpisodePart(season: 1, episode: 2, name: "Reef Blower"),
                    EpisodePart(season: 1, episode: 3, name: "Tea at the Treedome")], releaseSeason: 1, releaseEpisode: 1)
        let second = Episode(season: 1, number: 4, title: "Bubblestand / Ripped Pants",
            parts: [EpisodePart(season: 1, episode: 4, name: "Bubblestand"),
                    EpisodePart(season: 1, episode: 5, name: "Ripped Pants")], releaseSeason: 1, releaseEpisode: 2)
        XCTAssertTrue(pilot.covers(season: 1, episode: 3))
        XCTAssertFalse(pilot.covers(season: 1, episode: 4))
        XCTAssertEqual(second.number, 4)
        XCTAssertEqual(second.releaseEpisode, 2)
        let previous = try await findPreviousEpisode(season: 1, episode: 4, seasonNumbers: [1]) { _ in [pilot, second] }
        XCTAssertEqual(previous, pilot)
        let references = [pilot, second].map {
            EpisodeMatch.SeasonEpisodeRef(number: $0.number, title: $0.title, partTitles: $0.parts.map(\.name),
                                         releaseSeason: $0.releaseSeason, releaseEpisode: $0.releaseEpisode)
        }
        XCTAssertEqual(EpisodeMatch.classify(filenameOrTitle: "SpongeBob.S01E02.Bubblestand.Ripped.Pants",
            requestedSeason: 1, requestedEpisode: 4, seasonEpisodes: references), .confirmed)
        XCTAssertEqual(EpisodeMatch.classify(filenameOrTitle: "SpongeBob.S01E02.1080p",
            requestedSeason: 1, requestedEpisode: 4, seasonEpisodes: references), .unknown)
        XCTAssertEqual(EpisodeMatch.classify(filenameOrTitle: "SpongeBob.S01E02.1080p",
            requestedSeason: 1, requestedEpisode: 1, seasonEpisodes: references), .mismatch(actualEpisode: 4))
    }

    func testJoinedCrossSeasonAliasDoesNotBecomeAnotherVisibleEpisode() throws {
        let data = Data(#"{"episodes":[{"season_number":3,"episode_number":30,"name":"Normal"}],"velvet_episode_aliases":[{"season_number":2,"episode_number":22,"name":"Shanghaied / Gary Takes a Bath","velvet_release_season":2,"velvet_release_episode":13,"velvet_episode_parts":[{"season":2,"episode":22,"id":123,"name":"Shanghaied"},{"season":3,"episode":31,"id":456,"name":"Gary Takes a Bath"}]}]}"#.utf8)
        let dto = try JSONDecoder().decode(TmdbSeasonDetailsDto.self, from: data)
        XCTAssertEqual(dto.episodes?.map(\.episodeNumber), [30])
        let alias = try XCTUnwrap(dto.episodeAliases?.first)
        XCTAssertEqual(alias.parts?.last?.episode, 31)
        XCTAssertEqual(alias.releaseEpisode, 13)
        let episode = Episode(season: 2, number: 22, title: "Shanghaied / Gary Takes a Bath",
            parts: alias.parts!.map { EpisodePart(season: $0.season, episode: $0.episode, name: $0.name) })
        XCTAssertTrue(episode.covers(season: 3, episode: 31))
        XCTAssertFalse(episode.covers(season: 3, episode: 30))
    }

    func testStandaloneStoryCannotBePlayedAsACompleteGroup() {
        let titles = ["Help Wanted", "Reef Blower", "Tea at the Treedome"]
        XCTAssertFalse(EpisodeMatch.coversGroup("SpongeBob.S01E02.Reef.Blower", partTitles: titles))
        XCTAssertFalse(EpisodeMatch.coversGroup("SpongeBob.S01E01.1080p", partTitles: titles))
        XCTAssertTrue(EpisodeMatch.coversGroup("SpongeBob.S01E01.Help.Wanted.Reef.Blowers.Tea.at.the.Treedome", partTitles: titles))
        XCTAssertTrue(EpisodeMatch.coversGroup("SpongeBob.Mermaid.Man.and.Barnacle.Boy.III.Squirrel.Jokes",
            partTitles: ["Mermaid Man & Barnacle Boy III", "Squirrel Jokes"]))
    }

}
