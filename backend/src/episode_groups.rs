//! Verified packaged-episode identities for a split-story catalogue.
//!
//! SpongeBob's TMDB aired order identifies individual stories while releases also
//! use packaged broadcast numbers. Never infer a package from adjacency, date, or
//! half the duration: the pilot is a triple and some packages cross TMDB seasons.
//! See data/spongebob_episode_groups.md for the source snapshot and update rules.
use serde::Deserialize;
use serde_json::{json, Value};
use std::{collections::{HashMap, HashSet}, sync::OnceLock, time::{Duration, Instant}};

pub const SPONGEBOB_ID: u64 = 387;

#[derive(Debug, Deserialize)]
pub struct EpisodePart {
    pub season: u64,
    pub episode: u64,
    pub id: u64,
    pub name: String,
    pub runtime: Option<u64>,
    pub air_date: Option<String>,
    pub still_path: Option<String>,
}
#[derive(Debug, Deserialize)]
pub struct EpisodeGroup {
    pub release_season: u32,
    pub release_episode: u32,
    pub parts: Vec<EpisodePart>,
}
impl EpisodeGroup {
    pub fn first(&self) -> &EpisodePart { &self.parts[0] }
    pub fn title(&self) -> String { self.parts.iter().map(|p| p.name.as_str()).collect::<Vec<_>>().join(" / ") }
    pub fn contains(&self, season: u64, episode: u64) -> bool {
        self.parts.iter().any(|p| (p.season, p.episode) == (season, episode))
    }
}
fn groups() -> &'static [EpisodeGroup] {
    static GROUPS: OnceLock<Vec<EpisodeGroup>> = OnceLock::new();
    GROUPS.get_or_init(|| serde_json::from_str(include_str!("data/spongebob_episode_groups.json")).expect("validated packaged episode fixture"))
}
pub fn group(show: u64, season: u64, episode: u64) -> Option<&'static EpisodeGroup> {
    if show != SPONGEBOB_ID { return None; }
    groups().iter().find(|g| g.contains(season, episode))
}
pub fn canonical_coordinates(show: u64, season: u64, episode: u64) -> (u64, u64) {
    group(show, season, episode).map(|g| (g.first().season, g.first().episode)).unwrap_or((season, episode))
}
pub fn episode_numbers(show: u64, season: u64, raw_count: u64) -> Option<Vec<u64>> {
    if show != SPONGEBOB_ID || season == 0 || raw_count == 0 || raw_count > 10_000 { return None; }
    // Preserve newly added/unmapped episodes. A static snapshot must never
    // silently hide a future story or invent an episode beyond the provider count.
    Some((1..=raw_count).filter(|number| {
        group(show, season, *number).is_none_or(|g| (g.first().season, g.first().episode) == (season, *number))
    }).collect())
}
fn identity_name(value: &str) -> String {
    value.to_lowercase().replace('&', "and").chars().filter(|c| c.is_alphanumeric()).collect()
}
fn verified_part(show: u64, episode: &Value) -> Option<(&'static EpisodeGroup, &'static EpisodePart)> {
    if episode["show_id"].as_u64().is_some_and(|id| id != show) { return None; }
    let g = group(show, episode["season_number"].as_u64()?, episode["episode_number"].as_u64()?)?;
    let p = g.parts.iter().find(|p| p.id == episode["id"].as_u64().unwrap_or(0)
        && Some(p.season) == episode["season_number"].as_u64()
        && Some(p.episode) == episode["episode_number"].as_u64())?;
    if identity_name(episode["name"].as_str()?) != identity_name(&p.name) { return None; }
    Some((g, p))
}
fn current_group_members(g: &EpisodeGroup, season: u64, value: &Value) -> bool {
    if value["season_number"].as_u64() != Some(season) { return false; }
    let Some(episodes) = value["episodes"].as_array() else { return false; };
    g.parts.iter().filter(|p| p.season == season).all(|part| {
        episodes.iter().filter(|ep| verified_part(SPONGEBOB_ID, ep)
            .is_some_and(|(_, p)| p.id == part.id)).count() == 1
    })
}
/// Check the current provider identities before using a snapshot's release
/// aliases. Metadata edits must fail closed, never turn a different episode into
/// a confidently selected package. Shared short-lived cache coalesces indexers.
pub async fn verified_group(http: &reqwest::Client, g: &EpisodeGroup) -> bool {
    static CACHE: OnceLock<tokio::sync::Mutex<HashMap<u64, (Instant, Value)>>> = OnceLock::new();
    let mut cache = CACHE.get_or_init(Default::default).lock().await;
    let seasons: HashSet<_> = g.parts.iter().map(|p| p.season).collect();
    for season in seasons {
        let fresh = cache.get(&season).is_some_and(|(at, _)| at.elapsed() < Duration::from_secs(1800));
        if !fresh {
            let Ok(value) = crate::catalog::tmdb(http, &format!("tv/387/season/{season}"), &[("language", "en-US".into())]).await else { return false; };
            cache.insert(season, (Instant::now(), value));
        }
        if !current_group_members(g, season, &cache[&season].1) { return false; }
    }
    true
}
fn annotate(g: &EpisodeGroup, mut value: Value) -> Value {
    let first = g.first();
    let is_anchor = value["id"].as_u64() == Some(first.id);
    value["id"] = json!(first.id);
    value["show_id"] = json!(SPONGEBOB_ID);
    value["season_number"] = json!(first.season);
    value["episode_number"] = json!(first.episode);
    value["name"] = json!(g.title());
    if !is_anchor {
        value["overview"] = Value::Null;
        value["air_date"] = json!(first.air_date);
        value["still_path"] = json!(first.still_path);
    }
    if g.parts.iter().all(|p| p.runtime.is_some()) {
        value["runtime"] = json!(g.parts.iter().filter_map(|p| p.runtime).sum::<u64>());
    }
    value["velvet_release_season"] = json!(g.release_season);
    value["velvet_release_episode"] = json!(g.release_episode);
    value["velvet_episode_parts"] = json!(g.parts.iter().map(|p| json!({"season":p.season,"episode":p.episode,"id":p.id,"name":p.name})).collect::<Vec<_>>());
    value
}
/// Canonicalize a metadata-verified explicit story (including a cross-season B).
pub fn normalize_episode(show: u64, episode: &Value) -> Value {
    verified_part(show, episode).map(|(g, _)| annotate(g, episode.clone())).unwrap_or_else(|| episode.clone())
}
/// Preserve TMDB identity/numbering but list a complete package once. Unknown or
/// changed identities remain untouched rather than silently removing stories.
pub fn normalize_season(show: u64, season: u64, value: &mut Value) {
    if show != SPONGEBOB_ID || season == 0 || value["season_number"].as_u64() != Some(season) || value.get("velvet_episode_aliases").is_some() { return; }
    let Some(episodes) = value["episodes"].as_array() else { return; };
    let mut output = Vec::new();
    let mut aliases = Vec::new();
    let mut seen = HashSet::new();
    for ep in episodes {
        let Some((g, _)) = verified_part(show, ep) else { output.push(ep.clone()); continue; };
        // If a same-season sibling's identity changed or disappeared, do not
        // remove the remaining segments on the strength of a stale snapshot.
        let complete_here = g.parts.iter().filter(|p| p.season == season).all(|p| {
            episodes.iter().any(|other| other["id"] == p.id && verified_part(show, other).is_some())
        });
        if !complete_here { output.push(ep.clone()); continue; }
        if !seen.insert(g.first().id) { continue; }
        let anchor = episodes.iter().find(|e| e["id"] == g.first().id).unwrap_or(ep);
        let normalized = annotate(g, anchor.clone());
        if g.first().season == season { output.push(normalized); } else { aliases.push(normalized); }
    }
    output.sort_by_key(|e| e["episode_number"].as_u64().unwrap_or(0));
    value["episodes"] = json!(output);
    value["velvet_episode_aliases"] = json!(aliases);
}
pub fn normalize_metadata(path: &str, value: &mut Value) {
    let parts: Vec<_> = path.split('/').collect();
    match parts.as_slice() {
        ["tv", "387", "season", season] => {
            if let Ok(season) = season.parse() { normalize_season(SPONGEBOB_ID, season, value); }
        }
        ["tv", "387"] if value["id"] == SPONGEBOB_ID => {
            if let Some(seasons) = value["seasons"].as_array_mut() {
                for season in seasons {
                    if season.get("velvet_episode_numbers").is_some() { continue; }
                    if let Some(numbers) = season["season_number"].as_u64().and_then(|s| episode_numbers(SPONGEBOB_ID, s, season["episode_count"].as_u64().unwrap_or(0))) {
                        season["episode_count"] = json!(numbers.len());
                        season["velvet_episode_numbers"] = json!(numbers);
                    }
                }
            }
            // Some clients request seasons appended to the show response.
            let keys: Vec<_> = value.as_object().into_iter().flat_map(|o| o.keys()).filter(|k| k.starts_with("season/")).cloned().collect();
            for key in keys {
                if let Ok(season) = key[7..].parse() { normalize_season(SPONGEBOB_ID, season, &mut value[&key]); }
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn raw(p: &EpisodePart) -> Value { json!({"id":p.id,"season_number":p.season,"episode_number":p.episode,"name":p.name,"runtime":p.runtime,"air_date":p.air_date}) }
    #[test]
    fn snapshot_identities_and_coordinates_are_unique() {
        let mut ids=HashSet::new(); let mut coordinates=HashSet::new();let mut releases=HashSet::new();
        for g in groups() { assert!((1..=3).contains(&g.parts.len()));assert!(releases.insert((g.release_season,g.release_episode)));
            for p in &g.parts {assert!(ids.insert(p.id));assert!(coordinates.insert((p.season,p.episode)));}
        }
        assert_eq!(groups().len(),343);
    }
    #[test]
    fn pilot_is_three_stories_and_second_package_is_not_reef_blower() {
        let g=group(387,1,2).unwrap();assert_eq!(g.parts.len(),3);assert_eq!(g.release_episode,1);
        let g=group(387,1,5).unwrap();assert_eq!(g.first().episode,4);assert_eq!(g.release_episode,2);
        assert_eq!(episode_numbers(387,1,41).unwrap().len(),20);
    }
    #[test]
    fn long_special_is_not_paired_and_cross_season_story_resolves_to_whole_package() {
        assert_eq!(group(387,2,9).unwrap().parts.len(),1);
        let g=group(387,3,31).unwrap();assert_eq!((g.first().season,g.first().episode),(2,22));assert_eq!(g.release_episode,13);
        let mut season=json!({"season_number":3,"episodes":[raw(&g.parts[1])]});
        normalize_season(387,3,&mut season);
        assert_eq!(season["episodes"].as_array().unwrap().len(),0);
        assert_eq!(season["velvet_episode_aliases"][0]["season_number"],2);
    }
    #[test]
    fn release_mapping_requires_current_ids_names_and_coordinates() {
        let g=group(387,1,1).unwrap();
        let mut v=json!({"season_number":1,"episodes":g.parts.iter().map(raw).collect::<Vec<_>>()});
        assert!(current_group_members(g,1,&v));
        v["episodes"][1]["episode_number"]=json!(99);assert!(!current_group_members(g,1,&v));
        v["episodes"][1]["episode_number"]=json!(2);v["episodes"][1]["name"]=json!("Changed story");
        assert!(!current_group_members(g,1,&v));
    }
    #[test]
    fn detail_counts_keep_canonical_numbers_and_new_unknown_episodes() {
        let mut detail=json!({"id":387,"seasons":[{"season_number":1,"episode_count":43},{"season_number":0,"episode_count":113}]});
        normalize_metadata("tv/387", &mut detail);
        assert_eq!(detail["seasons"][0]["episode_count"],22);
        let numbers=detail["seasons"][0]["velvet_episode_numbers"].as_array().unwrap();
        assert!(numbers.contains(&json!(42)) && numbers.contains(&json!(43)));
        assert!(!numbers.contains(&json!(2)));
        assert_eq!(detail["seasons"][1]["episode_count"],113);
        let once=detail.clone();normalize_metadata("tv/387", &mut detail);assert_eq!(once,detail);
    }
    #[test]
    fn normalization_is_idempotent_and_unknown_metadata_is_preserved() {
        let g=group(387,1,1).unwrap();let mut season=json!({"season_number":1,"episodes":g.parts.iter().map(raw).collect::<Vec<_>>()});
        normalize_season(387,1,&mut season);assert_eq!(season["episodes"].as_array().unwrap().len(),1);
        assert_eq!(season["episodes"][0]["runtime"],23);
        let once=season.clone();normalize_season(387,1,&mut season);assert_eq!(once,season);
        let mut changed=raw(&g.parts[1]);changed["id"]=json!(999);
        let mut season=json!({"season_number":1,"episodes":[raw(&g.parts[0]),changed.clone(),raw(&g.parts[2])]});
        normalize_season(387,1,&mut season);assert_eq!(season["episodes"].as_array().unwrap().len(),3);
        assert!(season["episodes"].as_array().unwrap().contains(&changed));
        assert!(group(999,1,1).is_none());assert!(group(387,99,1).is_none());
    }
}
