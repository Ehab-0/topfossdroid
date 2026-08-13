#!/usr/bin/env python3
import argparse
import html
import io
import json
import logging
import math
import os
import re
import tarfile
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps, UnidentifiedImageError

from fdroid import merge_catalogs, read_repository
from forge import enrich_apps, normalize_repository
from metrics import read_metrics
from utils import fetch, fetch_file, write_json

ROOT = Path(__file__).resolve().parents[1]

POPULAR_WEIGHTS = {"stars": 0.50, "downloads": 0.35, "activity": 0.15}

DISCOVERY_SOURCES = {
    "obtainium": "https://github.com/ImranR98/apps.obtainium.imranr.dev/archive/refs/heads/main.tar.gz",
    "androidFoss": "https://raw.githubusercontent.com/offa/android-foss/master/README.md",
    "awesomeFdroid": "https://raw.githubusercontent.com/moneytoo/awesome-fdroid/master/README.md",
    "repositoryRegistry": "https://raw.githubusercontent.com/userkilled/FDroid-List-Repository/master/droidify_repos.json",
}


def plain_description(value):
    if not value:
        return value
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n• ", value)
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"(?i)</?(?:p|div|ul|ol|h[1-6])(?:\s[^>]*)?>", "\n", value)
    value = re.sub(r"(?i)</?(?:a|b|i|strong|em|code|span)(?:\s[^>]*)?>", "", value)
    value = re.sub(r"</?[A-Za-z][^>\n]*(?:>|$)", "", value)
    value = re.sub(r"</?[A-Za-z][A-Za-z0-9]*(?=\s)", "", value)
    value = html.unescape(value)
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def safe_package_name(package):
    return re.sub(r"[^A-Za-z0-9._-]", "_", package)


def save_thumbnail(source, destination):
    with Image.open(source) as image:
        image.seek(0)
        image = image.convert("RGBA")
        image = ImageOps.contain(image, (64, 64), Image.Resampling.LANCZOS)
        thumbnail = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        thumbnail.alpha_composite(image, ((64 - image.width) // 2, (64 - image.height) // 2))
        thumbnail.save(destination, "WEBP", quality=82, method=6)


def download_icons(apps, limit=None):
    icons = ROOT / "icons"
    icons.mkdir(exist_ok=True)
    existing = {path.stem: path for path in icons.iterdir() if path.is_file() and 0 < path.stat().st_size <= 2_000_000}
    done = failures = candidates = 0
    used = set()
    host_failures = {}
    unavailable_hosts = set()
    for app in apps:
        urls = app.pop("iconCandidates", [])
        safe_package = safe_package_name(app["package"])
        cached = existing.get(safe_package)
        if not app.get("rankingEligible", True) and not cached:
            app["icon"] = None
            continue
        if urls or cached:
            candidates += 1
        if cached:
            destination = icons / f"{safe_package}.webp"
            try:
                if cached != destination:
                    save_thumbnail(cached, destination)
                    cached.unlink()
                app["icon"] = destination.relative_to(ROOT).as_posix()
                used.add(destination)
                done += 1
                continue
            except (OSError, UnidentifiedImageError) as error:
                logging.warning("Could not optimize cached icon for %s: %s", app["package"], error)
        if not urls or (limit is not None and done >= limit):
            app["icon"] = None
            continue
        for url in urls:
            host = urlparse(url).hostname
            if host in unavailable_hosts:
                continue
            try:
                body, content_type = fetch_file(url)
                destination = icons / f"{safe_package}.webp"
                save_thumbnail(io.BytesIO(body), destination)
                app["icon"] = destination.relative_to(ROOT).as_posix()
                used.add(destination)
                done += 1
                host_failures[host] = 0
                break
            except Exception as error:
                logging.warning("Could not cache icon for %s from %s: %s", app["package"], url, error)
                if isinstance(error, urllib.error.URLError) and not isinstance(error, urllib.error.HTTPError):
                    host_failures[host] = host_failures.get(host, 0) + 1
                    if host_failures[host] >= 5:
                        unavailable_hosts.add(host)
                        logging.warning("Skipping remaining icons from unavailable host %s", host)
        else:
            app["icon"] = None
            failures += 1
    for path in icons.iterdir():
        if path.is_file() and path not in used:
            path.unlink()
    return {"cached": done, "candidates": candidates, "failures": failures}


def detail_path(package):
    return ROOT / "data/details" / f"{safe_package_name(package)}.json"


def repository_url(host, path):
    domains = {"github": "github.com", "gitlab": "gitlab.com", "codeberg": "codeberg.org"}
    return f"https://{domains[host]}/{path}" if host in domains and path else None


def activity_score(value, today=None):
    if not value:
        return None
    try:
        updated = date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None
    days = max(0, ((today or date.today()) - updated).days)
    if days <= 30:
        return 1.0
    if days <= 90:
        return 0.85
    if days <= 180:
        return 0.65
    if days <= 365:
        return 0.40
    return 0.15


def add_popular_scores(apps):
    eligible = [app for app in apps if app.get("rankingEligible", True)]
    stars = [(app.get("repo") or {}).get("stars") for app in eligible]
    downloads = [(app.get("downloads") or {}).get("fdroid") for app in eligible]
    star_logs = [math.log10(value + 1) for value in stars if isinstance(value, (int, float))]
    download_logs = [math.log10(value + 1) for value in downloads if isinstance(value, (int, float))]

    def normalized(value, values):
        if not values or max(values) == min(values):
            return 1.0
        return (math.log10(value + 1) - min(values)) / (max(values) - min(values))

    for app in apps:
        if not app.get("rankingEligible", True):
            app["popularScore"] = None
            continue
        repo = app.get("repo") or {}
        downloads = app.get("downloads") or {}
        stars = repo.get("stars")
        fdroid_downloads = downloads.get("fdroid")
        components = []
        if isinstance(stars, (int, float)):
            components.append((POPULAR_WEIGHTS["stars"], normalized(stars, star_logs)))
        if isinstance(fdroid_downloads, (int, float)):
            components.append((POPULAR_WEIGHTS["downloads"], normalized(fdroid_downloads, download_logs)))
        if not components:
            app["popularScore"] = None
            continue
        activity = activity_score(repo.get("pushedAt") or app.get("updatedAt"))
        if activity is not None:
            components.append((POPULAR_WEIGHTS["activity"], activity))
        weighted_total = sum(weight * value for weight, value in components)
        app["popularScore"] = round(1000 * weighted_total / sum(weight for weight, _ in components))


def read_previous_apps():
    try:
        previous = json.loads((ROOT / "data/apps.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not previous or "p" not in previous[0]:
        return previous
    apps = []
    for item in previous:
        try:
            detail = json.loads(detail_path(item["p"]).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            detail = {}
        repo_path = item.get("r")
        host = item.get("h")
        owner, _, name = (repo_path or "").rpartition("/")
        repo = None
        if repo_path:
            repo = {
                "host": host, "owner": owner, "name": name,
                "url": repository_url(host, repo_path), "stars": item.get("st"),
                "stars30d": item.get("t30"), "trendPeriodDays": item.get("tp"),
                "archived": item.get("ar"), **detail.get("repo", {}),
            }
        sources = detail.get("sources", [{"id": source} for source in item.get("src", [])])
        apps.append({
            "package": item["p"], "name": item["n"], "summary": item.get("s"),
            "description": detail.get("description"), "categories": item.get("c", []),
            "antiFeatures": detail.get("antiFeatures", []), "sources": sources,
            "license": detail.get("license"), "website": detail.get("website"),
            "sourceCode": detail.get("sourceCode") or repository_url(host, repo_path),
            "issueTracker": detail.get("issueTracker"), "donate": detail.get("donate"),
            "latestVersion": detail.get("latestVersion"), "latestVersionCode": detail.get("latestVersionCode"),
            "updatedAt": detail.get("updatedAt") or item.get("u"), "addedAt": item.get("a"),
            "repo": repo, "downloads": {"fdroid": item["dl"]} if item.get("dl") is not None else None,
            "icon": item.get("i"),
            "rankingEligible": item.get("e", True),
        })
    return apps


def public_list_record(app):
    repo = app.get("repo") or {}
    repo_path = f'{repo["owner"]}/{repo["name"]}' if repo.get("owner") and repo.get("name") else None
    record = {
        "p": app["package"], "n": app["name"], "s": app.get("summary"), "i": app.get("icon"),
        "c": app.get("categories", []), "src": [source["id"] for source in app.get("sources", [])],
        "r": repo_path, "h": repo.get("host"), "st": repo.get("stars"), "t30": repo.get("stars30d"),
        "tp": repo.get("trendPeriodDays"), "dl": (app.get("downloads") or {}).get("fdroid"),
        "u": repo.get("pushedAt") or app.get("updatedAt"), "a": app.get("addedAt"), "ar": repo.get("archived"),
        "po": app.get("popularScore"),
    }
    if not app.get("rankingEligible", True):
        record["e"] = False
    return {key: value for key, value in record.items() if value is not None}


def public_detail_record(app):
    repo = app.get("repo") or {}
    return {
        "description": plain_description(app.get("description")), "latestVersion": app.get("latestVersion"),
        "latestVersionCode": app.get("latestVersionCode"), "license": app.get("license") or repo.get("license"),
        "website": app.get("website"), "sourceCode": repo.get("url") or app.get("sourceCode"),
        "issueTracker": app.get("issueTracker"), "donate": app.get("donate"),
        "antiFeatures": app.get("antiFeatures", []), "sources": app.get("sources", []),
        "updatedAt": app.get("updatedAt"),
        "repo": {key: repo.get(key) for key in ("forks", "openIssues", "latestReleaseTag", "updatedAt", "pushedAt", "license") if repo.get(key) is not None},
    }


def write_public_data(apps):
    details = ROOT / "data/details"
    details.mkdir(parents=True, exist_ok=True)
    expected = set()
    for app in apps:
        path = detail_path(app["package"])
        write_json(path, public_detail_record(app), compact=True)
        expected.add(path)
    for path in details.glob("*.json"):
        if path not in expected:
            path.unlink()
    write_json(ROOT / "data/apps.json", [public_list_record(app) for app in apps], compact=True)


def update_history(apps):
    path = ROOT / "data/history.json"
    try:
        history = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        history = {}
    today = date.today().isoformat()
    snapshot = {app["repo"]["url"]: app["repo"]["stars"] for app in apps if isinstance((app.get("repo") or {}).get("stars"), int)}
    if snapshot:
        history[today] = snapshot
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    history = {day: values for day, values in sorted(history.items()) if day >= cutoff}
    for app in apps:
        repo = app.get("repo") or {}
        current = repo.get("stars")
        samples = [(day, values[repo.get("url")]) for day, values in history.items() if day < today and isinstance(values.get(repo.get("url")), int)]
        target = (date.today() - timedelta(days=30)).isoformat()
        old = [sample for sample in samples if sample[0] <= target]
        baseline = max(old) if old else (min(samples) if samples else None)
        repo["stars30d"] = current - baseline[1] if isinstance(current, int) and baseline else None
        repo["trendPeriodDays"] = (date.today() - date.fromisoformat(baseline[0])).days if baseline else None
    write_json(path, history, compact=True)


def validate(apps, icon_stats, forge_counts, token_present):
    recognized = [app for app in apps if (app.get("repo") or {}).get("host") in ("github", "gitlab", "codeberg")]
    for app in apps:
        repo = app.get("repo")
        if repo:
            if repo.get("stars") is not None and not isinstance(repo["stars"], int):
                raise ValueError(f"Non-numeric stars for {app['package']}")
            if repo.get("stars30d") is not None and not isinstance(repo["stars30d"], int):
                raise ValueError(f"Non-numeric stars30d for {app['package']}")
        score = app.get("popularScore")
        if score is not None and (not isinstance(score, int) or not 0 <= score <= 1000):
            raise ValueError(f"Invalid Popular score for {app['package']}: {score}")
        icon = app.get("icon")
        if icon and (icon.startswith("/") or not (ROOT / icon).is_file()):
            raise ValueError(f"Invalid local icon for {app['package']}: {icon}")
    if token_present and forge_counts["recognizedGithub"] >= 1000:
        available = forge_counts["recognizedGithub"] - forge_counts["githubUnavailableRepos"]
        coverage = forge_counts["githubEnrichedRepos"] / available if available else 0
        if coverage < 0.85:
            raise ValueError(f"Authenticated GitHub enrichment coverage is too low: {forge_counts['githubEnrichedRepos']}/{available} available repositories ({coverage:.1%})")

        top_downloaded = sorted(
            (app for app in apps if isinstance((app.get("downloads") or {}).get("fdroid"), (int, float))),
            key=lambda app: app["downloads"]["fdroid"], reverse=True,
        )[:50]
        top_recognized = [app for app in top_downloaded if (app.get("repo") or {}).get("host") in ("github", "gitlab", "codeberg")]
        top_stars = sum(isinstance((app.get("repo") or {}).get("stars"), int) for app in top_recognized)
        if len(top_recognized) >= 40 and top_stars < 40:
            raise ValueError(f"Most Downloaded star coverage is too low: {top_stars}/{len(top_recognized)} recognized forge apps")
    if icon_stats["candidates"] >= 1000 and icon_stats["cached"] < icon_stats["candidates"] // 2:
        raise ValueError(f"Icon cache coverage is too low: {icon_stats['cached']}/{icon_stats['candidates']}")


def log_build_summary(apps, forge_counts):
    available = forge_counts["recognizedGithub"] - forge_counts["githubUnavailableRepos"]
    coverage = forge_counts["githubEnrichedRepos"] / available if available else 0
    top_downloaded = sorted(
        (app for app in apps if isinstance((app.get("downloads") or {}).get("fdroid"), (int, float))),
        key=lambda app: app["downloads"]["fdroid"], reverse=True,
    )[:50]
    top_recognized = [app for app in top_downloaded if (app.get("repo") or {}).get("host") in ("github", "gitlab", "codeberg")]
    top_stars = sum(isinstance((app.get("repo") or {}).get("stars"), int) for app in top_recognized)
    top_unsupported = sum(bool(app.get("sourceCode")) and not app.get("repo") for app in top_downloaded)
    top_without_source = sum(not app.get("sourceCode") for app in top_downloaded)

    logging.info("Forge enrichment\n----------------\nGitHub app records: %d\nUnique GitHub repos: %d\nGitHub repos enriched: %d\nGitHub repos unavailable: %d\nGitHub repos failed: %d\nGitHub enrichment coverage: %.1f%%\n\nGitLab unique repos: %d\nGitLab enriched: %d\nGitLab failed: %d\n\nCodeberg unique repos: %d\nCodeberg enriched: %d\nCodeberg failed: %d\n\nMost Downloaded top 50\n----------------------\nTop downloaded apps: %d\nRecognized forge repos: %d\nWith numeric stars: %d\nMissing stars: %d\n  No source URL: %d\n  Unsupported forge: %d\n  Recognized forge failure: %d",
        forge_counts["githubAppRecords"], forge_counts["recognizedGithub"],
        forge_counts["githubEnrichedRepos"], forge_counts["githubUnavailableRepos"],
        forge_counts["githubFailedRepos"], coverage * 100,
        forge_counts["recognizedGitlab"], forge_counts["gitlabEnrichedRepos"],
        forge_counts["gitlabFailedRepos"], forge_counts["recognizedCodeberg"],
        forge_counts["codebergEnrichedRepos"], forge_counts["codebergFailedRepos"],
        len(top_downloaded), len(top_recognized),
        top_stars, len(top_downloaded) - top_stars, top_without_source, top_unsupported,
        len(top_recognized) - top_stars)


def previous_catalog(previous_apps, source_id):
    records = []
    for app in previous_apps:
        catalog = next((source for source in app.get("sources", []) if source.get("id") == source_id), None)
        if catalog:
            record = dict(app)
            record["catalog"] = catalog
            record["iconCandidates"] = []
            records.append(record)
    return records


def failure_kind(error):
    if isinstance(error, urllib.error.HTTPError):
        return "HTTP failure"
    if isinstance(error, urllib.error.URLError):
        return "timeout" if "timed out" in str(error).lower() else "HTTP failure"
    if isinstance(error, (json.JSONDecodeError, ValueError)):
        return "invalid index"
    return "parse failure"


def repository_links(text):
    links = set()
    for url in re.findall(r"https?://[^\s)>\]}`\"']+", text):
        repo = normalize_repository(url.rstrip(".,;"))
        if repo:
            links.add(repo["url"].lower())
    return links


def build_discovery_audit(apps, configured_sources):
    known_packages = {app["package"] for app in apps}
    known_repos = {(app.get("repo") or {}).get("url", "").lower() for app in apps}
    audit = {"policy": "Discovery only; candidates require separate deterministic source, license, Android, and distribution validation.", "highConfidenceIncluded": 0, "sources": {}}
    try:
        body = fetch(DISCOVERY_SOURCES["obtainium"], timeout=30)
        packages, repos = set(), set()
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            for member in archive.getmembers():
                if "/public/data/apps/complex/" not in member.name or not member.name.endswith(".json") or not member.isfile():
                    continue
                packages.add(member.name.rsplit("/", 1)[-1][:-5])
                data = json.load(archive.extractfile(member))
                for config in data.get("configs", []):
                    repo = normalize_repository(config.get("url"))
                    if repo:
                        repos.add(repo["url"].lower())
        unknown = sorted(packages - known_packages)
        audit["sources"]["obtainium"] = {"candidatesDiscovered": len(packages), "alreadyKnown": len(packages & known_packages), "highConfidenceNew": 0, "ambiguousSkipped": len(unknown), "recognizedRepositories": len(repos), "unknownPackageSample": unknown[:100], "status": "OK"}
    except Exception as error:
        audit["sources"]["obtainium"] = {"status": failure_kind(error), "error": str(error)}

    for key in ("androidFoss", "awesomeFdroid"):
        try:
            body = fetch(DISCOVERY_SOURCES[key], timeout=30)
            repos = repository_links(body.decode("utf-8", errors="replace"))
            unknown = sorted(repos - known_repos)
            audit["sources"][key] = {"candidatesDiscovered": len(repos), "alreadyKnown": len(repos & known_repos), "highConfidenceNew": 0, "ambiguousSkipped": len(unknown), "unknownRepositorySample": unknown[:100], "status": "OK"}
        except Exception as error:
            audit["sources"][key] = {"status": failure_kind(error), "error": str(error)}

    try:
        body = fetch(DISCOVERY_SOURCES["repositoryRegistry"], timeout=30)
        repositories = json.loads(body).get("repositories", [])
        addresses = {item.get("address", "").rstrip("/") for item in repositories if item.get("address")}
        configured = {source["url"].rstrip("/") for source in configured_sources}
        unknown = sorted(addresses - configured)
        audit["sources"]["repositoryRegistry"] = {"candidatesDiscovered": len(addresses), "alreadyConfigured": len(addresses & configured), "highConfidenceNew": 0, "ambiguousSkipped": len(unknown), "unknownSourceSample": unknown[:100], "status": "OK"}
    except Exception as error:
        audit["sources"]["repositoryRegistry"] = {"status": failure_kind(error), "error": str(error)}

    audit["sources"]["accrescent"] = {"status": "skipped", "reason": "The catalog is not FOSS-only and no simple structured source-and-license filter was verified; no packages were imported."}
    path = ROOT / "data/audits/coverage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, audit)
    return audit


def main():
    parser = argparse.ArgumentParser(description="Build the Top Fossdroid static dataset")
    parser.add_argument("--limit", type=int, help="Limit each catalog and network enrichment for local verification")
    parser.add_argument("--enrich-limit", type=int, help="Limit forge calls without limiting catalog ingestion")
    parser.add_argument("--icon-limit", type=int, help="Limit cached icons without limiting catalog ingestion")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config = [source for source in json.loads((ROOT / "config/repositories.json").read_text())["repositories"] if source.get("enabled", True)]
    unsupported_types = [source["id"] for source in config if source.get("type", "fdroid") != "fdroid"]
    if unsupported_types:
        raise SystemExit(f"Unsupported configured source types: {', '.join(unsupported_types)}")
    previous_apps = read_previous_apps()
    seen_packages = set()
    all_catalogs, source_counts, source_stats, source_health, failures = [], {}, {}, [], []
    for source in sorted(config, key=lambda item: item.get("priority", 999)):
        try:
            items = read_repository(source)
            if args.limit:
                items = items[:args.limit]
            all_catalogs.append(items)
            source_counts[source["id"]] = len(items)
            packages = {item["package"] for item in items}
            source_stats[source["id"]] = {"rawRecords": len(items), "alreadyKnown": len(packages & seen_packages), "newUnique": len(packages - seen_packages)}
            seen_packages.update(packages)
            source_health.append({"id": source["id"], "status": "OK", "records": len(items), "stale": False})
        except Exception as error:
            items = previous_catalog(previous_apps, source["id"])
            if items:
                logging.error("Could not refresh %s; reusing %d prior records: %s", source["name"], len(items), error)
                all_catalogs.append(items)
                source_counts[source["id"]] = len(items)
                packages = {item["package"] for item in items}
                source_stats[source["id"]] = {"rawRecords": len(items), "alreadyKnown": len(packages & seen_packages), "newUnique": len(packages - seen_packages)}
                seen_packages.update(packages)
                source_health.append({"id": source["id"], "status": failure_kind(error), "records": len(items), "stale": True})
            else:
                logging.error("Skipping %s: %s", source["name"], error)
                source_health.append({"id": source["id"], "status": failure_kind(error), "records": 0, "stale": False})
            failures.append({"source": source["id"], "kind": failure_kind(error), "error": str(error)})
    if not all_catalogs:
        raise SystemExit("No repository could be read; refusing to replace the dataset")
    apps = merge_catalogs(all_catalogs)
    forge_counts = enrich_apps(apps, previous_apps, args.enrich_limit if args.enrich_limit is not None else args.limit)
    build_discovery_audit(apps, config)
    metrics = read_metrics()
    for app in apps:
        app["downloads"] = metrics.get(app["package"])
    update_history(apps)
    add_popular_scores(apps)
    icon_stats = download_icons(apps, args.icon_limit if args.icon_limit is not None else args.limit)
    validate(apps, icon_stats, forge_counts, bool(os.environ.get("GITHUB_TOKEN")))
    log_build_summary(apps, forge_counts)
    apps.sort(key=lambda item: item["name"].casefold())
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    ranking_eligible = sum(app.get("rankingEligible", True) for app in apps)
    archive_only = sum(not app.get("rankingEligible", True) and any(source.get("channel") == "archive" for source in app["sources"]) for app in apps)
    nightly_only = sum(not app.get("rankingEligible", True) and any(source.get("channel") == "nightly" for source in app["sources"]) for app in apps)
    meta = {"generatedAt": generated, "appCount": ranking_eligible, "totalKnownApps": len(apps), "rankingEligibleApps": ranking_eligible, "archiveOnlyApps": archive_only, "nightlyOnlyApps": nightly_only, "multiCatalogApps": sum(len(app["sources"]) > 1 for app in apps), "repositoryCount": len(config), "sources": [{key: source[key] for key in ("id", "name", "authority", "channel", "rankingEligible")} for source in config], "sourceCounts": source_counts, "sourceStats": source_stats, "sourceHealth": source_health, "githubApps": forge_counts["github"], "gitlabApps": forge_counts["gitlab"], "codebergApps": forge_counts["codeberg"], "recognizedGithubRepos": forge_counts["recognizedGithub"], "recognizedGitlabRepos": forge_counts["recognizedGitlab"], "recognizedCodebergRepos": forge_counts["recognizedCodeberg"], "githubEnrichedRepos": forge_counts["githubEnrichedRepos"], "githubUnavailableRepos": forge_counts["githubUnavailableRepos"], "githubFailedRepos": forge_counts["githubFailedRepos"], "gitlabEnrichedRepos": forge_counts["gitlabEnrichedRepos"], "gitlabFailedRepos": forge_counts["gitlabFailedRepos"], "codebergEnrichedRepos": forge_counts["codebergEnrichedRepos"], "codebergFailedRepos": forge_counts["codebergFailedRepos"], "missingRecognizedStarApps": forge_counts["missingRecognized"], "numericStarApps": sum(isinstance((app.get("repo") or {}).get("stars"), int) for app in apps), "metricsApps": sum(app["downloads"] is not None for app in apps), "forgeHostCounts": {host: sum((app.get("repo") or {}).get("host") == host for app in apps) for host in ("github", "gitlab", "codeberg")}, "iconCandidates": icon_stats["candidates"], "cachedIcons": icon_stats["cached"], "iconFailures": icon_stats["failures"], "metrics": {"name": "F-Droid Metrics Distilled", "url": "https://grote.gitlab.io/fdroid-metrics-distilled/", "scope": "Top 50 packages; summed weekly measurements", "from": min((x["from"] for x in metrics.values()), default=None), "to": max((x["to"] for x in metrics.values()), default=None)}, "failures": failures}
    write_public_data(apps)
    write_json(ROOT / "data/meta.json", meta)
    logging.info("Source health\n%-24s %-16s %8s", "Source", "Status", "Records")
    for health in source_health:
        logging.info("%-24s %-16s %8d%s", next(source["name"] for source in config if source["id"] == health["id"]), health["status"], health["records"], " (stale)" if health["stale"] else "")
    logging.info("Built %d known apps, %d ranking eligible, %d archive-only (%d GitHub, %d GitLab, %d Codeberg, %d metrics, %d/%d icons, %d icon failures)", len(apps), ranking_eligible, archive_only, forge_counts["github"], forge_counts["gitlab"], forge_counts["codeberg"], meta["metricsApps"], icon_stats["cached"], icon_stats["candidates"], icon_stats["failures"])


if __name__ == "__main__":
    main()
