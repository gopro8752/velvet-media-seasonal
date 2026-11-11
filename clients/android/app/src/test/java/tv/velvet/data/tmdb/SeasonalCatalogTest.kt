package tv.velvet.data.tmdb

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import tv.velvet.data.model.RowStyle
import tv.velvet.data.settings.VelvetSettings

class SeasonalCatalogTest {
    private val settings = VelvetSettings("local-secret", emptyList(), emptyList(), true, 8, "2160p", false,
        backendUrl = "https://backend.example.test/prefix", syncToken = "app-token")
    private val envelope = "\"schemaVersion\":1,\"revision\":\"revision-a\",\"effectiveDate\":\"2026-11-01\",\"timezone\":\"UTC\""
    private val rows = "[{\"id\":\"seasonal_arbitrary-new\",\"title\":\"Server title\",\"style\":\"landscape\",\"artworkUrl\":\"https://images.example.test/banner.jpg\"}]"

    @Test fun discoveryRejectsInvalidContractsInsteadOfInventingLocalRows() = runBlocking {
        val invalid = listOf(
            "{$envelope,\"rows\":$rows}".replace("schemaVersion\":1", "schemaVersion\":2"),
            "{$envelope,\"rows\":$rows}".replace("\"UTC\"", "\"America/New_York\""),
            "{$envelope,\"rows\":$rows}".replace("2026-11-01", "2026-02-30"),
            "{$envelope,\"rows\":$rows}".replace("seasonal_arbitrary-new", "holiday_christmas"),
            "{$envelope,\"rows\":$rows}".replace("seasonal_arbitrary-new", "seasonal_Upper"),
            "{$envelope,\"rows\":$rows}".replace("seasonal_arbitrary-new", "seasonal_" + "x".repeat(80)),
            "{$envelope,\"rows\":$rows}".replace("\"landscape\"", "\"top10\""),
        )
        for (body in invalid) {
            val catalog = fixture(body)
            var rejected = false
            try { catalog.discover() } catch (_: Exception) { rejected = true }
            assertTrue("Invalid contract accepted: $body", rejected)
        }
    }

    @Test fun rowItemsUseExactRequestedPageAndRefetchWithoutCache() = runBlocking {
        val requests = mutableListOf<okhttp3.Request>()
        var counter = 0
        val item = """{"mediaId":42,"type":"show","title":"A show","year":"2025","rating":8.1,"posterUrl":"https://img.example.test/p.jpg","backdropUrl":null,"logoUrl":null,"overview":"Overview","genres":["Comedy"],"runtimeMinutes":25,"imdbId":"tt1234567"}"""
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            requests.add(chain.request()); counter++
            val body = "{$envelope,\"id\":\"seasonal_arbitrary-new\",\"title\":\"Server title\",\"style\":\"poster\",\"artworkUrl\":null,\"page\":2,\"totalPages\":3,\"stale\":false,\"items\":[$item]}"
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body(body.replace("A show", "Show $counter").toResponseBody()).build()
        }.build()
        val catalog = SeasonalCatalog(client, Json) { settings }
        val first = catalog.items("seasonal_arbitrary-new", 2).single()
        val second = catalog.items("seasonal_arbitrary-new", 2).single()
        assertEquals(42, first.id)
        assertEquals(tv.velvet.data.model.MediaType.SHOW, first.type)
        assertEquals("2025", first.year)
        assertEquals(8.1, first.rating!!, 0.0)
        assertEquals(listOf("Comedy"), first.genres)
        assertEquals(25, first.runtimeMinutes)
        assertEquals("tt1234567", first.imdbId)
        assertEquals("Show 2", second.title)
        assertEquals(2, requests.size)
        requests.forEach {
            assertEquals("/prefix/catalog/v1/seasonal/rows/seasonal_arbitrary-new", it.url.encodedPath)
            assertEquals("page=2", it.url.encodedQuery)
            assertTrue(it.cacheControl.noCache)
            assertTrue(it.cacheControl.noStore)
        }
    }

    @Test fun responseBodyParsingDoesNotBlockCallerThread() = runBlocking {
        val caller = Thread.currentThread().name
        var bodyThread: String? = null
        val body = "{$envelope,\"rows\":[]}"
        val catalog = SeasonalCatalog(OkHttpClient.Builder().addInterceptor { chain ->
            val responseBody = object : okhttp3.ResponseBody() {
                override fun contentType(): okhttp3.MediaType? = null
                override fun contentLength(): Long = -1
                override fun source(): okio.BufferedSource {
                    bodyThread = Thread.currentThread().name
                    return okio.Buffer().writeUtf8(body)
                }
            }
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body(responseBody).build()
        }.build(), Json) { settings }
        catalog.discover()
        assertNotNull(bodyThread)
        assertNotEquals(caller, bodyThread)
    }

    @Test fun discoveryRefetchesAndPropagatesOldServerOrNetworkFailures() = runBlocking {
        var status = 200
        var body = "{$envelope,\"rows\":$rows}"
        val catalog = SeasonalCatalog(OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(status).message("fixture")
                .body(body.toResponseBody()).build()
        }.build(), Json) { settings }
        assertEquals(1, catalog.discover().size)
        body = "{$envelope,\"rows\":[]}"
        assertTrue(catalog.discover().isEmpty())
        status = 404
        var rejected = false
        try { catalog.discover() } catch (_: java.io.IOException) { rejected = true }
        assertTrue("Old servers must not restore earlier seasonal rows", rejected)
        status = 200
        body = "not JSON"
        rejected = false
        try { catalog.discover() } catch (_: Exception) { rejected = true }
        assertTrue(rejected)
    }

    @Test fun invalidItemRequestsNeverReachNetwork() = runBlocking {
        val catalog = fixture("{}")
        for ((id, page) in listOf("../seasonal_x" to 1, "seasonal_x" to 0, "seasonal_x" to 21)) {
            var rejected = false
            try { catalog.items(id, page) } catch (_: IllegalArgumentException) { rejected = true }
            assertTrue(rejected)
        }
    }

    @Test fun itemResponsesEnforceIdentityPageAndBoundedItems() = runBlocking {
        val item = """{"mediaId":42,"type":"movie","title":"Movie"}"""
        val valid = "{$envelope,\"id\":\"seasonal_valid\",\"title\":\"Title\",\"style\":\"poster\",\"artworkUrl\":null,\"page\":1,\"totalPages\":1,\"stale\":false,\"items\":[$item]}"
        for (body in listOf(
            valid.replace("seasonal_valid", "seasonal_other"),
            valid.replace("\"page\":1", "\"page\":2"),
            valid.replace("\"totalPages\":1", "\"totalPages\":21"),
            valid.replace("[$item]", List(21) { item }.joinToString(",", "[", "]")),
            valid.replace("\"movie\"", "\"episode\""),
        )) {
            var rejected = false
            try { fixture(body).items("seasonal_valid", 1) } catch (_: Exception) { rejected = true }
            assertTrue("Invalid item envelope accepted", rejected)
        }
        assertEquals("Movie", fixture(valid.replace("\"stale\":false", "\"stale\":true"))
            .items("seasonal_valid", 1).single().title)
    }

    private fun fixture(body: String, code: Int = 200): SeasonalCatalog = SeasonalCatalog(
        OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(code).message("fixture")
                .body(body.toResponseBody()).build()
        }.build(), Json,
    ) { settings }

    @Test fun discoveryAcceptsArbitraryServerRowsInOrderWithoutThirdPartyCredentials() = runBlocking {
        val requests = mutableListOf<okhttp3.Request>()
        val client = OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            requests.add(chain.request())
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body("{$envelope,\"rows\":$rows}".toResponseBody()).build()
        }).build()
        val catalog = SeasonalCatalog(client, Json) { settings }
        val specs = catalog.discover()
        assertEquals(listOf("seasonal_arbitrary-new"), specs.map { it.id })
        assertEquals("Server title", specs.single().title)
        assertEquals(RowStyle.LANDSCAPE, specs.single().style)
        assertEquals("https://images.example.test/banner.jpg", specs.single().artworkUrl)
        assertEquals("/prefix/catalog/v1/seasonal", requests.single().url.encodedPath)
        assertNull(requests.single().url.query)
        assertEquals("Bearer app-token", requests.single().header("Authorization"))
        assertFalse(requests.single().url.toString().contains("local-secret"))
    }
}
