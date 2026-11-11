package tv.velvet.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import tv.velvet.data.model.RowSpec
import tv.velvet.data.model.StreamSourceConfig
import tv.velvet.data.settings.SettingsRepository
import tv.velvet.data.settings.VelvetSettings

import tv.velvet.di.AppContainer
import tv.velvet.ui.HomeRowOrder

/** One Home row as displayed in Settings: enabled rows first (in user order), then disabled. */
data class HomeRowEntry(
    val spec: RowSpec,
    val enabled: Boolean,
    val canMoveUp: Boolean,
    val canMoveDown: Boolean,
    val serverManaged: Boolean = false,
)

data class SettingsUiState(
    val loaded: Boolean = false,
    val settings: VelvetSettings = VelvetSettings(
        tmdbApiKey = SettingsRepository.DEFAULT_TMDB_KEY,
        streamSources = emptyList(),
        rowOrder = emptyList(),
        autoplayNext = true,
        autoplayCountdownSecs = 8,
        preferredQuality = "2160p",
        tunneledPlayback = false,
    ),
    val rowEntries: List<HomeRowEntry> = emptyList(),
)

class SettingsViewModel(container: AppContainer) : ViewModel() {

    private val settingsRepository = container.settingsRepository
    private val syncEngine = container.syncEngine

    /** Last-sync result/time line shown under the "Sync now" row. */
    val syncStatus: StateFlow<String> = syncEngine.status

    private val tmdb = container.tmdbRepository

    private var seasonalRows: List<RowSpec> = emptyList()
    private val allRows: List<RowSpec> get() = seasonalRows + tmdb.allRows

    private val _state = MutableStateFlow(SettingsUiState(rowEntries = buildRowEntries(emptyList())))
    val state: StateFlow<SettingsUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            combine(settingsRepository.settings, settingsRepository.activeProfileId) { s, profile -> s to profile }
                .collectLatest { (s, _) ->
                    seasonalRows = emptyList()
                    apply(s, loaded = true)
                    seasonalRows = try { tmdb.seasonalRows() }
                    catch (cancelled: CancellationException) { throw cancelled }
                    catch (_: Exception) { emptyList() }
                    apply(_state.value.settings, loaded = true)
                }
        }
    }

    // ---------------------------------------------------------------- sources

    fun toggleSource(index: Int) {
        if (!_state.value.loaded) return
        val sources = _state.value.settings.streamSources
        if (index !in sources.indices) return
        persistSources(
            sources.mapIndexed { i, s -> if (i == index) s.copy(enabled = !s.enabled) else s }
        )
    }

    fun removeSource(index: Int) {
        if (!_state.value.loaded) return
        val sources = _state.value.settings.streamSources
        if (index !in sources.indices) return
        persistSources(sources.filterIndexed { i, _ -> i != index })
    }

    fun addSource(name: String, url: String) {
        if (!_state.value.loaded) return
        val baseUrl = normalizeSourceUrl(url)
        if (baseUrl.isEmpty()) return
        val sources = _state.value.settings.streamSources
        // No duplicate URLs: querying the same source twice is never useful, and
        // baseUrl doubles as the stable list key in the Settings UI.
        if (sources.any { it.baseUrl.equals(baseUrl, ignoreCase = true) }) return
        val displayName = name.trim().ifEmpty { "Source ${sources.size + 1}" }
        persistSources(sources + StreamSourceConfig(name = displayName, baseUrl = baseUrl, enabled = true))
    }

    private fun persistSources(sources: List<StreamSourceConfig>) {
        apply(_state.value.settings.copy(streamSources = sources))
        viewModelScope.launch { settingsRepository.setStreamSources(sources) }
    }

    // -------------------------------------------------------------- home rows

    /** Move an enabled row up (-1) or down (+1) within the effective order. Persists immediately. */
    fun moveRow(rowId: String, delta: Int) {
        if (rowId.startsWith("seasonal_")) return
        if (!_state.value.loaded) return
        val order = effectiveEnabledOrder()
        val from = order.indexOf(rowId)
        if (from < 0) return // disabled rows have no persistable position
        val to = from + delta
        if (to !in order.indices) return
        val next = order.toMutableList().apply { add(to, removeAt(from)) }
        persistRowOrder(next)
    }

    /**
     * Include/exclude a row from rowOrder. When the persisted order is empty (= "all
     * enabled" default), the full default list is materialized first, then modified.
     * Re-enabled rows are appended to the end of the enabled list.
     */
    fun toggleRow(rowId: String) {
        if (rowId.startsWith("seasonal_")) return
        if (!_state.value.loaded) return
        val order = effectiveEnabledOrder().toMutableList()
        if (rowId in order) {
            // Never allow zero enabled rows: an empty persisted order means "all enabled",
            // so removing the last entry would paradoxically re-enable everything.
            if (order.size == 1) return
            order.remove(rowId)
        } else {
            if (allRows.none { it.id == rowId }) return
            order.add(rowId)
        }
        persistRowOrder(order)
    }

    fun resetRowOrder() {
        persistRowOrder(emptyList())
    }

    private fun effectiveEnabledOrder(): List<String> =
        enabledOrder(_state.value.settings.rowOrder).filterNot { it.startsWith("seasonal_") }

    /** Same server-owned seasonal order as Home; only ordinary ids are persisted. */
    private fun enabledOrder(rowOrder: List<String>): List<String> =
        HomeRowOrder.migrateAndAppend(rowOrder, allRows.map { it.id })

    private fun persistRowOrder(order: List<String>) {
        apply(_state.value.settings.copy(rowOrder = order))
        viewModelScope.launch { settingsRepository.setRowOrder(order) }
    }

    private fun buildRowEntries(rowOrder: List<String>): List<HomeRowEntry> {
        val byId = allRows.associateBy { it.id }
        val enabledSpecs = enabledOrder(rowOrder).mapNotNull { byId[it] }
        val enabledIds = enabledSpecs.map { it.id }.toSet()
        val disabledSpecs = allRows.filter { it.id !in enabledIds }
        return enabledSpecs.mapIndexed { i, spec ->
            val managed = spec.id.startsWith("seasonal_")
            HomeRowEntry(
                spec = spec,
                enabled = true,
                canMoveUp = !managed && i > seasonalRows.size,
                canMoveDown = !managed && i < enabledSpecs.lastIndex,
                serverManaged = managed,
            )
        } + disabledSpecs.map { spec ->
            HomeRowEntry(spec = spec, enabled = false, canMoveUp = false, canMoveDown = false)
        }
    }

    // --------------------------------------------------------------- playback

    fun setAutoplayNext(enabled: Boolean) {
        apply(_state.value.settings.copy(autoplayNext = enabled))
        viewModelScope.launch { settingsRepository.setAutoplayNext(enabled) }
    }

    fun setCountdownSecs(secs: Int) {
        apply(_state.value.settings.copy(autoplayCountdownSecs = secs))
        viewModelScope.launch { settingsRepository.setAutoplayCountdownSecs(secs) }
    }

    fun setPreferredQuality(quality: String) {
        apply(_state.value.settings.copy(preferredQuality = quality))
        viewModelScope.launch { settingsRepository.setPreferredQuality(quality) }
    }

    fun setTunneledPlayback(enabled: Boolean) {
        apply(_state.value.settings.copy(tunneledPlayback = enabled))
        viewModelScope.launch { settingsRepository.setTunneledPlayback(enabled) }
    }

    // ---------------------------------------------------------------- backend

    /** The single URL that drives streaming + sync (writes both under the hood). */
    fun setBackendUrl(url: String) {
        val trimmed = url.trim().trimEnd('/')
        viewModelScope.launch { settingsRepository.setBackendUrl(trimmed) }
    }

    // ------------------------------------------------------------------- sync

    fun setSyncEnabled(enabled: Boolean) {
        apply(_state.value.settings.copy(syncEnabled = enabled))
        viewModelScope.launch { settingsRepository.setSyncEnabled(enabled) }
    }

    /** Blank is allowed — it leaves the toggle on but renders the engine inert. */
    fun setSyncUrl(url: String) {
        val trimmed = url.trim().trimEnd('/')
        apply(_state.value.settings.copy(syncUrl = trimmed))
        viewModelScope.launch { settingsRepository.setSyncUrl(trimmed) }
    }

    fun setSyncToken(token: String) {
        val trimmed = token.trim()
        apply(_state.value.settings.copy(syncToken = trimmed))
        viewModelScope.launch { settingsRepository.setSyncToken(trimmed) }
    }

    fun syncNow() = syncEngine.syncNow()

    // ------------------------------------------------------------------- tmdb

    fun setTmdbApiKey(key: String) {
        val trimmed = key.trim()
        if (trimmed.isEmpty()) return
        apply(_state.value.settings.copy(tmdbApiKey = trimmed))
        viewModelScope.launch { settingsRepository.setTmdbApiKey(trimmed) }
    }

    // ---------------------------------------------------------------- helpers

    /**
     * Optimistic update; the DataStore flow re-emits the same values right after.
     * The stored row order is migrated on READ (trending_movies -> popular_now), so a
     * hand-tuned order survives the row rename and re-persists on the next edit. Only the
     * RENAME is applied to the mirrored value — [enabledOrder] resolves server seasonal
     * rows for display only, never fabricating a persisted preference.
     */
    private fun apply(settings: VelvetSettings, loaded: Boolean = _state.value.loaded) {
        val migrated = settings.copy(rowOrder = HomeRowOrder.migrate(settings.rowOrder))
        _state.value = SettingsUiState(
            loaded = loaded,
            settings = migrated,
            rowEntries = buildRowEntries(migrated.rowOrder),
        )
    }

    companion object {
        /** Trim whitespace, strip a trailing /manifest.json and any trailing slashes. */
        fun normalizeSourceUrl(raw: String): String {
            var url = raw.trim().trimEnd('/')
            if (url.endsWith("/manifest.json", ignoreCase = true)) {
                url = url.dropLast("/manifest.json".length)
            }
            return url.trimEnd('/')
        }
    }
}
