package tv.velvet.data.tmdb

import kotlinx.serialization.json.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.CacheControl
import okhttp3.OkHttpClient
import tv.velvet.data.model.MediaItem
import tv.velvet.data.model.MediaType
import tv.velvet.data.model.RowSpec
import tv.velvet.data.model.RowStyle
import tv.velvet.data.net.await
import tv.velvet.data.settings.VelvetSettings
import java.io.IOException

/** Server-owned global curation; never persisted, date-filtered, or sent to TMDB. */
internal class SeasonalCatalog(
    client: OkHttpClient,
    private val json: Json,
    private val settings: suspend () -> VelvetSettings,
) {
    private val client = tmdbMetadataClient(client)

    suspend fun discover(): List<RowSpec> {
        val body = get("catalog/v1/seasonal")
        validateEnvelope(body)
        val rows = body.getValue("rows").jsonArray.map { row(it.jsonObject) }
        require(rows.map { it.id }.distinct().size == rows.size) { "Duplicate seasonal row ids" }
        return rows
    }

    suspend fun items(id: String, page: Int): List<MediaItem> {
        require(id.matches(Regex("seasonal_[a-z0-9_-]+")) && id.length <= 80)
        require(page in 1..20)
        val body = get("catalog/v1/seasonal/rows/$id", mapOf("page" to page.toString()))
        validateEnvelope(body)
        require(row(body).id == id)
        require(body.getValue("page").jsonPrimitive.int == page)
        require(body.getValue("totalPages").jsonPrimitive.int in 0..20)
        body.getValue("stale").jsonPrimitive.boolean
        val items = body.getValue("items").jsonArray
        require(items.size <= 20)
        return items.map { item ->
            val value = item.jsonObject
            MediaItem(
                id = value.getValue("mediaId").jsonPrimitive.int.also { require(it > 0) },
                type = MediaType.fromKey(value.getValue("type").jsonPrimitive.content),
                title = value.getValue("title").jsonPrimitive.content,
                year = value["year"]?.jsonPrimitive?.contentOrNull,
                rating = value["rating"]?.jsonPrimitive?.doubleOrNull,
                posterUrl = value["posterUrl"]?.jsonPrimitive?.contentOrNull,
                backdropUrl = value["backdropUrl"]?.jsonPrimitive?.contentOrNull,
                logoUrl = value["logoUrl"]?.jsonPrimitive?.contentOrNull,
                overview = value["overview"]?.jsonPrimitive?.contentOrNull,
                genres = value["genres"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty(),
                runtimeMinutes = value["runtimeMinutes"]?.jsonPrimitive?.intOrNull,
                imdbId = value["imdbId"]?.jsonPrimitive?.contentOrNull,
            )
        }
    }

    private suspend fun get(path: String, params: Map<String, String> = emptyMap()): JsonObject = withContext(Dispatchers.IO) {
        val request = tmdbMetadataRequest(settings(), path, params).newBuilder()
            .cacheControl(CacheControl.Builder().noCache().noStore().build()).build()
        client.await(request).use { response ->
            if (!response.isSuccessful) throw IOException("Seasonal catalog HTTP ${response.code}")
            json.parseToJsonElement(response.body?.string() ?: throw IOException("Empty seasonal catalog")).jsonObject
        }
    }

    private fun validateEnvelope(value: JsonObject) {
        require(value.getValue("schemaVersion").jsonPrimitive.int == 1)
        require(value.getValue("revision").jsonPrimitive.content.isNotBlank())
        require(value.getValue("timezone").jsonPrimitive.content == "UTC")
        val date = value.getValue("effectiveDate").jsonPrimitive.content
        require(date.matches(Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}")))
        java.time.LocalDate.parse(date) // Format validation only, never a local activation rule.
    }

    private fun row(value: JsonObject) = RowSpec(
        id = value.getValue("id").jsonPrimitive.content.also {
            require(it.matches(Regex("seasonal_[a-z0-9_-]+")) && it.length <= 80)
        },
        title = value.getValue("title").jsonPrimitive.content,
        style = when (value.getValue("style").jsonPrimitive.content) {
            "poster" -> RowStyle.POSTER
            "landscape" -> RowStyle.LANDSCAPE
            else -> throw IOException("Unsupported seasonal row style")
        },
        artworkUrl = value["artworkUrl"]?.jsonPrimitive?.contentOrNull,
    )
}
