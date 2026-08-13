import logging
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from utils import fetch_json, iso_date, localized


def _latest_version(versions):
    candidates = list((versions or {}).values())
    if not candidates:
        return {}
    return max(candidates, key=lambda item: (item.get("manifest", {}).get("versionCode", -1), item.get("added", 0)))


def _icon_url(repo_url, metadata):
    icon = localized(metadata.get("icon"))
    if isinstance(icon, dict):
        icon = icon.get("name")
    return urljoin(repo_url.rstrip("/") + "/", str(icon).lstrip("/")) if icon else None


def _safe_url(value):
    if not isinstance(value, str) or not value:
        return value
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not hostname:
        return None
    host = hostname
    if port:
        host += f":{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, ""))


def read_repository(config):
    url = config["url"].rstrip("/") + "/index-v2.json"
    logging.info("Reading %s", config["name"])
    index = fetch_json(url)
    packages = index.get("packages")
    if not isinstance(packages, dict):
        raise ValueError(f"invalid F-Droid index from {config['name']}: packages is not an object")
    apps = []
    for package, raw in packages.items():
        metadata = raw.get("metadata", {})
        version = _latest_version(raw.get("versions"))
        manifest = version.get("manifest", {})
        anti_features = metadata.get("antiFeatures") or version.get("antiFeatures") or {}
        if isinstance(anti_features, dict):
            anti_features = list(anti_features)
        donate = metadata.get("donate")
        if isinstance(donate, list):
            donate = donate[0] if donate else None
        apps.append({
            "package": package,
            "name": localized(metadata.get("name")) or manifest.get("label") or package,
            "summary": localized(metadata.get("summary")),
            "description": localized(metadata.get("description")),
            "categories": metadata.get("categories") or [],
            "license": metadata.get("license"),
            "website": _safe_url(metadata.get("webSite")),
            "sourceCode": _safe_url(metadata.get("sourceCode")),
            "issueTracker": _safe_url(metadata.get("issueTracker")),
            "donate": _safe_url(donate),
            "iconCandidates": [_icon_url(config["url"], metadata)] if _icon_url(config["url"], metadata) else [],
            "latestVersion": manifest.get("versionName"),
            "latestVersionCode": manifest.get("versionCode"),
            "updatedAt": iso_date(metadata.get("lastUpdated") or version.get("added")),
            "addedAt": iso_date(metadata.get("added")),
            "antiFeatures": sorted(anti_features),
            "catalog": {
                "id": config["id"], "name": config["name"],
                "url": config["appUrl"].format(package=quote(package)),
                "authority": config["authority"], "channel": config["channel"],
                "rankingEligible": config["rankingEligible"],
            },
        })
    logging.info("Read %d apps from %s", len(apps), config["name"])
    return apps


def merge_catalogs(catalog_apps):
    merged = {}
    fields = ("name", "summary", "description", "license", "website", "sourceCode", "issueTracker", "donate", "addedAt")
    for apps in catalog_apps:
        for app in apps:
            target = merged.setdefault(app["package"], {"package": app["package"], "categories": [], "antiFeatures": [], "sources": []})
            for field in fields:
                if not target.get(field) and app.get(field) is not None:
                    target[field] = app[field]
            current_version = target.get("latestVersionCode")
            candidate_version = app.get("latestVersionCode")
            if current_version is None or (candidate_version is not None and candidate_version > current_version):
                target["latestVersion"] = app.get("latestVersion")
                target["latestVersionCode"] = candidate_version
            if app.get("updatedAt") and app["updatedAt"] > (target.get("updatedAt") or ""):
                target["updatedAt"] = app["updatedAt"]
            target["categories"] = sorted(set(target["categories"] + app["categories"]))
            target["antiFeatures"] = sorted(set(target["antiFeatures"] + app["antiFeatures"]))
            for icon in app["iconCandidates"]:
                if icon not in target.setdefault("iconCandidates", []):
                    target["iconCandidates"].append(icon)
            if not any(source["id"] == app["catalog"]["id"] for source in target["sources"]):
                target["sources"].append(app["catalog"])
            target["rankingEligible"] = any(source.get("rankingEligible", True) for source in target["sources"])
    return list(merged.values())
