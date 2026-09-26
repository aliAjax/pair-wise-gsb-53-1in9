"""随行家属名单规则：登记校验、年龄计算与角色权限。"""
from datetime import date
from typing import Any, Dict

from .domain import ValidationError, boolean, choice, text


RELATIONSHIPS = ["spouse", "child", "parent", "sibling", "other"]
MINOR_AGE = 18
FAMILY_ACTION_ROLES = {
    "add": {"intake_officer", "case_officer"},
    "remove": {"case_officer", "supervisor"},
    "document": {"intake_officer", "case_officer", "legal_rep"},
}
MUTABLE_STATES = {"draft", "submitted", "evidence_requested", "response_received"}


def parse_day(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("%s必须是YYYY-MM-DD日期" % field)
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValidationError("%s必须是有效日期" % field) from exc


def age_on(birth: date, on: date) -> int:
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


def is_minor(birth: date, on: date) -> bool:
    return age_on(birth, on) < MINOR_AGE


class FamilyRules:
    def known_role(self, role: str) -> bool:
        if role == "admin":
            return True
        return any(role in roles for roles in FAMILY_ACTION_ROLES.values())

    def role_can(self, role: str, action: str) -> bool:
        return role == "admin" or role in FAMILY_ACTION_ROLES.get(action, set())

    def validate_member(self, payload: Dict[str, Any], as_of: date) -> Dict[str, Any]:
        data = dict(payload or {})
        member = {
            "name": text(data, "name"),
            "relationship": choice(data, "relationship", RELATIONSHIPS),
            "birth_date": parse_day(data.get("birth_date"), "birth_date"),
            "guardianship_declaration": boolean(data, "guardianship_declaration", False),
        }
        if member["birth_date"] > as_of:
            raise ValidationError("birth_date不能晚于当前日期")
        member["birth_date"] = member["birth_date"].isoformat()
        return member

    def same_member(self, left: Dict[str, Any], right: Dict[str, Any]) -> bool:
        return (
            left["name"] == right["name"]
            and left["relationship"] == right["relationship"]
            and left["birth_date"] == right["birth_date"]
        )
