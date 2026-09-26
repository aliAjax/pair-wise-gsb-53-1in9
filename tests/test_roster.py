import unittest

from src import documents, roster
from src.domain import ValidationError
from src.rules import DomainRules


def make_payload(members=None, required=("passport", "sponsor_letter"), received_day=100, response_day=100):
    payload = {
        "applicant_id": "A-900",
        "case_type": "family",
        "received_day": received_day,
        "response_day": response_day,
        "required_documents": list(required),
    }
    payload["members"], payload["roster_changes"] = roster.init_ledger(members or [], received_day)
    payload["person_documents"] = {}
    payload["verified_persons"] = {}
    documents.recompute(payload)
    return payload


class RosterRulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = DomainRules()

    def test_intake_registers_relationship_and_birth(self):
        prepared = self.rules.prepare_create(
            {
                "applicant_id": "A-1",
                "case_type": "family",
                "received_day": 100,
                "deadline_days": 30,
                "response_day": 100,
                "representation_active": True,
                "required_documents": ["passport"],
                "members": [
                    {"name": "王某", "relationship": "spouse", "birth_day": -7200},
                    {"name": "王小明", "relationship": "child", "birth_day": 50},
                ],
            }
        )
        members = prepared["members"]
        self.assertEqual([m["id"] for m in members], ["M1", "M2"])
        self.assertTrue(members[0]["adult"])
        self.assertFalse(members[1]["adult"])
        self.assertEqual([c["type"] for c in prepared["roster_changes"]], ["member_registered", "member_registered"])
        pending = {(item["person_id"], item["document"]) for item in prepared["document_summary"]["pending_documents"]}
        self.assertIn(("M2", documents.GUARDIANSHIP_DOCUMENT), pending)
        self.assertNotIn(("M1", documents.GUARDIANSHIP_DOCUMENT), pending)

    def test_add_member_recalculates_and_appears_in_pending(self):
        payload = make_payload()
        roster.add_member(payload, {"name": "王小明", "relationship": "child", "birth_day": 50}, 110)
        documents.recompute(payload)
        people = {view["id"]: view for view in payload["document_summary"]["people"]}
        self.assertEqual(people["M1"]["required_documents"], [documents.IDENTITY_DOCUMENT, documents.GUARDIANSHIP_DOCUMENT])
        self.assertEqual(people["M1"]["missing_documents"], [documents.IDENTITY_DOCUMENT, documents.GUARDIANSHIP_DOCUMENT])
        self.assertTrue(any(c["type"] == "member_added" for c in payload["roster_changes"]))

    def test_withdraw_keeps_history_and_removes_requirements(self):
        payload = make_payload([{"name": "王某", "relationship": "spouse", "birth_day": -7200}])
        documents.attach_documents(payload, "M1", [documents.IDENTITY_DOCUMENT])
        roster.withdraw_member(payload, "M1", "未随行", 120)
        documents.recompute(payload)
        view = next(view for view in payload["document_summary"]["people"] if view["id"] == "M1")
        self.assertEqual(view["status"], "withdrawn")
        self.assertEqual(view["required_documents"], [])
        self.assertEqual(view["submitted_documents"], [documents.IDENTITY_DOCUMENT])
        self.assertTrue(any(c["type"] == "member_withdrawn" for c in payload["roster_changes"]))

    def test_adult_child_drops_guardianship(self):
        payload = make_payload([{"name": "王小明", "relationship": "child", "birth_day": 50}])
        view = next(view for view in payload["document_summary"]["people"] if view["id"] == "M1")
        self.assertIn(documents.GUARDIANSHIP_DOCUMENT, view["required_documents"])
        roster.refresh_adult_flags(payload, 50 + 18 * 365)
        documents.recompute(payload)
        view = next(view for view in payload["document_summary"]["people"] if view["id"] == "M1")
        self.assertNotIn(documents.GUARDIANSHIP_DOCUMENT, view["required_documents"])
        self.assertTrue(any(c["type"] == "member_adult" for c in payload["roster_changes"]))

    def test_rejected_inputs(self):
        with self.assertRaises(ValidationError):
            make_payload([{"name": "", "relationship": "spouse", "birth_day": 1}])
        with self.assertRaises(ValidationError):
            make_payload([{"name": "x", "relationship": "uncle", "birth_day": 1}])
        with self.assertRaises(ValidationError):
            make_payload([{"name": "x", "relationship": "child", "birth_day": 999}])
        payload = make_payload()
        with self.assertRaises(ValidationError):
            roster.withdraw_member(payload, roster.APPLICANT_ID, "", 100)


class DocumentDecisionGateTest(unittest.TestCase):
    def setUp(self):
        self.rules = DomainRules()

    def _record(self, payload):
        return {"id": 1, "state": "submitted", "payload": payload}

    def test_decide_blocked_until_each_person_verified(self):
        payload = make_payload(
            [
                {"name": "王某", "relationship": "spouse", "birth_day": -7200},
                {"name": "王小明", "relationship": "child", "birth_day": 50},
            ]
        )
        documents.attach_documents(payload, "P0", ["passport", "sponsor_letter"])
        documents.attach_documents(payload, "M1", [documents.IDENTITY_DOCUMENT])
        documents.attach_documents(payload, "M2", [documents.IDENTITY_DOCUMENT])
        # 子女缺监护声明，主申请人也未核对
        blockers = documents.decision_blockers(payload)
        self.assertTrue(any("A-900尚未逐人核对" in b for b in blockers))
        self.assertTrue(any("监护声明" in b for b in blockers))
        documents.verify_person(payload, "P0")
        with self.assertRaises(ValidationError):
            documents.verify_person(payload, "M2")
        documents.attach_documents(payload, "M2", [documents.GUARDIANSHIP_DOCUMENT])
        documents.verify_person(payload, "M1")
        documents.verify_person(payload, "M2")
        self.assertEqual(documents.decision_blockers(payload), [])

    def test_rules_decide_enforces_gate_and_member_recalculation(self):
        payload = make_payload([{"name": "王小明", "relationship": "child", "birth_day": 50}])
        documents.attach_documents(payload, "P0", ["passport", "sponsor_letter"])
        record = self._record(payload)
        with self.assertRaises(ValidationError):
            self.rules.apply_action(record, "decide", {"decision": "granted", "decision_reason": "x"})
        documents.verify_person(payload, "P0")
        documents.attach_documents(payload, "M1", [documents.IDENTITY_DOCUMENT, documents.GUARDIANSHIP_DOCUMENT])
        documents.verify_person(payload, "M1")
        state, new_payload, _ = self.rules.apply_action(
            record, "decide", {"decision": "granted", "decision_reason": "材料齐全"}
        )
        self.assertEqual(state, "decided")
        self.assertEqual(new_payload["decision"], "granted")

    def test_stay_action_keeps_state(self):
        record = self._record(make_payload())
        state, payload, _ = self.rules.apply_action(
            record, "add_member", {"name": "王某", "relationship": "spouse", "birth_day": -7200, "current_day": 105}
        )
        self.assertEqual(state, "submitted")
        self.assertIn("M1", {p["id"] for p in payload["document_summary"]["people"]})

    def test_materials_attach_to_person(self):
        payload = make_payload()
        view = documents.attach_documents(payload, "P0", ["passport"])
        self.assertEqual(view["missing_documents"], ["sponsor_letter"])
        with self.assertRaises(ValidationError):
            documents.verify_person(payload, "P0")
        documents.attach_documents(payload, "P0", ["sponsor_letter"])
        view = documents.verify_person(payload, "P0")
        self.assertTrue(view["verified"])


if __name__ == "__main__":
    unittest.main()
