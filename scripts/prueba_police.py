#!/usr/bin/env python3
"""Audit and optionally align Data Documentation Confluence pages."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from typing import Any


DEFAULT_SPACE_NAME = "Analytics Documentation Team"
DEFAULT_STALE_DAYS = 180
DEFAULT_ENGINE_ORDER = (
    "Paid Media",
    "Owned Media",
    "Earned Media",
    "Retail Media",
)
DEFAULT_ALIGN_STATUSES = ("Incomplete", "Empty")
REQUIRED_SECTIONS = (
    "Overview",
    "Scope",
    "Business Use",
    "KPIs",
    "Grain & Keys",
    "Joins",
    "Important Fields",
    "Mapping",
    "Data Logic (Raw + Silver + Gold)",
    "Refresh",
    "Notes/Limitations",
)
SECTION_WORD_THRESHOLDS = {
    "Overview": 10,
    "Scope": 10,
    "Business Use": 10,
    "KPIs": 4,
    "Grain & Keys": 4,
    "Joins": 4,
    "Important Fields": 4,
    "Mapping": 6,
    "Data Logic (Raw + Silver + Gold)": 6,
    "Refresh": 2,
    "Notes/Limitations": 4,
}
PLACEHOLDER_PATTERNS = (
    re.compile(r"\btbd\b"),
    re.compile(r"\btodo\b"),
    re.compile(r"\bcoming soon\b"),
    re.compile(r"\bnot specified\b"),
    re.compile(r"\bto be defined\b"),
    re.compile(r"\bfill (?:me|this|in)\b"),
    re.compile(r"^\s*(?:n/?a|-|pending)\s*$"),
)
OBSOLETE_PATTERNS = (
    re.compile(r"\bthis page is obsolete\b"),
    re.compile(r"\bobsolete page\b"),
    re.compile(r"\bthis page is deprecated\b"),
    re.compile(r"\bdeprecated page\b"),
    re.compile(r"\bthis documentation is deprecated\b"),
    re.compile(r"\barchived documentation\b"),
    re.compile(r"\bdo not use\b"),
    re.compile(r"\bno longer maintained\b"),
)
HEADING_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", flags=re.I | re.S)
HELPER_PATH_CANDIDATES = (
    Path(__file__).resolve().parents[2]
    / "confluence-content-manager"
    / "scripts"
    / "confluence_manager.py",
    Path("./confluence-content-manager/scripts/confluence_manager.py"),
)


@dataclass
class ExtractedSection:
    heading: str
    normalized_heading: str
    level: int
    body_text: str
    body_words: int


@dataclass
class PageAudit:
    engine: str
    title: str
    page_id: str
    status: str
    last_updated: str | None
    word_count: int
    matched_sections: list[str]
    missing_sections: list[str]
    weak_sections: list[str]
    heading_variants: list[str]
    outdated_reasons: list[str]
    suggestions: list[str]
    notes: list[str]
    exact_template_match: bool


def load_helper() -> Any:
    helper_path = next((path for path in HELPER_PATH_CANDIDATES if path.exists()), None)
    if helper_path is None:
        raise RuntimeError(
            "Missing dependency: confluence-content-manager/scripts/confluence_manager.py"
        )

    module_name = "prueba_police_confluence_manager"
    spec = importlib.util.spec_from_file_location(module_name, helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load helper module from {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit and optionally align Data Documentation documentation in Confluence."
    )
    parser.add_argument("--base-url", help="Optional Confluence base URL override")
    parser.add_argument("--cloud-id", help="Optional Atlassian cloud ID override")
    parser.add_argument(
        "--auth-mode",
        choices=("auto", "bearer", "basic"),
        default="auto",
        help="Authentication mode override",
    )
    parser.add_argument("--email", help="Optional Confluence email override")
    parser.add_argument(
        "--token-file",
        default="~/.config/confluence/api.token",
        help="Path to the token file",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Audit Data Documentation pages")
    add_common_filters(audit)
    audit.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help="Mark pages as Outdated when older than this number of days",
    )

    align = subparsers.add_parser(
        "align",
        help="Preview or apply template-alignment updates for non-OK pages",
    )
    add_common_filters(align)
    align.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help="Use the same stale threshold as the audit step",
    )
    align.add_argument(
        "--status",
        default=",".join(DEFAULT_ALIGN_STATUSES),
        help="Comma-separated statuses to target, for example Incomplete,Empty",
    )
    align.add_argument(
        "--apply",
        action="store_true",
        help="Apply the updates in Confluence. Without this flag the command is preview-only.",
    )

    return parser.parse_args()


def add_common_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--space", default=DEFAULT_SPACE_NAME, help="Space name or key")
    parser.add_argument(
        "--engine",
        action="append",
        default=[],
        help="Repeat to limit the scope to one or more engine folders",
    )
    parser.add_argument(
        "--page",
        action="append",
        default=[],
        help="Repeat to limit the scope to one or more exact page titles",
    )


def normalize_text(value: str) -> str:
    text = unescape(value).casefold().replace("&", " and ")
    text = re.sub(r"^\d+(?:\.\d+)*\s*[-.)]?\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_title(value: str) -> str:
    normalized = normalize_text(value)
    normalized = re.sub(r"\bdata\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def split_csv_values(raw: str) -> set[str]:
    return {item.strip().casefold() for item in raw.split(",") if item.strip()}


def parse_datetime(cm: Any, raw: str | None) -> datetime | None:
    if hasattr(cm, "parse_iso_datetime"):
        return cm.parse_iso_datetime(raw)
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def extract_sections(cm: Any, raw_html: str) -> list[ExtractedSection]:
    matches = list(HEADING_RE.finditer(raw_html))
    sections: list[ExtractedSection] = []
    for index, match in enumerate(matches):
        heading = cm.strip_tags(match.group(2))
        level = int(match.group(1))
        end = len(raw_html)
        for next_match in matches[index + 1 :]:
            next_level = int(next_match.group(1))
            if next_level <= level:
                end = next_match.start()
                break
        body_text = cm.strip_tags(raw_html[match.end() : end])
        sections.append(
            ExtractedSection(
                heading=heading,
                normalized_heading=normalize_text(heading),
                level=level,
                body_text=body_text,
                body_words=len(body_text.split()),
            )
        )
    return sections


def classify_heading(normalized_heading: str) -> str | None:
    collapsed = normalized_heading.replace(" ", "")
    if "overview" in collapsed:
        return "Overview"
    if normalized_heading.startswith("scope") or " scope " in f" {normalized_heading} ":
        return "Scope"
    if ("business" in normalized_heading and ("use" in normalized_heading or "purpose" in normalized_heading)) or "use case" in normalized_heading:
        return "Business Use"
    if "kpi" in normalized_heading or "calculation" in normalized_heading:
        return "KPIs"
    if "grain" in normalized_heading and ("key" in normalized_heading or "identif" in normalized_heading):
        return "Grain & Keys"
    if normalized_heading.startswith("joins") or " join" in normalized_heading:
        return "Joins"
    if "connect" in normalized_heading and "table" in normalized_heading:
        return "Joins"
    if "important field" in normalized_heading or "data field" in normalized_heading:
        return "Important Fields"
    if normalized_heading == "fields":
        return "Important Fields"
    if normalized_heading.startswith("mapping") or " source to l1 " in f" {normalized_heading} ":
        return "Mapping"
    if "data logic" in normalized_heading or "data lineage" in normalized_heading:
        return "Data Logic (Raw + Silver + Gold)"
    if normalized_heading.startswith("refresh") or " refresh" in normalized_heading:
        return "Refresh"
    if "how often" in normalized_heading:
        return "Refresh"
    if "note" in normalized_heading or "limitation" in normalized_heading:
        return "Notes/Limitations"
    return None


def exact_heading_value(section_name: str) -> str:
    return normalize_text(section_name)


def is_exact_heading(section_name: str, normalized_heading: str) -> bool:
    return normalized_heading == exact_heading_value(section_name)


def choose_section(section_name: str, sections: list[ExtractedSection]) -> ExtractedSection | None:
    candidates = [section for section in sections if classify_heading(section.normalized_heading) == section_name]
    if not candidates:
        return None
    exact = [section for section in candidates if is_exact_heading(section_name, section.normalized_heading)]
    pool = exact or candidates
    return max(pool, key=lambda section: section.body_words)


def looks_placeholder(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return True
    if normalized in {"n a", "na", "pending", "todo", "tbd", "coming soon", "not specified"}:
        return True
    if len(normalized.split()) > 12:
        return False
    return any(pattern.search(normalized) for pattern in PLACEHOLDER_PATTERNS)


def find_obsolete_markers(text: str) -> list[str]:
    normalized = normalize_text(text)
    matches: list[str] = []
    for pattern in OBSOLETE_PATTERNS:
        found = pattern.search(normalized)
        if found:
            matches.append(found.group(0))
    return sorted(set(matches))


def sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", unescape(value).upper()).strip("_")
    return cleaned or "DATA_SOURCE"


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        normalized = normalize_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item.strip())
    return deduped


def infer_mapping_rows(cm: Any, signals: Any, title: str) -> list[list[str]]:
    source_prefix = sanitize_identifier(getattr(signals, "source_label", "") or title)
    schema_name_lookup = {
        normalize_text(field.name): field.name
        for field in getattr(signals, "schema_fields", [])
        if getattr(field, "name", "")
    }
    candidate_fields = dedupe_preserve_order(
        [
            *[field for field in getattr(signals, "key_fields", []) if field and normalize_text(field) != "not specified"],
            *[
                field
                for field in getattr(signals, "joins", {}).values()
                if field and normalize_text(field) != "not specified"
            ],
            *[
                row[0]
                for row in getattr(signals, "important_fields", [])
                if row
                and row[0]
                and normalize_text(row[0]) != "not specified"
                and normalize_text(row[0]) in schema_name_lookup
            ],
        ]
    )
    if not candidate_fields and getattr(signals, "schema_fields", None):
        candidate_fields = [
            field.name
            for field in getattr(signals, "schema_fields", [])[:6]
            if field.name and normalize_text(field.name) != "not specified"
        ]

    rows: list[list[str]] = []
    for field_name in candidate_fields[:8]:
        source_field = field_name.strip()
        if source_field.upper().startswith("L1_"):
            l1_field = source_field
            source_field = "source." + source_field[3:].casefold()
        else:
            l1_field = f"L1_{source_prefix}_{sanitize_identifier(source_field)}"
        rows.append([source_field, l1_field])

    return rows or [["Not specified", f"L1_{source_prefix}_FIELD"]]


def infer_mapping_transformations(signals: Any, lines: list[str]) -> list[str]:
    haystack = " ".join([*getattr(signals, "logic_points", []), *lines]).casefold()
    transformations: list[str] = []

    if any(token in haystack for token in ("fx", "exchange rate", "currency", "usd", "eur")):
        transformations.append("Currency conversion (FX)")
    if any(token in haystack for token in ("date", "calendar", "week", "month", "alignment")):
        transformations.append("Date alignment")
    if any(token in haystack for token in ("upper", "null", "standardized", "harmonis", "trim", "cast")):
        transformations.append("Standardization (UPPER, null handling, casting)")
    if any(token in haystack for token in ("join", "lookup", "merge")):
        transformations.append("Join enrichment for brand, product, or geography attributes")
    if any(token in haystack for token in ("case when", "roas", "cpa", "cpc", "derived", "calculated")):
        transformations.append("Derived KPI calculation")

    if not transformations:
        transformations = [
            "Column renaming into the L1 naming convention",
            "Standardization of keys and important attributes",
            "Validation of dates, numeric types, and null handling before publishing to L1",
        ]

    return dedupe_preserve_order(transformations)[:5]


def build_mapping_section(cm: Any, page: dict[str, Any]) -> str:
    title = str(page.get("title", "")).strip() or "Data Source"
    raw_html = page.get("body", {}).get("storage", {}).get("value", "")
    lines = cm.html_to_lines(raw_html)
    signals = cm.build_signal_package(title, raw_html, lines)
    mapping_rows = infer_mapping_rows(cm, signals, title)
    transformations = infer_mapping_transformations(signals, lines)
    return "\n".join(
        [
            "<h1>8. Mapping</h1>",
            "<p>This section summarizes how the most relevant source fields are transformed into L1 fields.</p>",
            cm.render_html_table(["Source Field", "L1 Field"], mapping_rows),
            "<p><strong>Main transformations applied:</strong></p>",
            cm.list_to_bullets(
                transformations,
                "Mapping rules were not explicitly documented in the previous version of the page.",
                limit=5,
            ),
        ]
    )


def build_aligned_body(cm: Any, page: dict[str, Any]) -> tuple[str, list[str]]:
    base_body, changes = cm.build_data_domain_body(page)
    mapping_section = build_mapping_section(cm, page)
    data_logic_heading = "<h1>8. Data Logic (Raw + Silver + Gold)</h1>"
    refresh_heading = "<h1>9. Refresh</h1>"
    notes_heading = "<h1>10. Notes / Limitations</h1>"

    if data_logic_heading not in base_body or refresh_heading not in base_body or notes_heading not in base_body:
        raise RuntimeError("Unexpected helper template format while injecting the Mapping section.")

    updated_body = base_body.replace(
        data_logic_heading,
        mapping_section + "\n<h1>9. Data Logic (Raw + Silver + Gold)</h1>",
        1,
    )
    updated_body = updated_body.replace(refresh_heading, "<h1>10. Refresh</h1>", 1)
    updated_body = updated_body.replace(notes_heading, "<h1>11. Notes / Limitations</h1>", 1)

    updated_changes = dedupe_preserve_order(
        [
            *changes,
            "mapping section generated from key source fields, inferred L1 targets, and main transformations",
        ]
    )
    return updated_body, updated_changes


def analyze_page(cm: Any, page: dict[str, Any], engine_name: str, stale_days: int) -> PageAudit:
    title = str(page.get("title", "")).strip() or "Untitled"
    page_id = str(page.get("id", ""))
    raw_html = page.get("body", {}).get("storage", {}).get("value", "")
    plain_text = cm.strip_tags(raw_html)
    word_count = len(plain_text.split())
    sections = extract_sections(cm, raw_html)

    matched_sections: list[str] = []
    missing_sections: list[str] = []
    weak_sections: list[str] = []
    heading_variants: list[str] = []

    for section_name in REQUIRED_SECTIONS:
        section = choose_section(section_name, sections)
        if section is None:
            missing_sections.append(section_name)
            continue
        matched_sections.append(section_name)
        if not is_exact_heading(section_name, section.normalized_heading):
            heading_variants.append(section_name)
        min_words = SECTION_WORD_THRESHOLDS[section_name]
        if section.body_words < min_words or looks_placeholder(section.body_text):
            weak_sections.append(section_name)

    updated_raw = (
        page.get("version", {}).get("when")
        or page.get("version", {}).get("createdAt")
        or page.get("updatedAt")
        or page.get("createdAt")
    )
    updated_at = parse_datetime(cm, updated_raw)
    outdated_reasons: list[str] = []
    if updated_at and updated_at < datetime.now(timezone.utc) - timedelta(days=stale_days):
        outdated_reasons.append(f"last update is older than {stale_days} days")
    obsolete_markers = find_obsolete_markers(f"{title} {plain_text}")
    if obsolete_markers:
        outdated_reasons.append(
            "contains obsolete markers: " + ", ".join(obsolete_markers)
        )

    if word_count < 25 or (word_count < 60 and not matched_sections):
        status = "Empty"
    elif outdated_reasons:
        status = "Outdated"
    elif missing_sections or weak_sections or word_count < 120:
        status = "Incomplete"
    else:
        status = "OK"

    # Business rule: the status should tell an owner what to do next, not just score a page.
    # Empty, outdated, and incomplete pages create different delivery risks and follow-ups.
    suggestions: list[str] = []
    if status == "Empty":
        suggestions.append("Rebuild the page from the required template or archive it if the source is no longer needed.")
    if missing_sections:
        suggestions.append("Add missing sections: " + ", ".join(missing_sections) + ".")
    if weak_sections:
        suggestions.append("Expand thin or placeholder sections: " + ", ".join(weak_sections) + ".")
    if heading_variants and status != "Empty":
        suggestions.append("Normalize section labels to the standard template headings.")
    if outdated_reasons:
        suggestions.append("Review freshness and confirm whether the page should be refreshed, archived, or replaced.")

    notes: list[str] = []
    if heading_variants and status == "OK":
        notes.append("all core sections exist, but some heading names drift from the standard")
    elif heading_variants:
        notes.append("heading variants detected")
    notes.extend(outdated_reasons)
    if not notes and status == "OK":
        notes.append("template-aligned")

    return PageAudit(
        engine=engine_name,
        title=title,
        page_id=page_id,
        status=status,
        last_updated=updated_at.date().isoformat() if updated_at else None,
        word_count=word_count,
        matched_sections=matched_sections,
        missing_sections=missing_sections,
        weak_sections=weak_sections,
        heading_variants=heading_variants,
        outdated_reasons=outdated_reasons,
        suggestions=suggestions,
        notes=notes,
        exact_template_match=not missing_sections and not heading_variants,
    )


def ordered_engines(immediate_children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {name.casefold(): index for index, name in enumerate(DEFAULT_ENGINE_ORDER)}
    return sorted(
        immediate_children,
        key=lambda page: (
            order.get(str(page.get("title", "")).casefold(), len(order)),
            str(page.get("title", "")).casefold(),
        ),
    )


def load_inventory(cm: Any, client: Any, args: argparse.Namespace) -> tuple[dict[str, Any], str, list[tuple[dict[str, Any], list[dict[str, Any]]]]]:
    space = client.find_space(args.space)
    root_page, route = cm.find_data_domains_root(client, space)
    descendants = client.search_content(
        f'space = "{space.get("key")}" AND ancestor = {root_page.get("id")}',
        limit=500,
        expand="body.storage,version,ancestors",
    )
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for page in descendants:
        ancestors = page.get("ancestors", [])
        parent_id = str(ancestors[-1]["id"]) if ancestors else ""
        by_parent.setdefault(parent_id, []).append(page)

    engine_filters = {item.casefold() for item in args.engine}
    page_filters = {item.casefold() for item in args.page}
    immediate_children = ordered_engines(by_parent.get(str(root_page.get("id")), []))

    grouped: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for engine_page in immediate_children:
        engine_name = str(engine_page.get("title", ""))
        if engine_filters and engine_name.casefold() not in engine_filters:
            continue
        leaf_pages = cm.descendant_leaf_pages(str(engine_page.get("id")), by_parent)
        filtered_pages: list[dict[str, Any]] = []
        for page in leaf_pages:
            if str(page.get("type", "page")).casefold() != "page":
                continue
            title = str(page.get("title", ""))
            if page_filters and title.casefold() not in page_filters:
                continue
            filtered_pages.append(page)
        grouped.append((engine_page, sorted(filtered_pages, key=lambda page: str(page.get("title", "")).casefold())))

    return space, route, grouped


def find_similar_pages(audits: list[PageAudit]) -> list[dict[str, str]]:
    similar: list[dict[str, str]] = []
    for index, left in enumerate(audits):
        for right in audits[index + 1 :]:
            if left.engine == right.engine:
                continue
            left_title = compact_title(left.title)
            right_title = compact_title(right.title)
            if not left_title or not right_title:
                continue
            ratio = SequenceMatcher(None, left_title, right_title).ratio()
            if left_title == right_title or ratio >= 0.92:
                similar.append(
                    {
                        "left_engine": left.engine,
                        "left_title": left.title,
                        "right_engine": right.engine,
                        "right_title": right.title,
                        "note": "informational only; cross-engine mirrors are allowed",
                    }
                )
    return similar


def build_audit_report(cm: Any, args: argparse.Namespace, grouped_pages: list[tuple[dict[str, Any], list[dict[str, Any]]]], route: str) -> dict[str, Any]:
    engine_reports: list[dict[str, Any]] = []
    flat_audits: list[PageAudit] = []
    status_counts = {status: 0 for status in ("OK", "Incomplete", "Empty", "Outdated")}

    for engine_page, pages in grouped_pages:
        engine_name = str(engine_page.get("title", ""))
        page_audits = [analyze_page(cm, page, engine_name, args.stale_days) for page in pages]
        for audit in page_audits:
            status_counts[audit.status] += 1
        flat_audits.extend(page_audits)
        engine_reports.append(
            {
                "engine": engine_name,
                "page_count": len(page_audits),
                "statuses": {
                    "OK": sum(1 for audit in page_audits if audit.status == "OK"),
                    "Incomplete": sum(1 for audit in page_audits if audit.status == "Incomplete"),
                    "Empty": sum(1 for audit in page_audits if audit.status == "Empty"),
                    "Outdated": sum(1 for audit in page_audits if audit.status == "Outdated"),
                },
                "pages": [asdict(audit) for audit in page_audits],
            }
        )

    return {
        "route": route,
        "pages_scanned": len(flat_audits),
        "status_counts": status_counts,
        "required_sections": list(REQUIRED_SECTIONS),
        "engines": engine_reports,
        "similar_pages": find_similar_pages(flat_audits),
    }


def render_markdown_audit(report: dict[str, Any]) -> str:
    lines = [
        "# Project Docs Health Monitor Audit",
        "",
        f"- Route: {report['route']}",
        f"- Pages scanned: {report['pages_scanned']}",
        (
            "- Status counts: "
            f"OK {report['status_counts']['OK']} | "
            f"Incomplete {report['status_counts']['Incomplete']} | "
            f"Empty {report['status_counts']['Empty']} | "
            f"Outdated {report['status_counts']['Outdated']}"
        ),
        "- Similar pages across engines are reported as informational only.",
        "",
    ]

    for engine in report["engines"]:
        lines.extend(
            [
                f"## {engine['engine']}",
                "",
                "| Page | Status | Coverage | Missing | Weak | Updated | Notes |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for page in engine["pages"]:
            coverage = f"{len(page['matched_sections'])}/{len(REQUIRED_SECTIONS)}"
            missing = ", ".join(page["missing_sections"]) or "-"
            weak = ", ".join(page["weak_sections"]) or "-"
            updated = page["last_updated"] or "-"
            notes = "; ".join(page["notes"]) or "-"
            lines.append(
                f"| {page['title']} | {page['status']} | {coverage} | {missing} | {weak} | {updated} | {notes} |"
            )
        if not engine["pages"]:
            lines.append("| - | - | - | - | - | - | No matching pages in scope |")
        lines.append("")

    actionable = [
        page
        for engine in report["engines"]
        for page in engine["pages"]
        if page["suggestions"]
    ]
    if actionable:
        lines.extend(["## Recommended Next Actions", ""])
        for page in actionable:
            suggestion = " ".join(page["suggestions"])
            lines.append(f"- {page['engine']} / {page['title']}: {suggestion}")
        lines.append("")

    if report["similar_pages"]:
        lines.extend(["## Similar Pages", ""])
        for pair in report["similar_pages"]:
            lines.append(
                "- "
                f"{pair['left_engine']} / {pair['left_title']} <-> "
                f"{pair['right_engine']} / {pair['right_title']}: {pair['note']}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def parse_status_filter(raw: str) -> set[str]:
    allowed = {"ok", "incomplete", "empty", "outdated"}
    values = split_csv_values(raw)
    unknown = values - allowed
    if unknown:
        raise RuntimeError(f"Unknown status filter: {', '.join(sorted(unknown))}")
    return {value.title() for value in values}


def build_alignment_report(cm: Any, client: Any, args: argparse.Namespace, grouped_pages: list[tuple[dict[str, Any], list[dict[str, Any]]]], route: str) -> dict[str, Any]:
    target_statuses = parse_status_filter(args.status)
    processed: list[dict[str, Any]] = []
    updated_count = 0

    for engine_page, pages in grouped_pages:
        engine_name = str(engine_page.get("title", ""))
        for page in pages:
            audit = analyze_page(cm, page, engine_name, args.stale_days)
            if audit.status not in target_statuses:
                continue
            new_body, changes = build_aligned_body(cm, page)
            action = "preview"
            if args.apply:
                client.update_page(page_id=audit.page_id, title=audit.title, body=new_body)
                action = "updated"
                updated_count += 1
            processed.append(
                {
                    "engine": engine_name,
                    "title": audit.title,
                    "page_id": audit.page_id,
                    "status_before": audit.status,
                    "action": action,
                    "changes": changes,
                }
            )

    return {
        "mode": "apply" if args.apply else "preview",
        "route": route,
        "statuses_targeted": sorted(target_statuses),
        "pages_selected": len(processed),
        "pages_updated": updated_count,
        "pages": processed,
    }


def render_markdown_alignment(report: dict[str, Any]) -> str:
    lines = [
        "# Project Docs Health Monitor Alignment",
        "",
        f"- Mode: {report['mode'].upper()}",
        f"- Route: {report['route']}",
        f"- Statuses targeted: {', '.join(report['statuses_targeted'])}",
        f"- Pages selected: {report['pages_selected']}",
        f"- Pages updated: {report['pages_updated']}",
        "",
    ]
    for index, page in enumerate(report["pages"], start=1):
        lines.append(
            f"{index}. {page['engine']} / {page['title']} | "
            f"status before: {page['status_before']} | "
            f"action: {page['action']} | "
            f"changes: {', '.join(page['changes'])}"
        )
    if not report["pages"]:
        lines.append("No pages matched the selected statuses and filters.")
    lines.append("")
    return "\n".join(lines)


def print_output(data: dict[str, Any], fmt: str, renderer) -> None:
    if fmt == "json":
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    print(renderer(data), end="")


def require_client(cm: Any, args: argparse.Namespace) -> Any:
    env_values = cm.load_env_file(Path("~/.config/confluence/.env"))
    token = cm.read_token(Path(args.token_file))
    return cm.ConfluenceClient(
        base_url=cm.resolved_setting(args.base_url, "CONFLUENCE_BASE_URL", env_values),
        cloud_id=args.cloud_id,
        token=token,
        auth_mode=args.auth_mode,
        email=cm.resolved_setting(args.email, "CONFLUENCE_EMAIL", env_values),
    )


def main() -> int:
    args = parse_args()
    try:
        cm = load_helper()
        client = require_client(cm, args)
        _, route, grouped_pages = load_inventory(cm, client, args)

        if args.command == "audit":
            report = build_audit_report(cm, args, grouped_pages, route)
            print_output(report, args.format, render_markdown_audit)
            return 0

        if args.command == "align":
            report = build_alignment_report(cm, client, args, grouped_pages, route)
            print_output(report, args.format, render_markdown_alignment)
            return 0

        raise RuntimeError(f"Unsupported command: {args.command}")
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
