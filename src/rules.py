"""移民案件期限与材料管理领域规则与状态转换。

随行家属台账的规则、材料计算与存储分开实现：
- 名单规则见 ``src/roster.py``；
- 材料计算见 ``src/documents.py``；
- 存储见 ``src/repository.py``。
"""
from typing import Any, Dict, Iterable, Tuple

from . import documents, roster
from .domain import Actor, Conflict, ValidationError, boolean, choice, integer, number, text, text_list


INITIAL_STATE = "draft"
CREATE_ROLES = {'intake_officer'}
ACTION_ROLES = {'submit': {'legal_rep', 'case_officer'}, 'request_evidence': {'case_officer'}, 'respond': {'legal_rep'}, 'decide': {'case_officer', 'supervisor'}, 'appeal': {'legal_rep'}, 'close': {'supervisor'}, 'add_member': {'intake_officer', 'legal_rep', 'case_officer'}, 'withdraw_member': {'intake_officer', 'legal_rep', 'case_officer'}, 'attach_document': {'legal_rep', 'case_officer'}, 'verify_person': {'legal_rep', 'case_officer', 'supervisor'}}
TRANSITIONS = {'submit': {'draft': 'submitted'}, 'request_evidence': {'submitted': 'evidence_requested'}, 'respond': {'evidence_requested': 'response_received'}, 'decide': {'submitted': 'decided', 'response_received': 'decided'}, 'appeal': {'decided': 'appealed'}, 'close': {'decided': 'closed', 'appealed': 'closed'}}
STAY_ACTIONS = {'add_member', 'withdraw_member', 'attach_document', 'verify_person'}
STAY_STATES = {'draft', 'submitted', 'evidence_requested', 'response_received'}


class DomainRules:
    INITIAL_STATE = INITIAL_STATE

    def known_role(self, role: str) -> bool:
        all_roles = set(CREATE_ROLES)
        for roles in ACTION_ROLES.values():
            all_roles.update(roles)
        return role == "admin" or role in all_roles

    def role_can_create(self, role: str) -> bool:
        return role == "admin" or role in CREATE_ROLES

    def role_can_action(self, role: str, action: str) -> bool:
        return role == "admin" or role in ACTION_ROLES.get(action, set())

    def validate_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(payload)
        text(p, "applicant_id")
        choice(p, "case_type", ["asylum", "family", "work"])
        integer(p, "received_day", 0)
        integer(p, "deadline_days", 1)
        integer(p, "response_day", 0)
        boolean(p, "representation_active")
        text_list(p, "required_documents", 1)
        if "members" in p:
            roster.init_ledger(p.get("members"), int(p["received_day"]))
        return p

    def prepare_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        p = self.validate_create(payload)
        p["deadline_day"] = int(p["received_day"]) + int(p["deadline_days"])
        p["days_remaining"] = int(p["deadline_day"]) - int(p["response_day"])
        p["overdue"] = p["days_remaining"] < 0
        members, changes = roster.init_ledger(p.pop("members", []), int(p["received_day"]))
        p["members"] = members
        p["roster_changes"] = changes
        p["person_documents"] = {}
        p["verified_persons"] = {}
        p["submitted_documents"] = []
        p["missing_documents"] = list(p["required_documents"])
        documents.recompute(p)
        return p

    def check_create_conflicts(self, payload: Dict[str, Any], existing: Iterable[Dict[str, Any]]) -> None:
        for item in existing:
            if item["state"] not in {"closed", "decided"} and item["payload"].get("applicant_id") == payload.get("applicant_id") and item["payload"].get("case_type") == payload.get("case_type"):
                raise Conflict("同一申请人同类型案件仍在处理中")

    def require_transition(self, record: Dict[str, Any], action: str) -> str:
        if action in STAY_ACTIONS:
            if record["state"] not in STAY_STATES:
                raise Conflict("当前状态不允许执行%s" % action)
            return record["state"]
        allowed = TRANSITIONS.get(action, {}).get(record["state"])
        if allowed is None:
            raise Conflict("当前状态不允许执行%s" % action)
        return allowed

    def _as_of_day(self, p: Dict[str, Any], data: Dict[str, Any]) -> int:
        value = data.get("current_day")
        if value is None:
            return documents.current_day(p)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError("current_day必须是整数")
        if value < 0:
            raise ValidationError("current_day不能小于0")
        return int(value)

    def _apply_stay_action(self, p: Dict[str, Any], action: str, data: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        documents.migrate_legacy(p)
        day = self._as_of_day(p, data)
        roster.refresh_adult_flags(p, day)
        if action == "add_member":
            member = roster.add_member(p, data, day)
            documents.recompute(p)
            return {}, "随行家属已登记：%s（%s）" % (member["name"], roster.relationship_label(member["relationship"]))
        if action == "withdraw_member":
            reason = data.get("reason", "")
            reason = reason.strip() if isinstance(reason, str) else ""
            member = roster.withdraw_member(p, text(data, "member_id"), reason, day)
            documents.recompute(p)
            return {}, "随行家属已退出：%s" % member["name"]
        if action == "attach_document":
            person_id = data.get("person_id") or roster.APPLICANT_ID
            view = documents.attach_documents(p, person_id, text_list(data, "documents", 1))
            return {}, "材料已挂到%s名下（%d项）" % (view.get("name", view["id"]), len(view["submitted_documents"]))
        if action == "verify_person":
            person_id = data.get("person_id") or roster.APPLICANT_ID
            view = documents.verify_person(p, person_id)
            return {}, "%s材料核对完成" % view.get("name", view["id"])
        raise Conflict("当前状态不允许执行%s" % action)

    def apply_action(self, record: Dict[str, Any], action: str, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any], str]:
        new_state = self.require_transition(record, action)
        data = dict(data or {})
        p = dict(record["payload"])
        changes: Dict[str, Any] = {}
        summary = ""
        if action in STAY_ACTIONS:
            changes, summary = self._apply_stay_action(p, action, data)
        elif action == "submit":
            docs = text_list(data, "documents", 1)
            documents.migrate_legacy(p)
            documents.attach_documents(p, roster.APPLICANT_ID, docs, replace=True)
            applicant = documents.person_view(roster.applicant_view(p), p)
            required = applicant["required_documents"]
            missing = [doc for doc in required if doc not in docs]
            if missing and not boolean(data, "supervisor_waiver"):
                raise ValidationError("缺少材料：" + ", ".join(missing))
            if p["overdue"] and not boolean(data, "supervisor_waiver"):
                raise ValidationError("案件已超过提交期限")
            changes["submitted_documents"] = docs
            changes["missing_documents"] = missing
            changes["waiver_used"] = boolean(data, "supervisor_waiver")
            changes["person_documents"] = p["person_documents"]
            changes["document_summary"] = p["document_summary"]
            summary = "申请材料已提交"
        elif action == "request_evidence":
            request_day = integer(data, "evidence_request_day", p["response_day"])
            allowed_days = integer(data, "allowed_days", 1)
            changes["evidence_request_day"] = request_day
            changes["evidence_due_day"] = request_day + allowed_days
            changes["evidence_request"] = text(data, "evidence_request")
            summary = "补件要求已发出"
        elif action == "respond":
            docs = text_list(data, "documents", 1)
            if int(data.get("response_day", p["response_day"])) > int(p["evidence_due_day"]):
                raise ValidationError("补件回应超过期限")
            changes["response_day"] = int(data["response_day"])
            changes["evidence_documents"] = docs
            summary = "补件已回应"
        elif action == "decide":
            documents.migrate_legacy(p)
            day = self._as_of_day(p, data)
            roster.refresh_adult_flags(p, day)
            documents.recompute(p)
            blockers = documents.decision_blockers(p)
            if blockers and not boolean(data, "supervisor_waiver"):
                raise ValidationError("随行人员材料未核完：" + "；".join(blockers))
            changes["decision"] = choice(data, "decision", ["granted", "denied", "withdrawn"])
            changes["decision_reason"] = text(data, "decision_reason")
            changes["decision_day"] = day
            changes["document_summary"] = p["document_summary"]
            changes["roster_changes"] = p["roster_changes"]
            summary = "案件已作出决定"
        elif action == "appeal":
            appeal_day = integer(data, "appeal_day", 0)
            if appeal_day > int(p["deadline_day"]) + 30:
                raise ValidationError("上诉窗口已关闭")
            changes["appeal_day"] = appeal_day
            changes["appeal_reason"] = text(data, "appeal_reason")
            summary = "上诉已登记"
        elif action == "close":
            changes["closure_note"] = text(data, "closure_note")
            summary = "案件归档"
        p.update(changes)
        return new_state, p, summary or ("已执行%s" % action)
