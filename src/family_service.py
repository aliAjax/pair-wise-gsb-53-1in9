"""随行家属台账用例：名单维护、材料重算与决定前逐人核查。"""
from datetime import date
from typing import Any, Dict, List, Optional, Union

from .domain import Actor, Conflict, NotFound, PermissionDenied, ValidationError, text, text_list
from .family_materials import MaterialCalculator
from .family_rules import MUTABLE_STATES, FamilyRules, age_on, is_minor, parse_day
from .repository import Repository


class FamilyService:
    def __init__(self, repository: Repository, rules: FamilyRules = None, materials: MaterialCalculator = None) -> None:
        self.repository = repository
        self.rules = rules or FamilyRules()
        self.materials = materials or MaterialCalculator()

    @staticmethod
    def _actor(actor: Actor) -> Actor:
        if actor is None or not actor.user_id.strip() or not actor.role.strip():
            raise PermissionDenied("缺少调用身份")
        return actor

    def _ensure_known_role(self, actor: Actor) -> None:
        if not self.rules.known_role(actor.role):
            raise PermissionDenied("角色无权访问该服务")

    def _ensure_role(self, actor: Actor, action: str) -> None:
        if not self.rules.role_can(actor.role, action):
            raise PermissionDenied("角色无权执行该家属操作")

    @staticmethod
    def _as_of(as_of: Optional[Union[str, date]]) -> date:
        if as_of is None:
            return date.today()
        if isinstance(as_of, date):
            return as_of
        return parse_day(as_of, "as_of")

    @staticmethod
    def _brief(member: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": member["id"], "name": member["name"], "relationship": member["relationship"], "birth_date": member["birth_date"]}

    def _mutable_record(self, record_id: int) -> Dict[str, Any]:
        record = self.repository.get(record_id)
        if record["state"] not in MUTABLE_STATES:
            raise Conflict("当前状态不允许变更家属名单")
        return record

    def _active_member(self, record_id: int, member_id: int) -> Dict[str, Any]:
        member = self.repository.get_family_member(member_id)
        if member["record_id"] != record_id:
            raise NotFound("家属不属于该案件")
        if member["status"] != "active":
            raise Conflict("家属已退出，仅保留历史记录")
        return member

    def _pending(self, record_id: int, as_of: date) -> Dict[str, Any]:
        documents = self.repository.list_family_documents(record_id)
        pending = {}
        for member in self.repository.list_family_members(record_id):
            if member["status"] != "active":
                continue
            missing = self.materials.missing_documents(member, documents.get(member["id"], []), as_of)
            if missing:
                pending[str(member["id"])] = {"name": member["name"], "missing_documents": missing}
        return pending

    def validate_new_members(self, value: Any) -> List[Dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValidationError("family_members必须是列表")
        day = date.today()
        prepared = []
        for item in value:
            if not isinstance(item, dict):
                raise ValidationError("family_members项必须是对象")
            member = self.rules.validate_member(item, day)
            if any(self.rules.same_member(member, existing) for existing in prepared):
                raise Conflict("家属名单存在重复成员")
            prepared.append(member)
        return prepared

    def add_member(self, actor: Actor, record_id: int, payload: Dict[str, Any], as_of: Optional[Union[str, date]] = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        self._ensure_role(actor, "add")
        self._mutable_record(record_id)
        day = self._as_of(as_of)
        member = self.rules.validate_member(payload, day)
        for existing in self.repository.list_family_members(record_id):
            if existing["status"] == "active" and self.rules.same_member(existing, member):
                raise Conflict("该家属已在名单中")
        saved = self.repository.add_family_member(record_id, member, actor.user_id)
        self.repository.add_family_event(record_id, saved["id"], "member_added", actor.user_id, {"member": self._brief(saved), "pending_after": self._pending(record_id, day)})
        return saved

    def remove_member(self, actor: Actor, record_id: int, member_id: int, payload: Dict[str, Any], as_of: Optional[Union[str, date]] = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        self._ensure_role(actor, "remove")
        self._mutable_record(record_id)
        member = self._active_member(record_id, member_id)
        reason = text(payload or {}, "reason")
        saved = self.repository.set_family_member_removed(member_id, actor.user_id, reason)
        self.repository.add_family_event(record_id, member_id, "member_removed", actor.user_id, {"member": self._brief(member), "reason": reason, "pending_after": self._pending(record_id, self._as_of(as_of))})
        return saved

    def add_documents(self, actor: Actor, record_id: int, member_id: int, payload: Dict[str, Any], as_of: Optional[Union[str, date]] = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        self._ensure_role(actor, "document")
        self._mutable_record(record_id)
        member = self._active_member(record_id, member_id)
        documents = text_list(payload or {}, "documents", 1)
        day = self._as_of(as_of)
        added = self.repository.add_family_documents(member_id, documents, actor.user_id)
        self.repository.add_family_event(record_id, member_id, "documents_added", actor.user_id, {"member": self._brief(member), "documents": added, "pending_after": self._pending(record_id, day)})
        submitted = self.repository.list_family_documents(record_id).get(member_id, [])
        return {"member_id": member_id, "added_documents": added, "missing_documents": self.materials.missing_documents(member, submitted, day)}

    def ledger(self, actor: Actor, record_id: int, as_of: Optional[Union[str, date]] = None) -> Dict[str, Any]:
        actor = self._actor(actor)
        self._ensure_known_role(actor)
        self.repository.get(record_id)
        return self._ledger(record_id, self._as_of(as_of))

    def _ledger(self, record_id: int, day: date) -> Dict[str, Any]:
        documents = self.repository.list_family_documents(record_id)
        members = []
        pending = {}
        for member in self.repository.list_family_members(record_id):
            entry = dict(member)
            birth = date.fromisoformat(member["birth_date"])
            entry["age"] = age_on(birth, day)
            entry["minor"] = is_minor(birth, day)
            entry["submitted_documents"] = documents.get(member["id"], [])
            if member["status"] == "active":
                entry["required_documents"] = self.materials.required_documents(member, day)
                entry["missing_documents"] = self.materials.missing_documents(member, entry["submitted_documents"], day)
                if entry["missing_documents"]:
                    pending[str(member["id"])] = {"name": member["name"], "missing_documents": entry["missing_documents"]}
            members.append(entry)
        return {
            "record_id": record_id,
            "as_of": day.isoformat(),
            "members": members,
            "pending": pending,
            "ready_for_decision": not pending,
            "events": self.repository.list_family_events(record_id),
        }

    def ensure_decision_ready(self, record_id: int, as_of: Optional[Union[str, date]] = None) -> None:
        pending = self._pending(record_id, self._as_of(as_of))
        if pending:
            parts = ["%s缺%s" % (info["name"], "、".join(info["missing_documents"])) for info in pending.values()]
            raise ValidationError("家属材料待补：" + "；".join(parts))
