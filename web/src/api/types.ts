/** Friendly names for the generated OpenAPI schema. Regenerate with `npm run api:types`. */
import type { components } from "./schema";

type S = components["schemas"];

/**
 * Types are generated with --default-non-nullable false so request bodies may omit defaulted fields.
 * Responses always include them, so read models restore those fields as required here.
 */
type Req<T, K extends keyof T> = Omit<T, K> & { [P in K]-?: NonNullable<T[P]> };

export type AgreementRead = Omit<Req<S["AgreementRead"], "currency" | "auto_release_threshold" | "require_reviewer_approval" | "pending_invitations" | "dispute_window_days">, "lint" | "milestones" | "funding"> & { lint: LintReport; milestones: MilestoneRead[]; funding: FundingSummary };
export type AgreementCreate = S["AgreementCreate"];
export type AgreementCreated = S["AgreementCreated"];
export type AgreementSummary = Req<S["AgreementSummary"], "in_dispute_window" | "refunded">;
export type AgreementStatus = S["AgreementStatus"];
export type MilestoneRead = Omit<S["MilestoneRead"], "criteria"> & { criteria: Criterion[] };
export type MilestoneDraft = S["MilestoneCreate-Input"];
export type MilestoneStatus = S["MilestoneStatus"];
export type Criterion = Omit<Req<S["Criterion-Output"], "verification">, "measure"> & { measure: Measure };
export type CriterionDraft = S["Criterion-Input"];
export type Measure = S["Measure-Output"];
export type MeasureDraft = S["Measure-Input"];
export type CriterionResult = S["CriterionResult"];
export type Comparator = S["Comparator"];
export type EvidenceType = S["EvidenceType"];
export type VerificationMethod = S["VerificationMethod"];
export type LintReport = Req<S["LintReport"], "issues">;
export type LintIssue = S["LintIssue"];
export type ExtractionResult = S["ExtractionResult"];
export type PartyRead = S["PartyRead"];
export type Role = S["Role"];
export type OrgRole = S["OrgRole"];
export type SettlementMode = S["SettlementMode"];
export type SettlementRead = S["SettlementRead"];
export type SettlementStatus = S["SettlementStatus"];
export type FundingSummary = Req<S["FundingSummary"], "in_dispute_window" | "refunded">;
export type EvidenceRead = S["EvidenceRead"];
export type EvidenceCreate = S["EvidenceCreate"];
export type EvidenceAccepted = S["EvidenceAccepted"];
export type VerdictRead = Req<S["VerdictRead"], "is_precheck">;
export type AuditEventRead = Req<S["AuditEventRead"], "detail" | "at">;
export type Me = S["Me"];
export type UserRead = S["UserRead"];
export type MembershipRead = S["MembershipRead"];
export type InvitationRead = S["InvitationRead"];
export type InvitationKind = S["InvitationKind"];
export type OrganizationRead = S["OrganizationRead"];
export type OrganizationCreate = S["OrganizationCreate"];
export type ApiKeyRead = S["ApiKeyRead"];
export type ApiKeyCreated = S["ApiKeyCreated"];
export type ReviewItem = S["ReviewItem"];
export type Portfolio = S["Portfolio"];
export type GitHubIntegration = Req<S["GitHubIntegration"], "content_type" | "events">;
export type SubscriptionRead = Req<S["SubscriptionRead"], "events">;
export type SubscriptionCreated = S["SubscriptionCreated"];
export type ReviewOutcome = S["ReviewOutcome"];
export type DisputePack = S["DisputePack"];
export type FundingStarted = S["FundingStarted"];
export type PayPalAccountRead = Req<S["PayPalAccountRead"], "payments_receivable" | "primary_email_confirmed" | "permissions_granted" | "started">;
export type DisbursementRead = S["DisbursementRead"];
export type PayoutRoute = S["PayoutRoute"];
