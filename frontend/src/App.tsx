import {
  AlertCircle,
  ArrowDownToLine,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Eye,
  FileImage,
  History,
  Images,
  Loader2,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  UploadCloud,
  X
} from "lucide-react";
import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./services/api";
import brandLogo from "./assets/image-processing-logo.svg";
import type {
  Decision,
  ImageMetrics,
  JobHistoryItem,
  JobHistoryResponse,
  JobProgress,
  JobResults,
  AIModelConfig,
  ProfileOption,
  ResultImage,
  UploadItem
} from "./types";
import { decisionLabel, isTerminalStatus, rejectCodeLabel, statusLabel } from "./utils/decision";

const MAX_IMAGES = 50;
const MAX_IMAGE_SIZE_MB = 25;
const UPLOAD_CONCURRENCY = 4;

const fallbackFilterProfiles: ProfileOption[] = [
  {
    id: "renovation_submission_v1",
    name: "装修照片基础筛选",
    description: "过滤尺寸不足、模糊、曝光异常和纯色图片"
  }
];

const fallbackBeautifyProfiles: ProfileOption[] = [
  {
    id: "renovation_natural_v1",
    name: "装修照片自然美化",
    description: "轻微提亮、对比度、色彩和锐度增强，保留现场真实状态"
  }
];

function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const itemsRef = useRef<UploadItem[]>([]);
  const operationVersionRef = useRef(0);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [filterProfiles, setFilterProfiles] = useState<ProfileOption[]>(fallbackFilterProfiles);
  const [beautifyProfiles, setBeautifyProfiles] = useState<ProfileOption[]>(fallbackBeautifyProfiles);
  const [filterProfile, setFilterProfile] = useState("renovation_submission_v1");
  const [beautifyProfile, setBeautifyProfile] = useState("renovation_natural_v1");
  const [job, setJob] = useState<JobProgress | null>(null);
  const [results, setResults] = useState<JobResults | null>(null);
  const [selectedImage, setSelectedImage] = useState<ResultImage | null>(null);
  const [resultFilter, setResultFilter] = useState<"all" | Decision>("all");
  const [history, setHistory] = useState<JobHistoryResponse | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [modelConfigOpen, setModelConfigOpen] = useState(false);
  const [modelConfigLoading, setModelConfigLoading] = useState(false);
  const [modelConfigSaving, setModelConfigSaving] = useState(false);
  const [modelConfig, setModelConfig] = useState<AIModelConfig | null>(null);
  const [modelApiKey, setModelApiKey] = useState("");

  const uploadedCount = items.filter((item) => item.status === "uploaded").length;
  const failedCount = items.filter((item) => item.status === "failed").length;
  const canCreateJob = items.length > 0 && !busy;
  const currentStep = results
    ? 4
    : job?.status === "tagging"
      ? 3
      : job?.status === "enhancing"
        ? 2
        : job && ["queued", "analyzing", "ranking"].includes(job.status)
          ? 1
          : 0;
  const averageScore = useMemo(() => {
    const selected = results?.images.filter((image) => image.decision === "selected") ?? [];
    if (!selected.length) return 0;
    return Math.round(selected.reduce((sum, image) => sum + image.score, 0) / selected.length);
  }, [results]);

  const visibleResults = useMemo(() => {
    if (!results) return [];
    if (resultFilter === "all") return results.images;
    return results.images.filter((image) => image.decision === resultFilter);
  }, [resultFilter, results]);

  useEffect(() => {
    api.getFilterProfiles().then(setFilterProfiles).catch(() => undefined);
    api.getBeautifyProfiles().then(setBeautifyProfiles).catch(() => undefined);
  }, []);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    return () => {
      itemsRef.current.forEach((item) => URL.revokeObjectURL(item.previewUrl));
    };
  }, []);

  useEffect(() => {
    if (!job || isTerminalStatus(job.status)) return;

    const operationVersion = operationVersionRef.current;
    const delay = job.progress < 20 ? 1000 : job.progress < 60 ? 2000 : job.progress < 85 ? 3000 : 5000;
    const timer = window.setTimeout(async () => {
      try {
        const nextJob = await api.getJob(job.job_id);
        if (!isCurrentOperation(operationVersion)) return;
        setJob(nextJob);

        if (nextJob.status === "completed" || nextJob.status === "partial_failed") {
          const nextResults = await api.getResults(nextJob.job_id);
          if (!isCurrentOperation(operationVersion)) return;
          setResults(nextResults);
          setSelectedImage(nextResults.images.find((image) => image.decision === "selected") ?? nextResults.images[0] ?? null);
          void loadHistory(operationVersion);
        }
      } catch (error) {
        if (!isCurrentOperation(operationVersion)) return;
        setMessage(error instanceof Error ? error.message : "查询任务进度失败");
      }
    }, delay);

    return () => window.clearTimeout(timer);
  }, [job]);

  function addFiles(fileList: FileList | File[]) {
    const incoming = Array.from(fileList);
    const availableSlots = MAX_IMAGES - items.length;
    const accepted = incoming.slice(0, availableSlots).filter((file) => file.type.startsWith("image/"));
    const oversized = accepted.find((file) => file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024);

    if (oversized) {
      setMessage(`${oversized.name} 超过 ${MAX_IMAGE_SIZE_MB}MB，已跳过。`);
    }

    const nextItems = accepted
      .filter((file) => file.size <= MAX_IMAGE_SIZE_MB * 1024 * 1024)
      .map<UploadItem>((file) => ({
        id: crypto.randomUUID(),
        file,
        previewUrl: URL.createObjectURL(file),
        status: "ready",
        progress: 0
      }));

    if (incoming.length > availableSlots) {
      setMessage(`最多支持 ${MAX_IMAGES} 张图片，本次已按上限加入。`);
    }

    setItems((current) => [...current, ...nextItems]);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) addFiles(event.target.files);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    addFiles(event.dataTransfer.files);
  }

  function removeItem(itemId: string) {
    setItems((current) => {
      const item = current.find((entry) => entry.id === itemId);
      if (item) URL.revokeObjectURL(item.previewUrl);
      return current.filter((entry) => entry.id !== itemId);
    });
  }

  function resetWorkspace() {
    operationVersionRef.current += 1;
    itemsRef.current.forEach((item) => URL.revokeObjectURL(item.previewUrl));
    itemsRef.current = [];
    setItems([]);
    setJob(null);
    setResults(null);
    setSelectedImage(null);
    setResultFilter("all");
    setBusy(false);
    setMessage(null);
  }

  function isCurrentOperation(operationVersion: number) {
    return operationVersion === operationVersionRef.current;
  }

  async function loadHistory(operationVersion?: number) {
    setHistoryLoading(true);
    try {
      const nextHistory = await api.getHistory();
      if (operationVersion !== undefined && !isCurrentOperation(operationVersion)) return;
      setHistory(nextHistory);
    } catch (error) {
      if (operationVersion !== undefined && !isCurrentOperation(operationVersion)) return;
      setMessage(error instanceof Error ? error.message : "加载历史记录失败");
    } finally {
      if (operationVersion === undefined || isCurrentOperation(operationVersion)) setHistoryLoading(false);
    }
  }

  function toggleHistory() {
    const nextOpen = !historyOpen;
    setHistoryOpen(nextOpen);
    if (nextOpen) {
      void loadHistory();
      window.setTimeout(() => document.querySelector<HTMLElement>(".history-panel")?.scrollIntoView({ behavior: "smooth" }), 0);
    }
  }

  async function openModelConfig() {
    setModelConfigOpen(true);
    setModelConfigLoading(true);
    setModelApiKey("");
    try {
      setModelConfig(await api.getAIModelConfig());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载模型配置失败");
    } finally {
      setModelConfigLoading(false);
    }
  }

  async function saveModelConfig() {
    if (!modelConfig || !modelConfig.base_url.trim() || !modelConfig.model.trim()) return;
    setModelConfigSaving(true);
    try {
      const updated = await api.updateAIModelConfig({
        enabled: modelConfig.enabled,
        base_url: modelConfig.base_url.trim(),
        model: modelConfig.model.trim(),
        ...(modelApiKey.trim() ? { api_key: modelApiKey.trim() } : {})
      });
      setModelConfig(updated);
      setModelApiKey("");
      setModelConfigOpen(false);
      setMessage("模型配置已保存，将用于新任务。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存模型配置失败");
    } finally {
      setModelConfigSaving(false);
    }
  }

  async function openHistoryJob(entry: JobHistoryItem) {
    const operationVersion = ++operationVersionRef.current;
    setMessage(null);
    try {
      const restoredJob = await api.getJob(entry.job_id);
      if (!isCurrentOperation(operationVersion)) return;
      setJob(restoredJob);
      if (isTerminalStatus(restoredJob.status)) {
        const restoredResults = await api.getResults(entry.job_id);
        if (!isCurrentOperation(operationVersion)) return;
        setResults(restoredResults);
        setSelectedImage(restoredResults.images.find((image) => image.decision === "selected") ?? restoredResults.images[0] ?? null);
      } else {
        setResults(null);
        setSelectedImage(null);
      }
      setHistoryOpen(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      if (!isCurrentOperation(operationVersion)) return;
      setMessage(error instanceof Error ? error.message : "打开历史任务失败");
    }
  }

  async function uploadOne(item: UploadItem, operationVersion: number) {
    try {
      updateItem(item.id, { status: "presigning", error: undefined }, operationVersion);
      const presigned = await api.presignUpload({
        filename: item.file.name,
        content_type: item.file.type || "application/octet-stream",
        file_size: item.file.size
      });
      if (!isCurrentOperation(operationVersion)) return undefined;
      updateItem(item.id, { objectKey: presigned.object_key, status: "uploading", progress: 1 }, operationVersion);
      await api.uploadToStorage(presigned.upload_url, item.file, (progress) => updateItem(item.id, { progress }, operationVersion));
      if (!isCurrentOperation(operationVersion)) return undefined;
      updateItem(item.id, { status: "uploaded", progress: 100, objectKey: presigned.object_key }, operationVersion);
      return presigned.object_key;
    } catch (error) {
      if (!isCurrentOperation(operationVersion)) return undefined;
      updateItem(item.id, {
        status: "failed",
        error: error instanceof Error ? error.message : "上传失败"
      }, operationVersion);
      throw error;
    }
  }

  async function startJob() {
    if (!canCreateJob) return;

    const operationVersion = ++operationVersionRef.current;
    setBusy(true);
    setMessage(null);
    setJob(null);
    setResults(null);
    setSelectedImage(null);

    try {
      const freshItems = items.filter((item) => item.status !== "uploaded");
      await runConcurrent(freshItems, UPLOAD_CONCURRENCY, (item) => uploadOne(item, operationVersion));
      if (!isCurrentOperation(operationVersion)) return;
      const latestItems = await waitForUploadedItems();
      if (!isCurrentOperation(operationVersion)) return;
      const objectKeys = latestItems.map((item) => item.objectKey).filter(Boolean) as string[];

      if (!objectKeys.length) {
        throw new Error("没有可创建任务的已上传图片");
      }

      const created = await api.createJob({
        filter_profile: filterProfile,
        beautify_profile: beautifyProfile,
        enhance_level: 1,
        max_selected: MAX_IMAGES,
        images: objectKeys.map((object_key) => ({ object_key }))
      });
      if (!isCurrentOperation(operationVersion)) return;

      const initialJob: JobProgress = {
        job_id: created.job_id,
        status: created.status,
        progress: 0,
        total: created.total,
        processed: 0,
        selected: 0,
        rejected: 0
      };
      setJob(initialJob);
    } catch (error) {
      if (!isCurrentOperation(operationVersion)) return;
      setMessage(error instanceof Error ? error.message : "创建任务失败");
    } finally {
      if (isCurrentOperation(operationVersion)) setBusy(false);
    }
  }

  function waitForUploadedItems(): Promise<UploadItem[]> {
    return new Promise((resolve) => {
      window.setTimeout(() => {
        setItems((current) => {
          resolve(current.filter((item) => item.status === "uploaded"));
          return current;
        });
      }, 0);
    });
  }

  function updateItem(itemId: string, patch: Partial<UploadItem>, operationVersion?: number) {
    if (operationVersion !== undefined && !isCurrentOperation(operationVersion)) return;
    setItems((current) => current.map((item) => (item.id === itemId ? { ...item, ...patch } : item)));
  }

  return (
    <main className="app-shell">
      <a className="skip-link" href="#mainWorkspace">
        跳到主内容
      </a>
      <header className="topbar">
        <div className="brand-lockup">
          <img className="brand-mark" src={brandLogo} alt="图片处理台" />
          <div>
            <h1>图片处理台</h1>
            <p className="eyebrow">IMAGE STUDIO</p>
          </div>
        </div>
        <nav className="top-nav app-flow" aria-label="图片处理流程">
          {["上传图片", "质量检测", "自动美化", "AI 标签", "结果"].map((label, index) => {
            const done = index < currentStep;
            const active = index === currentStep;
            return (
              <div key={label} className={`flow-step ${active ? "active" : ""} ${done ? "done" : ""}`} aria-current={active ? "step" : undefined}>
                <span>{done ? <Check size={13} aria-hidden="true" /> : index + 1}</span>
                <strong>{label}</strong>
                {index < 4 && <i aria-hidden="true" />}
              </div>
            );
          })}
        </nav>
        <div className="topbar-actions">
          <span className="mode-pill">{import.meta.env.VITE_USE_MOCK_API === "false" ? "真实处理" : "模拟演示"}</span>
          <button className="model-config-button" type="button" aria-label="配置 AI 模型" title="配置 AI 模型" onClick={() => void openModelConfig()}>
            <SlidersHorizontal size={17} aria-hidden="true" />
            <span>模型配置</span>
          </button>
          <button className="tool-button" type="button" aria-label="历史记录" title="历史记录" onClick={toggleHistory}>
            <History size={18} aria-hidden="true" />
          </button>
          <button className="tool-button" type="button" aria-label="重置工作区" title="重置工作区" onClick={resetWorkspace}>
            <RefreshCw size={18} aria-hidden="true" />
          </button>
        </div>
      </header>

      {message && (
        <section className="notice" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <span>{message}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setMessage(null)}>
            <X size={16} aria-hidden="true" />
          </button>
        </section>
      )}

      {modelConfigOpen && (
        <div className="model-config-backdrop" role="presentation" onMouseDown={() => !modelConfigSaving && setModelConfigOpen(false)}>
          <section className="model-config-dialog" role="dialog" aria-modal="true" aria-labelledby="modelConfigTitle" onMouseDown={(event) => event.stopPropagation()}>
            <div className="model-config-heading">
              <div>
                <span>AI MODEL</span>
                <h2 id="modelConfigTitle">模型配置</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭模型配置" onClick={() => setModelConfigOpen(false)} disabled={modelConfigSaving}><X size={17} aria-hidden="true" /></button>
            </div>
            {modelConfigLoading || !modelConfig ? <div className="model-config-loading"><Loader2 className="spin" size={20} aria-hidden="true" />正在读取配置</div> : <>
              <label className="config-toggle"><input type="checkbox" checked={modelConfig.enabled} onChange={(event) => setModelConfig({ ...modelConfig, enabled: event.target.checked })} /><span>启用 AI 标签</span></label>
              <label className="config-field">接口地址<input type="url" value={modelConfig.base_url} onChange={(event) => setModelConfig({ ...modelConfig, base_url: event.target.value })} placeholder="https://api.example.com/v1" /></label>
              <label className="config-field">模型名称<input value={modelConfig.model} onChange={(event) => setModelConfig({ ...modelConfig, model: event.target.value })} placeholder="请输入模型名称" /></label>
              <label className="config-field">API Key<input type="password" value={modelApiKey} onChange={(event) => setModelApiKey(event.target.value)} placeholder={modelConfig.api_key_configured ? "已配置，留空则保持不变" : "请输入 API Key"} autoComplete="new-password" /></label>
              <p className="config-note">使用 OpenAI 兼容的 Chat Completions 接口。API Key 不会在页面中显示。</p>
              <div className="model-config-actions"><button className="ghost-button" type="button" onClick={() => setModelConfigOpen(false)} disabled={modelConfigSaving}>取消</button><button className="primary-button" type="button" onClick={() => void saveModelConfig()} disabled={modelConfigSaving || !modelConfig.base_url.trim() || !modelConfig.model.trim()}>{modelConfigSaving && <Loader2 className="spin" size={16} aria-hidden="true" />}{modelConfigSaving ? "保存中" : "保存配置"}</button></div>
            </>}
          </section>
        </div>
      )}

      <div className="workspace-grid" id="mainWorkspace">
        <section className="control-panel reference-control">
          <div className="panel-intro">
            <span className="panel-kicker">BATCH ENHANCE</span>
            <div>
              <h2>图片处理</h2>
              <p>批量筛选装修照片，并自然优化可用图片。</p>
            </div>
          </div>

          <div className="step-section">
            <div className="step-heading"><span>1</span><div><h3>上传图片</h3><p>支持一次导入多张现场照片</p></div></div>
            <section
              className={`upload-zone ${dragActive ? "is-dragging" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragActive(false)}
              onDrop={onDrop}
            >
              <div className="drop-target">
                <UploadCloud size={26} aria-hidden="true" />
                <div><strong>上传装修照片</strong><span>点击选择或拖入图片</span></div>
                <input ref={fileInputRef} id="filePicker" type="file" accept="image/*" multiple onChange={onFileChange} />
                <button className="secondary-button" type="button" onClick={() => fileInputRef.current?.click()}><FileImage size={16} aria-hidden="true" />选择图片</button>
              </div>
              {items.length > 0 && <div className="upload-grid" aria-live="polite">{items.map((item) => (
                <article className="upload-card" key={item.id}>
                  <img src={item.previewUrl} alt={item.file.name} />
                  <div className="upload-card-body"><div><strong title={item.file.name}>{item.file.name}</strong><span>{formatBytes(item.file.size)}</span></div><UploadStatus item={item} /></div>
                  <button className="icon-button" type="button" aria-label={`移除 ${item.file.name}`} onClick={() => removeItem(item.id)}><Trash2 size={16} aria-hidden="true" /></button>
                </article>
              ))}</div>}
            </section>
          </div>

          <div className="step-section">
            <div className="step-heading"><span>2</span><div><h3>处理标准</h3><p>为本次任务选择质量筛选和美化方案</p></div></div>
            <div className="field-stack">
              <label htmlFor="filterProfile">筛选标准</label>
            <select id="filterProfile" value={filterProfile} onChange={(event) => setFilterProfile(event.target.value)}>
              {filterProfiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </select>
            <p>{filterProfiles.find((profile) => profile.id === filterProfile)?.description}</p>
            </div>
            <div className="field-stack">
              <label htmlFor="beautifyProfile">美化标准</label>
            <select id="beautifyProfile" value={beautifyProfile} onChange={(event) => setBeautifyProfile(event.target.value)}>
              {beautifyProfiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </select>
            <p>{beautifyProfiles.find((profile) => profile.id === beautifyProfile)?.description}</p>
            </div>
          </div>

          <button className="primary-button" type="button" onClick={startJob} disabled={!canCreateJob}>
            {busy ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <Sparkles size={18} aria-hidden="true" />}
            {busy ? "正在创建任务" : "上传并开始处理"}
          </button>
        </section>

        <section className="result-stage" aria-label="生成结果">
          <div className="result-stage-heading"><div><h2>生成结果</h2><p>{results ? "结果会按处理顺序排列" : "完成上传并处理后，结果将在这里出现"}</p></div></div>
          {!results && <div className="result-empty"><span><Sparkles size={25} aria-hidden="true" /></span><strong>从一组现场照片，整理出可用成果</strong><p>在左侧上传照片并选择处理标准。每张图片都会独立筛选、美化和打标签。</p></div>}
          {results && <ResultsPanel results={results} averageScore={averageScore} resultFilter={resultFilter} visibleResults={visibleResults} selectedImage={selectedImage} onFilterChange={setResultFilter} onSelectImage={setSelectedImage} />}
        </section>
      </div>

      {job && (
        <section className="progress-band">
          <div className="progress-copy">
            <Clock3 size={20} aria-hidden="true" />
            <div>
              <h2>{statusLabel(job.status)}</h2>
              <p>
                {job.processed}/{job.total} 已处理，{job.selected} 已选中，{job.rejected} 已淘汰
              </p>
            </div>
          </div>
          <div className="progress-meter" aria-label={`任务进度 ${job.progress}%`}>
            <span style={{ width: `${job.progress}%` }} />
          </div>
          <strong>{job.progress}%</strong>
        </section>
      )}

      {historyOpen && (
        <HistoryPanel
          history={history}
          loading={historyLoading}
          onRefresh={() => void loadHistory()}
          onOpen={(entry) => void openHistoryJob(entry)}
        />
      )}
    </main>
  );
}

function ResultsPanel({
  results,
  averageScore,
  resultFilter,
  visibleResults,
  selectedImage,
  onFilterChange,
  onSelectImage
}: {
  results: JobResults;
  averageScore: number;
  resultFilter: "all" | Decision;
  visibleResults: ResultImage[];
  selectedImage: ResultImage | null;
  onFilterChange: (value: "all" | Decision) => void;
  onSelectImage: (image: ResultImage) => void;
}) {
  return (
    <section className="results-panel">
      <div className="results-summary">
        <Metric label="总图片" value={results.summary.total} />
        <Metric label="保留并美化" value={results.summary.selected} tone="selected" />
        <Metric label="未通过标准" value={results.summary.rejected} tone="rejected" />
        <Metric label="保留图平均分" value={averageScore} />
      </div>
      <div className="result-toolbar">
        <div className="tabs" role="tablist" aria-label="结果筛选">
          {[["all", "全部"], ["selected", "保留并美化"], ["tagging", "标签生成中"], ["rejected", "未通过标准"], ["failed", "处理失败"]].map(([value, label]) => (
            <button key={value} className={resultFilter === value ? "active" : ""} type="button" role="tab" aria-selected={resultFilter === value} onClick={() => onFilterChange(value as "all" | Decision)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="result-layout">
        <div className="result-grid">
          {visibleResults.map((image) => <ResultCard key={image.image_id} image={image} active={selectedImage?.image_id === image.image_id} onOpen={() => onSelectImage(image)} />)}
        </div>
        <aside className="detail-panel" aria-label="图片详情">{selectedImage ? <ImageDetail image={selectedImage} /> : <EmptyDetail />}</aside>
      </div>
    </section>
  );
}

function HistoryPanel({
  history,
  loading,
  onRefresh,
  onOpen
}: {
  history: JobHistoryResponse | null;
  loading: boolean;
  onRefresh: () => void;
  onOpen: (entry: JobHistoryItem) => void;
}) {
  return (
    <section className="history-panel" aria-label="任务历史记录">
      <div className="history-heading">
        <div>
          <h2>历史记录</h2>
          <p>最近 {history?.total ?? 0} 个图片处理任务</p>
        </div>
        <button className="icon-button" type="button" aria-label="刷新历史记录" onClick={onRefresh} disabled={loading}>
          <RefreshCw className={loading ? "spin" : ""} size={17} aria-hidden="true" />
        </button>
      </div>
      {!history?.items.length && !loading ? (
        <p className="history-empty">还没有历史任务。</p>
      ) : (
        <div className="history-list">
          {history?.items.map((entry) => (
            <button className="history-row" type="button" key={entry.job_id} onClick={() => onOpen(entry)}>
              <span className={`decision-badge ${entry.status === "completed" ? "selected" : entry.status === "partial_failed" || entry.status === "failed" ? "rejected" : ""}`}>
                {statusLabel(entry.status)}
              </span>
              <strong>{entry.job_id}</strong>
              <span>{formatHistoryDate(entry.created_at)}</span>
              <span>{formatProcessingDuration(entry.created_at, entry.completed_at)}</span>
              <span>{entry.processed}/{entry.total} 已处理</span>
              <span>{entry.selected} 保留，{entry.rejected} 淘汰</span>
              <span className="history-model">AI · {entry.ai_tagging_model ?? "未启用"}</span>
              <ChevronRight size={18} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function Stage({ active, done, label }: { active: boolean; done: boolean; label: string }) {
  return (
    <div className={`stage ${active ? "active" : ""} ${done ? "done" : ""}`}>
      <span>{done ? <Check size={16} aria-hidden="true" /> : <CircleDot size={15} aria-hidden="true" />}</span>
      <strong>{label}</strong>
      <ChevronRight size={16} aria-hidden="true" />
    </div>
  );
}

function UploadStatus({ item }: { item: UploadItem }) {
  const text = {
    ready: "待上传",
    presigning: "获取地址",
    uploading: `上传 ${item.progress}%`,
    uploaded: "已上传",
    failed: item.error ?? "失败"
  }[item.status];

  return (
    <div className={`upload-status ${item.status}`}>
      <span>{text}</span>
      <div className="mini-meter">
        <i style={{ width: `${item.progress}%` }} />
      </div>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: "selected" | "rejected" }) {
  return (
    <div className={`metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ResultCard({ image, active, onOpen }: { image: ResultImage; active: boolean; onOpen: () => void }) {
  const previewUrl = image.enhanced_url ?? image.original_url;

  return (
    <article className={`result-card ${active ? "active" : ""}`}>
      <button type="button" onClick={onOpen} aria-label={`查看 ${image.image_id} 详情`}>
        {previewUrl ? (
          <img src={previewUrl} alt={`${image.image_id} 预览`} loading="lazy" />
        ) : (
          <span>预览暂不可用</span>
        )}
        <span className={`decision-badge ${image.decision}`}>{decisionLabel(image.decision)}</span>
      </button>
      <div className="result-meta">
        <div>
          <strong>{image.image_id}</strong>
          <span>{image.decision === "selected" ? "美化图已生成" : "原图保留供查看"}</span>
        </div>
        <b>{image.score}</b>
      </div>
      <p>{image.reject_codes?.[0] ? rejectCodeLabel(image.reject_codes[0]) : image.reasons[0] ?? "基础质量达标"}</p>
      {image.ai_tags?.tags.length ? (
        <div className="tag-row" aria-label="AI 自动标签">
          {image.ai_tags.tags.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      ) : null}
    </article>
  );
}

function ImageDetail({ image }: { image: ResultImage }) {
  const [showEnhanced, setShowEnhanced] = useState(true);
  const [compare, setCompare] = useState(50);
  const imageUrl = showEnhanced && image.enhanced_url ? image.enhanced_url : image.original_url;
  const openUrl = image.enhanced_url ?? image.original_url;

  return (
    <>
      <div className="detail-preview">
        {image.original_url && image.enhanced_url && showEnhanced ? (
          <div className="compare-viewer">
            <img src={image.original_url} alt={`${image.image_id} 原图`} />
            <img
              className="compare-enhanced"
              src={image.enhanced_url}
              alt={`${image.image_id} 美化图`}
              style={{ clipPath: `inset(0 ${100 - compare}% 0 0)` }}
            />
            <span className="compare-line" style={{ left: `${compare}%` }} />
            <input
              aria-label="调整原图和美化图对比位置"
              type="range"
              min={0}
              max={100}
              value={compare}
              onChange={(event) => setCompare(Number(event.target.value))}
            />
          </div>
        ) : imageUrl ? (
          <img src={imageUrl} alt={`${image.image_id} 大图预览`} />
        ) : (
          <p>该图片的预览地址暂不可用。</p>
        )}
        <div className="preview-actions">
          <button type="button" className={!showEnhanced ? "active" : ""} onClick={() => setShowEnhanced(false)}>
            <Eye size={16} aria-hidden="true" />
            原图
          </button>
          <button type="button" className={showEnhanced ? "active" : ""} onClick={() => setShowEnhanced(true)} disabled={!image.enhanced_url}>
            <Sparkles size={16} aria-hidden="true" />
            美化图
          </button>
          {openUrl ? (
            <a href={openUrl} target="_blank" rel="noreferrer">
              <ArrowDownToLine size={16} aria-hidden="true" />
              打开
            </a>
          ) : null}
        </div>
      </div>

      <div className="detail-header">
        <div>
          <span className={`decision-badge ${image.decision}`}>{decisionLabel(image.decision)}</span>
          <h2>{image.image_id}</h2>
        </div>
        <strong>{image.score}</strong>
      </div>

      <div className="metrics-compare">
        <MetricList title="美化前" metrics={image.metrics} />
        <MetricList title="美化后" metrics={image.enhanced_metrics} />
      </div>

      <div className="explain-grid">
        <Explanation title="处理说明" items={image.reasons} empty="暂无处理说明" />
        <Explanation
          title="淘汰原因"
          items={(image.reject_codes ?? []).map(rejectCodeLabel)}
          empty="无淘汰原因"
        />
      </div>
      <AITags tags={image.ai_tags} />
    </>
  );
}

function AITags({ tags }: { tags?: ResultImage["ai_tags"] }) {
  if (!tags) return null;
  const confidence = tags.confidence == null ? null : `${Math.round(tags.confidence * 100)}%`;
  return (
    <section className="ai-tags-panel">
      <div className="ai-tags-heading">
        <h3>AI 内容标签</h3>
        <span className={`tagging-status ${tags.status}`}>
          {tags.status === "completed" ? "已生成" : tags.status === "failed" ? "暂不可用" : "生成中"}
        </span>
      </div>
      {tags.summary ? <p>{tags.summary}</p> : null}
      {confidence ? <small>标签可信度 {confidence}</small> : null}
      {tags.tags.length ? <div className="tag-row">{tags.tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
      {tags.candidate_tags.length ? <p className="tag-note">候选：{tags.candidate_tags.join("、")}</p> : null}
      {tags.risks.length ? <p className="tag-risk">提示：{tags.risks.join("、")}</p> : null}
      {tags.error_message ? <p className="tag-error">{tags.error_message}</p> : null}
    </section>
  );
}

function MetricList({ title, metrics }: { title: string; metrics?: ImageMetrics }) {
  return (
    <section className="metrics-panel">
      <h3>{title}</h3>
      {metrics && Object.keys(metrics).length ? (
        <div className="metrics-list">
          {Object.entries(metrics).map(([key, value]) => (
            <div className="metric-row" key={key}>
              <span>{metricLabel(key)}</span>
              <div>
                <i style={{ width: `${value}%` }} />
              </div>
              <b>{value}</b>
            </div>
          ))}
        </div>
      ) : (
        <p className="metrics-empty">尚未生成美化后评分</p>
      )}
    </section>
  );
}

function Explanation({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <section className="explanation">
      <h3>{title}</h3>
      {items.length ? (
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p>{empty}</p>
      )}
    </section>
  );
}

function EmptyDetail() {
  return (
    <div className="empty-detail">
      <FileImage size={36} aria-hidden="true" />
      <strong>选择一张图片查看详情</strong>
    </div>
  );
}

function metricLabel(key: string): string {
  const labels: Record<string, string> = {
    sharpness: "清晰度",
    exposure: "曝光",
    contrast: "对比度",
    noise: "噪点控制",
    composition: "构图",
    face_quality: "人脸质量",
    subject_quality: "主体质量",
    uniqueness: "唯一性"
  };
  return labels[key] ?? key;
}

async function runConcurrent<T>(items: T[], concurrency: number, worker: (item: T) => Promise<unknown>) {
  const queue = [...items];
  const runners = Array.from({ length: Math.min(concurrency, queue.length) }, async () => {
    while (queue.length) {
      const item = queue.shift();
      if (item) await worker(item);
    }
  });
  await Promise.all(runners);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatHistoryDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(new Date(value));
}

function formatProcessingDuration(createdAt: string, completedAt?: string | null): string {
  if (!completedAt) return "处理中";

  const durationSeconds = Math.max(0, Math.round((new Date(completedAt).getTime() - new Date(createdAt).getTime()) / 1000));
  if (!Number.isFinite(durationSeconds)) return "耗时未知";
  if (durationSeconds < 60) return `耗时 ${durationSeconds} 秒`;

  const minutes = Math.floor(durationSeconds / 60);
  const seconds = durationSeconds % 60;
  return seconds ? `耗时 ${minutes} 分 ${seconds} 秒` : `耗时 ${minutes} 分`;
}

export default App;
