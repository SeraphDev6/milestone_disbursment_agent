"""Single source of truth: every entity is defined once with SQLModel and used as table, API input and output.

Value objects stored inside rows (Criterion, Measure, CriterionResult, lint) are SQLModel models kept in typed
JSON columns. The only other schemas are the AI output schemas in app.ai.agents, which are deliberately loose.
"""

from app.models.agreements import (
    Agreement,
    AgreementBase,
    AgreementCreate,
    AgreementRead,
    FundingSummary,
    Milestone,
    MilestoneBase,
    MilestoneCreate,
    MilestoneRead,
    Party,
    PartyBase,
    PartyCreate,
    PartyRead,
)
from app.models.criteria import Criterion, CriterionResult, LintIssue, LintReport, Measure
from app.models.enums import (
    AgreementStatus,
    Comparator,
    EvidenceType,
    LintLevel,
    MilestoneStatus,
    Role,
    SettlementMode,
    SettlementStatus,
    VerificationMethod,
)
from app.models.events import (
    AuditEvent,
    AuditEventRead,
    Integration,
    OutboxMessage,
    Subscription,
    SubscriptionCreate,
    SubscriptionCreated,
    SubscriptionRead,
    WebhookEvent,
)
from app.models.evidence import Evidence, EvidenceCreate, EvidenceRead, StoredFile, Verdict, VerdictRead
from app.models.settlement import LedgerEntry, Settlement, SettlementRead

__all__ = [
    "Agreement",
    "AgreementBase",
    "AgreementCreate",
    "AgreementRead",
    "AgreementStatus",
    "AuditEvent",
    "AuditEventRead",
    "Comparator",
    "Criterion",
    "CriterionResult",
    "Evidence",
    "EvidenceCreate",
    "EvidenceRead",
    "EvidenceType",
    "FundingSummary",
    "Integration",
    "LedgerEntry",
    "LintIssue",
    "LintLevel",
    "LintReport",
    "Measure",
    "Milestone",
    "MilestoneBase",
    "MilestoneCreate",
    "MilestoneRead",
    "MilestoneStatus",
    "OutboxMessage",
    "Party",
    "PartyBase",
    "PartyCreate",
    "PartyRead",
    "Role",
    "Settlement",
    "SettlementMode",
    "SettlementRead",
    "SettlementStatus",
    "StoredFile",
    "Subscription",
    "SubscriptionCreate",
    "SubscriptionCreated",
    "SubscriptionRead",
    "Verdict",
    "VerdictRead",
    "VerificationMethod",
    "WebhookEvent",
]
