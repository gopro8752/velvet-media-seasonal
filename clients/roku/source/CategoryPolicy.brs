' The former hard-coded Monstober category has been retired for every profile.
function VHasLegacyHalloween(profileId as string) as boolean
    return false
end function

function VLegacyHalloweenResponseCurrent(context as dynamic, profileId as string) as boolean
    return false
end function

' Namespace detection is only for fail-closed cache eviction, never activation.
function VIsSeasonalCatalogRow(row as dynamic) as boolean
    id = VStr(VGet(row, "id"))
    return Left(id, 9) = "seasonal_" or Left(id, 8) = "holiday_"
end function

function VCatalogRowsFromResponse(data as dynamic) as object
    output = []
    for each row in VArray(VGet(data, "rows"))
        failed = VBool(VGet(row, "stale"))
        for each id in VArray(VGet(data, "failedRows"))
            if VStr(id) = VStr(VGet(row, "id")) then failed = true
        end for
        if not VIsSeasonalCatalogRow(row) or not failed then output.Push(row)
    end for
    return output
end function

function VCatalogRowsWithoutSeasonal(rows as dynamic) as object
    output = []
    for each row in VArray(rows)
        if not VIsSeasonalCatalogRow(row) then output.Push(row)
    end for
    return output
end function

' Rendering and Play all/Shuffle share the same saved, profile-scoped category list.
function VProfileCategories(categories as dynamic, halloween as dynamic, profileId as string) as object
    output = []
    if profileId = "" then return output
    for each category in VArray(categories)
        if VStr(VGet(category, "id")) <> "halloween-nostalgia" then output.Push(category)
    end for
    return output
end function
