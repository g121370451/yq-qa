import type { AuthUser, DocumentJob, LoginResponse, MethodDocumentList, MethodRuntime, QaTask, RuntimeConfig, RagMethod, TaskEvent } from "./types";

const DEFAULT_BASE_URL = "http://127.0.0.1:18082";
const TOKEN_KEY = "yq_auth_token";

export class ApiError extends Error {
  status: number;
  body: string;

  constructor(status: number, body: string) {
    super(body || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

export function getInitialBackendUrl(): string {
  return localStorage.getItem("yq_backend_url") || DEFAULT_BASE_URL;
}

export function saveBackendUrl(url: string): void {
  localStorage.setItem("yq_backend_url", url.replace(/\/$/, ""));
}

export function getAuthToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function saveAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const token = getAuthToken();
  const headers: Record<string, string> = headersToRecord(init?.headers);
  if (!(init?.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

function headersToRecord(headers?: HeadersInit): Record<string, string> {
  if (!headers) return {};
  if (headers instanceof Headers) return Object.fromEntries(headers.entries());
  if (Array.isArray(headers)) return Object.fromEntries(headers);
  return { ...headers };
}

export async function login(baseUrl: string, username: string, password: string): Promise<LoginResponse> {
  const response = await request<LoginResponse>(baseUrl, "/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  saveAuthToken(response.access_token);
  return response;
}

export function getCurrentUser(baseUrl: string): Promise<AuthUser | null> {
  return request<AuthUser | null>(baseUrl, "/v1/auth/me");
}

export async function logout(baseUrl: string): Promise<void> {
  try {
    await request(baseUrl, "/v1/auth/logout", { method: "POST", body: "{}" });
  } catch {
    // Local logout should still succeed if the token is already invalid.
  } finally {
    clearAuthToken();
  }
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

export function getMethodRuntime(baseUrl: string, methodId: string): Promise<MethodRuntime> {
  return request<MethodRuntime>(baseUrl, `/v1/rag-methods/${encodeURIComponent(methodId)}/runtime`);
}

export function getMethodHealth(baseUrl: string, methodId: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(baseUrl, `/v1/rag-methods/${encodeURIComponent(methodId)}/health`);
}

export function startMethod(baseUrl: string, methodId: string): Promise<MethodRuntime> {
  return request<MethodRuntime>(baseUrl, `/v1/rag-methods/${encodeURIComponent(methodId)}/start`, {
    method: "POST",
    body: "{}",
  });
}

export function stopMethod(baseUrl: string, methodId: string): Promise<MethodRuntime> {
  return request<MethodRuntime>(baseUrl, `/v1/rag-methods/${encodeURIComponent(methodId)}/stop`, {
    method: "POST",
    body: "{}",
  });
}

export function restartMethod(baseUrl: string, methodId: string): Promise<MethodRuntime> {
  return request<MethodRuntime>(baseUrl, `/v1/rag-methods/${encodeURIComponent(methodId)}/restart`, {
    method: "POST",
    body: "{}",
  });
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
  const source = new EventSource(`${baseUrl}${streamPath(path)}`);
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

function streamPath(path: string): string {
  const token = getAuthToken();
  if (!token) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}access_token=${encodeURIComponent(token)}`;
}
