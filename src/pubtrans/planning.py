"""Layout-engine-independent translation planning value."""

from __future__ import annotations

from dataclasses import dataclass

from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m1.plan import KernelPlan
from pubtrans.m1.terminology import TerminologySnapshot


@dataclass(frozen=True, slots=True)
class PlannedTranslation:
    terminology: TerminologySnapshot
    plan: KernelPlan

    def validate(self, document: PreparedDocument) -> None:
        self.terminology.validate_against(document)
        self.plan.validate_against(
            document=document,
            terminology=self.terminology,
        )
