package tv.velvet.data.tmdb

import tv.velvet.data.model.Episode
import tv.velvet.data.model.MediaDetails
import tv.velvet.data.model.MediaItem
import tv.velvet.data.model.RowSpec

/**
 * Native TMDB-backed discovery. Implementation: TmdbRepositoryImpl in this package.
 *
 * Contract notes for the implementation:
 *  - A configured backend proxies TMDB reads and keeps the TMDB credential server-side.
 *    Without a backend, a user-supplied local key enables standalone metadata browsing.
 *  - Image URLs are built as https://image.tmdb.org/t/p/{size}{path}
 *    (posters w500, backdrops w1280, logos w500 preferring PNG, stills w400, profiles w185).
 *  - Every MediaItem MUST have title/year/rating populated — cards always show them.
 *  - details() must fetch external_ids (imdbId) and images (logo) via append_to_response.
 *  - In-memory LRU caching for ordinary discovery/details; seasonal reads are never cached.
 *  - Availability gate: every movie discover row must send with_release_type=4|5 scoped by
 *    region=US AND release_date.lte=today (TMDB ignores with_release_type unless it is
 *    region-scoped with a release_date bound), and /trending-backed rows must drop movies
 *    whose details().digitallyAvailable is false. Home never shows what usenet can't serve.
 *  - rowItems() THROWS on fetch/parse failure — an empty list means "genuinely nothing",
 *    never "the network blipped", so the caller can render a real retry state.
 *  - Row ids are persisted in settings: "trending_movies" was renamed "popular_now" in
 *    v1.2.0 and stored orders swap it in place on read.
 */
interface TmdbRepository {

    /** Ordinary discovery rows, in default order. Stable ids are stored in settings. */
    val allRows: List<RowSpec>

    /** Fresh active global rows in backend order. Failure must hide seasonal sections. */
    suspend fun seasonalRows(): List<RowSpec>

    /**
     * Items for a discovery row. Seasonal ids go directly to the backend, without rotation
     * or cache. Not called for continue_watching / my_list. Page is 1-based.
     * Discover-backed rows rotate the page they actually fetch once per day for variety, so
     * page 1 is "today's slice", not literally TMDB page 1.
     */
    suspend fun rowItems(rowId: String, page: Int = 1): List<MediaItem>

    suspend fun details(id: Int, type: tv.velvet.data.model.MediaType): MediaDetails

    /**
     * Episodes of one season. THROWS on fetch/parse failure — callers must not conflate
     * "request failed" with "season has no episodes" (autoplay would skip seasons).
     */
    suspend fun episodes(showId: Int, season: Int): List<Episode>

    /** Resolve an old single-story target to its verified full-episode identity. */
    suspend fun episode(showId: Int, season: Int, number: Int): Episode? =
        episodes(showId, season).firstOrNull { it.covers(season, number) }

    suspend fun search(query: String): List<MediaItem>

    /** Resolve TMDB id from an IMDb id (find endpoint) — used by deep links if ever needed. */
    suspend fun findByImdb(imdbId: String): MediaItem?
}
