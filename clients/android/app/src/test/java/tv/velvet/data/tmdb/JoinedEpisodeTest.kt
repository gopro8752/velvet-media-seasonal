package tv.velvet.data.tmdb

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test
import tv.velvet.data.model.Episode
import tv.velvet.data.model.EpisodePart
import tv.velvet.data.streams.EpisodeMatch
import tv.velvet.data.streams.EpisodeTitleVerdict

class JoinedEpisodeTest {
    private val pilot = Episode(1, 1, "Help Wanted / Reef Blower / Tea at the Treedome", parts = listOf(
        EpisodePart(1, 1, "Help Wanted"), EpisodePart(1, 2, "Reef Blower"), EpisodePart(1, 3, "Tea at the Treedome")),
        releaseSeason = 1, releaseEpisode = 1)
    private val second = Episode(1, 4, "Bubblestand / Ripped Pants", parts = listOf(
        EpisodePart(1, 4, "Bubblestand"), EpisodePart(1, 5, "Ripped Pants")), releaseSeason = 1, releaseEpisode = 2)
    private fun ref(e: Episode) = EpisodeMatch.SeasonEpisodeRef(e.number, e.title, e.parts.map { it.name }, e.releaseSeason, e.releaseEpisode)

    @Test fun pilotHasThreeStoriesAndOnlyOnePlaybackIdentity() {
        for (number in 1..3) assertTrue(pilot.covers(1, number))
        assertFalse(pilot.covers(1, 4))
        assertFalse(pilot.covers(2, 2))
        assertEquals(listOf(1, 2, 3), pilot.coveredEpisodes.map { it.episode })
        assertEquals(4, second.number)
        assertEquals(2, second.releaseEpisode)
    }

    @Test fun standaloneEpisodeKeepsItsOriginalIdentity() {
        val ordinary = Episode(4, 8, "Ordinary episode")
        assertEquals(listOf(EpisodePart(4, 8, "Ordinary episode")), ordinary.coveredEpisodes)
        assertTrue(ordinary.covers(4, 8))
        assertFalse(ordinary.covers(4, 9))
    }

    @Test fun crossSeasonStoryResolvesWithoutGuessingAnOffset() {
        val joined = Episode(2, 22, "Shanghaied / Gary Takes a Bath", parts = listOf(
            EpisodePart(2, 22, "Shanghaied"), EpisodePart(3, 31, "Gary Takes a Bath")))
        assertTrue(joined.covers(3, 31))
        assertEquals(2, joined.season)
        assertEquals(22, joined.number)
        assertFalse(joined.covers(3, 30))
    }

    @Test fun previousUsesOneFullEpisodeAndNeverReturnsThePilotsBStory() = runBlocking {
        assertEquals(pilot, findPreviousEpisode(1, 4, listOf(1)) { listOf(pilot, second) })
    }

    @Test fun releaseNumbersAndComponentTitlesDoNotDemoteTheCorrectCombinedFile() {
        val season = listOf(ref(pilot), ref(second))
        assertEquals(EpisodeTitleVerdict.Unknown, EpisodeMatch.classify("SpongeBob.S01E02.1080p", 1, 4, season))
        assertEquals(EpisodeTitleVerdict.Confirmed, EpisodeMatch.classify(
            "SpongeBob.S01E02.Bubblestand.Ripped.Pants.1080p", 1, 4, season))
        assertEquals(EpisodeTitleVerdict.Mismatch(1), EpisodeMatch.classify(
            "SpongeBob.S01E01.Help.Wanted.Reef.Blower.Tea.at.the.Treedome", 1, 4, season))
        assertEquals(EpisodeTitleVerdict.Mismatch(4), EpisodeMatch.classify("SpongeBob.S01E02.1080p", 1, 1, season))
    }

    @Test fun unmappedNumberingRemainsConservative() {
        assertEquals(EpisodeTitleVerdict.Unknown,
            EpisodeMatch.classify("SpongeBob.S01E40.1080p", 1, 4, listOf(ref(pilot), ref(second))))
        assertEquals(EpisodeTitleVerdict.Mismatch(8),
            EpisodeMatch.classify("Normal.Show.S01E08", 1, 7, listOf(EpisodeMatch.SeasonEpisodeRef(7, "Hello"))))
    }

    @Test fun metadataKeepsCrossSeasonAliasesSeparateFromVisibleRows() {
        val decoded = Json { ignoreUnknownKeys = true }.decodeFromString(TmdbSeasonDetailsDto.serializer(), """
            {"episodes":[{"season_number":3,"episode_number":30,"name":"Normal"}],
            "velvet_episode_aliases":[{"season_number":2,"episode_number":22,"name":"Shanghaied / Gary Takes a Bath",
            "velvet_release_season":2,"velvet_release_episode":13,
            "velvet_episode_parts":[{"season":2,"episode":22,"id":123,"name":"Shanghaied"},
            {"season":3,"episode":31,"id":456,"name":"Gary Takes a Bath"}]}]}
        """)
        assertEquals(listOf(30), decoded.episodes.map { it.episodeNumber })
        assertEquals(22, decoded.episodeAliases.single().episodeNumber)
        assertEquals(31, decoded.episodeAliases.single().parts.last().episode)
        assertEquals(13, decoded.episodeAliases.single().releaseEpisode)
    }
    @Test fun standaloneStoryAndAmbiguousNumbersCannotPlayAsACompleteGroup() {
        val titles = pilot.parts.map { it.name }
        assertFalse(EpisodeMatch.coversGroup("SpongeBob.S01E02.Reef.Blower", titles))
        assertFalse(EpisodeMatch.coversGroup("SpongeBob.S01E01.1080p", titles))
        assertTrue(EpisodeMatch.coversGroup("SpongeBob.S01E01.Help.Wanted.Reef.Blowers.Tea.at.the.Treedome", titles))
        assertTrue(EpisodeMatch.coversGroup("SpongeBob.Mermaid.Man.and.Barnacle.Boy.III.Squirrel.Jokes",
            listOf("Mermaid Man & Barnacle Boy III", "Squirrel Jokes")))
        assertTrue(EpisodeMatch.coversGroup("ordinary.number.only", emptyList()))
    }

}
