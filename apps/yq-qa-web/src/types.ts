export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type MergeStrategy = "auto" | "none" | "llm";

export interface RuntimeConfig {
  rag_manager_base_url: string;
  rag_manager_timeout_seconds: number;
  default_method_ids: string[];
  max_concurrent_tasks: number;
  method_timeout_seconds: number;
  upload_dir: string;
  max_concurrent_ingestion_jobs: number;
  merge_enabled: boolean;
  merge_base_url?: string | null;
  merge_api_key_set?: boolean;
  merge_api_key_masked?: string | null;
  merge_api_key?: string | null;
  merge_model?: string | null;
  merge_timeout_seconds: number;
  merge_temperature: number;
  db_path?: string | null;
}

export interface RagMethod {
  method_id: string;
  backend_type?: string;
  display_name?: string | null;
  status?: string;
  worker_url?: string | null;
  enabled?: boolean;
}

export interface Source {
  source_id?: string | null;
  title?: string | null;
  url?: string | null;
  snippet?: string | null;
  score?: number | null;
  metadata?: Record<string, unknown>;
}

export interface MethodAnswer {
  method_id: string;
  status: "succeeded" | "failed";
  answer: string;
  sources: Source[];
  latency_ms?: number | null;
  error?: string | null;
}

export interface QaTask {
  task_id: string;
  request_id: string;
  status: TaskStatus;
  question: string;
  method_ids: string[];
  merge_strategy: MergeStrategy;
  merged_answer?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  results?: MethodAnswer[];
}

export interface TaskEvent {
  event_id: number;
  task_id: string;
  event_type: string;
  message?: string | null;
  data: Record<string, unknown>;
  created_at: string;
}

export interface DocumentProgress {
  total_documents: number;
  completed_documents: number;
  failed_documents: number;
  running_documents: number;
  pending_documents: number;
  progress_percent: number;
  message?: string | null;
}

export interface UploadedDocument {
  document_id: string;
  filename: string;
  path: string;
  title?: string | null;
  size_bytes?: number | null;
}

export interface DocumentJob {
  job_id: string;
  method_id: string;
  status: TaskStatus;
  manager_job_id?: string | null;
  error?: string | null;
  progress: DocumentProgress;
  documents?: UploadedDocument[];
  created_at: string;
  updated_at: string;
}

export interface MethodDocument {
  document_id?: string | null;
  id?: string | null;
  title?: string | null;
  name?: string | null;
  path?: string | null;
  url?: string | null;
  uri?: string | null;
  corpus_path?: string | null;
  status?: string | null;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface MethodDocumentList {
  method_id: string;
  documents: MethodDocument[];
  raw?: unknown;
}
