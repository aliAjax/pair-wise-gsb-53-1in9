"""逐人材料计算（纯领域逻辑，不涉及名单规则实现与存储）。

材料要求按"当前在册人员"重算：
- 主申请人、配偶、成年家属：身份材料；
- 未成年子女：身份材料 + 监护声明；
- 退出者：不再产生新要求，已提交材料挂在其名下保留历史。

所有材料统一存放于 ``payload["person_documents"]``，键为人员编号。
"""
from typing import Any, Dict, List, Optional

from . import roster
from .domain import ValidationError, text


IDENTITY_DOCUMENT = "identity_document"
GUARDIANSHIP_DOCUMENT = "guardianship_statement"
DOCUMENT_LABELS = {
    IDENTITY_DOCUMENT: "身份材料",
    GUARDIANSHIP_DOCUMENT: "监护声明",
}


def document_label(code: str) -> str:
    return DOCUMENT_LABELS.get(code, code)


def requirement_for(person: Dict[str, Any], applicant_required: Optional[List[str]] = None) -> List[str]:
    """按人员当前状态/关系/年龄计算材料清单。"""
    if person.get("status") == "withdrawn":
        return []
    if person.get("relationship") == roster.APPLICANT_RELATIONSHIP:
        return list(applicant_required if applicant_required is not None else [IDENTITY_DOCUMENT])
    requirements = [IDENTITY_DOCUMENT]
    if person.get("relationship") == "child" and not person.get("adult", False):
        requirements.append(GUARDIANSHIP_DOCUMENT)
    return requirements


def migrate_legacy(payload: Dict[str, Any]) -> None:
    """把早期挂在案件顶层的材料迁移到主申请人名下（幂等）。

    案件级 required_documents/missing_documents 保留，它们仍是主申请人的材料口径。
    """
    store = payload.setdefault("person_documents", {})
    existing = list(store.get(roster.APPLICANT_ID, []))
    for doc in payload.get("submitted_documents", []) or []:
        if doc not in existing:
            existing.append(doc)
    if existing or roster.APPLICANT_ID not in store:
        store[roster.APPLICANT_ID] = existing


def submitted_for(payload: Dict[str, Any], person_id: str) -> List[str]:
    return list(payload.get("person_documents", {}).get(person_id, []))


def person_view(person: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    applicant_required = payload.get("required_documents") if person.get("relationship") == roster.APPLICANT_RELATIONSHIP else None
    required = requirement_for(person, applicant_required)
    submitted = submitted_for(payload, person["id"])
    missing = [doc for doc in required if doc not in submitted]
    view = dict(person)
    view.update(
        {
            "required_documents": required,
            "submitted_documents": submitted,
            "missing_documents": missing,
            "verified": bool(payload.get("verified_persons", {}).get(person["id"])),
        }
    )
    return view


def recompute(payload: Dict[str, Any]) -> Dict[str, Any]:
    """按当前名单重算每人的要求、缺项与待补区。退出者只保留名下材料。"""
    migrate_legacy(payload)
    people_views = []
    pending: List[Dict[str, Any]] = []
    for person in roster.all_people(payload):
        view = person_view(person, payload)
        people_views.append(view)
        for doc in view["missing_documents"]:
            pending.append(
                {
                    "person_id": person["id"],
                    "person_name": person.get("name", ""),
                    "relationship": person.get("relationship", ""),
                    "document": doc,
                    "document_label": document_label(doc),
                }
            )
    payload["document_summary"] = {
        "people": people_views,
        "pending_documents": pending,
        "missing_documents": sorted({item["document"] for item in pending}),
        "active_people": len(roster.active_people(payload)),
        "verified_people": _verified_active_count(payload),
    }
    return payload["document_summary"]


def _verified_active_count(payload: Dict[str, Any]) -> int:
    verified = payload.get("verified_persons", {})
    return sum(1 for person in roster.active_people(payload) if verified.get(person["id"]))


def attach_documents(
    payload: Dict[str, Any],
    person_id: Optional[str],
    documents: List[str],
    replace: bool = False,
) -> Dict[str, Any]:
    """把材料挂到对应人员名下，随后按当前名单重算缺项。"""
    person = roster.get_person(payload, person_id or roster.APPLICANT_ID)
    if person.get("status") == "withdrawn":
        raise ValidationError("已退出名单的人员不再接收新材料：%s" % person["id"])
    clean = []
    for doc in documents:
        doc = (doc or "").strip()
        if not doc:
            raise ValidationError("材料名称不能为空")
        if doc not in clean:
            clean.append(doc)
    store = payload.setdefault("person_documents", {})
    submitted = list(clean if replace else store.get(person["id"], []) + clean)
    store[person["id"]] = list(dict.fromkeys(submitted))
    recompute(payload)
    return person_view(person, payload)


def verify_person(payload: Dict[str, Any], person_id: Optional[str]) -> Dict[str, Any]:
    """逐人核对：无缺项后才允许标记完成。"""
    person = roster.get_person(payload, person_id or roster.APPLICANT_ID)
    if person.get("status") == "withdrawn":
        raise ValidationError("退出名单的人员无需核对")
    required = requirement_for(person, payload.get("required_documents") if person.get("relationship") == roster.APPLICANT_RELATIONSHIP else None)
    submitted = submitted_for(payload, person["id"])
    missing = [doc for doc in required if doc not in submitted]
    if missing:
        raise ValidationError("%s材料未齐：%s" % (person.get("name", person["id"]), ", ".join(document_label(doc) for doc in missing)))
    payload.setdefault("verified_persons", {})[person["id"]] = True
    recompute(payload)
    return person_view(person, payload)


def decision_blockers(payload: Dict[str, Any]) -> List[str]:
    """决定前逐人核对的拦截原因；为空表示可以决定。"""
    verified = payload.get("verified_persons", {})
    blockers: List[str] = []
    for person in roster.active_people(payload):
        view = person_view(person, payload)
        if view["missing_documents"]:
            blockers.append(
                "%s缺少材料：%s"
                % (person.get("name", person["id"]), ", ".join(document_label(doc) for doc in view["missing_documents"]))
            )
        elif not verified.get(person["id"]):
            blockers.append("%s尚未逐人核对" % person.get("name", person["id"]))
    return blockers


def current_day(payload: Dict[str, Any]) -> int:
    for key in ("response_day", "evidence_request_day", "received_day"):
        if key in payload:
            return int(payload[key])
    return 0
