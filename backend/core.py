"""Pure plan and document reconciliation helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse


DOC_PATH = re.compile(r"^/document/d/([A-Za-z0-9_-]{20,})/(?:edit)?/?$")
VERSION = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(\d+)$")
JS_TRIM_WHITESPACE = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def google_document_id(url: str | None) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "docs.google.com":
        return None
    match = DOC_PATH.fullmatch(parsed.path)
    return match.group(1) if match else None


def apply_document_links(plans: dict, registry: dict) -> list[str]:
    """Validate the reviewed registry and derive its effective links in plans."""
    if registry.get("schema_version") != 1 or not isinstance(registry.get("documents"), list):
        return ["documents/documents.json: unsupported schema"]
    tracks = {track["id"]: track for prime in plans["primes"] for track in prime["tracks"]}
    errors: list[str] = []
    ids: set[str] = set()
    urls: set[str] = set()
    assignments: set[str] = set()
    for index, document in enumerate(registry["documents"]):
        at = f"documents[{index}]"
        doc_id = document.get("id")
        google_id = google_document_id(document.get("url"))
        if not isinstance(doc_id, str) or not re.fullmatch(r"[a-z0-9-]+", doc_id) or doc_id in ids:
            errors.append(f"{at}.id: missing or duplicate slug")
        if not google_id or google_id in urls:
            errors.append(f"{at}.url: must be a unique Google Doc link")
        sources = document.get("track_sources")
        if not document.get("name") or not isinstance(sources, list) or not sources:
            errors.append(f"{at}: name and at least one track source are required")
            continue
        ids.add(doc_id)
        urls.add(google_id)
        for source in sources:
            assignment = f"{source.get('track_id')}:{source.get('label')}"
            track = tracks.get(source.get("track_id"))
            if not track or not source.get("label") or assignment in assignments:
                errors.append(f"{at}.track_sources: unknown track, missing label, or duplicate mapping {assignment}")
                continue
            assignments.add(assignment)
            reference = next((item for item in track["sources"] if item.get("label") == source["label"]), None)
            if reference is None:
                reference = {"label": source["label"], "visibility": "restricted"}
                track["sources"].append(reference)
            reference["url"] = document["url"]
    return errors


def normalize_document_text(value: str) -> str:
    # Match scripts/watch-documents.mjs so the server and GitHub agree on hashes.
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    return normalized.strip(JS_TRIM_WHITESPACE) + "\n"


def document_hash(value: str) -> str:
    return hashlib.sha256(normalize_document_text(value).encode("utf-8")).hexdigest()


def compare_documents(registry: dict, baseline: dict, text_by_id: dict[str, str]) -> list[dict]:
    result = []
    for document in registry["documents"]:
        digest = document_hash(text_by_id[document["id"]])
        previous = baseline.get("documents", {}).get(document["id"], {}).get("sha256")
        result.append({
            "id": document["id"], "name": document["name"], "url": document["url"],
            "tracks": [source["track_id"] for source in document["track_sources"]],
            "status": "new" if previous is None else "unchanged" if previous == digest else "changed",
            "sha256": digest, "previous_sha256": previous,
        })
    return result


def next_plan_version(current: str, now: datetime | None = None) -> str:
    match = VERSION.fullmatch(current)
    if not match:
        raise ValueError(f"Unsupported plan version: {current}")
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    return f"{today}.1" if today > match.group(1) else f"{match.group(1)}.{int(match.group(2)) + 1}"


def apply_track_proposal(plans: dict, document: dict, proposal: dict) -> dict:
    if not isinstance(proposal, dict) or not isinstance(proposal.get("tracks"), list) or not isinstance(proposal.get("review_notes"), list):
        raise ValueError(f"{document['id']}: invalid model response")
    allowed = {source["track_id"] for source in document["track_sources"]}
    seen: set[str] = set()
    updated = copy.deepcopy(plans)
    for item in proposal["tracks"]:
        if not isinstance(item, dict) or not isinstance(item.get("track_id"), str) or not isinstance(item.get("track_json"), str) or item["track_id"] not in allowed or item["track_id"] in seen:
            raise ValueError(f"{document['id']}: response contains an unknown or duplicate track")
        track_id = item["track_id"]
        seen.add(track_id)
        try:
            revised = json.loads(item["track_json"])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{document['id']}: invalid track JSON") from exc
        match = next(((prime, index) for prime in updated["primes"] for index, track in enumerate(prime["tracks"]) if track["id"] == track_id), None)
        if not match or not isinstance(revised, dict) or revised.get("id") != track_id or not isinstance(revised.get("phases"), list):
            raise ValueError(f"{document['id']}: track identity or phases changed unexpectedly")
        prime, index = match
        original = prime["tracks"][index]
        for key in ("facet", "kind", "instance", "visible", "rate_limit_metadata", "exposure_source", "skyeco_url", "skyeco_label"):
            if original.get(key) != revised.get(key):
                raise ValueError(f"{document['id']}: {track_id}.{key} needs manual review")
        sources = revised.get("sources")
        if not isinstance(sources, list) or not all(isinstance(source, dict) for source in sources):
            raise ValueError(f"{document['id']}: sources are missing")
        for source in original["sources"]:
            if source.get("visibility") == "public" and source not in sources:
                raise ValueError(f"{document['id']}: public source links need manual review")
        if any(isinstance(source.get("url"), str) and not source["url"].startswith("https://") for source in sources):
            raise ValueError(f"{document['id']}: source links must use HTTPS")
        for mapping in document["track_sources"]:
            if mapping["track_id"] != track_id:
                continue
            link = next((source for source in sources if source.get("label") == mapping["label"]), None)
            if google_document_id(link.get("url") if link else None) != google_document_id(document["url"]):
                raise ValueError(f"{document['id']}: mapped Google Doc source link must be preserved")
        old_ids = [phase["id"] for phase in original["phases"]]
        new_ids = [phase.get("id") if isinstance(phase, dict) else None for phase in revised["phases"]]
        if not all(isinstance(phase_id, str) for phase_id in new_ids):
            raise ValueError(f"{document['id']}: phase IDs must be strings")
        if new_ids[: len(old_ids)] != old_ids or len(set(new_ids)) != len(new_ids):
            raise ValueError(f"{document['id']}: existing phase IDs and order must be preserved")
        prime["tracks"][index] = revised
    return updated


def render_plan_changes(original_text: str, original: dict, revised: dict) -> tuple[str, list[str]]:
    """Replace revised track objects only, retaining the surrounding hand formatting."""
    text = original_text
    changed = []
    old_tracks = {track["id"]: track for prime in original["primes"] for track in prime["tracks"]}
    for prime in revised["primes"]:
        for track in prime["tracks"]:
            if track == old_tracks.get(track["id"]):
                continue
            track_id = track["id"]
            regex = re.compile(r'\{\s*"id"\s*:\s*' + re.escape(json.dumps(track_id)) + r"\s*,")
            matches = list(regex.finditer(text))
            if len(matches) != 1:
                raise ValueError(f"Could not locate unique track {track_id} in plans/plans.json")
            start = matches[0].start()
            found, length = json.JSONDecoder().raw_decode(text[start:])
            if found.get("id") != track_id or not isinstance(found.get("phases"), list):
                raise ValueError(f"Invalid track range for {track_id}")
            indent = re.match(r"\s*", text[text.rfind("\n", 0, start) + 1 : start]).group()
            replacement = json.dumps(track, indent=2, ensure_ascii=False).replace("\n", "\n" + indent)
            text = text[:start] + replacement + text[start + length :]
            changed.append(track_id)
    version_line = '"version": ' + json.dumps(original["version"])
    if version_line not in text:
        raise ValueError("Could not locate plans.version")
    text = text.replace(version_line, '"version": ' + json.dumps(revised["version"]), 1)
    if json.loads(text) != revised:
        raise ValueError("Rendered plan differs from validated proposal")
    return text, changed


def make_baseline(documents: list[dict], version: str, now: datetime | None = None) -> dict:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {"schema_version": 1, "captured_at_utc": timestamp, "plan_version": version,
            "documents": {item["id"]: {"sha256": item["sha256"]} for item in documents}}
