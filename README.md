# Top Fossdroid

[Top Fossdroid](https://ehab-0.github.io/topfossdroid/) is a simple way to discover open-source Android apps. Search thousands of apps, browse by category or source, and compare projects by recent updates, repository stars, and available F-Droid download data.

## Find Android apps you can trust and inspect

Top Fossdroid brings app information from broad F-Droid catalogs and verified official project repositories into one fast, searchable website. Each listing points back to its catalogs and, when available, its public source code on GitHub, GitLab, or Codeberg.

You can use it to:

- Find free and open-source Android apps by name, category, or package ID.
- Browse broad catalogs and official upstream repositories without duplicate package listings.
- See whether a project is active and when it was last updated.
- Compare GitHub, GitLab, or Codeberg stars and recent repository activity.
- Open the official website, source code, issue tracker, or donation page.

No account is required. The website does not install apps or host APK files; downloads remain with the original catalog.

## How rankings work

**Popular** is the default ranking. At build time, Top Fossdroid applies `log10(value + 1)` to each available star and F-Droid download value, min-max normalizes each signal across records that have it, and combines 50% stars, 35% downloads, and 15% activity. Activity is 1.00 for updates within 30 days, 0.85 within 90, 0.65 within 180, 0.40 within 365, and 0.15 when older. Missing signals are omitted and the remaining weights are renormalized; an app needs at least stars or downloads to receive a Popular score.

**Fresh & Popular** uses the Popular order and includes apps updated within 180 days with at least 500 stars or 10,000 measured F-Droid downloads. **Hidden Gems** includes apps updated within 365 days with fewer than 5,000 known repository stars and at least 25,000 measured F-Droid downloads, ordered by downloads descending, stars ascending, then update date descending.

Most Starred and Most Downloaded use their named current snapshot metric. Trending compares available star history. Recently Updated uses the latest available repository activity or catalog update date, and Recently Added uses the catalog index date. Stars show repository popularity, not app quality. F-Droid download figures cover only packages and time periods published by the public F-Droid Metrics Distilled dataset; they are not total Android installs.

Missing information stays missing instead of being shown as zero. Catalogs can occasionally disagree or be unavailable, so use each app's source links when you need to verify details.

Android package ID is the app identity. Different package variants remain separate, while every catalog membership for the same package is retained. An app is eligible for current rankings when at least one of its sources has `rankingEligible: true`; archive/nightly-only records remain known but do not enter rankings. Source authority and catalog count are provenance, not popularity signals.

## Data sources

Top Fossdroid combines public metadata from:

- broad catalogs: [F-Droid](https://f-droid.org/), [IzzyOnDroid](https://apt.izzysoft.de/fdroid/), and [Guardian Project](https://guardianproject.info/)
- verified official project repositories listed in [`config/repositories.json`](config/repositories.json)
- historical F-Droid Archive metadata, excluded from current rankings
- [GitHub](https://github.com/), [GitLab](https://gitlab.com/), and allowlisted [Codeberg](https://codeberg.org/) repository metadata
- [F-Droid Metrics Distilled](https://grote.gitlab.io/fdroid-metrics-distilled/)

Obtainium crowdsourced configurations, `offa/android-foss`, `awesome-fdroid`, and a maintained F-Droid repository registry are compared at build time for coverage auditing only. They are not authoritative metadata and do not automatically add apps. Accrescent is currently excluded because its catalog is not FOSS-only and a reliable structured FOSS subset was not established.

App descriptions, icons, and metadata belong to their original publishers and catalogs. Source attribution is included in each listing.

## Run it locally

Top Fossdroid is a static website with a Python data builder. It requires Python 3.9 or newer.

```bash
python -m pip install -r requirements.txt
python scripts/build_data.py
python -m http.server 8000
```

Then open `http://localhost:8000`. For a quick test build, run `python scripts/build_data.py --limit 10`.

The site is automatically rebuilt and deployed to GitHub Pages through [GitHub Actions](.github/workflows/pages.yml).

## Add an official repository

Add one object to [`config/repositories.json`](config/repositories.json) with `id`, `name`, `type: "fdroid"`, the repository `url`, `appUrl`, `authority`, `channel`, `rankingEligible`, and `enabled`. Valid authority values are `official_ecosystem`, `official_project`, `curated_third_party`, and `discovery_only`; channels are `stable`, `archive`, `nightly`, or `candidate`. Verify project ownership and a live `index-v2.json` first. The generic parser handles every enabled F-Droid-compatible entry, so no source-specific parser is needed.

Discovery candidates are included only when an official source repository, Android-app identity, open-source license, and official release/distribution evidence can all be established deterministically. Ambiguous candidates are left in the generated audit, not shipped as apps.

## License

The Top Fossdroid code is available under the [MIT License](LICENSE). App metadata and icons remain subject to their original owners' licenses.
