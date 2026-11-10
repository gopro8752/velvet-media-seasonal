# SpongeBob packaged episode identities

The adjacent JSON is public metadata, not a release catalogue or credentials.
It maps individual TMDB story IDs to complete packaged programmes. It was
verified on 2026-11-09 against TMDB's two explicit episode-order datasets:

- [Packaging Order](https://www.themoviedb.org/tv/387-spongebob-squarepants/episode_group/6a4c32c23f2079d720dedb0f)
- [Joined Packaging Order](https://www.themoviedb.org/tv/387-spongebob-squarepants/episode_group/6a779c62710e503af51c5dee)
- Primary programme example: [Paramount+ season 1](https://www.paramountplus.com/shows/spongebob-squarepants/episodes/1/) lists Help Wanted / Reef Blowers / Tea at the Treedome as one programme, followed by Bubblestand / Ripped Pants.

The joined-order entries are explicit package boundaries. For each matching
season, locate each joined entry's TMDB episode ID in the full packaging order;
collect from that anchor up to, but not including, the next anchor. Keep the
original TMDB season, episode number, and ID of every story. The release season
comes from the package season; release episode is the one-based joined order.
The snapshot contains 343 packages and 659 stories. Some cross TMDB seasons.
No dates, runtimes, or odd/even arithmetic are used to decide a group.

The JSON contains names, IDs, dates, runtime and TMDB image paths only. Fetching
updates uses the operator's runtime TMDB credential; no credential is stored in
this file. Validate unique IDs and story coordinates, unique release coordinates,
monotonic anchor positions, a first anchor at zero, and one to three stories per
package before accepting an update. Check the triple pilot, full-length specials
and cross-season Shanghaied / Gary Takes a Bath explicitly.

The runtime validates provider episode IDs and names before collapsing metadata.
Unknown or changed metadata is preserved. Future/unmapped stories remain visible.
The first story's TMDB coordinates remain the playback/history identity; aliases
resolve explicit requests for any included story to that same full package.

Release numbers alone cannot establish content: S01E02 can label Reef Blower
alone or Bubblestand / Ripped Pants. The resolver searches both known numbering
forms but only offers files positively naming every story in the requested
package, after verifying the show prefix. Bare ambiguous titles and solo A/B
files are not offered as complete packages. Playback starts at the real beginning
and ends at the real end, with no approximate midpoint seeks.
