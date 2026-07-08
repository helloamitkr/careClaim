import type {
  CaseCreated,
  CaseDetail,
  CaseListItem,
  FixtureTemplate,
  ReviewAction,
  ReviewResult,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";

interface FastApiValidationError {
  loc?: (string | number)[];
  msg?: string;
}

function formatApiError(status: number, body: string): string {
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        return (detail as FastApiValidationError[])
          .map((e) => {
            // Drop "body" and union-member type names (IngestCaseRequest,
            // list[IngestCaseRequest]) — keep only real field segments.
            const segments = (e.loc ?? []).filter(
              (s) => typeof s === "number" || (s !== "body" && !/^[A-Z]|\[/.test(String(s))),
            );
            const field = segments.length ? segments.join(".") : "body";
            return `${field}: ${e.msg ?? "invalid"}`;
          })
          .join("\n");
      }
    }
  } catch {
    // body wasn't JSON — fall through to the raw text below
  }
  return `${status}: ${body}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(formatApiError(response.status, body));
  }
  return response.json() as Promise<T>;
}

export function listFixtures(): Promise<FixtureTemplate[]> {
  return request("/api/fixtures");
}

export function createCase(template: string): Promise<CaseCreated> {
  return request("/api/cases", {
    method: "POST",
    body: JSON.stringify({ template }),
  });
}

export function ingestCase(caseJson: Record<string, unknown>): Promise<CaseCreated> {
  return request("/api/cases/ingest", {
    method: "POST",
    body: JSON.stringify(caseJson),
  });
}

export function listCases(): Promise<CaseListItem[]> {
  return request("/api/cases");
}

export function getCase(caseId: string): Promise<CaseDetail> {
  return request(`/api/cases/${caseId}`);
}

export function reviewCase(
  caseId: string,
  action: ReviewAction,
  reviewer: string,
  note?: string,
): Promise<ReviewResult> {
  return request(`/api/cases/${caseId}/review`, {
    method: "POST",
    body: JSON.stringify({ action, reviewer, note }),
  });
}

export function getStats(): Promise<import("./types").Stats> {
  return request("/api/stats");
}

export function ingestBulk(
  cases: unknown[],
): Promise<import("./types").BulkIngestResult> {
  return request("/api/cases/ingest", {
    method: "POST",
    body: JSON.stringify(cases),
  });
}
