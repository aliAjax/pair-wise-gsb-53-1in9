import tempfile
import unittest
from pathlib import Path

from app import build_service
from src.domain import Actor, Conflict, NotFound, PermissionDenied, ValidationError


CREATE_DATA = {'applicant_id': 'A-900', 'case_type': 'family', 'received_day': 100, 'deadline_days': 30, 'response_day': 110, 'representation_active': True, 'required_documents': ['passport', 'sponsor_letter']}
INTAKE = Actor("creator", "intake_officer")
OFFICER = Actor("officer", "case_officer")
LEGAL = Actor("lawyer", "legal_rep")

SPOUSE = {"name": "王芳", "relationship": "spouse", "birth_date": "1990-05-01"}
MINOR_CHILD = {"name": "小明", "relationship": "child", "birth_date": "2015-03-01"}
CHILD_DOCS = ["identity_document", "relationship_proof", "birth_certificate", "guardianship_declaration"]


class FamilyLedgerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = build_service(str(Path(self.temp.name) / "test.db"))
        self.family = self.service.family

    def tearDown(self):
        self.temp.cleanup()

    def _create(self, extra=None):
        payload = dict(CREATE_DATA)
        if extra:
            payload.update(extra)
        return self.service.create(INTAKE, "IMM-30001", payload)

    def test_intake_registers_relationship_birth_date_and_guardianship(self):
        record = self._create({"family_members": [SPOUSE, dict(MINOR_CHILD, guardianship_declaration=True)]})
        ledger = self.family.ledger(INTAKE, record["id"], as_of="2026-01-01")
        self.assertEqual(len(ledger["members"]), 2)
        spouse, child = ledger["members"]
        self.assertEqual(spouse["relationship"], "spouse")
        self.assertEqual(child["birth_date"], "2015-03-01")
        self.assertTrue(child["minor"])
        self.assertIn("guardianship_declaration", child["required_documents"])
        self.assertNotIn("guardianship_declaration", child["missing_documents"])
        self.assertIn("marriage_certificate", spouse["missing_documents"])

    def test_pending_area_recomputed_after_roster_and_documents(self):
        record = self._create()
        child = self.family.add_member(INTAKE, record["id"], MINOR_CHILD)
        ledger = self.family.ledger(INTAKE, record["id"], as_of="2026-01-01")
        self.assertFalse(ledger["ready_for_decision"])
        self.assertIn(str(child["id"]), ledger["pending"])
        result = self.family.add_documents(LEGAL, record["id"], child["id"], {"documents": CHILD_DOCS})
        self.assertEqual(result["missing_documents"], [])
        ledger = self.family.ledger(INTAKE, record["id"], as_of="2026-01-01")
        self.assertTrue(ledger["ready_for_decision"])
        spouse = self.family.add_member(INTAKE, record["id"], SPOUSE)
        ledger = self.family.ledger(INTAKE, record["id"], as_of="2026-01-01")
        self.assertIn(str(spouse["id"]), ledger["pending"])
        self.assertNotIn(str(child["id"]), ledger["pending"])

    def test_adulthood_changes_requirements_on_reopen(self):
        record = self._create()
        self.family.add_member(INTAKE, record["id"], MINOR_CHILD)
        minor_view = self.family.ledger(INTAKE, record["id"], as_of="2026-01-01")
        self.assertIn("guardianship_declaration", minor_view["members"][0]["required_documents"])
        adult_view = self.family.ledger(INTAKE, record["id"], as_of="2040-01-01")
        member = adult_view["members"][0]
        self.assertFalse(member["minor"])
        self.assertNotIn("guardianship_declaration", member["required_documents"])

    def test_decision_requires_each_member_cleared(self):
        record = self._create({"family_members": [MINOR_CHILD]})
        record = self.service.act(LEGAL, record["id"], record["version"], "submit", {"documents": ["passport", "sponsor_letter"]})
        with self.assertRaises(ValidationError) as ctx:
            self.service.act(OFFICER, record["id"], record["version"], "decide", {"decision": "granted", "decision_reason": "材料充分"})
        self.assertIn("小明", str(ctx.exception))
        child = self.family.ledger(INTAKE, record["id"])["members"][0]
        self.family.add_documents(LEGAL, record["id"], child["id"], {"documents": CHILD_DOCS})
        record = self.service.get_record(INTAKE, record["id"])
        decided = self.service.act(OFFICER, record["id"], record["version"], "decide", {"decision": "granted", "decision_reason": "材料充分"})
        self.assertEqual(decided["state"], "decided")

    def test_removed_member_keeps_history_and_stops_blocking(self):
        record = self._create({"family_members": [SPOUSE]})
        spouse = self.family.ledger(INTAKE, record["id"])["members"][0]
        self.family.add_documents(LEGAL, record["id"], spouse["id"], {"documents": ["identity_document"]})
        removed = self.family.remove_member(OFFICER, record["id"], spouse["id"], {"reason": "离婚退出"})
        self.assertEqual(removed["status"], "removed")
        ledger = self.family.ledger(INTAKE, record["id"])
        member = ledger["members"][0]
        self.assertEqual(member["status"], "removed")
        self.assertEqual(member["submitted_documents"], ["identity_document"])
        self.assertEqual(member["removed_reason"], "离婚退出")
        self.assertTrue(ledger["ready_for_decision"])
        events = [event["event"] for event in ledger["events"]]
        self.assertEqual(events, ["member_added", "documents_added", "member_removed"])
        with self.assertRaises(Conflict):
            self.family.remove_member(OFFICER, record["id"], spouse["id"], {"reason": "重复退出"})
        with self.assertRaises(Conflict):
            self.family.add_documents(LEGAL, record["id"], spouse["id"], {"documents": ["passport"]})

    def test_family_permissions(self):
        record = self._create()
        with self.assertRaises(PermissionDenied):
            self.family.add_member(LEGAL, record["id"], SPOUSE)
        member = self.family.add_member(INTAKE, record["id"], SPOUSE)
        with self.assertRaises(PermissionDenied):
            self.family.remove_member(INTAKE, record["id"], member["id"], {"reason": "无权操作"})
        with self.assertRaises(PermissionDenied):
            self.family.ledger(Actor("outsider", "outsider"), record["id"])

    def test_member_validation_and_duplicates(self):
        record = self._create()
        with self.assertRaises(ValidationError):
            self.family.add_member(INTAKE, record["id"], {"name": "某人", "relationship": "cousin", "birth_date": "1990-01-01"})
        with self.assertRaises(ValidationError):
            self.family.add_member(INTAKE, record["id"], {"name": "某人", "relationship": "spouse", "birth_date": "1990-13-01"})
        with self.assertRaises(ValidationError):
            self.family.add_member(INTAKE, record["id"], {"name": "某人", "relationship": "spouse", "birth_date": "2999-01-01"})
        self.family.add_member(INTAKE, record["id"], SPOUSE)
        with self.assertRaises(Conflict):
            self.family.add_member(INTAKE, record["id"], SPOUSE)
        with self.assertRaises(Conflict):
            self._create({"family_members": [SPOUSE, SPOUSE]})
        with self.assertRaises(NotFound):
            self.family.add_documents(LEGAL, record["id"], 999, {"documents": ["passport"]})

    def test_roster_frozen_after_decision(self):
        record = self._create()
        record = self.service.act(LEGAL, record["id"], record["version"], "submit", {"documents": ["passport", "sponsor_letter"]})
        record = self.service.act(OFFICER, record["id"], record["version"], "decide", {"decision": "granted", "decision_reason": "材料充分"})
        with self.assertRaises(Conflict):
            self.family.add_member(INTAKE, record["id"], SPOUSE)

    def test_record_detail_shows_roster_pending_and_events(self):
        record = self._create({"family_members": [MINOR_CHILD]})
        detail = self.service.get_record(INTAKE, record["id"])
        self.assertIn("family", detail)
        self.assertEqual(len(detail["family"]["members"]), 1)
        self.assertTrue(detail["family"]["pending"])
        self.assertEqual(detail["family"]["events"][0]["event"], "member_added")


if __name__ == "__main__":
    unittest.main()
