import type { DocumentJob, MethodDocumentList, QaTask, RuntimeConfig, RagMethod, TaskEvent } from "./types";

const DEFAULT_BASE_URL = "http://127.0.0.1:18082";

export function getInitialBackendUrl(): string {
  return localStorage.getItem("yq_backend_url") || DEFAULT_BASE_URL;
}

export function saveBackendUrl(url: string): void {
  localStorage.setItem("yq_backend_url", url.replace(/\/$/, ""));
}

async function request<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: init?.body instanceof FormData
      ? init.headers
      : { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function getConfig(baseUrl: string): Promise<RuntimeConfig> {
  return request<RuntimeConfig>(baseUrl, "/v1/config");
}

export function updateConfig(baseUrl: string, config: Partial<RuntimeConfig>): Promise<RuntimeConfig> {
  return request<RuntimeConfig>(baseUrl, "/v1/config", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export async function listMethods(baseUrl: string): Promise<RagMethod[]> {
  const payload = await request<{ methods?: RagMethod[] }>(baseUrl, "/v1/rag-methods");
  return payload.methods || [];
}

export async function listMethodDocuments(baseUrl: string, methodId: string): Promise<MethodDocumentList> {
  const payload = await request<Record<string, unknown>>(
    baseUrl,
    `/v1/rag-methods/${encodeURIComponent(methodId)}/documents`,
  );
  return {
    method_id: String(payload.method_id || methodId),
    documents: normalizeDocuments(payload.documents ?? payload.resources ?? payload.items ?? payload.data),
    raw: payload,
  };
}

function normalizeDocuments(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is Record<string, unknown> => item !== null && typeof item === "object");
  }
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["documents", "resources", "items", "data", "entries"]) {
      const nested = normalizeDocuments(record[key]);
      if (nested.length > 0) return nested;
    }
  }
  return [];
}

export function uploadDocuments(
  baseUrl: string,
  methodId: string,
  files: File[],
  metadata: Record<string, unknown>,
  options: Record<string, unknown>,
): Promise<{ job_id: string; method_id: string; status: string; documents: unknown[] }> {
  const form = new FormData();
  form.set("method_id", methodId);
  for (const file of files) form.append("files", file);
  form.set("metadata_json", JSON.stringify(metadata || {}));
  form.set("options_json", JSON.stringify(options || {}));
  form.set("relative_paths_json", JSON.stringify(files.map(fileRelativePath)));
  return request(baseUrl, "/v1/documents/upload", { method: "POST", body: form });
}

function fileRelativePath(file: File): string {
  const withDirectory = file as File & { webkitRelativePath?: string };
  return withDirectory.webkitRelativePath || file.name;
}

export function listDocumentJobs(baseUrl: string): Promise<{ jobs: DocumentJob[]; total: number }> {
  return request(baseUrl, "/v1/documents/ingestion-jobs");
}

export function getDocumentJob(baseUrl: string, jobId: string): Promise<DocumentJob> {
  return request(baseUrl, `/v1/documents/ingestion-jobs/${jobId}`);
}

export function createQaTask(
  baseUrl: string,
  payload: {
    question: string;
    method_ids?: string[];
    merge_strategy: string;
    options?: Record<string, unknown>;
  },
): Promise<{ task_id: string; status: string }> {
  return request(baseUrl, "/v1/qa/tasks", { method: "POST", body: JSON.stringify(payload) });
}

export function listQaTasks(baseUrl: string): Promise<{ tasks: QaTask[]; total: number }> {
  return request(baseUrl, "/v1/qa/tasks");
}

export function getQaTask(baseUrl: string, taskId: string): Promise<QaTask> {
  return request(baseUrl, `/v1/qa/tasks/${taskId}`);
}

export function openEventStream(
  baseUrl: string,
  path: string,
  onEvent: (event: TaskEvent | Record<string, unknown>, eventType: string) => void,
  onError?: (error: Event) => void,
): EventSource {
  const source = new EventSource(`${baseUrl}${path}`);
  const eventTypes = [
    "queued",
    "running",
    "progress",
    "method_started",
    "method_finished",
    "merge_started",
    "merge_finished",
    "succeeded",
    "failed",
    "cancelled",
    "task",
    "job",
  ];
  for (const type of eventTypes) {
    source.addEventListener(type, (event) => {
      onEvent(JSON.parse((event as MessageEvent).data), type);
    });
  }
  source.onerror = (error) => {
    onError?.(error);
  };
  return source;
}
