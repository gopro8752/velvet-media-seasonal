sub main()
    m.failures = 0
    resetHome()
    ' These server rows cannot both be active under any device-local calendar.
    m.catalogRows = [
        {id: "seasonal_surprise_2099", title: "Server special", style: "landscape", items: [{mediaId: 42, type: "movie"}], page: 1, totalPages: 1},
        {id: "holiday_valentines", title: "Server February", items: []},
        {id: "holiday_halloween", title: "Server October", items: []},
        {id: "trending", title: "Trending", items: []}
    ]
    composeHome()
    expect(m.model.rows.Count() = 4, "all server rows survive a mismatched local clock")
    if m.model.rows.Count() = 4
        expect(m.model.rows[0].id = "seasonal_surprise_2099" and m.model.rows[1].id = "holiday_valentines" and m.model.rows[2].id = "holiday_halloween", "server-defined arbitrary IDs and order are preserved")
        expect(m.model.rows[0].items[0].mediaId = 42, "inline items remain playable without category lookup")
    end if
    ' Cached home still revalidates on every entry, without a date query.
    navigate("home")
    expect(homeRequestCount() = 1, "home entry refreshes cached catalog")
    for each request in m.requestQueue
        if VStr(VGet(m.requests[request.id], "kind")) = "home" then expect(request.path = "/catalog/v1/home", "home uses additive server-authoritative endpoint without a device date")
    end for
    m.requests = {}
    m.requestQueue = []
    m.catalogDate = localDate()
    onSyncTimer()
    expect(homeRequestCount() = 1, "periodic home refresh does not wait for device midnight")
    for each request in m.requestQueue
        if VStr(VGet(m.requests[request.id], "kind")) = "home" then expect(request.path = "/catalog/v1/home", "timer also uses additive versioned home without a date")
    end for
    resetHome()
    m.catalogRows = [{id: "seasonal_new_server_id", items: []}, {id: "holiday_any_legacy", items: []}, {id: "popular_now", items: [{mediaId: 8}]}]
    composeHome()
    deliverResult("home", {}, false, invalid, 404)
    expect(m.catalogRows.Count() = 1 and m.catalogRows[0].id = "popular_now", "old backend 404 drops seasonal namespaces but retains nonseasonal cache")
    expect(m.model.rows.Count() = 1 and m.model.message = "offline", "failure immediately republishes safe rows and existing error")
    composeHome()
    expect(m.model.rows.Count() = 1, "later sync composition cannot resurrect expired seasonal rows")
    ' A cold start against an old backend still loads ordinary discovery.
    resetHome()
    m.categories = [{id: "saved-personal", title: "Personal", items: []}]
    navigate("home")
    completeHome(404, {})
    expect(homeRequestCount() = 1, "unsupported v1 queues one legacy fallback on cold start")
    for each request in m.requestQueue
        if VStr(VGet(m.requests[request.id], "kind")) = "home"
            expect(request.path = "/catalog/home", "legacy fallback never supplies a device date")
            expect(request.method = "GET" and request.timeout = 40000, "fallback retains home request policy")
        end if
    end for
    onSyncTimer()
    expect(homeRequestCount() = 1, "timer coalesces with the legacy fallback")
    completeHome(200, {rows: [{id: "holiday_never_known", items: []}, {id: "popular_now", items: [{mediaId: 8}]}]})
    expect(m.catalogRows.Count() = 1, "fallback structurally discards legacy holiday rows")
    if m.catalogRows.Count() = 1 then expect(m.catalogRows[0].id = "popular_now", "cold start retains ordinary discovery from the old server")
    expect(m.model.rows.Count() = 2 and m.model.rows[0].id = "saved-personal", "fallback preserves profile-saved categories")
    onSyncTimer()
    for each request in m.requestQueue
        if VStr(VGet(m.requests[request.id], "kind")) = "home" then expect(request.path = "/catalog/v1/home", "next refresh probes v1 again")
    end for
    completeHome(404, {})
    completeHome(404, {})
    expect(homeRequestCount() = 0, "legacy fallback failure cannot recurse")
    resetHome()
    m.catalogRows = [{id: "seasonal_expired"}, {id: "holiday_unknown"}, {id: "popular_cached"}]
    navigate("home")
    completeHome(500, {})
    expect(homeRequestCount() = 0, "transient v1 failure never falls back to legacy seasons")
    expect(m.catalogRows.Count() = 1 and m.catalogRows[0].id = "popular_cached", "transient v1 failure hides seasons but preserves general cache")
    navigate("home")
    completeHome(401, {})
    expect(homeRequestCount() = 0, "authentication failure never triggers legacy fallback")
    ' Partial failures must not pass stale seasonal rows through a successful HTTP response.
    deliverResult("home", {rows: [{id: "seasonal_stale", stale: true}, {id: "seasonal_failed"}, {id: "seasonal_fresh"}, {id: "trending", stale: true}], failedRows: ["seasonal_failed"], message: "partial"}, true)
    expect(m.catalogRows.Count() = 2, "partial home omits failed and stale seasonal rows only")
    if m.catalogRows.Count() = 2 then expect(m.catalogRows[0].id = "seasonal_fresh" and m.catalogRows[1].id = "trending", "fresh season and stale nonseasonal rows retain server order")
    deliverResult("home", {rows: [{id: "seasonal_replacement"}], effectiveDate: "2099-01-01", timezone: "UTC"}, true)
    expect(m.catalogRows.Count() = 1 and m.catalogRows[0].id = "seasonal_replacement", "successful manifest replaces cache rather than merging removed seasons")
    resetHome()
    m.catalogRows = [{id: "seasonal_current", items: []}]
    m.model = {page: "details"}
    m.history = [{page: "home", rows: [{id: "seasonal_expired_snapshot"}]}]
    goBack()
    expect(homeRequestCount() = 1, "returning from details also refreshes home authority")
    expect(m.model.rows.Count() = 1 and m.model.rows[0].id = "seasonal_current", "back never restores a detached seasonal snapshot")
    onSyncTimer()
    onSyncTimer()
    expect(homeRequestCount() = 1, "slow home requests do not overlap across timer ticks")
    m.generation += 1
    onSyncTimer()
    expect(homeRequestCount() = 2, "an old page request cannot suppress the current home refresh")
    resetHome()
    m.sync.progress = [{profileId: "profile-a", mediaId: 1, positionMs: 2000, durationMs: 100000}]
    m.sync.mylist = [{profileId: "profile-a", mediaId: 2}]
    m.categories = [{id: "saved-personal", title: "Personal", items: []}]
    m.preferences.layouts[preferenceLayoutKey()] = {order: ["popular", "seasonal_alpha", "mylist", "seasonal_beta", "saved-personal", "continue"], hidden: []}
    deliverResult("home", {rows: [{id: "seasonal_beta", title: "Beta"}, {id: "seasonal_alpha", title: "Alpha"}, {id: "popular", title: "Popular"}]}, true)
    expect(homeRowIds() = "mylist,saved-personal,continue,seasonal_beta,seasonal_alpha,popular", "stored moves cannot override server seasonal order or its placement after personal rows")
    deliverResult("home", {rows: [{id: "seasonal_alpha", title: "Alpha"}, {id: "seasonal_beta", title: "Beta"}, {id: "popular", title: "Popular"}]}, true)
    expect(homeRowIds() = "mylist,saved-personal,continue,seasonal_alpha,seasonal_beta,popular", "a reordered server manifest immediately supersedes the stored seasonal layout")
    m.preferences.layouts[preferenceLayoutKey()].hidden = ["seasonal_alpha"]
    composeHome()
    expect(homeRowIds() = "mylist,saved-personal,continue,seasonal_beta,popular", "explicit seasonal hide preferences remain effective")
    preferenceRowsMenu()
    known = preferenceKnownRows()
    expect(known[3].id = "seasonal_alpha" and known[4].id = "seasonal_beta", "settings lists seasonal rows in server order too")
    for each entry in m.preferenceActions
        if VStr(VGet(entry, "rowId")) = "seasonal_beta" then expect(Instr(1, VStr(entry.subtitle), "server") > 0, "settings explains server-managed seasonal ordering")
    end for
    m.preferenceRow = "seasonal_beta"
    preferenceRowMenu()
    for each entry in m.preferenceActions
        expect(entry.command <> "moveRow", "seasonal settings never offers move commands")
    end for
    expect(m.preferenceActions[0].command = "toggleRow", "seasonal visibility remains explicitly customizable")
    savedOrder = FormatJson(preferenceLayout().order)
    m.preferenceActions = [{command: "moveRow", direction: -1}]
    handlePreferencesChoice(0)
    expect(FormatJson(preferenceLayout().order) = savedOrder, "even a stale seasonal move command cannot rewrite stored layout")
    m.preferenceRow = "popular"
    preferenceRowMenu()
    hasMove = false
    for each entry in m.preferenceActions
        if entry.command = "moveRow" then hasMove = true
    end for
    expect(hasMove, "ordinary discovery rows retain move controls")
    ' Regression checks for the existing profile-scoped personal collection path.
    resetHome()
    m.categories = [{id: "saved-a", title: "A collection", items: [{mediaId: 1}]}]
    m.view.action = {kind: "selectProfile", profileId: "profile-b"}
    onViewAction()
    expect(m.categories.Count() = 0 and categoryRows().Count() = 0, "profile switch clears the previous profile categories immediately")
    deliverResult("savedCategories", {categories: [{id: "late-a", title: "Old profile"}]}, true, {profileId: "profile-a"})
    expect(m.categories.Count() = 0, "late saved-category responses cannot cross profiles")
    personal = {id: "archived-generated-collection", title: "My saved episodes", items: [{mediaId: 7, type: "show", season: 2, episode: 3}]}
    deliverResult("savedCategories", {categories: [personal]}, true, {profileId: "profile-b"})
    expect(categoryRows().Count() = 1 and categoryRows()[0].category, "saved and archived personal collections retain collection controls")
    prepareCategory(personal.id, false)
    prepareCategory(personal.id, true)
    queueRequests = []
    for each request in m.requestQueue
        if m.requests[request.id].kind = "queue" then queueRequests.Push(request)
    end for
    expect(queueRequests.Count() = 2, "Play all and Shuffle keep using the personal category queue")
    if queueRequests.Count() = 2
        expect(not queueRequests[0].body.shuffle and queueRequests[1].body.shuffle, "queue requests preserve shuffle choice")
        expect(queueRequests[1].body.items[0].episode = 3, "episode metadata survives the queue path")
    end if
    if m.failures = 0 then print "VELVET_TEST_PASS"
end sub

sub resetHome()
    m.profileId = "profile-a"
    m.sync = {profiles: [], progress: [], mylist: []}
    m.categories = []
    m.catalogRows = []
    m.model = {page: "home", busy: false, message: ""}
    m.view = {CallFunc: viewCallFunc}
    m.player = {visible: false}
    m.baseUrl = "https://backend.example.test"
    m.token = ""
    m.registry = {Write: registryWrite, Flush: registryFlush}
    m.playGeneration = 0
    m.preferences = {quality: "2160p", layouts: {}}
    m.preferenceAvailableRows = []
    m.top = {}
    m.generation = 0
    m.backendEpoch = 0
    m.requestSequence = 0
    m.requests = {}
    m.requestQueue = []
    m.failedPushes = []
    ' Keep network work queued rather than constructing SceneGraph Tasks.
    m.tasks = [{request: {id: "platform-1"}}, {request: {id: "platform-2"}}, {request: {id: "platform-3"}}]
end sub

function homeRowIds() as string
    ids = []
    for each row in m.model.rows
        ids.Push(row.id)
    end for
    return ids.Join(",")
end function

function homeRequestCount() as integer
    count = 0
    for each id in m.requests
        if m.requests[id].kind = "home" then count += 1
    end for
    return count
end function

sub closeCategoryComposer(restore as boolean)
end sub

sub closeLiveBrowser()
end sub

sub closeSportsBrowser()
end sub

' Only platform/UI boundaries are replaced; controllers are production code.
sub registryWrite(key as string, value as string)
end sub

sub registryFlush()
end sub

sub viewCallFunc(name as string)
end sub

' Complete a real queued home request, preserving its production context.
sub completeHome(status as integer, data as object)
    id = ""
    for each pendingId in m.requests
        if m.requests[pendingId].kind = "home" then id = pendingId
    end for
    if id = ""
        expect(false, "expected a queued home request to complete")
        return
    end if
    for i = m.requestQueue.Count() - 1 to 0 step -1
        if m.requestQueue[i].id = id then m.requestQueue.Delete(i)
    end for
    event = {response: {id: id, ok: status = 200, status: status, data: data, error: "offline"}, GetData: resultEventData}
    onApiResult(event)
end sub

sub deliverResult(kind as string, data as object, ok as boolean, context = invalid as dynamic, status = 200 as integer)
    m.requests["response"] = {kind: kind, generation: m.generation, epoch: m.backendEpoch, context: context}
    event = {response: {id: "response", ok: ok, status: status, data: data, error: "offline"}, GetData: resultEventData}
    onApiResult(event)
end sub

function resultEventData() as object
    return m.response
end function

function handleCategoryResponse(kind as string, result as object, context as dynamic) as boolean
    return false
end function

function handleSportsBrowserError(kind as string, message as string, context as dynamic) as boolean
    return false
end function

function handleLiveBrowserError(kind as string, message as string) as boolean
    return false
end function

function handleLiveBrowserResponse(kind as string, data as object) as boolean
    return false
end function

function handleSportsBrowserResponse(kind as string, data as object, context as dynamic) as boolean
    return false
end function

sub expect(condition as boolean, reason as string)
    if not condition
        m.failures += 1
        print "VELVET_TEST_FAIL: " + reason
    end if
end sub
