import json
import logging
import os
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, unquote, urlparse

from utils import fetch_json

GITHUB_GRAPHQL = "https://api.github.com/graphql"
GITHUB_BATCH_SIZE = 50
FORGEJO_HOSTS = {"codeberg.org"}


def normalize_repository(url):
    if not url:
        return None
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    parts = [unquote(item) for item in parsed.path.strip("/").split("/") if item]
    if host in ("github.com", "www.github.com") and len(parts) >= 2:
        owner, repo = parts[:2]
        repo = repo.removesuffix(".git")
        if not owner or not repo:
            return None
        return {"host": "github", "owner": owner, "name": repo, "url": f"https://github.com/{owner}/{repo}"}
    if host == "gitlab.com" and len(parts) >= 2:
        cut = next((i for i, item in enumerate(parts) if item in ("-", "releases", "issues", "tree", "blob")), len(parts))
        path = parts[:cut]
        if len(path) >= 2:
            repo = path[-1].removesuffix(".git")
            owner = "/".join(path[:-1])
            if not repo:
                return None
            return {"host": "gitlab", "owner": owner, "name": repo, "url": f"https://gitlab.com/{owner}/{repo}"}
    if host in FORGEJO_HOSTS and len(parts) >= 2:
        owner, repo = parts[:2]
        repo = repo.removesuffix(".git")
        if owner and repo:
            return {"host": "codeberg", "owner": owner, "name": repo, "url": f"https://codeberg.org/{owner}/{repo}"}
    return None


def enrich_github(repos, token):
    enriched = {}
    unavailable = set()
    failed = set()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for start in range(0, len(repos), GITHUB_BATCH_SIZE):
        batch = repos[start:start + GITHUB_BATCH_SIZE]
        fields = []
        for index, repo in enumerate(batch):
            owner = json.dumps(repo["owner"])
            name = json.dumps(repo["name"])
            fields.append(f'''r{index}:repository(owner:{owner},name:{name}){{stargazerCount forkCount isArchived createdAt updatedAt pushedAt defaultBranchRef{{name}} licenseInfo{{spdxId}} latestRelease{{publishedAt tagName}}}}''')
        payload = json.dumps({"query": "query{" + " ".join(fields) + " rateLimit{remaining resetAt}}"}).encode()
        try:
            response = fetch_json(GITHUB_GRAPHQL, headers=headers, data=payload)
        except urllib.error.HTTPError as error:
            if error.code in (403, 429) and error.headers.get("X-RateLimit-Remaining") == "0":
                logging.error("GitHub GraphQL rate limit exhausted: HTTP %d; resets at %s", error.code, error.headers.get("X-RateLimit-Reset", "unknown"))
            elif error.code in (401, 403):
                logging.error("GitHub GraphQL HTTP authentication failure: HTTP %d", error.code)
            else:
                logging.error("GitHub GraphQL HTTP failure: HTTP %d", error.code)
            raise
        except Exception as error:
            logging.error("GitHub GraphQL request failure after %d repositories: %s", start, error)
            raise
        if start == 0:
            rate_limit = (response.get("data") or {}).get("rateLimit") or {}
            logging.info("GitHub GraphQL first batch: HTTP success, rate limit remaining=%s", rate_limit.get("remaining", "unknown"))
            if rate_limit.get("remaining") == 0:
                reset_at = rate_limit.get("resetAt") or "unknown"
                logging.error("GitHub GraphQL rate limit exhausted; resets at %s", reset_at)
                raise RuntimeError(f"GitHub GraphQL rate limit exhausted; resets at {reset_at}")

        errors = response.get("errors") or []
        errors_by_alias = {}
        for error in errors:
            path = error.get("path") or []
            alias = path[0] if path and isinstance(path[0], str) else None
            error_type = (error.get("type") or (error.get("extensions") or {}).get("type") or "unknown").upper()
            message = error.get("message") or "unknown GraphQL error"
            if alias:
                errors_by_alias[alias] = (error_type, message)
            else:
                logging.error("GitHub GraphQL permission/auth failure (%s): %s", error_type, message)
                raise RuntimeError(f"GitHub GraphQL failed: {error_type}: {message}")
        data = response.get("data") or {}
        for index, repo in enumerate(batch):
            alias = f"r{index}"
            key = repo["url"].lower()
            item = data.get(alias)
            if not item:
                error_type, message = errors_by_alias.get(alias, ("unknown", "repository returned no data"))
                if error_type == "NOT_FOUND" or "could not resolve to a repository" in message.lower():
                    unavailable.add(key)
                    logging.warning("GitHub repository unavailable: %s", repo["url"])
                elif error_type in ("FORBIDDEN", "UNAUTHORIZED") or "permission" in message.lower():
                    logging.error("GitHub GraphQL permission/auth failure for %s (%s): %s", repo["url"], error_type, message)
                    raise RuntimeError(f"GitHub GraphQL permission/auth failure: {message}")
                elif "rate limit" in message.lower():
                    logging.error("GitHub GraphQL rate limit exhausted: %s", message)
                    raise RuntimeError(f"GitHub GraphQL rate limit exhausted: {message}")
                else:
                    failed.add(key)
                    logging.error("GitHub repository enrichment failed for %s (%s): %s", repo["url"], error_type, message)
                continue
            license_info = item.get("licenseInfo") or {}
            release = item.get("latestRelease") or {}
            result = {
                **repo,
                "stars": item.get("stargazerCount"),
                "forks": item.get("forkCount"),
                "archived": item.get("isArchived"),
                "createdAt": item.get("createdAt"),
                "updatedAt": item.get("updatedAt"),
                "pushedAt": item.get("pushedAt"),
                "defaultBranch": (item.get("defaultBranchRef") or {}).get("name"),
                "latestReleaseAt": release.get("publishedAt"),
                "latestReleaseTag": release.get("tagName"),
            }
            if license_info.get("spdxId") not in (None, "NOASSERTION"):
                result["license"] = license_info["spdxId"]
            if not isinstance(result["stars"], int):
                failed.add(key)
                logging.error("GitHub repository returned non-numeric stars: %s", repo["url"])
                continue
            enriched[key] = result
        logging.info("Enriched %d/%d GitHub repositories", min(start + len(batch), len(repos)), len(repos))
    return enriched, unavailable, failed


def enrich_gitlab(repo):
    project = quote(f"{repo['owner']}/{repo['name']}", safe="")
    data = fetch_json(f"https://gitlab.com/api/v4/projects/{project}")
    return {**repo, "stars": data.get("star_count"), "forks": data.get("forks_count"), "openIssues": data.get("open_issues_count"), "archived": data.get("archived"), "createdAt": data.get("created_at"), "updatedAt": data.get("last_activity_at"), "pushedAt": None, "defaultBranch": data.get("default_branch")}


def enrich_forgejo(repo):
    if repo["host"] not in ("codeberg",) or urlparse(repo["url"]).hostname not in FORGEJO_HOSTS:
        raise ValueError("Forgejo host is not allowlisted")
    owner = quote(repo["owner"], safe="")
    name = quote(repo["name"], safe="")
    data = fetch_json(f"https://codeberg.org/api/v1/repos/{owner}/{name}")
    result = {
        **repo, "stars": data.get("stars_count"), "forks": data.get("forks_count"),
        "openIssues": data.get("open_issues_count"), "archived": data.get("archived"),
        "createdAt": data.get("created_at"), "updatedAt": data.get("updated_at"),
        "pushedAt": data.get("updated_at"), "defaultBranch": data.get("default_branch"),
    }
    license_name = (data.get("license") or {}).get("spdx_id")
    if license_name:
        result["license"] = license_name
    return result


def enrich_apps(apps, previous_apps, limit=None):
    previous = {}
    for app in previous_apps:
        repo = app.get("repo") or {}
        if repo.get("url") and isinstance(repo.get("stars"), int):
            previous[repo["url"].lower()] = repo

    for app in apps:
        app["repo"] = normalize_repository(app.get("sourceCode"))
    repos = {}
    for app in apps:
        repo = app.get("repo")
        if repo:
            repos.setdefault(repo["url"].lower(), repo)
    selected = list(repos.values())[:limit] if limit is not None else list(repos.values())
    cache = dict(previous)

    github_repos = [repo for repo in selected if repo["host"] == "github"]
    token = os.environ.get("GITHUB_TOKEN")
    logging.info("GitHub authentication: %s", "available" if token else "unavailable")
    logging.info("GitHub app records: %d", sum((app.get("repo") or {}).get("host") == "github" for app in apps))
    logging.info("Unique canonical GitHub repos: %d", sum(repo["host"] == "github" for repo in repos.values()))
    github_enriched = {}
    github_unavailable = set()
    github_failed = set()
    if github_repos and token:
        logging.info("GitHub enrichment mode: authenticated GraphQL")
        github_enriched, github_unavailable, github_failed = enrich_github(github_repos, token)
        cache.update(github_enriched)
    elif github_repos:
        logging.warning("GITHUB_TOKEN is not set; skipping GitHub enrichment and preserving cached values")

    gitlab_enriched = set()
    gitlab_failed = set()
    gitlab_repos = [item for item in selected if item["host"] == "gitlab" and item["url"].lower() not in cache]
    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = {executor.submit(enrich_gitlab, repo): repo for repo in gitlab_repos}
        for request in as_completed(requests):
            repo = requests[request]
            key = repo["url"].lower()
            try:
                cache[key] = request.result()
                if isinstance(cache[key].get("stars"), int):
                    gitlab_enriched.add(key)
                else:
                    gitlab_failed.add(key)
            except Exception as error:
                gitlab_failed.add(key)
                logging.warning("Could not enrich %s: %s", repo["url"], error)

    codeberg_enriched = set()
    codeberg_failed = set()
    codeberg_repos = [item for item in selected if item["host"] == "codeberg"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        requests = {executor.submit(enrich_forgejo, repo): repo for repo in codeberg_repos}
        for request in as_completed(requests):
            repo = requests[request]
            key = repo["url"].lower()
            try:
                cache[key] = request.result()
                if isinstance(cache[key].get("stars"), int):
                    codeberg_enriched.add(key)
                else:
                    codeberg_failed.add(key)
            except Exception as error:
                codeberg_failed.add(key)
                logging.warning("Could not enrich %s: %s", repo["url"], error)

    counts = {"github": 0, "gitlab": 0, "codeberg": 0, "githubAppRecords": sum((app.get("repo") or {}).get("host") == "github" for app in apps), "recognizedGithub": len([repo for repo in repos.values() if repo["host"] == "github"]), "recognizedGitlab": len([repo for repo in repos.values() if repo["host"] == "gitlab"]), "recognizedCodeberg": len([repo for repo in repos.values() if repo["host"] == "codeberg"]), "githubEnrichedRepos": len(github_enriched), "githubUnavailableRepos": len(github_unavailable), "githubFailedRepos": len(github_failed), "gitlabEnrichedRepos": len(gitlab_enriched), "gitlabFailedRepos": len(gitlab_failed), "codebergEnrichedRepos": len(codeberg_enriched), "codebergFailedRepos": len(codeberg_failed), "missingRecognized": 0}
    for app in apps:
        repo = app.get("repo")
        if repo and repo["url"].lower() in cache:
            app["repo"] = {**repo, **cache[repo["url"].lower()]}
            if isinstance(app["repo"].get("stars"), int):
                counts[repo["host"]] += 1
        if repo and not isinstance(app["repo"].get("stars"), int):
            counts["missingRecognized"] += 1
    logging.info("Star coverage: %d GitHub apps, %d GitLab apps, %d Codeberg apps; %d recognized apps missing numeric stars", counts["github"], counts["gitlab"], counts["codeberg"], counts["missingRecognized"])
    no_source = sum(not app.get("sourceCode") for app in apps)
    unsupported_forge = sum(bool(app.get("sourceCode")) and not app.get("repo") for app in apps)
    logging.info("Missing star reasons: no source URL=%d, unsupported forge=%d, recognized repo without enrichment=%d", no_source, unsupported_forge, counts["missingRecognized"])
    return counts
