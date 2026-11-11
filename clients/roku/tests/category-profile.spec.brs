sub main()
    m.failures = 0
    halloween = {id: "halloween-nostalgia", title: "Monstober & Nick Halloween", items: [{mediaId: 1}]}
    generated = {id: "new-halloween-collection", title: "Monstober & Nick Halloween", items: [{mediaId: 2}]}
    expect(not VHasLegacyHalloween("default"), "original profile no longer fetches retired category")
    expect(not VHasLegacyHalloween("another-profile"), "other profiles never fetch retired category")
    visible = VProfileCategories([halloween, generated], halloween, "default")
    expect(visible.Count() = 1 and visible[0].id = generated.id, "retired ID is removed without deleting a different personal category with the same title")
    visible = VProfileCategories([generated], halloween, "another-profile")
    expect(visible.Count() = 1 and visible[0].id = generated.id, "other profile keeps its own saved categories")
    visible = VProfileCategories([generated], halloween, "")
    expect(visible.Count() = 0, "no profile exposes no categories")
    expect(not VLegacyHalloweenResponseCurrent({profileId: "default"}, "default"), "late retired category and queue responses cannot revive it")
    ' Activation belongs to the server; client policy only evicts unverified cache.
    safe = VCatalogRowsWithoutSeasonal([{id: "seasonal_unknown"}, {id: "holiday_unknown"}, generated])
    expect(safe.Count() = 1 and safe[0].id = generated.id, "failed seasonal catalog never removes a personal category by title")
    if m.failures = 0 then print "VELVET_TEST_PASS"
end sub

sub expect(condition as boolean, reason as string)
    if not condition
        m.failures += 1
        print "VELVET_TEST_FAIL: " + reason
    end if
end sub
