"""随行家属台账规则（纯领域逻辑，不涉及材料计算与存储）。

职责：
- 收案登记家属的关系与出生日期；
- 新增、退出家属，退出者保留台账历史；
- 依据出生日期判定未成年/成年，成年变动写入台账记录。

台账数据存放在案件 payload 中，持久化由 repository 负责。
"""
from typing import Any, Dict, List, Optional, Tuple

from .domain import ValidationError, choice, integer, text


APPLICANT_ID = "P0"
APPLICANT_RELATIONSHIP = "main_applicant"
RELATIONSHIPS = ("spouse", "child", "parent", "sibling", "other")
ADULT_AGE_DAYS = 18 * 365
MEMBER_ID_PREFIX = "M"


def is_adult(birth_day: int, as_of_day: int) -> bool:
    return int(as_of_day) - int(birth_day) >= ADULT_AGE_DAYS


def relationship_label(relationship: str) -> str:
    return {
        APPLICANT_RELATIONSHIP: "主申请人",
        "spouse": "配偶",
        "child": "子女",
        "parent": "父母",
        "sibling": "兄弟姐妹",
        "other": "其他亲属",
    }.get(relationship, relationship)


def validate_member_input(data: Dict[str, Any], as_of_day: Optional[int] = None) -> Dict[str, Any]:
    """校验单个家属登记项。"""
    info = {
        "name": text(data, "name"),
        "relationship": choice(data, "relationship", list(RELATIONSHIPS)),
        "birth_day": integer(data, "birth_day"),
    }
    if as_of_day is not None and info["birth_day"] > int(as_of_day):
        raise ValidationError("出生日期不能晚于登记日期")
    return info


def _next_member_id(members: List[Dict[str, Any]]) -> str:
    biggest = 0
    for member in members:
        member_id = str(member.get("id", ""))
        if member_id.startswith(MEMBER_ID_PREFIX):
            try:
                biggest = max(biggest, int(member_id[len(MEMBER_ID_PREFIX):]))
            except ValueError:
                continue
    return "%s%d" % (MEMBER_ID_PREFIX, biggest + 1)


def _make_member(member_id: str, info: Dict[str, Any], added_day: int, status: str = "active") -> Dict[str, Any]:
    return {
        "id": member_id,
        "name": info["name"],
        "relationship": info["relationship"],
        "birth_day": int(info["birth_day"]),
        "status": status,
        "added_day": int(added_day),
        "withdrawn_day": None,
        "withdraw_reason": "",
        "adult": is_adult(info["birth_day"], added_day),
    }


def _same_person(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return (
        left.get("name") == right.get("name")
        and left.get("relationship") == right.get("relationship")
        and int(left.get("birth_day", -1)) == int(right.get("birth_day", -2))
    )


def _append_change(
    payload: Dict[str, Any],
    change_type: str,
    member: Dict[str, Any],
    day: int,
    detail: str,
) -> Dict[str, Any]:
    changes = payload.setdefault("roster_changes", [])
    entry = {
        "seq": len(changes) + 1,
        "day": int(day),
        "type": change_type,
        "member_id": member["id"],
        "member_name": member["name"],
        "relationship": member["relationship"],
        "detail": detail,
    }
    changes.append(entry)
    return entry


def init_ledger(raw_members: Any, received_day: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """收案时根据登记信息建立初始台账，返回人员名单与收案登记记录。"""
    if raw_members is None:
        raw_members = []
    if not isinstance(raw_members, list):
        raise ValidationError("members必须是列表")
    members: List[Dict[str, Any]] = []
    changes: List[Dict[str, Any]] = []
    ledger_seed = {"roster_changes": changes}
    for raw in raw_members:
        if not isinstance(raw, dict):
            raise ValidationError("家属登记项必须是对象")
        info = validate_member_input(raw, received_day)
        member = _make_member(_next_member_id(members), info, received_day)
        if any(existing["status"] == "active" and _same_person(existing, member) for existing in members):
            raise ValidationError("随行家属名单中存在重复人员：%s" % info["name"])
        members.append(member)
        _append_change(
            ledger_seed,
            "member_registered",
            member,
            received_day,
            "收案登记：%s%s" % (relationship_label(member["relationship"]), member["name"]),
        )
    return members, changes


def applicant_view(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": APPLICANT_ID,
        "name": str(payload.get("applicant_id", "")),
        "relationship": APPLICANT_RELATIONSHIP,
        "birth_day": None,
        "status": "active",
        "added_day": int(payload.get("received_day", 0)),
        "withdrawn_day": None,
        "withdraw_reason": "",
        "adult": True,
    }


def all_people(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """主申请人始终在列，其后是含退出者在内的全部家属台账。"""
    return [applicant_view(payload)] + list(payload.get("members", []))


def active_members(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [member for member in payload.get("members", []) if member.get("status") == "active"]


def active_people(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [applicant_view(payload)] + active_members(payload)


def get_person(payload: Dict[str, Any], person_id: str) -> Dict[str, Any]:
    person_id = person_id or APPLICANT_ID
    if person_id == APPLICANT_ID:
        return applicant_view(payload)
    for member in payload.get("members", []):
        if member.get("id") == person_id:
            return member
    raise ValidationError("人员不在随行名单中：%s" % person_id)


def add_member(payload: Dict[str, Any], data: Dict[str, Any], day: int) -> Dict[str, Any]:
    """名单新增：登记后人员立即在列，材料缺项由材料计算环节重算。"""
    members = payload.setdefault("members", [])
    info = validate_member_input(data, day)
    if any(existing.get("status") == "active" and existing.get("name") == info["name"] and existing.get("relationship") == info["relationship"] for existing in members):
        raise ValidationError("该人员已在随行名单中：%s" % info["name"])
    member = _make_member(_next_member_id(members), info, day)
    members.append(member)
    _append_change(payload, "member_added", member, day, "新增%s%s" % (relationship_label(member["relationship"]), member["name"]))
    return member


def withdraw_member(payload: Dict[str, Any], member_id: str, reason: str, day: int) -> Dict[str, Any]:
    """名单退出：状态置为 withdrawn，历史材料与变动记录保留。"""
    if member_id == APPLICANT_ID:
        raise ValidationError("主申请人不能退出随行名单")
    member = get_person(payload, member_id)
    if member.get("status") != "active":
        raise ValidationError("该人员已退出名单：%s" % member_id)
    member["status"] = "withdrawn"
    member["withdrawn_day"] = int(day)
    member["withdraw_reason"] = reason or "未说明"
    _append_change(
        payload,
        "member_withdrawn",
        member,
        day,
        "%s%s退出名单（%s）" % (relationship_label(member["relationship"]), member["name"], member["withdraw_reason"]),
    )
    return member


def refresh_adult_flags(payload: Dict[str, Any], day: int) -> List[Dict[str, Any]]:
    """按当前日期重算成年标记；未成年子女成年时留下变动记录。"""
    flipped: List[Dict[str, Any]] = []
    for member in active_members(payload):
        adult_now = is_adult(member["birth_day"], day)
        if adult_now and not member.get("adult", False):
            _append_change(payload, "member_adult", member, day, "%s%s已成年，监护声明要求移除" % (relationship_label(member["relationship"]), member["name"]))
            flipped.append(member)
        member["adult"] = adult_now
    return flipped
