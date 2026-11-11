package tv.velvet.data.tmdb

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.OkHttpClient
import tv.velvet.data.settings.VelvetSettings

/** Test-only pipe adapter: all HTTP, validation and media mapping are production code. */
fun main(): Unit = runBlocking {
    var settings = VelvetSettings(tmdbApiKey = "never-send-this", streamSources = emptyList(),
        rowOrder = emptyList(), autoplayNext = false, autoplayCountdownSecs = 5,
        preferredQuality = "1080p", tunneledPlayback = false)
    val catalog = SeasonalCatalog(OkHttpClient(), Json) { settings }
    while (true) {
        val line = readlnOrNull() ?: break
        val args = line.split('\t')
        settings = settings.copy(backendUrl = args[1], syncUrl = args[1], syncToken = args[2])
        val result = try {
            if (args[0] == "clock") buildJsonObject { put("deviceDate", java.time.LocalDate.now().toString()) }
            else if (args[0] == "discover") JsonArray(catalog.discover().map { row -> buildJsonObject {
                put("id", row.id); put("title", row.title)
                put("style", row.style.name.lowercase()); put("artworkUrl", row.artworkUrl)
            } }) else JsonArray(catalog.items(args[3], args[4].toInt()).map { item -> buildJsonObject {
                put("id", item.id); put("type", item.type.key); put("title", item.title)
                put("year", item.year); put("rating", item.rating)
                put("posterUrl", item.posterUrl); put("backdropUrl", item.backdropUrl)
                put("logoUrl", item.logoUrl); put("overview", item.overview)
                put("genres", JsonArray(item.genres.map(::JsonPrimitive)))
                put("runtimeMinutes", item.runtimeMinutes); put("imdbId", item.imdbId)
            } })
        } catch (error: Exception) {
            // SeasonalCatalog deliberately throws; production repository catches and hides.
            System.err.println("seasonal request rejected: ${error.message}")
            JsonArray(emptyList())
        }
        println(result)
        System.out.flush()
    }
    kotlin.system.exitProcess(0)
}
