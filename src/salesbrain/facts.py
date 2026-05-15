from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .timeutil import parse_iso_datetime


AMOUNT_NUMBER_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")

PRIMARY_OBJECT_TYPES = {"opportunity", "project", "customer", "lead", "eboss_task"}
BUSINESS_OBJECT_TYPES = {"opportunity", "project", "customer", "lead", "eboss_task"}

NAME_KEYS = (
    "optName",
    "opportunityName",
    "projectName",
    "custName",
    "customerName",
    "leadName",
    "taskName",
    "name",
    "title",
)
ID_KEYS = ("id", "optId", "opportunityId", "projectId", "custId", "customerId", "leadId", "taskId")
STAGE_KEYS = (
    "stageName",
    "optStageName",
    "opportunityStageName",
    "projectStageName",
    "phaseName",
    "statusName",
    "optStateName",
    "projectStatusName",
)
FOLLOW_TIME_KEYS = (
    "followTime",
    "followDate",
    "lastFollowTime",
    "lastFollowDate",
    "createTime",
    "createdAt",
    "updateTime",
    "updatedAt",
)

AMOUNT_RULES: dict[str, list[tuple[str, tuple[str, ...], str]]] = {
    "opportunity": [
        ("opportunity_detail", ("expectedAmount", "expectedAmt", "optAmount", "amount", "estimatedAmount", "salesAmount"), "high"),
        ("opportunity_forecast", ("forecastAmount", "forecastAmt", "amount", "totalAmount"), "medium"),
        ("opportunity_budget", ("budgetAmount", "budgetAmt", "amount", "totalAmount"), "medium"),
        ("opportunity_actual", ("actualAmount", "contractAmount", "amount", "totalAmount"), "low"),
        ("opportunity", ("expectedAmount", "expectedAmt", "optAmount", "amount", "estimatedAmount", "salesAmount"), "medium"),
    ],
    "project": [
        ("project_budget", ("budgetAmount", "budgetAmt", "totalAmount", "amount", "projectAmount"), "high"),
        ("project_forecast", ("forecastAmount", "forecastAmt", "amount", "totalAmount"), "medium"),
        ("project_actual", ("actualAmount", "contractAmount", "amount", "totalAmount"), "low"),
        ("project_detail", ("projectAmount", "budgetAmount", "forecastAmount", "amount", "totalAmount"), "medium"),
        ("project", ("projectAmount", "budgetAmount", "forecastAmount", "amount", "totalAmount"), "medium"),
    ],
}


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _first_value(payload: dict[str, Any], keys: tuple[str, ...] | list[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _iter_values(value: Any, *, prefix: str = ""):
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, key, nested
            if isinstance(nested, (dict, list)):
                yield from _iter_values(nested, prefix=path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            path = f"{prefix}[{index}]"
            if isinstance(nested, (dict, list)):
                yield from _iter_values(nested, prefix=path)


def parse_amount_yuan(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    multiplier = 1.0
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    text = str(value).strip()
    if not text:
        return None
    if "亿" in text:
        multiplier = 100_000_000.0
    elif "万" in text:
        multiplier = 10_000.0
    match = AMOUNT_NUMBER_RE.search(text.replace("，", ","))
    if not match:
        return None
    try:
        number = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return int(round(number * multiplier))


def amount_display(amount_yuan: int | None) -> str | None:
    if amount_yuan is None:
        return None
    if abs(amount_yuan) >= 10_000:
        value = amount_yuan / 10_000
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return f"{amount_yuan}元"


def _parse_date(value: Any) -> datetime | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        try:
            if number > 1_000_000_000_000:
                return datetime.fromtimestamp(number / 1000)
            if number > 1_000_000_000:
                return datetime.fromtimestamp(number)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return parse_iso_datetime(text)
    except Exception:
        pass
    for fmt, size in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y/%m/%d %H:%M:%S", 19),
        ("%Y-%m-%d", 10),
        ("%Y/%m/%d", 10),
    ):
        try:
            return datetime.strptime(text[:size], fmt)
        except Exception:
            continue
    return None


def _date_iso(value: Any) -> str | None:
    parsed = _parse_date(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def _latest_iso(values: list[Any]) -> str | None:
    parsed = [item for item in (_parse_date(value) for value in values) if item is not None]
    if not parsed:
        return None
    return max(parsed).isoformat(timespec="seconds")


def _record_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = _loads(row.get("payload_json"), {})
    return payload if isinstance(payload, dict) else {}


def _record_summary(row: dict[str, Any], payload: dict[str, Any], object_type: str) -> tuple[str, str]:
    object_id = (
        payload.get("salesbrain_parent_id")
        if object_type not in PRIMARY_OBJECT_TYPES and object_type not in {"opportunity_detail", "project_detail"}
        else None
    )
    object_id = object_id or row.get("object_id") or _first_value(payload, ID_KEYS)
    object_name = (
        payload.get("salesbrain_parent_name")
        if object_type not in PRIMARY_OBJECT_TYPES and object_type not in {"opportunity_detail", "project_detail"}
        else None
    )
    object_name = object_name or row.get("object_name") or _first_value(payload, NAME_KEYS) or object_id
    return str(object_id or ""), str(object_name or "")


def _source_payloads_for_amount(fact: dict[str, Any], source_type: str) -> list[dict[str, Any]]:
    source_payloads = fact.get("source_payloads")
    if not isinstance(source_payloads, dict):
        return []
    values = source_payloads.get(source_type)
    return values if isinstance(values, list) else []


def _amount_candidates(fact: dict[str, Any]) -> list[dict[str, Any]]:
    object_type = str(fact.get("object_type") or "")
    candidates: list[dict[str, Any]] = []
    for source_type, keys, confidence in AMOUNT_RULES.get(object_type, []):
        for payload_index, payload in enumerate(_source_payloads_for_amount(fact, source_type)):
            for path, key, value in _iter_values(payload):
                if key not in keys:
                    continue
                amount = parse_amount_yuan(value)
                if amount is None:
                    continue
                candidates.append(
                    {
                        "amount_yuan": amount,
                        "amount_display": amount_display(amount),
                        "source": f"{source_type}.{path}",
                        "source_type": source_type,
                        "source_payload_index": payload_index,
                        "confidence": confidence,
                    }
                )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for candidate in candidates:
        key = (int(candidate["amount_yuan"]), str(candidate["source"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _amount_confidence(primary: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> str:
    if primary is None:
        return "missing"
    amounts = sorted({int(candidate["amount_yuan"]) for candidate in candidates})
    if len(amounts) > 1:
        low, high = amounts[0], amounts[-1]
        if abs(high - low) > max(100, abs(primary["amount_yuan"]) * 0.01):
            return "conflict"
    return str(primary.get("confidence") or "medium")


def _first_stage(fact: dict[str, Any]) -> tuple[str | None, str | None]:
    source_payloads = fact.get("source_payloads")
    if not isinstance(source_payloads, dict):
        return None, None
    for source_type in (
        "opportunity_stage",
        "project_stage",
        "opportunity_detail",
        "project_detail",
        "opportunity",
        "project",
    ):
        for payload in source_payloads.get(source_type, []) or []:
            for path, key, value in _iter_values(payload):
                if key in STAGE_KEYS and value not in (None, ""):
                    return str(value), f"{source_type}.{path}"
    return None, None


def _latest_follow(fact: dict[str, Any]) -> tuple[str | None, str | None]:
    source_payloads = fact.get("source_payloads")
    if not isinstance(source_payloads, dict):
        return None, None
    candidates: list[tuple[str, str]] = []
    for source_type in ("opportunity_follow_record", "project_follow_record", "opportunity", "project", "customer"):
        for payload in source_payloads.get(source_type, []) or []:
            for path, key, value in _iter_values(payload):
                if key in FOLLOW_TIME_KEYS:
                    iso = _date_iso(value)
                    if iso:
                        candidates.append((iso, f"{source_type}.{path}"))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0]


def _days_since(iso_value: str | None, now: datetime) -> int | None:
    if not iso_value:
        return None
    parsed = _parse_date(iso_value)
    if not parsed:
        return None
    if parsed.tzinfo is None and now.tzinfo is not None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    delta = now - parsed
    return max(0, int(delta.total_seconds() // 86400))


def _object_ref(object_type: str, object_id: str) -> str:
    return f"{object_type}:{object_id}"


def _strip_internal(fact: dict[str, Any], now: datetime) -> dict[str, Any]:
    candidates = _amount_candidates(fact)
    primary = candidates[0] if candidates else None
    stage, stage_source = _first_stage(fact)
    last_follow_at, last_follow_source = _latest_follow(fact)
    amount_yuan = int(primary["amount_yuan"]) if primary else None
    out = {
        "object_type": fact["object_type"],
        "object_id": fact["object_id"],
        "object_ref": _object_ref(fact["object_type"], fact["object_id"]),
        "name": fact.get("name") or fact["object_id"],
        "amount_yuan": amount_yuan,
        "amount_display": amount_display(amount_yuan),
        "amount_source": primary.get("source") if primary else None,
        "amount_confidence": _amount_confidence(primary, candidates),
        "amount_candidates": candidates[:6],
        "stage": stage,
        "stage_source": stage_source,
        "last_follow_at": last_follow_at,
        "last_follow_source": last_follow_source,
        "days_since_last_follow": _days_since(last_follow_at, now),
        "data_quality": [],
    }
    if out["amount_confidence"] == "conflict":
        out["data_quality"].append("amount_conflict")
    if amount_yuan is None and fact["object_type"] in {"opportunity", "project"}:
        out["data_quality"].append("amount_missing")
    if not last_follow_at and fact["object_type"] in {"opportunity", "project", "customer"}:
        out["data_quality"].append("follow_time_missing")
    return out


def _init_fact(facts: dict[str, dict[str, Any]], object_type: str, object_id: str, name: str) -> dict[str, Any]:
    key = _object_ref(object_type, object_id)
    fact = facts.get(key)
    if fact is None:
        fact = {
            "object_type": object_type,
            "object_id": object_id,
            "name": name or object_id,
            "source_payloads": {},
        }
        facts[key] = fact
    elif name and (not fact.get("name") or fact.get("name") == fact.get("object_id")):
        fact["name"] = name
    return fact


def build_business_fact_package(store: Any, *, now: datetime, limit_per_type: int = 2000) -> dict[str, Any]:
    source_types = [
        "opportunity",
        "opportunity_detail",
        "opportunity_stage",
        "opportunity_task",
        "opportunity_follow_record",
        "opportunity_budget",
        "opportunity_forecast",
        "opportunity_actual",
        "project",
        "project_detail",
        "project_stage",
        "project_task",
        "project_follow_record",
        "project_budget",
        "project_forecast",
        "project_actual",
        "customer",
        "lead",
        "eboss_task",
    ]
    facts: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    for source_type in source_types:
        rows = store.latest_raw_records_by_type(source_type, limit_per_type)
        source_counts[source_type] = len(rows)
        for row in rows:
            payload = _record_payload(row)
            if source_type.startswith("opportunity"):
                object_type = "opportunity"
            elif source_type.startswith("project"):
                object_type = "project"
            elif source_type == "customer":
                object_type = "customer"
            elif source_type == "lead":
                object_type = "lead"
            elif source_type == "eboss_task":
                object_type = "eboss_task"
            else:
                continue
            object_id, name = _record_summary(row, payload, source_type)
            if not object_id:
                continue
            fact = _init_fact(facts, object_type, object_id, name)
            fact.setdefault("source_payloads", {}).setdefault(source_type, []).append(payload)

    fact_list = [_strip_internal(fact, now) for fact in facts.values()]
    fact_list.sort(
        key=lambda item: (
            0 if item["object_type"] in {"opportunity", "project"} else 1,
            -(item.get("amount_yuan") or 0),
            item.get("name") or "",
        )
    )
    high_value_opportunities = [
        fact for fact in fact_list
        if fact["object_type"] == "opportunity" and (fact.get("amount_yuan") or 0) >= 100_000
    ][:10]
    stale_objects = [
        fact for fact in fact_list
        if fact["object_type"] in {"opportunity", "project", "customer"}
        and (fact.get("days_since_last_follow") is None or int(fact.get("days_since_last_follow") or 0) >= 7)
    ][:15]
    amount_conflicts = [
        fact for fact in fact_list
        if fact.get("amount_confidence") == "conflict"
    ][:20]
    return {
        "semantic_contract": {
            "version": "business-facts-v1",
            "source_of_truth": "Use business_facts as the primary source for IDs, names, amounts, stages, and follow-up dates. EBOSS raw records are fallback context only.",
            "object_identity_rule": "Use object_type + object_id. Do not match projects or opportunities by name alone.",
            "amount_rule": "Use amount_yuan and amount_display from business_facts. Do not infer amounts from EBOSS raw payloads.",
            "task_binding_rule": "When a task refers to an EBOSS object, return source_type as the object type and source_ref as the object_id.",
            "confidence_rule": "If amount_confidence is conflict or missing, mention uncertainty instead of presenting the amount as definite.",
        },
        "facts": fact_list[:200],
        "facts_by_ref": {fact["object_ref"]: fact for fact in fact_list[:200]},
        "work_state": {
            "high_value_opportunities": high_value_opportunities,
            "stale_objects": stale_objects,
            "amount_conflicts": amount_conflicts,
            "counts": {
                "facts": len(fact_list),
                "opportunities": sum(1 for fact in fact_list if fact["object_type"] == "opportunity"),
                "projects": sum(1 for fact in fact_list if fact["object_type"] == "project"),
                "customers": sum(1 for fact in fact_list if fact["object_type"] == "customer"),
                "leads": sum(1 for fact in fact_list if fact["object_type"] == "lead"),
            },
            "source_counts": source_counts,
        },
    }


def fact_index_from_context(context: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(context, dict):
        return {}
    facts_by_ref = context.get("facts_by_ref")
    if isinstance(facts_by_ref, dict):
        return {str(key): value for key, value in facts_by_ref.items() if isinstance(value, dict)}
    business_facts = context.get("business_facts")
    if not isinstance(business_facts, list):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for fact in business_facts:
        if not isinstance(fact, dict):
            continue
        object_type = str(fact.get("object_type") or "")
        object_id = str(fact.get("object_id") or "")
        if object_type and object_id:
            index[_object_ref(object_type, object_id)] = fact
    return index
