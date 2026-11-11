package tv.velvet.ui.home

import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.RETURNS_DEEP_STUBS
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`
import tv.velvet.data.model.MediaItem
import tv.velvet.data.model.MediaType
import tv.velvet.data.model.RowSpec
import tv.velvet.data.model.RowStyle
import tv.velvet.data.settings.VelvetSettings
import tv.velvet.data.tmdb.TmdbRepository
import tv.velvet.di.AppContainer

/** Real ViewModel + virtual coroutine time; only Android storage/sync boundaries are mocked. */
@OptIn(ExperimentalCoroutinesApi::class)
class HomeSeasonalLifecycleTest {
    private val scope = TestScope()
    private val settings = MutableStateFlow(VelvetSettings(
        tmdbApiKey = "", streamSources = emptyList(), rowOrder = emptyList(),
        autoplayNext = true, autoplayCountdownSecs = 10, preferredQuality = "1080p",
        tunneledPlayback = false,
    ))
    private val profile = MutableStateFlow<String?>("default")
    private val seasonal = RowSpec("seasonal_server", "Server collection", RowStyle.POSTER)
    private val repository = SeasonalRepository()
    private lateinit var home: HomeViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(StandardTestDispatcher(scope.testScheduler))
        val container = mock(AppContainer::class.java, RETURNS_DEEP_STUBS)
        `when`(container.settingsRepository.settings).thenReturn(settings)
        `when`(container.settingsRepository.activeProfileId).thenReturn(profile)
        `when`(container.profilesRepository.profiles).thenReturn(flowOf(emptyList()))
        for (id in listOf("default", "other")) {
            `when`(container.categoryRepository.generated(id)).thenReturn(flowOf(emptyList()))
            `when`(container.database.watchProgressDao().progressKeys(id)).thenReturn(flowOf(emptyList()))
            `when`(container.database.watchProgressDao().watchedShowIds(id)).thenReturn(flowOf(emptyList()))
            `when`(container.database.watchProgressDao().continueWatching(id)).thenReturn(flowOf(emptyList()))
            `when`(container.database.myListDao().myList(id)).thenReturn(flowOf(emptyList()))
        }
        `when`(container.tmdbRepository).thenReturn(repository)
        home = HomeViewModel(container)
    }

    @After
    fun tearDown() {
        home.viewModelScope.cancel()
        Dispatchers.resetMain()
    }

    @Test
    fun visibleHomeRevalidatesDisabledServerManifestWithinSixtySeconds() = runHomeTest {
        repository.rows = listOf(seasonal)
        runCurrent()
        home.onHomeResumed()
        runCurrent()
        assertEquals(listOf(seasonal), home.tmdbRows.value)
        val requests = repository.manifestRequests

        repository.rows = emptyList() // Server disables the collection while Home stays visible.
        advanceTimeBy(59_999)
        runCurrent()
        assertEquals(requests, repository.manifestRequests)
        advanceTimeBy(1)
        runCurrent()

        assertEquals("Home must revalidate at the 60-second foreground bound", requests + 1, repository.manifestRequests)
        assertEquals(emptyList<RowSpec>(), home.tmdbRows.value)
    }

    @Test
    fun backgroundHomeMakesNoSeasonalRequestsIncludingSettingsProfileAndRetry() = runHomeTest {
        repository.rows = listOf(seasonal)
        runCurrent()
        assertEquals("Creating a background Home must not request the seasonal manifest", 0, repository.manifestRequests)
        home.onHomeResumed()
        runCurrent()
        val requests = repository.manifestRequests
        home.onHomePaused()
        settings.value = settings.value.copy(backendUrl = "https://changed.example")
        profile.value = "other"
        home.retryFailedRows()
        runCurrent()
        advanceTimeBy(180_000)
        runCurrent()
        assertEquals("Paused Home must not revalidate, even after invalidation", requests, repository.manifestRequests)

        home.onHomeResumed()
        runCurrent()
        assertEquals(requests + 1, repository.manifestRequests)
        advanceTimeBy(60_000)
        runCurrent()
        assertEquals(requests + 2, repository.manifestRequests)
    }

    @Test
    fun pausingHomeCancelsInFlightSeasonalItems() = runHomeTest {
        repository.rows = listOf(seasonal)
        repository.items = { awaitCancellation() }
        runCurrent()
        home.onHomeResumed()
        runCurrent()
        home.onHomePaused()
        runCurrent()
        assertEquals("Seasonal item requests must not continue in the background", 1, repository.cancelledItems)
        assertEquals(emptyList<RowSpec>(), home.tmdbRows.value)
    }

    @Test
    fun duplicateResumeDoesNotMultiplyOrPostponeTimerAndPauseHasNoCatchUp() = runHomeTest {
        runCurrent()
        home.onHomeResumed()
        runCurrent()
        val requests = repository.manifestRequests
        advanceTimeBy(30_000)
        home.onHomeResumed()
        runCurrent()
        assertEquals(requests, repository.manifestRequests)
        advanceTimeBy(30_000)
        runCurrent()
        assertEquals(requests + 1, repository.manifestRequests)
        home.onHomePaused()
        home.onHomePaused() // Disposal may follow ON_PAUSE.
        advanceTimeBy(180_000)
        runCurrent()
        assertEquals(requests + 1, repository.manifestRequests)
        home.onHomeResumed()
        runCurrent()
        assertEquals(requests + 2, repository.manifestRequests)
        advanceTimeBy(60_000)
        runCurrent()
        assertEquals(requests + 3, repository.manifestRequests)
        home.viewModelScope.cancel()
        advanceTimeBy(120_000)
        runCurrent()
        assertEquals(requests + 3, repository.manifestRequests)
    }

    @Test
    fun tickAdoptsServerOrderMetadataAndFreshItemsWithoutLocalDateFiltering() = runHomeTest {
        val second = RowSpec("seasonal_other", "Another collection", RowStyle.POSTER)
        val oldItem = MediaItem(1, MediaType.MOVIE, "Old content")
        val newItem = MediaItem(2, MediaType.MOVIE, "Fresh content")
        repository.rows = listOf(seasonal, second)
        repository.items = { if (it == seasonal.id) listOf(oldItem) else emptyList() }
        settings.value = settings.value.copy(rowOrder = listOf(seasonal.id, second.id))
        runCurrent()
        home.onHomeResumed()
        runCurrent()
        assertEquals(listOf(seasonal, second), home.tmdbRows.value)
        assertEquals(RowUiState.Loaded(listOf(oldItem)), home.rowStates.value[seasonal.id])

        val updated = seasonal.copy(title = "Server renamed it", style = RowStyle.LANDSCAPE,
            artworkUrl = "https://images.example/new.jpg")
        repository.rows = listOf(second, updated)
        repository.items = { if (it == seasonal.id) listOf(newItem) else emptyList() }
        advanceTimeBy(60_000)
        runCurrent()
        assertEquals(listOf(second, updated), home.tmdbRows.value)
        assertEquals(RowUiState.Loaded(listOf(newItem)), home.rowStates.value[seasonal.id])
    }

    @Test
    fun staleManifestCannotOverwriteNewGenerationOrReappearAfterPause() = runHomeTest {
        val pending = CompletableDeferred<List<RowSpec>>()
        repository.manifest = { withContext(NonCancellable) { pending.await() } }
        runCurrent()
        home.onHomeResumed()
        runCurrent()
        val current = seasonal.copy(id = "seasonal_current")
        repository.manifest = { listOf(current) }
        advanceTimeBy(60_000)
        runCurrent()
        assertEquals(listOf(current), home.tmdbRows.value)
        pending.complete(listOf(seasonal))
        runCurrent()
        assertEquals(listOf(current), home.tmdbRows.value)

        val paused = CompletableDeferred<List<RowSpec>>()
        repository.manifest = { withContext(NonCancellable) { paused.await() } }
        advanceTimeBy(60_000)
        runCurrent()
        home.onHomePaused()
        paused.complete(listOf(seasonal))
        runCurrent()
        assertEquals(emptyList<RowSpec>(), home.tmdbRows.value)
    }

    @Test
    fun staleItemsCannotOverwriteSameRowIdAfterTimerRefresh() = runHomeTest {
        val pending = CompletableDeferred<List<MediaItem>>()
        val stale = MediaItem(1, MediaType.MOVIE, "Stale")
        val fresh = MediaItem(2, MediaType.MOVIE, "Fresh")
        repository.rows = listOf(seasonal)
        repository.items = { withContext(NonCancellable) { pending.await() } }
        runCurrent()
        home.onHomeResumed()
        runCurrent()
        repository.items = { listOf(fresh) }
        advanceTimeBy(60_000)
        runCurrent()
        assertEquals(RowUiState.Loaded(listOf(fresh)), home.rowStates.value[seasonal.id])
        pending.complete(listOf(stale))
        runCurrent()
        assertEquals(RowUiState.Loaded(listOf(fresh)), home.rowStates.value[seasonal.id])
    }

    @Test
    fun unavailableManifestAtTickHidesSeasonalRows() = runHomeTest {
        repository.rows = listOf(seasonal)
        runCurrent()
        home.onHomeResumed()
        runCurrent()
        repository.manifest = { error("Server unavailable") }
        advanceTimeBy(60_000)
        runCurrent()
        assertEquals(emptyList<RowSpec>(), home.tmdbRows.value)
    }

    private fun runHomeTest(block: suspend TestScope.() -> Unit) = scope.runTest {
        try {
            block()
        } finally {
            // viewModelScope is independent of runTest; cancel its repeating hero job
            // before runTest drains the scheduler, including on assertion failures.
            home.viewModelScope.cancel()
        }
    }

    private class SeasonalRepository : TmdbRepository by mock(TmdbRepository::class.java) {
        override val allRows = emptyList<RowSpec>()
        var rows = emptyList<RowSpec>()
        var manifestRequests = 0
        var manifest: suspend () -> List<RowSpec> = { rows }
        var items: suspend (String) -> List<MediaItem> = { emptyList() }
        var cancelledItems = 0
        override suspend fun seasonalRows(): List<RowSpec> {
            manifestRequests++
            return manifest()
        }
        override suspend fun rowItems(rowId: String, page: Int): List<MediaItem> = try {
            items(rowId)
        } catch (cancelled: CancellationException) {
            cancelledItems++
            throw cancelled
        }
    }
}
