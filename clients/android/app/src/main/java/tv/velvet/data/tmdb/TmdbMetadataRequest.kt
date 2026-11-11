package tv.velvet.data.tmdb

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import tv.velvet.data.settings.VelvetSettings
import java.io.IOException

/** A server redirect cannot move catalog/metadata requests or credentials to another route. */
internal fun tmdbMetadataClient(base: OkHttpClient): OkHttpClient = base.newBuilder()
    .followRedirects(false)
    .followSslRedirects(false)
    .build()

/**
 * Backend metadata/catalog uses the app token, never a device TMDB credential. A missing or
 * invalid backend must fail visibly; silently falling back would bypass its policy
 * and break again whenever the server owner rotates the upstream key.
 */
internal fun tmdbMetadataRequest(
    settings: VelvetSettings,
    path: String,
    params: Map<String, String>,
): Request {
    val segments = path.trim('/').split('/')
    require(segments.all { it.matches(Regex("[a-zA-Z0-9_-]+")) }) {
        "Metadata path must be a relative TMDB endpoint"
    }
    val backend = settings.backendUrl.trim().trimEnd('/')
    val seasonal = path == "catalog/v1/seasonal" || path.startsWith("catalog/v1/seasonal/")
    if (seasonal) {
        if (path == "catalog/v1/seasonal") {
            require(params.isEmpty()) { "Seasonal discovery has no client date or query" }
        } else {
            require(segments.size == 5 && segments[3] == "rows")
            val id = segments[4]
            require(id.matches(Regex("seasonal_[a-z0-9_-]+")) && id.length <= 80)
            require(params.keys == setOf("page") && params.getValue("page").toIntOrNull() in 1..20)
        }
    }
    val useBackend = backend.isNotBlank()
    if (seasonal && !useBackend) throw IOException("Seasonal discovery needs a Velvet backend.")
    val base = (if (useBackend) backend else "https://api.themoviedb.org/3")
        .toHttpUrlOrNull() ?: throw IOException("Check your backend URL in Settings.")
    if (base.username.isNotEmpty() || base.password.isNotEmpty() ||
        base.query != null || base.fragment != null
    ) throw IOException("The backend URL must not contain credentials, a query, or a fragment.")

    val url = base.newBuilder()
    // A reverse-proxy path prefix belongs to the configured backend; preserve it.
    if (useBackend && !seasonal) url.addPathSegments("metadata/tmdb")
    segments.forEach { url.addPathSegment(it) }
    if (!seasonal) url.addQueryParameter("language", params["language"] ?: "en-US")
    params.forEach { (key, value) ->
        if (!key.equals("api_key", ignoreCase = true) &&
            !key.equals("access_token", ignoreCase = true) && key != "language"
        ) url.addQueryParameter(key, value)
    }
    if (!useBackend) {
        val key = settings.tmdbApiKey.trim()
        if (key.isBlank()) throw IOException("Connect your Velvet backend in Settings.")
        url.addQueryParameter("api_key", key)
    }

    return Request.Builder().url(url.build()).get().apply {
        if (useBackend && settings.syncToken.isNotBlank()) {
            header("Authorization", "Bearer ${settings.syncToken.trim()}")
        }
    }.build()
}
