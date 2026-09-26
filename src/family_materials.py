"""随行家属材料计算：按当前名单推导所需材料与待补缺项。"""
from datetime import date
from typing import Any, Dict, List

from .family_rules import is_minor


COMMON_DOCUMENTS = ["identity_document", "relationship_proof"]
RELATIONSHIP_DOCUMENTS = {
    "spouse": ["marriage_certificate"],
    "child": ["birth_certificate"],
    "parent": ["dependency_proof"],
    "sibling": ["dependency_proof"],
    "other": ["dependency_proof"],
}
MINOR_CHILD_DOCUMENTS = ["guardianship_declaration"]
GUARDIANSHIP_DOCUMENT = "guardianship_declaration"


class MaterialCalculator:
    def required_documents(self, member: Dict[str, Any], as_of: date) -> List[str]:
        docs = list(COMMON_DOCUMENTS)
        docs.extend(RELATIONSHIP_DOCUMENTS.get(member["relationship"], []))
        birth = date.fromisoformat(member["birth_date"])
        if member["relationship"] == "child" and is_minor(birth, as_of):
            docs.extend(MINOR_CHILD_DOCUMENTS)
        return docs

    def missing_documents(self, member: Dict[str, Any], submitted: List[str], as_of: date) -> List[str]:
        provided = set(submitted)
        if member.get("guardianship_declaration"):
            provided.add(GUARDIANSHIP_DOCUMENT)
        return [doc for doc in self.required_documents(member, as_of) if doc not in provided]
