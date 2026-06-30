import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Database,
  FileText,
  FileUp,
  Loader2,
  MessageSquareText,
  RefreshCw,
  Save,
  Settings,
  LogOut,
  Play,
  XCircle,
  RotateCw,
  Square,
} from "lucide-react";
import {
  ApiError,
  clearAuthToken,
  createQaTask,
  getConfig,
  getCurrentUser,
  getMethodHealth,
  getDocumentJob,
  getInitialBackendUrl,
  getQaTask,
  login,
  listMethodDocuments,
  listDocumentJobs,
  listMethods,
  listQaTasks,
  logout,
  openEventStream,
  restartMethod,
  saveBackendUrl,
  startMethod,
  stopMethod,
  updateConfig,
  uploadDocuments,
} from "./api";
import type { AuthUser, DocumentJob, MergeStrategy, QaTask, RagMethod, RuntimeConfig } from "./types";
import type { MethodDocument } from "./types";

type View = "config" | "documents" | "qa";

const emptyConfig: RuntimeConfig = {
  rag_manager_base_url: "http://127.0.0.1:18081",
  rag_manager_timeout_seconds: 1200,
  default_method_ids: [],
  max_concurrent_tasks: 4,
  method_timeout_seconds: 1200,
  upload_dir: "data/uploads",
  max_concurrent_ingestion_jobs: 2,
  merge_enabled: false,
  merge_base_url: "",
  merge_model: "",
  merge_timeout_seconds: 300,
  merge_temperature: 0.2,
};

export function App() {
  const [backendUrl, setBackendUrl] = useState(getInitialBackendUrl());
  const [activeView, setActiveView] = useState<View>("qa");
  const [config, setConfig] = useState<RuntimeConfig>(emptyConfig);
  const [methods, setMethods] = useState<RagMethod[]>([]);
  const [qaTasks, setQaTasks] = useState<QaTask[]>([]);
  const [documentJobs, setDocumentJobs] = useState<DocumentJob[]>([]);
  const [status, setStatus] = useState("未连接");
  const [error, setError] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);

  async function refreshAll() {
    setError(null);
    try {
      saveBackendUrl(backendUrl);
      const [nextConfig, nextMethods, nextTasks, nextJobs] = await Promise.all([
        getConfig(backendUrl),
        listMethods(backendUrl).catch(() => []),
        listQaTasks(backendUrl).catch(() => ({ tasks: [], total: 0 })),
        listDocumentJobs(backendUrl).catch(() => ({ jobs: [], total: 0 })),
      ]);
      setConfig({ ...emptyConfig, ...nextConfig, merge_api_key: "" });
      setMethods(nextMethods);
      setQaTasks(nextTasks.tasks);
      setDocumentJobs(nextJobs.jobs);
      setStatus("已连接");
      setAuthRequired(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearAuthToken();
        setCurrentUser(null);
        setAuthRequired(true);
        setStatus("未登录");
        setError(null);
        return;
      }
      setStatus("连接失败");
      setError(errorMessage(err));
    }
  }

  useEffect(() => {
    void bootstrap();
  }, []);

  async function bootstrap() {
    setError(null);
    saveBackendUrl(backendUrl);
    try {
      const user = await getCurrentUser(backendUrl);
      setCurrentUser(user);
      setAuthChecked(true);
      await refreshAll();
    } catch (err) {
      setAuthChecked(true);
      if (err instanceof ApiError && err.status === 401) {
        clearAuthToken();
        setCurrentUser(null);
        setAuthRequired(true);
        setStatus("未登录");
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        setCurrentUser(null);
        setAuthRequired(false);
        await refreshAll();
        return;
      }
      setStatus("连接失败");
      setError(errorMessage(err));
    }
  }

  async function handleLogin(username: string, password: string) {
    setError(null);
    saveBackendUrl(backendUrl);
    const response = await login(backendUrl, username, password);
    setCurrentUser(response.user);
    setAuthRequired(false);
    await refreshAll();
  }

  async function handleLogout() {
    await logout(backendUrl);
    setCurrentUser(null);
    setAuthRequired(true);
    setStatus("未登录");
  }

  if (!authChecked) {
    return <div className="loading-screen"><Loader2 className="spin" size={24} />正在连接</div>;
  }

  if (authRequired || (!currentUser && status === "未登录")) {
    return (
      <LoginPage
        backendUrl={backendUrl}
        setBackendUrl={setBackendUrl}
        onLogin={handleLogin}
      />
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Bot size={24} />
          <div>
            <strong>YQ-QA</strong>
            <span>{status}</span>
          </div>
        </div>
        <nav className="nav">
          <NavButton active={activeView === "qa"} icon={<MessageSquareText />} label="问答" onClick={() => setActiveView("qa")} />
          <NavButton active={activeView === "documents"} icon={<FileUp />} label="文档入库" onClick={() => setActiveView("documents")} />
          <NavButton active={activeView === "config"} icon={<Settings />} label="配置" onClick={() => setActiveView("config")} />
        </nav>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="backend-control">
            <label>Backend</label>
            <input value={backendUrl} onChange={(event) => setBackendUrl(event.target.value)} />
            <button className="icon-button" onClick={() => void refreshAll()} title="刷新">
              <RefreshCw size={18} />
            </button>
          </div>
          <StatusPill status={status} />
          {currentUser && (
            <div className="user-control">
              <span>{currentUser.display_name || currentUser.username}</span>
              <button className="icon-button" onClick={() => void handleLogout()} title="退出登录">
                <LogOut size={18} />
              </button>
            </div>
          )}
        </header>

        {error && <Notice type="error" message={error} />}

        {activeView === "config" && (
          <ConfigPage
            backendUrl={backendUrl}
            config={config}
            setConfig={setConfig}
            methods={methods}
            refresh={refreshAll}
            setError={setError}
          />
        )}
        {activeView === "documents" && (
          <DocumentsPage
            backendUrl={backendUrl}
            methods={methods}
            jobs={documentJobs}
            setJobs={setDocumentJobs}
            setError={setError}
          />
        )}
        {activeView === "qa" && (
          <QaPage
            backendUrl={backendUrl}
            config={config}
            methods={methods}
            tasks={qaTasks}
            setTasks={setQaTasks}
            setError={setError}
          />
        )}
      </main>
    </div>
  );
}

function LoginPage({
  backendUrl,
  setBackendUrl,
  onLogin,
}: {
  backendUrl: string;
  setBackendUrl: (value: string) => void;
  onLogin: (username: string, password: string) => Promise<void>;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      await onLogin(username, password);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-screen">
      <section className="login-panel">
        <div className="brand login-brand">
          <Bot size={26} />
          <div>
            <strong>YQ-QA</strong>
            <span>登录后继续</span>
          </div>
        </div>
        <Field label="Backend">
          <input value={backendUrl} onChange={(event) => setBackendUrl(event.target.value)} />
        </Field>
        <Field label="用户名">
          <input value={username} onChange={(event) => setUsername(event.target.value)} />
        </Field>
        <Field label="密码">
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </Field>
        {error && <Notice type="error" message={error} />}
        <button className="primary" disabled={!username || !password || submitting} onClick={() => void submit()}>
          {submitting ? <Loader2 className="spin" size={18} /> : <Bot size={18} />}
          登录
        </button>
      </section>
    </main>
  );
}

function ConfigPage({
  backendUrl,
  config,
  setConfig,
  methods,
  refresh,
  setError,
}: {
  backendUrl: string;
  config: RuntimeConfig;
  setConfig: Dispatch<SetStateAction<RuntimeConfig>>;
  methods: RagMethod[];
  refresh: () => Promise<void>;
  setError: (message: string | null) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [methodBusy, setMethodBusy] = useState<Record<string, string>>({});
  const [methodHealth, setMethodHealth] = useState<Record<string, string>>({});

  const methodIds = methods.map((method) => method.method_id);

  async function runMethodAction(
    methodId: string,
    action: "start" | "stop" | "restart" | "health",
  ) {
    setMethodBusy((current) => ({ ...current, [methodId]: action }));
    setError(null);
    try {
      if (action === "start") await startMethod(backendUrl, methodId);
      if (action === "stop") await stopMethod(backendUrl, methodId);
      if (action === "restart") await restartMethod(backendUrl, methodId);
      if (action === "health") {
        const health = await getMethodHealth(backendUrl, methodId);
        const status = typeof health.status === "string" ? health.status : "unknown";
        setMethodHealth((current) => ({ ...current, [methodId]: status }));
      }
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setMethodBusy((current) => {
        const next = { ...current };
        delete next[methodId];
        return next;
      });
    }
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload: Partial<RuntimeConfig> = { ...config };
      if (apiKeyInput.trim()) payload.merge_api_key = apiKeyInput.trim();
      else delete payload.merge_api_key;
      const next = await updateConfig(backendUrl, payload);
      setConfig({ ...emptyConfig, ...next, merge_api_key: "" });
      setApiKeyInput("");
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel-grid">
      <div className="panel-grid two">
        <div className="panel">
          <PanelTitle icon={<Settings />} title="后端配置" />
          <Field label="RAG Manager">
            <input value={config.rag_manager_base_url} onChange={(event) => setConfig({ ...config, rag_manager_base_url: event.target.value })} />
          </Field>
          <Field label="上传目录">
            <input value={config.upload_dir} onChange={(event) => setConfig({ ...config, upload_dir: event.target.value })} />
          </Field>
          <div className="field-row">
            <Field label="问答并发">
              <input type="number" min={1} value={config.max_concurrent_tasks} onChange={(event) => setConfig({ ...config, max_concurrent_tasks: Number(event.target.value) })} />
            </Field>
            <Field label="入库并发">
              <input type="number" min={1} value={config.max_concurrent_ingestion_jobs} onChange={(event) => setConfig({ ...config, max_concurrent_ingestion_jobs: Number(event.target.value) })} />
            </Field>
          </div>
          <Field label="默认 Method">
            <MultiSelect
              options={methodIds}
              selected={config.default_method_ids}
              onChange={(selected) => setConfig({ ...config, default_method_ids: selected })}
            />
          </Field>
        </div>

        <div className="panel">
          <PanelTitle icon={<Bot />} title="答案合并" />
          <label className="toggle">
            <input type="checkbox" checked={config.merge_enabled} onChange={(event) => setConfig({ ...config, merge_enabled: event.target.checked })} />
            <span>启用合并模型</span>
          </label>
          <Field label="Base URL">
            <input value={config.merge_base_url || ""} onChange={(event) => setConfig({ ...config, merge_base_url: event.target.value })} />
          </Field>
          <Field label="Model">
            <input value={config.merge_model || ""} onChange={(event) => setConfig({ ...config, merge_model: event.target.value })} />
          </Field>
          <Field label={config.merge_api_key_set ? `API Key (${config.merge_api_key_masked})` : "API Key"}>
            <input type="password" value={apiKeyInput} onChange={(event) => setApiKeyInput(event.target.value)} placeholder="留空则不修改" />
          </Field>
          <div className="field-row">
            <Field label="Timeout">
              <input type="number" min={1} value={config.merge_timeout_seconds} onChange={(event) => setConfig({ ...config, merge_timeout_seconds: Number(event.target.value) })} />
            </Field>
            <Field label="Temperature">
              <input type="number" min={0} max={2} step={0.1} value={config.merge_temperature} onChange={(event) => setConfig({ ...config, merge_temperature: Number(event.target.value) })} />
            </Field>
          </div>
          <button className="primary" onClick={() => void save()} disabled={saving}>
            {saving ? <Loader2 className="spin" size={18} /> : <Save size={18} />}
            保存配置
          </button>
        </div>
      </div>

      <MethodManagerPanel
        methods={methods}
        busy={methodBusy}
        health={methodHealth}
        onAction={runMethodAction}
      />
    </section>
  );
}

function MethodManagerPanel({
  methods,
  busy,
  health,
  onAction,
}: {
  methods: RagMethod[];
  busy: Record<string, string>;
  health: Record<string, string>;
  onAction: (methodId: string, action: "start" | "stop" | "restart" | "health") => Promise<void>;
}) {
  return (
    <div className="panel">
      <div className="panel-title-row">
        <PanelTitle icon={<Database />} title="Method 管理" />
      </div>
      <div className="method-grid">
        {methods.length === 0 && <div className="empty">暂无 method</div>}
        {methods.map((method) => {
          const methodBusy = busy[method.method_id];
          const status = method.status || "unknown";
          return (
            <article className="method-card" key={method.method_id}>
              <div className="method-card-head">
                <div>
                  <strong>{method.display_name || method.method_id}</strong>
                  <div className="meta">{method.method_id}</div>
                </div>
                <StatusBadge status={status} />
              </div>
              <div className="method-facts">
                <span>{method.backend_type || "backend unknown"}</span>
                <span>{method.worker_url || "worker 未注册"}</span>
                {method.pid ? <span>pid {method.pid}</span> : <span>pid -</span>}
                {health[method.method_id] && <span>health {health[method.method_id]}</span>}
              </div>
              <div className="method-actions">
                <button
                  className="secondary"
                  disabled={Boolean(methodBusy) || status === "running"}
                  onClick={() => void onAction(method.method_id, "start")}
                  type="button"
                >
                  {methodBusy === "start" ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
                  启动
                </button>
                <button
                  className="secondary"
                  disabled={Boolean(methodBusy) || status !== "running"}
                  onClick={() => void onAction(method.method_id, "stop")}
                  type="button"
                >
                  {methodBusy === "stop" ? <Loader2 className="spin" size={16} /> : <Square size={16} />}
                  停止
                </button>
                <button
                  className="secondary"
                  disabled={Boolean(methodBusy)}
                  onClick={() => void onAction(method.method_id, "restart")}
                  type="button"
                >
                  {methodBusy === "restart" ? <Loader2 className="spin" size={16} /> : <RotateCw size={16} />}
                  重启
                </button>
                <button
                  className="secondary"
                  disabled={Boolean(methodBusy) || status !== "running"}
                  onClick={() => void onAction(method.method_id, "health")}
                  type="button"
                >
                  {methodBusy === "health" ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
                  健康
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function DocumentsPage({
  backendUrl,
  methods,
  jobs,
  setJobs,
  setError,
}: {
  backendUrl: string;
  methods: RagMethod[];
  jobs: DocumentJob[];
  setJobs: Dispatch<SetStateAction<DocumentJob[]>>;
  setError: (message: string | null) => void;
}) {
  const [methodId, setMethodId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [metadata, setMetadata] = useState("{}");
  const [options, setOptions] = useState("{\"max_concurrency\":1,\"poll_interval_sec\":2}");
  const [methodDocuments, setMethodDocuments] = useState<MethodDocument[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(jobs[0]?.job_id || null);
  const [uploading, setUploading] = useState(false);
  const streams = useRef<Record<string, EventSource>>({});
  const selectedJob = jobs.find((job) => job.job_id === selectedJobId) || jobs[0] || null;

  useEffect(() => {
    if (!methodId && methods.length) setMethodId(methods[0].method_id);
  }, [methods, methodId]);

  useEffect(() => {
    if (!selectedJobId && jobs.length > 0) setSelectedJobId(jobs[0].job_id);
  }, [selectedJobId, jobs]);

  useEffect(() => {
    if (methodId) void refreshMethodDocuments();
  }, [backendUrl, methodId]);

  useEffect(() => {
    return () => {
      for (const stream of Object.values(streams.current)) stream.close();
      streams.current = {};
    };
  }, [backendUrl]);

  useEffect(() => {
    for (const job of jobs) {
      if (job.status === "queued" || job.status === "running") {
        attachDocumentStream(backendUrl, job.job_id, setJobs, streams.current, refreshMethodDocuments);
      }
    }
  }, [backendUrl, jobs, setJobs]);

  async function submit() {
    setUploading(true);
    setError(null);
    try {
      const result = await uploadDocuments(
        backendUrl,
        methodId,
        files,
        JSON.parse(metadata || "{}"),
        JSON.parse(options || "{}"),
      );
      const job = await getDocumentJob(backendUrl, result.job_id);
      setJobs((current) => upsert(current, job, "job_id"));
      setSelectedJobId(job.job_id);
      attachDocumentStream(backendUrl, result.job_id, setJobs, streams.current, refreshMethodDocuments);
      void refreshMethodDocuments();
      setFiles([]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  async function refreshMethodDocuments() {
    if (!methodId) return;
    setDocumentsLoading(true);
    setError(null);
    try {
      const result = await listMethodDocuments(backendUrl, methodId);
      setMethodDocuments(result.documents);
    } catch (err) {
      setMethodDocuments([]);
      setError(errorMessage(err));
    } finally {
      setDocumentsLoading(false);
    }
  }

  return (
    <section className="document-workspace">
      <aside className="panel document-sidebar">
        <div className="panel-title-row">
          <PanelTitle icon={<Database />} title="入库任务" />
          <button className="icon-button" onClick={() => void refreshDocumentJobs(backendUrl, setJobs, setError)} title="刷新入库任务">
            <RefreshCw size={18} />
          </button>
        </div>
        <DocumentJobHistory
          jobs={jobs}
          selectedJobId={selectedJob?.job_id || null}
          onSelect={setSelectedJobId}
        />
      </aside>

      <section className="document-main">
        <div className="document-detail">
          <DocumentJobDetailPanel job={selectedJob} />
          <MethodDocumentList
            methodId={methodId}
            documents={methodDocuments}
            loading={documentsLoading}
            refresh={refreshMethodDocuments}
          />
        </div>

        <div className="panel document-composer">
          <div className="composer-grid">
            <Field label="目标 Method">
              <select value={methodId} onChange={(event) => setMethodId(event.target.value)}>
                {methods.map((method) => (
                  <option key={method.method_id} value={method.method_id}>{method.method_id}</option>
                ))}
              </select>
            </Field>
            <Field label="文件">
              <FolderFileInput onFiles={setFiles} />
            </Field>
          </div>
          {files.length > 0 && (
            <div className="file-selection">
              {files.length} 个文件已选择
              <span>{selectedFileSummary(files)}</span>
            </div>
          )}
          <details className="composer-options">
            <summary>Metadata JSON</summary>
            <textarea value={metadata} onChange={(event) => setMetadata(event.target.value)} />
          </details>
          <details className="composer-options">
            <summary>Options JSON</summary>
            <textarea value={options} onChange={(event) => setOptions(event.target.value)} />
          </details>
          <button className="primary" disabled={!methodId || files.length === 0 || uploading} onClick={() => void submit()}>
            {uploading ? <Loader2 className="spin" size={18} /> : <FileUp size={18} />}
            上传并入库
          </button>
        </div>
      </section>
    </section>
  );
}

function QaPage({
  backendUrl,
  config,
  methods,
  tasks,
  setTasks,
  setError,
}: {
  backendUrl: string;
  config: RuntimeConfig;
  methods: RagMethod[];
  tasks: QaTask[];
  setTasks: Dispatch<SetStateAction<QaTask[]>>;
  setError: (message: string | null) => void;
}) {
  const [question, setQuestion] = useState("");
  const [selectedMethods, setSelectedMethods] = useState<string[]>(config.default_method_ids);
  const [mergeStrategy, setMergeStrategy] = useState<MergeStrategy>("auto");
  const [options, setOptions] = useState("{\"target_uri\":\"viking://resources/\"}");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(tasks[0]?.task_id || null);
  const [submitting, setSubmitting] = useState(false);
  const streams = useRef<Record<string, EventSource>>({});
  const selectedTask = tasks.find((task) => task.task_id === selectedTaskId) || tasks[0] || null;
  const duplicateTask = findDuplicateTask(tasks, question);

  useEffect(() => {
    if (selectedMethods.length === 0) setSelectedMethods(config.default_method_ids);
  }, [config.default_method_ids]);

  useEffect(() => {
    if (!selectedTaskId && tasks.length > 0) setSelectedTaskId(tasks[0].task_id);
  }, [selectedTaskId, tasks]);

  useEffect(() => {
    return () => {
      for (const stream of Object.values(streams.current)) stream.close();
      streams.current = {};
    };
  }, [backendUrl]);

  useEffect(() => {
    for (const task of tasks) {
      if (task.status === "queued" || task.status === "running") {
        attachQaStream(backendUrl, task.task_id, setTasks, streams.current);
      }
    }
  }, [backendUrl, tasks, setTasks]);

  async function submit() {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await createQaTask(backendUrl, {
        question: trimmedQuestion,
        method_ids: selectedMethods.length ? selectedMethods : undefined,
        merge_strategy: mergeStrategy,
        options: JSON.parse(options || "{}"),
      });
      const task = await getQaTask(backendUrl, result.task_id);
      setTasks((current) => upsert(current, task, "task_id"));
      setSelectedTaskId(task.task_id);
      attachQaStream(backendUrl, result.task_id, setTasks, streams.current);
      setQuestion("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="qa-workspace">
      <aside className="panel qa-history">
        <div className="panel-title-row">
          <PanelTitle icon={<MessageSquareText />} title="问题历史" />
          <button className="icon-button" onClick={() => void refreshTaskList(backendUrl, setTasks, setError)} title="刷新历史">
            <RefreshCw size={18} />
          </button>
        </div>
        <QaHistoryList
          tasks={tasks}
          selectedTaskId={selectedTask?.task_id || null}
          onSelect={setSelectedTaskId}
        />
      </aside>

      <section className="qa-main">
        <div className="qa-detail">
          <div className="panel question-panel">
            <PanelTitle icon={<MessageSquareText />} title="问题" />
            {selectedTask ? (
              <>
                <div className="question-text">{selectedTask.question}</div>
                <div className="qa-detail-meta">
                  <StatusBadge status={selectedTask.status} />
                  <span>{selectedTask.method_ids.join(", ")}</span>
                </div>
              </>
            ) : (
              <div className="empty">还没有选中的问题</div>
            )}
          </div>

          <div className="panel answer-panel">
            <PanelTitle icon={<Bot />} title="答案" />
            {selectedTask ? <QaAnswerDetail task={selectedTask} /> : <div className="empty">提交或选择一个问题后显示答案</div>}
          </div>
        </div>

        <div className="panel qa-composer">
          <Field label="问题">
            <textarea
              className="composer-input"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="输入一个单轮问题"
            />
          </Field>
          {duplicateTask && (
            <DuplicateQuestionNotice
              task={duplicateTask}
              onView={() => setSelectedTaskId(duplicateTask.task_id)}
            />
          )}
          <div className="composer-grid">
            <Field label="Method">
              <MultiSelect options={methods.map((method) => method.method_id)} selected={selectedMethods} onChange={setSelectedMethods} />
            </Field>
            <Field label="合并策略">
              <select value={mergeStrategy} onChange={(event) => setMergeStrategy(event.target.value as MergeStrategy)}>
                <option value="auto">auto</option>
                <option value="llm">llm</option>
                <option value="none">none</option>
              </select>
            </Field>
          </div>
          <details className="composer-options">
            <summary>Options JSON</summary>
            <textarea value={options} onChange={(event) => setOptions(event.target.value)} />
          </details>
          <button className="primary" disabled={!question.trim() || submitting} onClick={() => void submit()}>
            {submitting ? <Loader2 className="spin" size={18} /> : <MessageSquareText size={18} />}
            提交
          </button>
        </div>
      </section>
    </section>
  );
}

function QaHistoryList({
  tasks,
  selectedTaskId,
  onSelect,
}: {
  tasks: QaTask[];
  selectedTaskId: string | null;
  onSelect: (taskId: string) => void;
}) {
  return (
    <div className="qa-history-list">
      {tasks.length === 0 && <div className="empty">暂无问题</div>}
      {tasks.map((task) => (
        <button
          className={task.task_id === selectedTaskId ? "history-item active" : "history-item"}
          key={task.task_id}
          onClick={() => onSelect(task.task_id)}
          type="button"
        >
          <div className="history-item-head">
            <strong>{task.question}</strong>
            <StatusBadge status={task.status} />
          </div>
          <div className="meta">{task.method_ids.join(", ") || "未指定 method"}</div>
          <div className="history-item-foot">
            <span>{formatTime(task.updated_at || task.created_at)}</span>
            {task.results && task.results.length > 0 && <span>{task.results.length} 个答案</span>}
          </div>
        </button>
      ))}
    </div>
  );
}

function QaAnswerDetail({ task }: { task: QaTask }) {
  const results = task.results || [];
  if (task.status === "queued") {
    return <div className="empty">问题已进入队列</div>;
  }
  if (task.status === "running" && results.length === 0) {
    return <div className="empty">正在等待 method 返回答案</div>;
  }
  return (
    <div className="answer-stack">
      {task.merged_answer && (
        <div className="merged answer-block">
          <b>最终答案</b>
          <p>{task.merged_answer}</p>
        </div>
      )}
      {results.map((result) => (
        <div className="answer answer-block" key={result.method_id}>
          <div className="answer-head">
            <b>{result.method_id}</b>
            <StatusBadge status={result.status} />
          </div>
          {result.error ? <p className="error-text">{result.error}</p> : <p>{result.answer}</p>}
          {result.sources?.length > 0 && <SourceList sources={result.sources} />}
        </div>
      ))}
      {task.error && <p className="error-text">{task.error}</p>}
      {results.length === 0 && !task.merged_answer && task.status !== "running" && <div className="empty">暂无答案</div>}
    </div>
  );
}

function DuplicateQuestionNotice({ task, onView }: { task: QaTask; onView: () => void }) {
  const message = task.status === "queued" || task.status === "running"
    ? "历史中有相同问题正在回答"
    : task.status === "succeeded"
      ? "历史中已有相同问题的答案"
      : "历史中有相同问题记录";
  return (
    <div className="duplicate-notice">
      <AlertCircle size={17} />
      <span>{message}</span>
      <button className="secondary compact-button" type="button" onClick={onView}>查看</button>
    </div>
  );
}

function DocumentJobHistory({
  jobs,
  selectedJobId,
  onSelect,
}: {
  jobs: DocumentJob[];
  selectedJobId: string | null;
  onSelect: (jobId: string) => void;
}) {
  return (
    <div className="document-job-list">
      {jobs.length === 0 && <div className="empty">暂无入库任务</div>}
      {jobs.map((job) => (
        <button
          className={job.job_id === selectedJobId ? "history-item active" : "history-item"}
          key={job.job_id}
          onClick={() => onSelect(job.job_id)}
          type="button"
        >
          <div className="history-item-head">
            <strong>{job.method_id}</strong>
            <StatusBadge status={job.status} />
          </div>
          <ProgressBar value={job.progress.progress_percent} />
          <div className="history-item-foot">
            <span>{job.progress.completed_documents}/{job.progress.total_documents} 完成</span>
            <span>{formatTime(job.updated_at || job.created_at)}</span>
          </div>
        </button>
      ))}
    </div>
  );
}

function DocumentJobDetailPanel({ job }: { job: DocumentJob | null }) {
  return (
    <div className="panel document-job-detail">
      <PanelTitle icon={<Database />} title="任务详情" />
      {!job && <div className="empty">还没有选中的入库任务</div>}
      {job && (
        <>
          <div className="document-job-head">
            <div>
              <strong>{job.method_id}</strong>
              <div className="meta">{job.job_id}</div>
            </div>
            <StatusBadge status={job.status} />
          </div>
          <ProgressBar value={job.progress.progress_percent} />
          <div className="progress-grid">
            <Metric label="总数" value={job.progress.total_documents} />
            <Metric label="完成" value={job.progress.completed_documents} />
            <Metric label="运行中" value={job.progress.running_documents} />
            <Metric label="等待" value={job.progress.pending_documents} />
            <Metric label="失败" value={job.progress.failed_documents} />
          </div>
          {job.progress.message && <div className="job-message">{job.progress.message}</div>}
          <div className="document-files">
            {(job.documents || []).map((document) => (
              <div className="file-line" key={document.document_id}>
                <b>{document.filename}</b>
                <span>{document.path}</span>
              </div>
            ))}
          </div>
          {job.error && <p className="error-text">{job.error}</p>}
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function MethodDocumentList({
  methodId,
  documents,
  loading,
  refresh,
}: {
  methodId: string;
  documents: MethodDocument[];
  loading: boolean;
  refresh: () => Promise<void>;
}) {
  return (
    <div className="panel list-panel">
      <div className="panel-title-row">
        <PanelTitle icon={<FileText />} title="Method 文件" />
        <button className="icon-button" onClick={() => void refresh()} disabled={!methodId || loading} title="刷新文件列表">
          {loading ? <Loader2 className="spin" size={18} /> : <RefreshCw size={18} />}
        </button>
      </div>
      <div className="meta method-meta">{methodId || "未选择 method"} · {documents.length} 个文件</div>
      <div className="list">
        {!loading && documents.length === 0 && <div className="empty">暂无文件</div>}
        {documents.map((document, index) => (
          <article className="item" key={methodDocumentKey(document, index)}>
            <div className="item-head">
              <strong>{methodDocumentTitle(document, index)}</strong>
              {document.status && <StatusBadge status={String(document.status)} />}
            </div>
            <div className="meta">{methodDocumentPath(document)}</div>
            {document.metadata && Object.keys(document.metadata).length > 0 && (
              <details className="sources">
                <summary>metadata</summary>
                <pre className="json-preview">{JSON.stringify(document.metadata, null, 2)}</pre>
              </details>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}

function FolderFileInput({ onFiles }: { onFiles: (files: File[]) => void }) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const input = folderInputRef.current;
    if (input) {
      input.setAttribute("webkitdirectory", "");
      input.setAttribute("directory", "");
    }
  }, []);

  return (
    <div className="file-picker">
      <button className="secondary" type="button" onClick={() => fileInputRef.current?.click()}>
        <FileText size={17} />
        选择文件
      </button>
      <button className="secondary" type="button" onClick={() => folderInputRef.current?.click()}>
        <FileUp size={17} />
        选择文件夹
      </button>
      <input
        ref={fileInputRef}
        className="hidden-input"
        type="file"
        multiple
        onChange={(event) => onFiles(Array.from(event.target.files || []))}
      />
      <input
        ref={folderInputRef}
        className="hidden-input"
        type="file"
        multiple
        onChange={(event) => onFiles(Array.from(event.target.files || []))}
      />
    </div>
  );
}

function selectedFileSummary(files: File[]): string {
  const paths = files.map((file) => {
    const withDirectory = file as File & { webkitRelativePath?: string };
    return withDirectory.webkitRelativePath || file.name;
  });
  const first = paths[0] || "";
  const folder = first.includes("/") ? first.split("/")[0] : "";
  return folder ? `${folder}/...` : first;
}

function methodDocumentKey(document: MethodDocument, index: number): string {
  return String(
    document.document_id
      || document.id
      || document.uri
      || document.path
      || document.url
      || document.corpus_path
      || index,
  );
}

function methodDocumentTitle(document: MethodDocument, index: number): string {
  return String(
    document.title
      || document.name
      || document.document_id
      || document.id
      || leafName(document.path)
      || leafName(document.corpus_path)
      || leafName(document.uri)
      || `document-${index + 1}`,
  );
}

function methodDocumentPath(document: MethodDocument): string {
  return String(
    document.path
      || document.corpus_path
      || document.uri
      || document.url
      || document.document_id
      || document.id
      || "",
  );
}

function leafName(value: unknown): string {
  if (!value) return "";
  const text = String(value).replace(/\\/g, "/");
  return text.split("/").filter(Boolean).pop() || text;
}

function SourceList({ sources }: { sources: { title?: string | null; snippet?: string | null }[] }) {
  return (
    <details className="sources">
      <summary>Sources ({sources.length})</summary>
      {sources.slice(0, 5).map((source, index) => (
        <div className="source" key={index}>
          <b>{source.title || `source-${index + 1}`}</b>
          <p>{source.snippet}</p>
        </div>
      ))}
    </details>
  );
}

function MultiSelect({ options, selected, onChange }: { options: string[]; selected: string[]; onChange: (selected: string[]) => void }) {
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  return (
    <div className="chips">
      {options.length === 0 && <span className="empty-inline">无可用 method</span>}
      {options.map((option) => (
        <button
          type="button"
          className={selectedSet.has(option) ? "chip selected" : "chip"}
          key={option}
          onClick={() => {
            if (selectedSet.has(option)) onChange(selected.filter((item) => item !== option));
            else onChange([...selected, option]);
          }}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: ReactNode; label: string; onClick: () => void }) {
  return <button className={active ? "nav-button active" : "nav-button"} onClick={onClick}>{icon}<span>{label}</span></button>;
}

function PanelTitle({ icon, title }: { icon: ReactNode; title: string }) {
  return <h2 className="panel-title">{icon}{title}</h2>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

function StatusPill({ status }: { status: string }) {
  return <span className={status === "已连接" ? "status ok" : status === "连接失败" ? "status bad" : "status"}>{status}</span>;
}

function StatusBadge({ status }: { status: string }) {
  const icon = status === "succeeded" ? <CheckCircle2 size={15} /> : status === "failed" ? <XCircle size={15} /> : status === "running" ? <Loader2 className="spin" size={15} /> : <AlertCircle size={15} />;
  return <span className={`badge ${status}`}>{icon}{status}</span>;
}

function ProgressBar({ value }: { value: number }) {
  return <div className="progress"><div style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>;
}

function Notice({ type, message }: { type: "error"; message: string }) {
  return <div className={`notice ${type}`}><AlertCircle size={18} />{message}</div>;
}

async function refreshTaskList(
  backendUrl: string,
  setTasks: Dispatch<SetStateAction<QaTask[]>>,
  setError: (message: string | null) => void,
) {
  setError(null);
  try {
    const result = await listQaTasks(backendUrl);
    setTasks(result.tasks);
  } catch (err) {
    setError(errorMessage(err));
  }
}

async function refreshDocumentJobs(
  backendUrl: string,
  setJobs: Dispatch<SetStateAction<DocumentJob[]>>,
  setError: (message: string | null) => void,
) {
  setError(null);
  try {
    const result = await listDocumentJobs(backendUrl);
    setJobs(result.jobs);
  } catch (err) {
    setError(errorMessage(err));
  }
}

function findDuplicateTask(tasks: QaTask[], question: string): QaTask | null {
  const normalized = question.trim();
  if (!normalized) return null;
  return tasks.find((task) => task.question.trim() === normalized) || null;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function attachDocumentStream(
  backendUrl: string,
  jobId: string,
  setJobs: React.Dispatch<React.SetStateAction<DocumentJob[]>>,
  streams: Record<string, EventSource>,
  onFinished?: () => void,
) {
  if (streams[jobId]) return;
  streams[jobId] = openEventStream(backendUrl, `/v1/documents/ingestion-jobs/${jobId}/stream`, async (_event, type) => {
    const job = await getDocumentJob(backendUrl, jobId);
    setJobs((jobs) => upsert(jobs, job, "job_id"));
    if (["succeeded", "failed", "cancelled", "job"].includes(type) && ["succeeded", "failed", "cancelled"].includes(job.status)) {
      streams[jobId]?.close();
      delete streams[jobId];
      onFinished?.();
    }
  });
}

function attachQaStream(
  backendUrl: string,
  taskId: string,
  setTasks: React.Dispatch<React.SetStateAction<QaTask[]>>,
  streams: Record<string, EventSource>,
) {
  if (streams[taskId]) return;
  streams[taskId] = openEventStream(backendUrl, `/v1/qa/tasks/${taskId}/stream`, async (_event, type) => {
    const task = await getQaTask(backendUrl, taskId);
    setTasks((tasks) => upsert(tasks, task, "task_id"));
    if (["succeeded", "failed", "cancelled", "task"].includes(type) && ["succeeded", "failed", "cancelled"].includes(task.status)) {
      streams[taskId]?.close();
      delete streams[taskId];
    }
  });
}

function upsert<T, K extends keyof T>(items: T[], item: T, key: K): T[] {
  const exists = items.some((current) => current[key] === item[key]);
  if (!exists) return [item, ...items];
  return items.map((current) => current[key] === item[key] ? item : current);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
