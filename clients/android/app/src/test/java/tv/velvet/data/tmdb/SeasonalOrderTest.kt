package tv.velvet.data.tmdb

import org.junit.Assert.assertEquals
import org.junit.Test
import tv.velvet.ui.HomeRowOrder

class SeasonalOrderTest {
    @Test fun retiredOnlyOrderFallsBackToOrdinaryDefaults() {
        assertEquals(listOf("seasonal_new", "popular_now", "comedy"),
            HomeRowOrder.migrateAndAppend(listOf("holiday_christmas", "seasonal_expired"),
                listOf("seasonal_new", "popular_now", "comedy")))
    }

    @Test fun serverOrderOverridesStoredSeasonalOrderWithoutChangingDiscoveryPreference() {
        assertEquals(listOf("seasonal_new", "seasonal_first", "comedy", "popular_now"),
            HomeRowOrder.migrateAndAppend(
                listOf("comedy", "seasonal_first", "trending_movies", "seasonal_expired"),
                listOf("seasonal_new", "seasonal_first", "popular_now", "comedy", "action"),
            ))
    }
}
