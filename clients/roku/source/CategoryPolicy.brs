' The former hard-coded Monstober category has been retired for every profile.
function VHasLegacyHalloween(profileId as string) as boolean
    return false
end function

function VLegacyHalloweenResponseCurrent(context as dynamic, profileId as string) as boolean
    return false
end function

function VSeasonalMovieRowActive(id as string, month as integer, day as integer) as boolean
    if id = "holiday_halloween" then return month >= 8 and month <= 10
    if id = "holiday_thanksgiving" then return month = 11
    if id = "holiday_christmas" then return month = 11 or month = 12
    if id = "holiday_newyear" then return (month = 12 and day >= 26) or (month = 1 and day <= 5)
    if id = "holiday_valentines" then return month = 2 and day <= 15
    return true
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
