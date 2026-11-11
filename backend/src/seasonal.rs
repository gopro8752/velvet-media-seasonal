//! Additive seasonal catalog. Legacy routes and their client-date semantics are unchanged.
use axum::{extract::{Path, Query, State}, http::{header, StatusCode}, response::{IntoResponse, Response}, Json};
use chrono::{Datelike, NaiveDate, Utc};
use futures::{stream, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{collections::{HashMap, HashSet}, path::PathBuf, sync::Arc};
use tokio::io::AsyncReadExt;

const MAX_BYTES: u64 = 128 * 1024;
#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Manifest { schema_version: u8, rows: Vec<Season> }
#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Season {
    id: String, title: String, style: String, enabled: bool,
    start: [u32; 2], end: [u32; 2],
    #[serde(default)] artwork_url: Option<String>,
    source: Source,
}
#[derive(Clone, Deserialize)]
#[serde(tag = "type", rename_all = "camelCase", deny_unknown_fields)]
enum Source {
    Items { items: Vec<CuratedItem> },
    Movies { keyword: u32, #[serde(default)] genres: Vec<u32> },
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CuratedItem {
    media_id: u32, r#type: String, title: String,
    #[serde(default)] year: Option<String>,
    #[serde(default)] rating: Option<f64>,
    #[serde(default)] poster_url: Option<String>,
    #[serde(default)] backdrop_url: Option<String>,
    #[serde(default)] logo_url: Option<String>,
    #[serde(default)] overview: Option<String>,
    #[serde(default)] genres: Vec<String>,
    #[serde(default)] runtime_minutes: Option<u32>,
    #[serde(default)] imdb_id: Option<String>,
}
impl CuratedItem {
    fn valid(&self) -> bool {
        self.media_id > 0 && self.media_id <= i32::MAX as u32 && matches!(self.r#type.as_str(), "movie" | "show")
            && !self.title.trim().is_empty() && self.title.chars().count() <= 400 && !self.title.chars().any(char::is_control)
            && self.year.as_ref().is_none_or(|y| y.len() <= 20)
            && self.rating.is_none_or(|r| r.is_finite() && (0.0..=10.0).contains(&r))
            && [&self.poster_url, &self.backdrop_url, &self.logo_url].iter().all(|u| u.as_deref().is_none_or(valid_art))
            && self.overview.as_ref().is_none_or(|s| s.chars().count() <= 12000)
            && self.genres.len() <= 20 && self.genres.iter().all(|s| s.chars().count() <= 80)
            && self.runtime_minutes.is_none_or(|r| r <= 10000)
            && self.imdb_id.as_ref().is_none_or(|s| s.starts_with("tt") && (7..=12).contains(&s.len()) && s[2..].bytes().all(|b|b.is_ascii_digit()))
    }
}
impl Season {
    fn active(&self, day: NaiveDate) -> bool {
        let md = [day.month(), day.day()];
        self.enabled && if self.start <= self.end { self.start <= md && md <= self.end } else { md >= self.start || md <= self.end }
    }
    fn descriptor(&self) -> Value { json!({"id":self.id,"title":self.title,"style":self.style,"artworkUrl":self.artwork_url}) }
}
fn valid_id(id: &str) -> bool {
    id.starts_with("seasonal_") && (10..=80).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"_-".contains(&b))
}
fn valid_art(url: &str) -> bool {
    url.strip_prefix("https://image.tmdb.org/t/p/").is_some_and(|path| !path.is_empty() && path.len() <= 300 && !path.contains("..") && path.bytes().all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b)))
}
fn parse(bytes: &[u8]) -> Result<Manifest, ()> {
    let manifest: Manifest = serde_json::from_slice(bytes).map_err(|_| ())?;
    if manifest.schema_version != 1 || manifest.rows.len() > 16 { return Err(()); }
    let mut ids = HashSet::new();
    for row in &manifest.rows {
        if !valid_id(&row.id) || !ids.insert(&row.id) || row.title.trim().is_empty() || row.title.chars().count() > 160 || row.title.chars().any(char::is_control)
            || !matches!(row.style.as_str(), "poster" | "landscape")
            || NaiveDate::from_ymd_opt(2000, row.start[0], row.start[1]).is_none()
            || NaiveDate::from_ymd_opt(2000, row.end[0], row.end[1]).is_none()
            || row.artwork_url.as_deref().is_some_and(|url| !valid_art(url)) { return Err(()); }
        match &row.source {
            Source::Items { items } if items.len() <= 400 && items.iter().all(CuratedItem::valid) && { let mut seen = HashSet::new(); items.iter().all(|i| seen.insert((&i.r#type, i.media_id))) } => {},
            Source::Movies { keyword, genres } if *keyword > 0 && *keyword <= i32::MAX as u32 && genres.len() <= 8 && genres.iter().all(|g| *g > 0 && *g <= i32::MAX as u32) => {},
            _ => return Err(()),
        }
    }
    Ok(manifest)
}
async fn load() -> Result<(Manifest, String), ()> {
    let explicit = std::env::var_os("VELVET_SEASONAL_MANIFEST");
    let path = explicit.clone().map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var_os("VELVET_CONFIG").unwrap_or_else(|| "velvet.toml".into())).with_file_name("seasonal.json")
    });
    let bytes = match tokio::fs::File::open(path).await {
        Ok(file) => { let mut bytes = Vec::new(); file.take(MAX_BYTES + 1).read_to_end(&mut bytes).await.map_err(|_| ())?; if bytes.len() as u64 > MAX_BYTES { return Err(()); } bytes },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound && explicit.is_none() => include_bytes!("../seasonal.default.json").to_vec(),
        Err(_) => return Err(()),
    };
    Ok((parse(&bytes)?, format!("{:x}", md5::compute(&bytes))))
}
fn response(value: Value) -> Response { ([(header::CACHE_CONTROL, "no-store")], Json(value)).into_response() }
fn unavailable() -> Response { crate::catalog::error(StatusCode::SERVICE_UNAVAILABLE, "Seasonal configuration unavailable.") }
fn valid_query(q: &HashMap<String, String>, paging: bool) -> bool {
    q.iter().all(|(key, value)| match key.as_str() {
        // Curation is global. This selector cannot read or write profile-owned state.
        "profileId" => !value.is_empty() && value.len() <= 128 && value.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
        "page" => paging,
        _ => false,
    })
}
pub async fn discovery(Query(q): Query<HashMap<String, String>>) -> Response {
    if !valid_query(&q, false) { return crate::catalog::error(StatusCode::BAD_REQUEST, "Invalid seasonal request."); }
    let Ok((manifest, revision)) = load().await else { return unavailable(); };
    let day = Utc::now().date_naive();
    response(json!({"schemaVersion":1,"revision":revision,"effectiveDate":day.to_string(),"timezone":"UTC","rows":manifest.rows.iter().filter(|r| r.active(day)).map(Season::descriptor).collect::<Vec<_>>()}))
}

pub async fn row(State(st): State<Arc<crate::catalog::CatalogState>>, Path(id): Path<String>, Query(q): Query<HashMap<String, String>>) -> Response {
    let page = q.get("page").map(String::as_str).unwrap_or("1");
    let page = match page.parse::<usize>().ok().filter(|p| (1..=20).contains(p) && p.to_string() == page) {
        Some(page) if valid_query(&q, true) => page,
        _ => return crate::catalog::error(StatusCode::BAD_REQUEST, "Invalid seasonal request."),
    };
    let Ok((manifest, revision)) = load().await else { return unavailable(); };
    let day = Utc::now().date_naive();
    let Some(row) = manifest.rows.iter().find(|r| r.id == id && r.active(day)) else { return crate::catalog::error(StatusCode::NOT_FOUND, "Seasonal collection unavailable."); };
    match load_row(&st, row, page, day, &revision).await {
        Ok(value) => response(value),
        Err(error) => error,
    }
}
async fn load_row(st: &crate::catalog::CatalogState, row: &Season, page: usize, day: NaiveDate, revision: &str) -> Result<Value, Response> {
    let (items, total_pages, stale) = match &row.source {
        Source::Items { items } => (json!(items.iter().skip((page - 1) * 20).take(20).collect::<Vec<_>>()), items.len().div_ceil(20) as u64, false),
        Source::Movies { keyword, genres } => crate::catalog_browse::seasonal_movies(st, *keyword, genres, page as u64, day).await?,
    };
    let mut value = row.descriptor();
    value.as_object_mut().unwrap().extend(json!({"schemaVersion":1,"revision":revision,"effectiveDate":day.to_string(),"timezone":"UTC","items":items,"page":page,"totalPages":total_pages,"stale":stale}).as_object().unwrap().clone());
    Ok(value)
}

pub async fn home(State(st): State<Arc<crate::catalog::CatalogState>>, Query(q): Query<HashMap<String, String>>) -> Response {
    if !valid_query(&q, false) { return crate::catalog::error(StatusCode::BAD_REQUEST, "Invalid seasonal request."); }
    let day = Utc::now().date_naive();
    let mut rows = Vec::new();
    let mut failed = Vec::new();
    let mut revision = String::new();
    match load().await {
        Ok((manifest, rev)) => {
            revision = rev;
            let requested: Vec<_> = manifest.rows.into_iter().filter(|r| r.active(day)).collect();
            let loaded: Vec<_> = stream::iter(requested.into_iter().map(|row| {
                let st = st.clone(); let revision = revision.clone();
                async move { let result = load_row(&st, &row, 1, day, &revision).await; (row.id, result) }
            })).buffered(4).collect().await;
            for (id, result) in loaded { match result { Ok(row) => rows.push(row), Err(_) => failed.push(id.clone()) } }
        }
        Err(_) => failed.push("seasonal_configuration".into()),
    }
    for (id, result) in crate::catalog_browse::general_home_rows(&st, day).await {
        match result { Ok(row) => rows.push(row), Err(_) => failed.push(id.to_owned()) }
    }
    if rows.is_empty() && !failed.is_empty() { return crate::catalog::error(StatusCode::BAD_GATEWAY, "The catalog could not be loaded. Try again."); }
    let message = if failed.is_empty() { None } else { Some("Some rows could not be loaded. Try again.") };
    response(json!({"schemaVersion":1,"revision":revision,"effectiveDate":day.to_string(),"timezone":"UTC","rows":rows,"failedRows":failed,"message":message}))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Value { json!({"schemaVersion":1,"rows":[{"id":"seasonal_meteor","title":"Meteor","enabled":true,"style":"poster","start":[1,1],"end":[12,31],"source":{"type":"items","items":[]}}]}) }
    #[test]
    fn manifest_refuses_untrusted_media_and_credential_destinations() {
        for item in [json!({"mediaId":0,"type":"movie","title":"Bad"}), json!({"mediaId":1,"type":"person","title":"Bad"}), json!({"mediaId":1,"type":"movie","title":"Bad","posterUrl":"https://evil.example/a.jpg"}), json!({"mediaId":1,"type":"movie","title":"Bad","source":"https://evil.example/"})] {
            let mut doc = fixture(); doc["rows"][0]["source"]["items"] = json!([item]);
            assert!(parse(&serde_json::to_vec(&doc).unwrap()).is_err(), "{doc}");
        }
    }
    #[test]
    fn defaults_preserve_overlap_year_boundary_and_leap_eligibility() {
        let manifest = parse(include_bytes!("../seasonal.default.json")).unwrap();
        let ids = |raw: &str| { let day = NaiveDate::parse_from_str(raw, "%Y-%m-%d").unwrap(); manifest.rows.iter().filter(|r|r.active(day)).map(|r|r.id.as_str()).collect::<Vec<_>>() };
        assert_eq!(ids("2026-07-31"), Vec::<&str>::new());
        assert_eq!(ids("2026-08-01"), ["seasonal_halloween"]);
        assert_eq!(ids("2026-10-31"), ["seasonal_halloween"]);
        assert_eq!(ids("2026-11-01"), ["seasonal_thanksgiving", "seasonal_christmas"]);
        assert_eq!(ids("2026-11-30"), ["seasonal_thanksgiving", "seasonal_christmas"]);
        assert_eq!(ids("2026-12-01"), ["seasonal_christmas"]);
        assert_eq!(ids("2026-12-26"), ["seasonal_christmas", "seasonal_newyear"]);
        assert_eq!(ids("2027-01-05"), ["seasonal_newyear"]);
        assert_eq!(ids("2027-01-06"), Vec::<&str>::new());
        assert_eq!(ids("2028-02-29"), Vec::<&str>::new());
        let mut doc = fixture(); doc["rows"][0]["start"] = json!([2,29]); doc["rows"][0]["end"] = json!([2,29]);
        let m = parse(&serde_json::to_vec(&doc).unwrap()).unwrap();
        assert!(m.rows[0].active(NaiveDate::from_ymd_opt(2028,2,29).unwrap()));
        assert!(!m.rows[0].active(NaiveDate::from_ymd_opt(2027,2,28).unwrap()));
    }
}
