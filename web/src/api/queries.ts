/**
 * Data hooks, one per resource. Query keys are hierarchical so a change invalidates exactly what it touched:
 *   ["me"], ["org", orgId, ...], ["agreement", agreementId, ...], ["milestone", milestoneId, ...]
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, asOrg, call } from "./client";
import type { components } from "./schema";
import { ApiError } from "./errors";
import type {
  AgreementSummary,
  PayPalAccountRead,
  AgreementRead,
  AuditEventRead,
  LintReport,
  SubscriptionRead,
  VerdictRead,
  AgreementCreate,
  EvidenceCreate,
  EvidenceVisibility,
  GitHubHandoverRead,
  GitHubInstallationRead,
  GitHubOrgStatus,
  GitHubRegistration,
  GitHubRepo,
  HandoverCheck,
  HandoverResult,
  MilestoneDraft,
  MilestoneStatus,
  OrgRole,
  OrganizationCreate,
} from "./types";
import { IN_FLIGHT } from "@/domain/status";

export const keys = {
  health: ["health"] as const,
  me: ["me"] as const,
  org: (id: string) => ["org", id] as const,
  orgMembers: (id: string) => ["org", id, "members"] as const,
  orgKeys: (id: string) => ["org", id, "api-keys"] as const,
  orgAgreements: (id: string, side?: string) => ["org", id, "agreements", side ?? "all"] as const,
  reviewQueue: (id: string) => ["org", id, "review-queue"] as const,
  portfolio: (id: string) => ["org", id, "portfolio"] as const,
  agreement: (id: string) => ["agreement", id] as const,
  invitations: (id: string) => ["agreement", id, "invitations"] as const,
  audit: (id: string) => ["agreement", id, "audit"] as const,
  subscriptions: (id: string) => ["agreement", id, "subscriptions"] as const,
  ledger: (id: string) => ["agreement", id, "ledger"] as const,
  packet: (id: string) => ["agreement", id, "packet"] as const,
  evidence: (mid: string) => ["milestone", mid, "evidence"] as const,
  verdict: (mid: string) => ["milestone", mid, "verdict"] as const,
  milestoneAudit: (mid: string) => ["milestone", mid, "audit"] as const,
};

// --- system & identity -------------------------------------------------------------------

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => call(api.GET("/health")),
    staleTime: Infinity,
    retry: 1,
  });
}

export const isMock = (h: { mode?: string } | undefined) => h?.mode === "mock";

export function useMe(enabled = true) {
  return useQuery({ queryKey: keys.me, queryFn: () => call(api.GET("/me")), enabled, staleTime: 30_000 });
}

export async function requestMagicLink(email: string) {
  return call(api.POST("/auth/magic-link", { body: { email } }));
}

/** Org SSO for this email's domain, if any: returns the identity provider URL, or null to use a sign-in link. */
export async function startSSO(email: string): Promise<string | null> {
  try {
    return (await call(api.POST("/auth/sso/start", { body: { email, to_app: true } }))).authorize_url;
  } catch (e) {
    // 404: no org has SSO for this domain. 422: the server isn't set up to return to the app. Either way, use a link.
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) return null;
    throw e;
  }
}

export async function verifyMagicLink(token: string) {
  return call(api.POST("/auth/magic-link/verify", { body: { token } }));
}

export async function signOutToken(token: string) {
  // Best effort: the session is dropped locally either way.
  await api.POST("/auth/sign-out", { headers: { Authorization: `Bearer ${token}` } }).catch(() => undefined);
}

export function useAcceptInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { token?: string; id?: string; org_id?: string | null; new_org?: OrganizationCreate | null }) =>
      id
        ? call(api.POST("/invitations/{invitation_id}/accept", { params: { path: { invitation_id: id } }, body: { org_id: body.org_id, new_org: body.new_org } }))
        : call(api.POST("/invitations/accept", { body: { token: body.token!, org_id: body.org_id, new_org: body.new_org } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.me }).then(() => qc.invalidateQueries({ queryKey: ["org"] })),
  });
}

// --- organizations -----------------------------------------------------------------------

export function useCreateOrg() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OrganizationCreate) => call(api.POST("/orgs", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.me }),
  });
}

export function useOrg(orgId: string | undefined) {
  return useQuery({
    queryKey: keys.org(orgId ?? ""),
    queryFn: () => call(api.GET("/orgs/{org_id}", { params: { path: { org_id: orgId! } } })),
    enabled: !!orgId,
  });
}

export function useUpdateOrg(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string | null; paypal_email?: string | null }) =>
      call(api.PATCH("/orgs/{org_id}", { params: { path: { org_id: orgId } }, body })),
    onSuccess: () => Promise.all([qc.invalidateQueries({ queryKey: keys.org(orgId) }), qc.invalidateQueries({ queryKey: keys.me })]),
  });
}

export function useMembers(orgId: string) {
  return useQuery({
    queryKey: keys.orgMembers(orgId),
    queryFn: () => call(api.GET("/orgs/{org_id}/members", { params: { path: { org_id: orgId } } })),
  });
}

export function useInviteMember(orgId: string) {
  return useMutation({
    mutationFn: (body: { email: string; role: OrgRole }) =>
      call(api.POST("/orgs/{org_id}/invitations", { params: { path: { org_id: orgId } }, body })),
  });
}

export function useUpdateMember(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: OrgRole }) =>
      call(api.PATCH("/orgs/{org_id}/members/{user_id}", { params: { path: { org_id: orgId, user_id: userId } }, body: { role } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.orgMembers(orgId) }),
  });
}

export function useRemoveMember(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      call(api.DELETE("/orgs/{org_id}/members/{user_id}", { params: { path: { org_id: orgId, user_id: userId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.orgMembers(orgId) }),
  });
}

export function useApiKeys(orgId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.orgKeys(orgId),
    queryFn: () => call(api.GET("/orgs/{org_id}/api-keys", { params: { path: { org_id: orgId } } })),
    enabled,
  });
}

export function useCreateApiKey(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; org_role: OrgRole }) =>
      call(api.POST("/orgs/{org_id}/api-keys", { params: { path: { org_id: orgId } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.orgKeys(orgId) }),
  });
}

export function useRevokeApiKey(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) =>
      call(api.DELETE("/orgs/{org_id}/api-keys/{key_id}", { params: { path: { org_id: orgId, key_id: keyId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.orgKeys(orgId) }),
  });
}

export function useOrgAgreements(orgId: string, side?: "payer" | "payee" | "reviewer") {
  return useQuery({
    queryKey: keys.orgAgreements(orgId, side),
    queryFn: () =>
      call(api.GET("/orgs/{org_id}/agreements", { params: { path: { org_id: orgId }, query: side ? { side } : {} } })) as Promise<AgreementSummary[]>,
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
}

export function useReviewQueue(orgId: string) {
  return useQuery({
    queryKey: keys.reviewQueue(orgId),
    queryFn: () => call(api.GET("/orgs/{org_id}/review-queue", { params: { path: { org_id: orgId } } })),
    refetchInterval: 30_000,
  });
}

export function usePortfolio(orgId: string) {
  return useQuery({
    queryKey: keys.portfolio(orgId),
    queryFn: () =>
      call(api.GET("/orgs/{org_id}/portfolio", { params: { path: { org_id: orgId } } })) as Promise<{ currency_totals: Record<string, Record<string, string>>; agreements: AgreementSummary[] }>,
  });
}

// --- agreements --------------------------------------------------------------------------

export async function extractAgreement(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append("file", file);
  return call(
    api.POST("/agreements/extract", {
      // openapi-fetch would JSON-encode; hand it the FormData untouched.
      body: body as unknown as { file: string },
      bodySerializer: (b) => b as unknown as FormData,
      signal,
    }),
  );
}

export async function lintDraft(total_amount: string, milestones: MilestoneDraft[], signal?: AbortSignal) {
  return call(api.POST("/criteria/lint", { body: { total_amount, milestones }, signal })) as Promise<LintReport>;
}

export function useCreateAgreement(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgreementCreate) =>
      call(api.POST("/orgs/{org_id}/agreements", { params: { path: { org_id: orgId } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org", orgId] }),
  });
}

/** One agreement, as one of your orgs. Polls while any tranche is mid-flight on the server. */
export function useAgreement(agreementId: string, orgId: string) {
  return useQuery({
    queryKey: keys.agreement(agreementId),
    queryFn: () => call(api.GET("/agreements/{agreement_id}", { params: { path: { agreement_id: agreementId } }, ...asOrg(orgId) })) as Promise<AgreementRead>,
    // Fast while the server is working or the payer is approving a hold in another tab; slower while money
    // waits on a window, a payout or a contest.
    refetchInterval: (q) => {
      const ms = q.state.data?.milestones ?? [];
      if (ms.some((m) => IN_FLIGHT.has(m.status) || m.awaiting_approval)) return 2_000;
      if (ms.some((m) => m.status === "held" || m.status === "contested")) return 10_000;
      return false;
    },
    // Coming back from the PayPal tab must show the result even if the last fetch was seconds ago.
    refetchOnWindowFocus: "always",
  });
}

function invalidateAgreement(qc: QueryClient, agreementId: string) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: ["agreement", agreementId] }),
    qc.invalidateQueries({ queryKey: ["milestone"] }),
    qc.invalidateQueries({ queryKey: ["org"] }),
  ]);
}

export function useReplaceMilestones(agreementId: string, orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { total_amount?: string | null; milestones: MilestoneDraft[] }) =>
      call(api.PUT("/agreements/{agreement_id}/milestones", { params: { path: { agreement_id: agreementId } }, body, ...asOrg(orgId) })) as Promise<AgreementRead>,
    onSuccess: (data) => {
      qc.setQueryData(keys.agreement(agreementId), data);
      return invalidateAgreement(qc, agreementId);
    },
  });
}

export function useConfirm(agreementId: string, orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { criteria_hash: string; auto_release_enabled?: boolean | null }) =>
      call(api.POST("/agreements/{agreement_id}/confirm", { params: { path: { agreement_id: agreementId } }, body, ...asOrg(orgId) })) as Promise<AgreementRead>,
    onSuccess: (data) => {
      qc.setQueryData(keys.agreement(agreementId), data);
      return invalidateAgreement(qc, agreementId);
    },
    onError: (e) => {
      // Someone changed the criteria since we loaded them: refetch so the person reviews the new version.
      if (e instanceof ApiError && e.status === 409) qc.invalidateQueries({ queryKey: keys.agreement(agreementId) });
    },
  });
}

export function useInvitations(agreementId: string, orgId: string) {
  return useQuery({
    queryKey: keys.invitations(agreementId),
    queryFn: () =>
      call(api.GET("/agreements/{agreement_id}/invitations", { params: { path: { agreement_id: agreementId } }, ...asOrg(orgId) })),
  });
}

export function useInvite(agreementId: string, orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; kind: "counterparty" | "reviewer" }) =>
      call(api.POST("/agreements/{agreement_id}/invitations", { params: { path: { agreement_id: agreementId } }, body, ...asOrg(orgId) })),
    onSuccess: () => invalidateAgreement(qc, agreementId),
  });
}

export function useRevokeInvitation(agreementId: string, orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) =>
      call(
        api.DELETE("/agreements/{agreement_id}/invitations/{invitation_id}", {
          params: { path: { agreement_id: agreementId, invitation_id: invitationId } },
          ...asOrg(orgId),
        }),
      ),
    onSuccess: () => invalidateAgreement(qc, agreementId),
  });
}

export function useAgreementAudit(agreementId: string, orgId: string) {
  return useQuery({
    queryKey: keys.audit(agreementId),
    queryFn: () => call(api.GET("/agreements/{agreement_id}/audit", { params: { path: { agreement_id: agreementId } }, ...asOrg(orgId) })) as Promise<AuditEventRead[]>,
    refetchInterval: 15_000,
  });
}

export function usePacket(agreementId: string, orgId: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.packet(agreementId),
    queryFn: () =>
      call(
        api.GET("/agreements/{agreement_id}/compliance-packet", {
          params: { path: { agreement_id: agreementId }, query: { format: "json" } },
          ...asOrg(orgId),
        }),
      ) as Promise<CompliancePacket>,
    enabled,
  });
}

/** Shape of the compliance packet JSON (app/services/packet.py); untyped in OpenAPI because it's a raw Response. */
export type PacketResult = { criterion_id: string; met: boolean; decided_by: string; observed?: string | null; evidence_ids: string[]; quote?: string | null; missing?: string | null };
export type PacketSkeptic = { results: PacketResult[]; rationale: string; confidence: number; model: string; prompt_version: string; disagrees_on: string[]; error?: string | null };
export type CompliancePacket = {
  generated_at: string;
  agreement: { id: string; title: string; total_amount: string; currency: string; settlement_mode: string; status: string; criteria_hash: string | null; auto_release_enabled: boolean; auto_release_threshold: number; released: string; dispute_window_days?: number; deliverable_release?: string; min_evidence_visibility?: string };
  parties: { role: string; name: string; paypal_email: string | null; confirmed_criteria_hash: string | null; confirmed_at: string | null }[];
  milestones: {
    id: string;
    title: string;
    amount: string;
    due_date: string | null;
    status: string;
    revealed_at?: string | null;
    evidence_withheld?: boolean;
    criteria: { id?: string; statement: string; measure: { metric: string; comparator: string; target?: unknown; unit?: string | null }; verification?: string; check_id?: string | null }[];
    evidence: { id: string; title: string; type: string; source: string; external_ref: string | null; pinned_ref: string; file_sha256?: string | null; payer_visibility?: string; dated: string | null; received_at: string; submitted_by: string; has_original_file: boolean }[];
    verdicts: { id: string; created_at: string; met: boolean; gate: string; model: string; prompt_version: string; model_confidence: number; criteria_hash: string; evidence_ids: string[]; results: PacketResult[]; rationale: string; skeptic?: PacketSkeptic | null }[];
    delivery?: ({ at: string; actor: string; action: string; matches?: boolean; how?: string; evidence_id?: string } & Record<string, unknown>)[];
    reviews: { at: string; actor: string; action: string; note: string | null }[];
    settlements: { attempt: number; mode: string; idempotency_key: string; paypal_ref: string | null; status: string; error: string | null; at: string }[];
    payout_route?: string | null;
    disbursements?: { kind: string; attempt: number; idempotency_key: string; paypal_ref: string | null; status: string; error: string | null; at: string }[];
    audit: { at: string; actor: string; action: string; from: string | null; to: string | null; detail: Record<string, unknown> }[];
  }[];
  ledger: { at: string; milestone_id: string | null; kind: string; amount: string; paypal_ref: string }[];
};

// --- integrations & events ---------------------------------------------------------------

export function useConnectGitHub(agreementId: string, orgId: string) {
  return useMutation({
    mutationFn: (body: string | GitHubRegistration) =>
      call(
        api.POST("/agreements/{agreement_id}/integrations/github", {
          params: { path: { agreement_id: agreementId } },
          body: typeof body === "string" ? { repo: body } : body,
          ...asOrg(orgId),
        }),
      ),
  });
}

/** Code handed over from a contractor's repo once the tranche was paid. Polls while one is still pending. */
export function useHandovers(milestoneId: string, orgId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["milestone", milestoneId, "handovers"],
    queryFn: () => call(api.GET("/milestones/{milestone_id}/handovers", { ...mp(milestoneId), ...asOrg(orgId) })) as Promise<GitHubHandoverRead[]>,
    enabled,
    refetchInterval: (q) => ((q.state.data ?? []).some((h) => h.status === "pending") ? 15_000 : false),
  });
}

export function useRetryHandover(milestoneId: string, orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (handoverId: string) =>
      call(api.POST("/handovers/{handover_id}/retry", { params: { path: { handover_id: handoverId } }, ...asOrg(orgId) })) as Promise<GitHubHandoverRead>,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["milestone", milestoneId, "handovers"] }),
  });
}

export function useSubscriptions(agreementId: string, orgId: string) {
  return useQuery({
    queryKey: keys.subscriptions(agreementId),
    queryFn: () =>
      call(api.GET("/agreements/{agreement_id}/subscriptions", { params: { path: { agreement_id: agreementId } }, ...asOrg(orgId) })) as Promise<SubscriptionRead[]>,
  });
}

export function useSubscribe(agreementId: string, orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { url: string; events?: string[] }) =>
      call(api.POST("/agreements/{agreement_id}/subscriptions", { params: { path: { agreement_id: agreementId } }, body, ...asOrg(orgId) })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.subscriptions(agreementId) }),
  });
}

export function useUnsubscribe(agreementId: string, orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (subscriptionId: string) =>
      call(
        api.DELETE("/agreements/{agreement_id}/subscriptions/{subscription_id}", {
          params: { path: { agreement_id: agreementId, subscription_id: subscriptionId } },
          ...asOrg(orgId),
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.subscriptions(agreementId) }),
  });
}

// --- tranches ----------------------------------------------------------------------------

const mp = (milestoneId: string) => ({ params: { path: { milestone_id: milestoneId } } });

export function useEvidence(milestoneId: string, orgId: string, status?: MilestoneStatus) {
  return useQuery({
    queryKey: [...keys.evidence(milestoneId), status],
    queryFn: () => call(api.GET("/milestones/{milestone_id}/evidence", { ...mp(milestoneId), ...asOrg(orgId) })),
  });
}

/** Latest verdict; null (not an error) when there isn't one yet. */
export function useVerdict(milestoneId: string, orgId: string, status?: MilestoneStatus) {
  return useQuery({
    queryKey: [...keys.verdict(milestoneId), status],
    queryFn: async () => {
      try {
        return (await call(api.GET("/milestones/{milestone_id}/verdict", { ...mp(milestoneId), ...asOrg(orgId) }))) as VerdictRead;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
  });
}

export function useMilestoneAudit(milestoneId: string, orgId: string, status?: MilestoneStatus) {
  return useQuery({
    queryKey: [...keys.milestoneAudit(milestoneId), status],
    queryFn: () => call(api.GET("/milestones/{milestone_id}/audit", { ...mp(milestoneId), ...asOrg(orgId) })) as Promise<AuditEventRead[]>,
  });
}

/** All tranche actions. Each refreshes the agreement and the tranche's evidence, verdict and audit. */
export function useTrancheActions(agreementId: string, milestoneId: string, orgId: string) {
  const qc = useQueryClient();
  const done = () => invalidateAgreement(qc, agreementId);
  const o = asOrg(orgId);
  const m = mp(milestoneId);
  return {
    fund: useMutation({ mutationFn: () => call(api.POST("/milestones/{milestone_id}/fund", { ...m, ...o })), onSuccess: done }),
    void: useMutation({ mutationFn: () => call(api.POST("/milestones/{milestone_id}/void", { ...m, ...o })), onSuccess: done }),
    release: useMutation({ mutationFn: () => call(api.POST("/milestones/{milestone_id}/release", { ...m, ...o })), onSuccess: done }),
    reverify: useMutation({ mutationFn: () => call(api.POST("/milestones/{milestone_id}/verify", { ...m, ...o })), onSuccess: done }),
    handover: useMutation({
      mutationFn: ({ evidenceId, body }: { evidenceId: string; body: HandoverCheck }) =>
        call(api.POST("/evidence/{evidence_id}/handover", { params: { path: { evidence_id: evidenceId } }, body, ...o })) as Promise<HandoverResult>,
      onSuccess: done,
    }),
    review: useMutation({
      mutationFn: (body: { decision: "approve" | "reject"; note?: string | null }) =>
        call(api.POST("/milestones/{milestone_id}/review", { ...m, body, ...o })),
      onSuccess: done,
    }),
    dispute: useMutation({
      mutationFn: (reason: string) => call(api.POST("/milestones/{milestone_id}/dispute", { ...m, body: { reason }, ...o })),
      onSuccess: done,
    }),
    submit: useMutation({
      mutationFn: (body: EvidenceCreate) => call(api.POST("/milestones/{milestone_id}/evidence", { ...m, body, ...o })),
      onSuccess: done,
    }),
    upload: useMutation({
      mutationFn: ({ files, note, dated, payer_visibility }: { files: File[]; note?: string; dated?: string; payer_visibility?: EvidenceVisibility }) => {
        const fd = new FormData();
        for (const f of files) fd.append("files", f, f.name);
        if (note) fd.append("note", note);
        if (dated) fd.append("dated", dated);
        if (payer_visibility) fd.append("payer_visibility", payer_visibility);
        return call(
          api.POST("/milestones/{milestone_id}/uploads", {
            ...m,
            ...o,
            body: fd as unknown as { files: string[] },
            bodySerializer: (b) => b as unknown as FormData,
          }),
        );
      },
      onSuccess: done,
    }),
    precheck: useMutation({
      mutationFn: (evidence: EvidenceCreate[]) => call(api.POST("/milestones/{milestone_id}/precheck", { ...m, body: { evidence }, ...o })) as Promise<VerdictRead>,
    }),
    payNow: useMutation({ mutationFn: () => call(api.POST("/milestones/{milestone_id}/payout", { ...m, ...o })), onSuccess: done }),
    contest: useMutation({
      mutationFn: (reason: string) => call(api.POST("/milestones/{milestone_id}/contest", { ...m, body: { reason }, ...o })),
      onSuccess: done,
    }),
    resolve: useMutation({
      mutationFn: (body: { decision: "release" | "refund"; note?: string }) => call(api.POST("/milestones/{milestone_id}/resolve", { ...m, body, ...o })),
      onSuccess: done,
    }),
  };
}

// --- ledger -------------------------------------------------------------------------------

export type LedgerView = components["schemas"]["LedgerView"];
export type LedgerEntry = components["schemas"]["LedgerEntryRead"];
export type SettlementRow = components["schemas"]["SettlementAttempt"];
export type DisbursementRow = components["schemas"]["DisbursementAttempt"];

export function useLedger(agreementId: string, orgId: string, refreshKey?: unknown) {
  return useQuery({
    queryKey: [...keys.ledger(agreementId), refreshKey],
    queryFn: () => call(api.GET("/agreements/{agreement_id}/ledger", { params: { path: { agreement_id: agreementId } }, ...asOrg(orgId) })),
  });
}

// --- payee PayPal account (partner onboarding) -----------------------------------------------

/** Whether this deployment has a GitHub App, and where this org installed it. */
export function useGitHubStatus(orgId: string) {
  return useQuery({
    queryKey: ["org", orgId, "github"],
    queryFn: () => call(api.GET("/orgs/{org_id}/github", { params: { path: { org_id: orgId } } })) as Promise<GitHubOrgStatus>,
  });
}

/** Repos this org's installations can read (empty when there's no app or no installation). */
export function useGitHubRepos(orgId: string, enabled = true) {
  return useQuery({
    queryKey: ["org", orgId, "github", "repos"],
    queryFn: () => call(api.GET("/orgs/{org_id}/github/repos", { params: { path: { org_id: orgId } } })) as Promise<GitHubRepo[]>,
    enabled,
  });
}

export function useGitHubActions(orgId: string) {
  const qc = useQueryClient();
  const p = { params: { path: { org_id: orgId } } };
  const done = () => qc.invalidateQueries({ queryKey: ["org", orgId, "github"] });
  return {
    install: useMutation({ mutationFn: () => call(api.POST("/orgs/{org_id}/github/install", p)) as Promise<{ install_url: string }> }),
    confirm: useMutation({
      mutationFn: (claim: string) => call(api.POST("/orgs/{org_id}/github/installations", { ...p, body: { claim } })) as Promise<GitHubInstallationRead>,
      onSuccess: done,
    }),
    unlink: useMutation({
      mutationFn: (installationId: number) =>
        call(api.DELETE("/orgs/{org_id}/github/installations/{installation_id}", { params: { path: { org_id: orgId, installation_id: installationId } } })),
      onSuccess: done,
    }),
  };
}

/** The org's connected PayPal account, or null when it has never started connecting. */
export function usePayPalAccount(orgId: string, waiting = false) {
  return useQuery({
    queryKey: ["org", orgId, "paypal"],
    queryFn: async () => {
      try {
        return (await call(api.GET("/orgs/{org_id}/paypal", { params: { path: { org_id: orgId } } }))) as PayPalAccountRead;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    refetchOnWindowFocus: true,
    // While someone is finishing onboarding in PayPal's tab, keep checking until the account is ready.
    refetchInterval: (q) => (waiting && !q.state.data?.ready ? 3_000 : false),
  });
}

export function usePayPalActions(orgId: string) {
  const qc = useQueryClient();
  const p = { params: { path: { org_id: orgId } } };
  return {
    connect: useMutation({ mutationFn: () => call(api.POST("/orgs/{org_id}/paypal/connect", p)) }),
    refresh: useMutation({
      mutationFn: () => call(api.POST("/orgs/{org_id}/paypal/refresh", p)) as Promise<PayPalAccountRead>,
      onSuccess: (data) => {
        qc.setQueryData(["org", orgId, "paypal"], data);
        return qc.invalidateQueries({ queryKey: ["agreement"] });
      },
    }),
  };
}

// --- SSO -----------------------------------------------------------------------------------

export type SSOConnection = components["schemas"]["SSOConnectionRead"];
export type SSOConnectionWrite = components["schemas"]["SSOConnectionWrite"];

/** The org's SSO setup, or null when there isn't one. */
export function useSSO(orgId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["org", orgId, "sso"],
    queryFn: async () => {
      try {
        return await call(api.GET("/orgs/{org_id}/sso", { params: { path: { org_id: orgId } } }));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    enabled,
  });
}

export function useSSOActions(orgId: string) {
  const qc = useQueryClient();
  const done = (data?: SSOConnection | null) => {
    if (data !== undefined) qc.setQueryData(["org", orgId, "sso"], data);
  };
  const p = { params: { path: { org_id: orgId } } };
  return {
    save: useMutation({ mutationFn: (body: SSOConnectionWrite) => call(api.PUT("/orgs/{org_id}/sso", { ...p, body })), onSuccess: done }),
    remove: useMutation({ mutationFn: () => call(api.DELETE("/orgs/{org_id}/sso", p)), onSuccess: () => done(null) }),
    verify: useMutation({
      mutationFn: (domain: string) => call(api.POST("/orgs/{org_id}/sso/domains/{domain}/verify", { params: { path: { org_id: orgId, domain } } })),
      onSuccess: done,
    }),
  };
}
