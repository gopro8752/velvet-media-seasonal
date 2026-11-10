package tv.velvet.data.categories

import androidx.datastore.preferences.core.mutablePreferencesOf
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class HalloweenProfileOwnershipTest {
    @Test fun retiredCollectionCannotBeLaunchedByAnyProfile() {
        for (profile in listOf("default", "craig", "", null)) {
            assertFalse(canAccessCategory(profile, HALLOWEEN_CATEGORY_ID))
        }
        assertTrue(canAccessCategory("default", "ai-halloween"))
        assertTrue(canAccessCategory("craig", "ai-halloween"))
        assertFalse(canAccessCategory(null, "ai-halloween"))
    }

    @Test fun startupMigrationDeletesOnlyRetiredIdentityAcrossProfileCaches() {
        val legacy = Category(HALLOWEEN_CATEGORY_ID, "Monstober & Nick Halloween", emptyList())
        val personal = Category("ai-halloween", "Monstober & Nick Halloween", emptyList())
        val other = Category("ai-movies", "My movies", emptyList())
        val first = stringPreferencesKey("generated_first-profile")
        val second = stringPreferencesKey("generated_second-profile")
        val legacyKey = stringPreferencesKey("halloween")
        val unrelated = stringPreferencesKey("unrelated")
        val serializer = ListSerializer(Category.serializer())
        val preferences = mutablePreferencesOf(
            legacyKey to Json.encodeToString(Category.serializer(), legacy),
            first to Json.encodeToString(serializer, listOf(legacy, personal)),
            second to Json.encodeToString(serializer, listOf(other)),
            unrelated to "keep",
        )
        retireLegacyCategoryPreferences(preferences, Json)
        assertNull(preferences[legacyKey])
        assertEquals(listOf(personal), Json.decodeFromString(serializer, preferences[first]!!))
        assertEquals(listOf(other), Json.decodeFromString(serializer, preferences[second]!!))
        assertEquals("keep", preferences[unrelated])
        val once = preferences.toPreferences()
        retireLegacyCategoryPreferences(preferences, Json)
        assertEquals(once, preferences.toPreferences())
    }
}
