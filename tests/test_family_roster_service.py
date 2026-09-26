import tempfile
import unittest
from pathlib import Path

from app import build_service
from src import documents
from src.domain import Actor, ValidationError


CREATE_DATA = {
    "applicant_id": "A-770",
    "case_type": "family",
    "received_day": 100,
    "deadline_days": 30,
    "response_day": 100,
    "representation_active": True,
    "required_documents": ["passport", "sponsor_letter"],
    "members": [{"name": "王小明", "relationship": "child", "birth_day": 50}],
}


class FamilyRosterServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))
        self.record = self.service.create(Actor("creator", "intake_officer"), "IMM-77001", CREATE_DATA)

    def tearDown(self):
        self.temp.cleanup()

    def act(self, role, action, data=None, record=None):
        record = record or self.record
        return self.service.act(Actor("operator", role), record["id"], record["version"], action, data or {})

    def test_roster_changes_recalculate_pending_and_persist_for_reopen(self):
        record = self.record
        # 提交后主申请人材料齐，但子女监护声明是新要求，决定被拦
        record = self.act("legal_rep", "submit", {"documents": ["passport", "sponsor_letter"]}, record)
        with self.assertRaises(ValidationError):
            self.act("case_officer", "decide", {"decision": "granted", "decision_reason": "x"}, record)

        # 名单新增配偶：待补区立即出现配偶身份材料
        record = self.act("legal_rep", "add_member", {"name": "王某", "relationship": "spouse", "birth_day": -7200, "current_day": 105}, record)
        pending = {(item["person_id"], item["document"]) for item in record["payload"]["document_summary"]["pending_documents"]}
        self.assertIn(("M2", documents.IDENTITY_DOCUMENT), pending)
        self.assertIn(("M1", documents.GUARDIANSHIP_DOCUMENT), pending)

        # 配偶退出：缺项消失，但其名下若有材料与历史仍保留
        record = self.act("case_officer", "withdraw_member", {"member_id": "M2", "reason": "不随行", "current_day": 108}, record)
        pending = {item["person_id"] for item in record["payload"]["document_summary"]["pending_documents"]}
        self.assertNotIn("M2", pending)
        withdrawn = next(m for m in record["payload"]["members"] if m["id"] == "M2")
        self.assertEqual(withdrawn["status"], "withdrawn")
        self.assertEqual(withdrawn["withdraw_reason"], "不随行")

        # 材料挂在子女名下并逐人核对
        record = self.act("legal_rep", "attach_document", {"person_id": "M1", "documents": [documents.IDENTITY_DOCUMENT, documents.GUARDIANSHIP_DOCUMENT]}, record)
        record = self.act("case_officer", "verify_person", {"person_id": "P0"}, record)
        record = self.act("case_officer", "verify_person", {"person_id": "M1"}, record)
        record = self.act("case_officer", "decide", {"decision": "granted", "decision_reason": "人员材料齐全"}, record)
        self.assertEqual(record["state"], "decided")

        # 重开服务（模拟重新打开详情页）：名单、缺项、变动记录仍可核对
        reopened = build_service(str(Path(self.temp.name) / "test.db"))
        fetched = reopened.get_record(Actor("viewer", "case_officer"), record["id"])
        people = {p["id"]: p for p in fetched["payload"]["document_summary"]["people"]}
        self.assertEqual(people["M1"]["status"], "active")
        self.assertEqual(people["M2"]["status"], "withdrawn")
        self.assertTrue(people["M1"]["verified"])
        change_types = [c["type"] for c in fetched["payload"]["roster_changes"]]
        self.assertEqual(
            change_types,
            ["member_registered", "member_added", "member_withdrawn"],
        )
        timeline = reopened.timeline(Actor("viewer", "case_officer"), record["id"])
        self.assertTrue(any(event["action"] == "add_member" for event in timeline))
        self.assertTrue(any(event["action"] == "withdraw_member" for event in timeline))

    def test_adult_child_guardianship_removed_at_decision(self):
        record = self.record
        record = self.act("legal_rep", "submit", {"documents": ["passport", "sponsor_letter"]}, record)
        record = self.act("legal_rep", "attach_document", {"person_id": "M1", "documents": [documents.IDENTITY_DOCUMENT]}, record)
        # 成年后（current_day 足够大）监护声明不再是缺项
        record = self.act("case_officer", "verify_person", {"person_id": "P0"}, record)
        record = self.act("case_officer", "verify_person", {"person_id": "M1", "current_day": 50 + 18 * 365}, record)
        record = self.act("case_officer", "decide", {"decision": "granted", "decision_reason": "已成年", "current_day": 50 + 18 * 365}, record)
        self.assertEqual(record["state"], "decided")
        self.assertTrue(any(c["type"] == "member_adult" for c in record["payload"]["roster_changes"]))


if __name__ == "__main__":
    unittest.main()
