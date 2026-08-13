import logging

from utils import fetch_json

BASE = "https://grote.gitlab.io/fdroid-metrics-distilled"


def read_metrics():
    metrics = {}
    packages = fetch_json(BASE + "/top/50.json")
    for package in packages:
        try:
            weeks = fetch_json(f"{BASE}/apps/{package}.json", attempts=2)
            if weeks:
                metrics[package] = {"fdroid": sum(value for value in weeks.values() if isinstance(value, int)), "from": min(weeks), "to": max(weeks)}
        except Exception as error:
            logging.warning("Could not read metrics for %s: %s", package, error)
    return metrics
