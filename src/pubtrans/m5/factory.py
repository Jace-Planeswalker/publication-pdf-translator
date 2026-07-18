"""M3 service factory for production structured-model quality stages."""

from __future__ import annotations

from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m1.services import ServiceBundle
from pubtrans.m2.executor import ResilientExecutor
from pubtrans.m2.executor import RetryPolicy
from pubtrans.m2.model import BudgetPolicy
from pubtrans.m2.services import ResilientServices
from pubtrans.m2.store import RecoveryStore
from pubtrans.planning import PlannedTranslation

from .config import ProductConfig
from .openai import StructuredModelClient
from .services import ModelQualityServices
from .services import HierarchicalGlobalReviewService


class ProductionServiceFactory:
    def __init__(
        self,
        *,
        config: ProductConfig,
        client: StructuredModelClient,
    ) -> None:
        self.config = config
        self.client = client

    def create(
        self,
        *,
        store: RecoveryStore,
        document: PreparedDocument,
        planned: PlannedTranslation,
    ) -> ServiceBundle:
        del document
        budget = BudgetPolicy.create(
            scope_key=planned.plan.plan_key,
            max_attempted_calls=self.config.max_translation_calls,
            max_estimated_tokens=self.config.max_estimated_tokens,
            max_estimated_microusd=self.config.max_estimated_microusd,
        )
        executor = ResilientExecutor(
            store,
            owner_id=f"quality-bus-{planned.plan.plan_key[:16]}",
            retry_policy=RetryPolicy(),
            lease_ttl_seconds=max(300.0, self.config.request_timeout_seconds + 60),
        )
        resilient = ResilientServices(
            ModelQualityServices(self.client).bundle,
            executor,
            budget,
        ).bundle
        return ServiceBundle(
            translation=resilient.translation,
            bilingual_review=resilient.bilingual_review,
            adjudication=resilient.adjudication,
            chinese_edit=resilient.chinese_edit,
            verification=resilient.verification,
            global_review=HierarchicalGlobalReviewService(
                client=self.client,
                executor=executor,
                budget=budget,
                max_chunk_characters=(
                    self.config.global_review_chunk_characters
                ),
            ),
        )
