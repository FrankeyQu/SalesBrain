from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


EBOSS_APIS: dict[str, dict[str, Any]] = {
    "get-user-by-name": {
        "method": "GET",
        "path": "/sale-user/page",
        "params": {
            "realName": {"required": False, "default": "%E9%92%B1%E9%BE%99"},
            "current": {"required": False, "default": "1"},
            "size": {"required": False, "default": "10"},
            "deptId": {"required": False},
            "status": {"required": False, "default": "1"},
        },
    },
    "get-project-list": {
        "method": "GET",
        "path": "/sale-project/project/page",
        "params": {
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
            "projectType": {"required": False},
            "projectTypeTag": {"required": False},
            "projectManagerIds": {"required": False},
            "projectStatusList": {"required": True, "default": "1,10,11,12"},
            "createTime": {"required": False},
            "keyword": {"required": False},
            "sheet": {"required": True, "default": "1"},
            "objType": {"required": False},
            "objId": {"required": False},
        },
    },
    "get-opportunity-list": {
        "method": "GET",
        "path": "/sale-mgt/salesOpportunity/list",
        "params": {
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
            "region": {"required": False},
            "regionCode": {"required": False},
            "estimatedOrderTime": {"required": False},
            "optStateList": {"required": True, "default": "1,2"},
            "optTypeList": {"required": False, "default": "1,2,3"},
            "chargerOpIdList": {"required": False},
            "createTime": {"required": False},
            "keyword": {"required": False},
            "opportunity": {"required": True, "default": "1"},
            "custId": {"required": False},
            "secondPartyId": {"required": False},
            "chargeDeptId": {"required": False},
            "preProjectDeptId": {"required": False},
            "preProjectChargeId": {"required": False},
            "beginEstimatedOrderTime": {"required": False},
            "endEstimatedOrderTime": {"required": False},
        },
    },
    "get-customer-list": {
        "method": "GET",
        "path": "/sale-mgt/custinfo/list",
        "params": {
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
            "industry": {"required": False},
            "region": {"required": False},
            "district": {"required": False},
            "customerType": {"required": False},
            "customerLevel": {"required": False},
            "customerLifecycle": {"required": True, "default": "1,2,3,4,5"},
            "chargeIdStr": {"required": False},
            "createTime": {"required": False},
            "keyword": {"required": False},
            "cust": {"required": True, "default": "6"},
        },
    },
    "get-lead-list": {
        "method": "GET",
        "path": "/sale-mgt/saleslead/list",
        "params": {
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
            "leadType": {"required": False},
            "leadStates": {"required": True, "default": "1,2,3,6,7,8,9"},
            "assignUserIds": {"required": False},
            "region": {"required": False},
            "createTime": {"required": False},
            "keyword": {"required": False},
            "lead": {"required": True, "default": "1"},
        },
    },
    "get-task-list": {
        "method": "GET",
        "path": "/sale-mgt/sales-task-plan/mine/queryTaskPlanForPage",
        "params": {
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
            "tabType": {"required": True, "default": "1"},
            "taskObj": {"required": False},
            "groupType": {"required": True, "default": "1"},
            "groupTypeIds": {"required": True, "default": "2,1,7,8,9"},
            "taskObjId": {"required": False},
            "noTaskUser": {"required": False, "default": "false"},
            "noFollowDays": {"required": False},
            "taskName": {"required": False},
            "statusTypes": {"required": False, "default": "2,1,7,8,9"},
            "statuss": {"required": False, "default": "5,1"},
            "prioritys": {"required": False},
            "taskUserIds": {"required": False},
        },
    },
    # V1 does not call this endpoint directly because /listFollow requires followObj/followObjId.
    "get-follow-byuser": {
        "method": "GET",
        "path": "/sale-mgt/salesfollow/listFollow",
        "params": {
            "userId": {"required": True},
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
            "startDate": {"required": False},
            "endDate": {"required": False},
        },
    },
    "get-follow-record": {
        "method": "GET",
        "path": "/sale-mgt/salesfollow/listFollow",
        "params": {
            "followObj": {"required": True, "default": "21"},
            "followObjId": {"required": True},
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
            "dateTag": {"required": False, "default": "0"},
            "milestoneId": {"required": False},
            "createUserIds": {"required": False},
            "followTaskName": {"required": False},
            "followUserIds": {"required": False},
            "keyWord": {"required": False},
        },
    },
    "get-daily-report-self": {
        "method": "GET",
        "path": "/sale-mgt/dailyLog/getDailyLog",
        "params": {
            "queryDate": {"required": False},
        },
    },
    "get-project-detail": {
        "method": "GET",
        "path": "/sale-project/project/detail",
        "params": {"id": {"required": True}},
    },
    "get-opportunity-detail": {
        "method": "GET",
        "path": "/sale-mgt/salesOpportunity/detail",
        "params": {"id": {"required": True}},
    },
    "get-stage-list": {
        "method": "GET",
        "path": "/sale-project/project-milestone/listMilestone",
        "params": {
            "projectId": {"required": True},
            "taskObj": {"required": True, "default": "21"},
            "ifOrderMs": {"required": False},
        },
    },
    "get-task-by-project-opp": {
        "method": "GET",
        "path": "/sale-mgt/sales-task-plan/listTaskByUserOrMilestoneForAI",
        "params": {
            "taskObj": {"required": True, "default": "21"},
            "taskObjId": {"required": True},
            "milestoneId": {"required": False},
        },
    },
    "get-project-budget": {
        "method": "GET",
        "path": "/sale-mgt/budgetprediction/budgetUnion/1",
        "params": {"projectId": {"required": True}},
    },
    "get-project-forecast": {
        "method": "GET",
        "path": "/sale-mgt/budgetprediction/budgetUnion/2",
        "params": {"projectId": {"required": True}},
    },
    "get-project-actual": {
        "method": "GET",
        "path": "/sale-mgt/budgetprediction/budgetUnion/3",
        "params": {"projectId": {"required": True}},
    },
    "get-opportunity-budget": {
        "method": "GET",
        "path": "/sale-mgt/budgetprediction/budgetUnion/1",
        "params": {"optId": {"required": True}},
    },
    "get-opportunity-forecast": {
        "method": "GET",
        "path": "/sale-mgt/budgetprediction/budgetUnion/2",
        "params": {"optId": {"required": True}},
    },
    "get-opportunity-actual": {
        "method": "GET",
        "path": "/sale-mgt/budgetprediction/budgetUnion/3",
        "params": {"optId": {"required": True}},
    },
    "get-requirement-list": {
        "method": "GET",
        "path": "/sale-project/needs/listPage",
        "params": {
            "requirementName": {"required": False},
            "needsManager": {"required": False},
            "productIds": {"required": False},
            "projectId": {"required": False},
            "priorityIds": {"required": False},
            "queryType": {"required": True, "default": "0"},
            "endTimeType": {"required": False},
            "createTimeType": {"required": False},
            "statuss": {"required": True, "default": "1,2"},
            "projectIds": {"required": False},
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
        },
    },
    "get-finance-alert": {
        "method": "GET",
        "path": "/sale-mgt/budgetprediction/warning",
        "params": {"id": {"required": True}},
    },
    "get-project-doc-list": {
        "method": "GET",
        "path": "/sale-project/project/project-document-list/forSkills",
        "params": {
            "attachObj": {"required": True, "default": "21"},
            "attachObjId": {"required": True},
        },
    },
    "get-payment-plan": {
        "method": "GET",
        "path": "/sale-mgt/sales-order/salesorderrepaymentplan/pagePlan",
        "params": {
            "current": {"required": True, "default": "1"},
            "size": {"required": True, "default": "10"},
            "tabType": {"required": True, "default": "1"},
            "sec": {"required": False},
            "orderId": {"required": False},
            "projectId": {"required": False},
            "planDate": {"required": False},
            "keyType": {"required": False, "default": "1"},
            "keyName": {"required": False},
            "isOverdue": {"required": False},
        },
    },
    "get-work-hours-budget-usage": {
        "method": "GET",
        "path": "/sale-mgt/salesWorkHours/details4Approval",
        "params": {
            "id": {"required": True},
        },
    },
}


def _pick_first(record: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


@dataclass(slots=True)
class EbossResponse:
    api_id: str
    status: int
    raw: dict[str, Any]

    @property
    def data(self) -> Any:
        return self.raw.get("data")

    @property
    def records(self) -> list[dict[str, Any]]:
        return EbossClient.extract_records(self.raw)


class EbossClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 240):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.timeout = timeout

    def build_params(self, api_id: str, params: dict[str, Any] | None = None) -> dict[str, str]:
        if api_id not in EBOSS_APIS:
            raise ValueError(f"unknown_eboss_api: {api_id}")
        api = EBOSS_APIS[api_id]
        provided = params or {}
        allowed = api["params"]
        unknown = sorted(set(provided) - set(allowed))
        if unknown:
            raise ValueError(f"unsupported_params_for_{api_id}: {unknown}")
        query: dict[str, str] = {}
        for name, meta in allowed.items():
            value = provided.get(name)
            if value not in (None, ""):
                query[name] = str(value)
            elif "default" in meta and meta["default"] != "":
                query[name] = str(meta["default"])
            elif meta.get("required"):
                raise ValueError(f"missing_required_param_for_{api_id}: {name}")
        return query

    def call(self, api_id: str, params: dict[str, Any] | None = None) -> EbossResponse:
        if api_id not in EBOSS_APIS:
            raise ValueError(f"unknown_eboss_api: {api_id}")
        api = EBOSS_APIS[api_id]
        if api["method"].upper() != "GET":
            raise ValueError("SalesBrain V1 only calls read-only GET APIs")

        query = self.build_params(api_id, params)
        url = self.base_url + api["path"]
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "api-key": self.api_key,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw_text) if raw_text else {}
            return EbossResponse(api_id=api_id, status=resp.status, raw=payload)

    @staticmethod
    def extract_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("records", "list", "rows"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            if all(isinstance(v, (str, int, float, bool)) or v is None for v in data.values()):
                return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(payload, dict) and all(
            isinstance(v, (str, int, float, bool)) or v is None for v in payload.values()
        ):
            return [payload]
        return []

    @staticmethod
    def extract_object_summary(record: dict[str, Any]) -> tuple[str | None, str | None]:
        object_id = _pick_first(
            record,
            ["id", "projectId", "optId", "opportunityId", "custId", "customerId", "leadId", "taskId"],
        )
        object_name = _pick_first(
            record,
            ["name", "projectName", "optName", "opportunityName", "custName", "customerName", "leadName", "taskName", "title"],
        )
        return object_id, object_name

    def call_paginated(
        self,
        api_id: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        api_params = dict(params or {})
        size_default = self.build_params(api_id, api_params).get("size")
        size = int(size_default or 10)
        records: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            api_params["current"] = str(page)
            api_params["size"] = str(size)
            response = self.call(api_id, api_params)
            page_records = response.records
            records.extend(page_records)
            if len(page_records) < size:
                break
        return records
