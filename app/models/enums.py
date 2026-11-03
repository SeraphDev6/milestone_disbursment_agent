from enum import StrEnum


class SettlementMode(StrEnum):
    escrow_release = "escrow_release"
    invoice_on_verify = "invoice_on_verify"


class Role(StrEnum):
    """An organization's role on one agreement. The same org can be payer on one agreement and payee on another."""

    payer = "payer"
    payee = "payee"
    reviewer = "reviewer"  # invited third party: reviews criteria and evidence, never moves money


class AgreementStatus(StrEnum):
    draft = "draft"
    awaiting_confirmation = "awaiting_confirmation"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class MilestoneStatus(StrEnum):
    pending = "pending"
    funded = "funded"
    evidence_received = "evidence_received"
    verifying = "verifying"
    needs_review = "needs_review"
    verified = "verified"
    settling = "settling"
    settled = "settled"
    settlement_failed = "settlement_failed"
    disputed = "disputed"
    overdue = "overdue"


class SettlementStatus(StrEnum):
    submitted = "submitted"
    succeeded = "succeeded"
    failed = "failed"
    unclaimed = "unclaimed"  # payout to an email with no PayPal account (fallback path)
    expired = "expired"  # authorization lapsed or invoice past timeout


class EvidenceType(StrEnum):
    document = "document"  # report, memo, PDF
    spreadsheet = "spreadsheet"  # budget, sign-in sheet, data table
    photo = "photo"
    link = "link"  # public URL: published page, video, listing
    code_change = "code_change"  # commit or pull request
    ci_run = "ci_run"
    dataset = "dataset"
    attestation = "attestation"  # signed statement from a named third party


class Comparator(StrEnum):
    at_least = "at_least"
    at_most = "at_most"
    equals = "equals"
    exists = "exists"
    on_or_before = "on_or_before"


class VerificationMethod(StrEnum):
    check = "check"  # deterministic check from the registry decides it
    model = "model"  # the model judges it, citing evidence
    human = "human"  # always goes to a person; never auto-releases


class LintLevel(StrEnum):
    error = "error"  # blocks confirm
    warning = "warning"  # shown, does not block


class OrgRole(StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"

    @property
    def rank(self) -> int:
        return {"viewer": 0, "member": 1, "admin": 2, "owner": 3}[self.value]

    def at_least(self, other: "OrgRole") -> bool:
        return self.rank >= other.rank


class InvitationKind(StrEnum):
    counterparty = "counterparty"  # join an agreement as its payee
    reviewer = "reviewer"  # join an agreement as a third-party reviewer
    member = "member"  # join an organization


class TokenKind(StrEnum):
    session = "session"  # a signed-in user
    api_key = "api_key"  # an org's integration or agent
