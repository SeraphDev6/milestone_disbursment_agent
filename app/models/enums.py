from enum import StrEnum


class SettlementMode(StrEnum):
    escrow_release = "escrow_release"
    invoice_on_verify = "invoice_on_verify"


class DeliverableRelease(StrEnum):
    """When the payer can open the payee's full evidence."""

    on_submit = "on_submit"  # as it arrives (grants)
    on_capture = "on_capture"  # only once the tranche is captured: the payer pays on the verdict (SOWs)


class PayoutRoute(StrEnum):
    """How a tranche's money reaches the payee."""

    direct = "direct"  # payee connected PayPal (partner onboarding): the hold and capture are in the payee's name
    platform = "platform"  # payee not connected: captured to the platform account, then sent with PayPal Payouts


class DisbursementKind(StrEnum):
    referenced_payout = "referenced_payout"  # release a delayed-disbursement capture to the connected payee
    payout = "payout"  # PayPal Payouts to the payee's email (platform route)
    refund = "refund"  # return a capture to the payer after a contest


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
    held = "held"  # captured from the payer; paid to the payee when the dispute window closes
    contested = "contested"  # payer or reviewer objected during the dispute window; payout paused
    settled = "settled"  # paid to the payee
    refunded = "refunded"  # captured, then returned to the payer after a contest
    settlement_failed = "settlement_failed"
    payout_failed = "payout_failed"  # captured, but paying the payee failed; release retries the payout
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
    oauth_access = "oauth_access"  # an MCP client acting for a user within one org (1 hour)
    oauth_refresh = "oauth_refresh"  # rotates on use; reuse revokes the family
