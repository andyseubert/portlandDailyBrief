from datetime import datetime, timedelta, timezone
import json
from .records import parse_time, safe_url


def get_weather(fetcher, settings, now):
    j = settings["jurisdiction"]
    point = f"{j['latitude']},{j['longitude']}"
    result = {"point": point, "periods": [], "alerts": [], "gaps": [], "coverage": "NWS alerts for this central Portland point; not all of Multnomah County."}
    # Current forecast/active alerts must never masquerade as historical weather.
    if abs((datetime.now(timezone.utc) - now).total_seconds()) > 3600:
        result["gaps"].append("Historical run: current forecast and active alerts intentionally omitted.")
        return result
    try:
        data, point_meta = fetcher.get("https://api.weather.gov/points/" + point)
        p = json.loads(data)["properties"]
        forecast_url = safe_url(p["forecast"], ["api.weather.gov"])
        data, meta = fetcher.get(forecast_url)
        forecast = json.loads(data)["properties"]
        updated = parse_time(forecast.get("updated") or forecast.get("updateTime"))
        if meta["status"] == "stale_cache" or point_meta["status"] == "stale_cache" or updated is None or now - updated > timedelta(hours=24):
            raise ValueError("Forecast unavailable or more than 24 hours old")
        periods = [p for p in forecast["periods"] if parse_time(p.get("endTime")) and parse_time(p["endTime"]) > now]
        if not periods:
            raise ValueError("No current forecast periods")
        result.update(forecast_url=forecast_url, updated_at=updated.isoformat(), fetched_at=meta["fetched_at"], periods=periods[:3])
    except Exception:
        result["gaps"].append("Portland forecast could not be verified from NWS; check weather.gov/pqr.")
    alert_url = "https://api.weather.gov/alerts/active?point=" + point
    try:
        data, meta = fetcher.get(alert_url)
        if meta["status"] == "stale_cache":
            raise ValueError("Stale alerts cannot establish current hazard status")
        obj = json.loads(data)
        if not isinstance(obj.get("features"), list):
            raise ValueError("Invalid alerts response")
        alerts = []
        for feature in obj["features"]:
            a = feature["properties"]
            expiry = parse_time(a.get("expires"))
            if expiry and expiry <= now:
                continue
            url = safe_url(feature.get("id", ""), ["api.weather.gov"])
            alerts.append({"event": a["event"], "headline": a.get("headline", ""), "severity": a.get("severity"), "expires": a.get("expires"), "url": url})
        result.update(alert_url=alert_url, alerts=alerts, alerts_checked_at=meta["fetched_at"], alerts_verified=True)
    except Exception:
        result["gaps"].append("Active NWS alerts could not be checked; this does not mean there are no alerts.")
    return result
