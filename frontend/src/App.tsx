import {
  AlertCircle,
  ArrowDownToLine,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  Clock3,
  Database,
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
import { ChangeEvent, DragEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./services/api";
import { LibraryWorkspace } from "./LibraryWorkspace";
import { ProfileWorkspace } from "./ProfileWorkspace";
import brandLogo from "./assets/image-processing-logo.svg";
import type {
  AIModelConfig,
  Decision,
  ImageMetrics,
  JobHistoryItem,
  JobHistoryResponse,
  JobProgress,
  JobResults,
  ProfileOption,
  ResultImage,
  ResultFilter,
  SimilarityCandidate,
  SimilarityTaggingResult,
  UploadItem
} from "./types";
import { decisionLabel, isTerminalStatus, rejectCodeLabel, statusLabel } from "./utils/decision";
import { createClientId } from "./utils/id";

const MAX_IMAGES = 500;
const MAX_IMAGE_SIZE_MB = 25;
const UPLOAD_CONCURRENCY = 6;
const PAGE_SIZE = 50;

type WorkflowStep = 1 | 2 | 3 | 4 | 5 | 6;

const WORKFLOW_STEPS: Array<{ id: WorkflowStep; title: string; description: string }> = [
  { id: 1, title: "创建任务", description: "导入与校验" },
  { id: 2, title: "处理方案", description: "选择执行标准" },
  { id: 3, title: "执行检查", description: "确认任务配置" },
  { id: 4, title: "自动处理", description: "查看实时进度" },
  { id: 5, title: "人工复核", description: "校验与修正" },
  { id: 6, title: "交付归档", description: "下载与留档" }
];

function App() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const itemsRef = useRef<UploadItem[]>([]);
  const operationVersionRef = useRef(0);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [processingStandards, setProcessingStandards] = useState<ProfileOption[]>([]);
  const [beautifyProfiles, setBeautifyProfiles] = useState<ProfileOption[]>([]);
  const [selectedStandardIds, setSelectedStandardIds] = useState<string[]>([]);
  const [beautifyProfile, setBeautifyProfile] = useState("");
  const filterEnabled = true;
  const beautifyEnabled = true;
  const similarityEnabled = true;
  const [job, setJob] = useState<JobProgress | null>(null);
  const [results, setResults] = useState<JobResults | null>(null);
  const [selectedImage, setSelectedImage] = useState<ResultImage | null>(null);
  const [resultFilter, setResultFilter] = useState<ResultFilter>("all");
  const [resultPage, setResultPage] = useState(0);
  const [uploadPage, setUploadPage] = useState(0);
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
  const [activeWorkspace, setActiveWorkspace] = useState<"processing" | "library" | "profiles">("processing");
  const [workflowStep, setWorkflowStep] = useState<WorkflowStep>(1);

  const uploadedCount = items.filter((item) => item.status === "uploaded").length;
  const failedCount = items.filter((item) => item.status === "failed").length;
  const canCreateJob = items.some((item) => item.file)
    && (!filterEnabled || selectedStandardIds.length === 2)
    && (!beautifyEnabled || Boolean(beautifyProfile))
    && !busy;
  const selectedStandards = processingStandards.filter((standard) => selectedStandardIds.includes(standard.id));
  const selectedBeautifyProfile = beautifyProfiles.find((profile) => profile.id === beautifyProfile);
  const estimatedMinutes = Math.max(1, Math.ceil(Math.max(items.length, 1) / 25));
  const averageScore = useMemo(() => {
    const selected = results?.images.filter((image) => image.decision === "selected") ?? [];
    if (!selected.length) return 0;
    return Math.round(selected.reduce((sum, image) => sum + image.score, 0) / selected.length);
  }, [results]);

  const visibleResults = results?.images ?? [];
  const pagedUploadItems = useMemo(
    () => items.slice(uploadPage * PAGE_SIZE, (uploadPage + 1) * PAGE_SIZE),
    [items, uploadPage]
  );

  function applyReviewResult(imageId: string, taggingResult: SimilarityTaggingResult) {
    const updateImage = (image: ResultImage): ResultImage => {
      if (image.image_id !== imageId) return image;
      const matched = taggingResult.decision === "matched";
      return {
        ...image,
        tagging_result: taggingResult,
        ai_tags: image.ai_tags ? {
          ...image.ai_tags,
          tags: matched ? taggingResult.tags : [],
          categories: matched ? { 素材库标签: taggingResult.tags } : {},
          candidate_tags: []
        } : image.ai_tags
      };
    };
    setResults((current) => current ? { ...current, images: current.images.map(updateImage) } : current);
    setSelectedImage((current) => current ? updateImage(current) : current);
    setMessage(taggingResult.decision === "matched" ? "人工复核已确认标签" : "人工复核已标记为未匹配");
  }

  async function reloadProcessingProfiles() {
    const [standards, beautify] = await Promise.all([
      api.getProcessingStandards(),
      api.getBeautifyProfiles()
    ]);
    setProcessingStandards(standards);
    setBeautifyProfiles(beautify);
    setSelectedStandardIds((current) => {
      const retained = current.filter((id) => standards.some((item) => item.id === id));
      return retained.length === 2 ? retained : standards.slice(0, 2).map((item) => item.id);
    });
    setBeautifyProfile((current) => (
      beautify.some((item) => item.id === current) ? current : (beautify[0]?.id ?? "")
    ));
  }

  function toggleProcessingStandard(standardId: string) {
    setSelectedStandardIds((current) => current.includes(standardId)
      ? current.filter((id) => id !== standardId)
      : current.length < 2 ? [...current, standardId] : current);
  }

  useEffect(() => {
    const reportStartupError = (error: unknown) => {
      setMessage(error instanceof Error ? error.message : "正式后端初始化失败");
    };
    void reloadProcessingProfiles().catch(reportStartupError);
  }, []);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    const missingPreviews = items.filter(
      (item) => item.status === "uploaded" && !item.previewUrl && item.objectKey
    );
    if (!missingPreviews.length) return;

    let active = true;
    void Promise.all(missingPreviews.map(async (item) => ({
      id: item.id,
      previewUrl: await api.getObjectPreviewUrl(item.objectKey!)
    }))).then((resolved) => {
      if (!active) return;
      const previewUrls = new Map(
        resolved
          .filter((entry): entry is { id: string; previewUrl: string } => Boolean(entry.previewUrl))
          .map((entry) => [entry.id, entry.previewUrl])
      );
      if (!previewUrls.size) return;
      setItems((current) => current.map((item) => (
        item.previewUrl || !previewUrls.has(item.id)
          ? item
          : { ...item, previewUrl: previewUrls.get(item.id) }
      )));
    });

    return () => { active = false; };
  }, [items]);

  useEffect(() => {
    return () => {
      itemsRef.current.forEach((item) => item.previewUrl && URL.revokeObjectURL(item.previewUrl));
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
        if (nextJob.progress > 0 || isTerminalStatus(nextJob.status)) {
          await loadResultPage(nextJob.job_id, operationVersion, resultPage, resultFilter);
        }
        if (isTerminalStatus(nextJob.status)) {
          void loadHistory(operationVersion);
        }
      } catch (error) {
        if (!isCurrentOperation(operationVersion)) return;
        setMessage(error instanceof Error ? error.message : "查询任务进度失败");
      }
    }, delay);

    return () => window.clearTimeout(timer);
  }, [job, resultFilter, resultPage]);

  useEffect(() => {
    if (!job) return;
    if (!isTerminalStatus(job.status)) {
      setWorkflowStep(4);
      return;
    }
    if (results) {
      setWorkflowStep((current) => current < 5 ? 5 : current);
    }
  }, [job, results]);

  async function loadResultPage(
    jobId: string,
    operationVersion: number,
    page: number,
    filter: ResultFilter
  ) {
    const nextResults = await api.getResults(
      jobId,
      PAGE_SIZE,
      page * PAGE_SIZE,
      filter === "all" ? undefined : filter
    );
    if (!isCurrentOperation(operationVersion)) return;
    setResults(nextResults);
    setJob((current) => current?.job_id === jobId ? {
      ...current,
      selected: nextResults.summary.selected,
      rejected: nextResults.summary.rejected,
      not_selected: nextResults.summary.not_selected
    } : current);
    setHistory((current) => current ? {
      ...current,
      items: current.items.map((entry) => entry.job_id === jobId ? {
        ...entry,
        selected: nextResults.summary.selected,
        rejected: nextResults.summary.rejected,
        not_selected: nextResults.summary.not_selected
      } : entry)
    } : current);
    setSelectedImage((current) =>
      nextResults.images.find((image) => image.image_id === current?.image_id)
      ?? nextResults.images.find((image) => image.decision === "selected")
      ?? nextResults.images[0]
      ?? null
    );
  }

  async function changeResultPage(page: number, filter = resultFilter) {
    if (!job) return;
    const operationVersion = operationVersionRef.current;
    setResultPage(page);
    setResultFilter(filter);
    try {
      await loadResultPage(job.job_id, operationVersion, page, filter);
    } catch (error) {
      if (isCurrentOperation(operationVersion)) {
        setMessage(error instanceof Error ? error.message : "加载结果失败");
      }
    }
  }

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
        id: createClientId(),
        file,
        filename: file.name,
        fileSize: file.size,
        contentType: file.type || "application/octet-stream",
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
      if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
      return current.filter((entry) => entry.id !== itemId);
    });
  }

  function resetWorkspace() {
    operationVersionRef.current += 1;
    itemsRef.current.forEach((item) => item.previewUrl && URL.revokeObjectURL(item.previewUrl));
    itemsRef.current = [];
    setItems([]);
    setJob(null);
    setResults(null);
    setSelectedImage(null);
    setResultFilter("all");
    setResultPage(0);
    setUploadPage(0);
    setBusy(false);
    setMessage(null);
    setWorkflowStep(1);
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
      setMessage(error instanceof Error ? error.message : "加载 AI 配置失败");
    } finally {
      setModelConfigLoading(false);
    }
  }

  async function saveModelConfig() {
    if (!modelConfig) return;
    setModelConfigSaving(true);
    try {
      const updated = await api.updateAIModelConfig({
        enabled: modelConfig.enabled,
        model: modelConfig.model.trim(),
        ...(modelApiKey.trim() ? { api_key: modelApiKey.trim() } : {})
      });
      setModelConfig(updated);
      setModelApiKey("");
      setModelConfigOpen(false);
      setMessage("AI 配置已保存，将用于新任务。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存 AI 配置失败");
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
      setResultPage(0);
      setResultFilter("all");
      if (isTerminalStatus(restoredJob.status)) {
        const restoredResults = await api.getResults(entry.job_id, PAGE_SIZE, 0);
        if (!isCurrentOperation(operationVersion)) return;
        setResults(restoredResults);
        setSelectedImage(restoredResults.images.find((image) => image.decision === "selected") ?? restoredResults.images[0] ?? null);
        setWorkflowStep(5);
      } else {
        setResults(null);
        setSelectedImage(null);
        setWorkflowStep(4);
      }
      setHistoryOpen(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      if (!isCurrentOperation(operationVersion)) return;
      setMessage(error instanceof Error ? error.message : "打开历史任务失败");
    }
  }

  async function uploadOne(
    item: UploadItem,
    registered: { id: string; object_key: string; upload_url: string },
    operationVersion: number
  ) {
    try {
      if (!item.file) throw new Error("本地图片已释放，请重新选择");
      if (!isCurrentOperation(operationVersion)) return undefined;
      updateItem(item.id, { objectKey: registered.object_key, status: "uploading", progress: 1 }, operationVersion);
      await api.uploadToStorage(registered.upload_url, item.file, (progress) => updateItem(item.id, { progress }, operationVersion));
      if (!isCurrentOperation(operationVersion)) return undefined;
      updateItem(item.id, { status: "uploaded", progress: 100, objectKey: registered.object_key }, operationVersion);
      return registered.id;
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
    setWorkflowStep(4);

    try {
      const uploadable = items.filter((item) => item.file);
      uploadable.forEach((item) => updateItem(item.id, { status: "presigning", error: undefined }, operationVersion));
      const batch = await api.createUploadBatch({
        processing_standards: selectedStandardIds,
        beautify_profile: beautifyEnabled ? beautifyProfile : undefined,
        filter_enabled: filterEnabled,
        beautify_enabled: beautifyEnabled,
        similarity_enabled: similarityEnabled,
        unmatched_standard_policy: "reject",
        enhance_level: 1,
        max_selected: uploadable.length,
        files: uploadable.map((item) => ({
          filename: item.filename,
          content_type: item.contentType,
          file_size: item.fileSize
        }))
      });
      const successfulIds: string[] = [];
      const registrations = uploadable.map((item, index) => ({ item, registered: batch.items[index] }));
      await runConcurrent(registrations, UPLOAD_CONCURRENCY, async ({ item, registered }) => {
        if (!registered) return;
        const uploadedId = await uploadOne(item, registered, operationVersion);
        if (uploadedId) successfulIds.push(uploadedId);
      });
      if (!isCurrentOperation(operationVersion)) return;
      if (!successfulIds.length) throw new Error("没有成功上传的图片，无法创建任务");
      const created = await api.completeUploadBatch(batch.batch_id, successfulIds);
      if (!isCurrentOperation(operationVersion)) return;

      setItems((current) => current.map((item) => {
        if (item.status !== "uploaded") return item;
        return { ...item, file: undefined };
      }));

      const initialJob: JobProgress = {
        job_id: created.job_id,
        status: created.status,
        progress: 0,
        total: created.total,
        processed: 0,
        selected: 0,
        rejected: 0,
        not_selected: 0,
        tagging: 0,
        stage_counts: { waiting: created.total }
      };
      setJob(initialJob);
    } catch (error) {
      if (!isCurrentOperation(operationVersion)) return;
      setMessage(error instanceof Error ? error.message : "创建任务失败");
      setWorkflowStep(3);
    } finally {
      if (isCurrentOperation(operationVersion)) setBusy(false);
    }
  }

  function updateItem(itemId: string, patch: Partial<UploadItem>, operationVersion?: number) {
    if (operationVersion !== undefined && !isCurrentOperation(operationVersion)) return;
    setItems((current) => current.map((item) => (item.id === itemId ? { ...item, ...patch } : item)));
  }

  async function cancelCurrentJob() {
    if (!job || isTerminalStatus(job.status)) return;
    try {
      const cancelled = await api.cancelJob(job.job_id);
      setJob(cancelled);
      setMessage("任务已取消，尚未开始的图片不会继续处理。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "取消任务失败");
    }
  }

  async function retryImage(imageId: string) {
    if (!job) return;
    try {
      const nextJob = await api.retryImage(job.job_id, imageId);
      setJob(nextJob);
      setMessage("失败图片已重新进入处理队列。");
      await loadResultPage(job.job_id, operationVersionRef.current, resultPage, resultFilter);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "重试图片失败");
    }
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
        <nav className="workspace-switch" aria-label="工作区">
          <button className={activeWorkspace === "processing" ? "active" : ""} type="button" aria-current={activeWorkspace === "processing" ? "page" : undefined} onClick={() => setActiveWorkspace("processing")}><Sparkles size={16} aria-hidden="true" />图片处理</button>
          <button className={activeWorkspace === "library" ? "active" : ""} type="button" aria-current={activeWorkspace === "library" ? "page" : undefined} onClick={() => setActiveWorkspace("library")}><Database size={16} aria-hidden="true" />素材库</button>
          <button className={activeWorkspace === "profiles" ? "active" : ""} type="button" aria-current={activeWorkspace === "profiles" ? "page" : undefined} onClick={() => setActiveWorkspace("profiles")}><SlidersHorizontal size={16} aria-hidden="true" />标准管理</button>
        </nav>
        <div className="topbar-actions">
          <span className="mode-pill">正式模式</span>
          <button className="model-config-button" type="button" aria-label="配置 AI" title="配置 AI" onClick={() => void openModelConfig()}>
            <SlidersHorizontal size={17} aria-hidden="true" />
            <span>AI 配置</span>
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
                <span>AI SETTINGS</span>
                <h2 id="modelConfigTitle">AI 配置</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭 AI 配置" onClick={() => setModelConfigOpen(false)} disabled={modelConfigSaving}><X size={17} aria-hidden="true" /></button>
            </div>
            {modelConfigLoading || !modelConfig ? <div className="model-config-loading"><Loader2 className="spin" size={20} aria-hidden="true" />正在读取配置</div> : <>
              <label className="config-toggle"><input type="checkbox" checked={modelConfig.enabled} onChange={(event) => setModelConfig({ ...modelConfig, enabled: event.target.checked })} /><span>启用原图 AI 识别、过滤与美化规划</span></label>
              <label className="config-field">模型<input value={modelConfig.model} maxLength={120} onChange={(event) => setModelConfig({ ...modelConfig, model: event.target.value })} /></label>
              <label className="config-field">接口地址<input value={modelConfig.base_url} readOnly /></label>
              <label className="config-field">API Key<input type="password" value={modelApiKey} onChange={(event) => setModelApiKey(event.target.value)} placeholder={modelConfig.api_key_configured ? "已配置，留空则保持不变" : "请输入 API Key"} autoComplete="new-password" /></label>
              <a className="config-key-link" href="https://router.keenlight.ai/home" target="_blank" rel="noreferrer">获取</a>
              <p className="config-note">API Key 仅保存在服务端且不会在页面回显；模型配置会冻结到新任务记录中。</p>
              <div className="model-config-actions"><button className="ghost-button" type="button" onClick={() => setModelConfigOpen(false)} disabled={modelConfigSaving}>取消</button><button className="primary-button" type="button" onClick={() => void saveModelConfig()} disabled={modelConfigSaving || !modelConfig.model.trim()}>{modelConfigSaving && <Loader2 className="spin" size={16} aria-hidden="true" />}{modelConfigSaving ? "保存中" : "保存配置"}</button></div>
            </>}
          </section>
        </div>
      )}

      {activeWorkspace === "processing" ? <>
        <div className={`sop-workspace ${workflowStep >= 4 ? "is-wide" : ""}`} id="mainWorkspace">
          <aside className="sop-rail" aria-label="图片处理 SOP">
            <div className="rail-heading">
              <div><span>SOP WORKFLOW</span><b>6 步</b></div>
              <strong>标准处理流程</strong>
              <small>从图片导入到结果交付，全程清晰可追踪</small>
            </div>
            <div className="rail-progress" aria-label={`当前第 ${workflowStep} 步，共 6 步`}>
              <div><span>当前进度</span><strong>{workflowStep} / 6</strong></div>
              <i><b style={{ width: `${(workflowStep / WORKFLOW_STEPS.length) * 100}%` }} /></i>
            </div>
            <ol>
              {WORKFLOW_STEPS.map((step) => {
                const done = step.id < workflowStep;
                const active = step.id === workflowStep;
                const navigable = active
                  || (!job && step.id < workflowStep && step.id <= 3)
                  || (Boolean(results) && step.id >= 5);
                return <li key={step.id} className={`${active ? "active" : ""} ${done ? "done" : ""}`}>
                  <button type="button" disabled={!navigable} aria-current={active ? "step" : undefined} onClick={() => setWorkflowStep(step.id)}>
                    <span>{done ? <Check size={15} aria-hidden="true" /> : step.id}</span>
                    <div><strong>{step.title}</strong><small>{step.description}</small></div>
                  </button>
                </li>;
              })}
            </ol>
            <div className="rail-help"><CircleDot size={17} aria-hidden="true" /><span><strong>流程说明</strong>每个页面只完成一个主要任务</span></div>
          </aside>

          <section className="sop-stage" aria-live="polite">
            {workflowStep === 1 && <>
              <header className="sop-stage-heading">
                <span>步骤 1 / 6</span>
                <h2>创建任务与导入图片</h2>
                <p>导入需要批量处理的图片，系统会自动校验格式与大小。</p>
              </header>
              <section
                className={`sop-upload-zone ${dragActive ? "is-dragging" : ""}`}
                onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={() => setDragActive(false)}
                onDrop={onDrop}
              >
                <UploadCloud size={34} aria-hidden="true" />
                <strong>拖拽图片到此处，或点击选择图片</strong>
                <p>支持 JPG、JPEG、PNG、WebP，单张不超过 {MAX_IMAGE_SIZE_MB}MB，最多 {MAX_IMAGES} 张</p>
                <input ref={fileInputRef} id="filePicker" type="file" accept="image/*" multiple onChange={onFileChange} />
                <button className="secondary-button" type="button" onClick={() => fileInputRef.current?.click()}><FileImage size={16} aria-hidden="true" />选择图片</button>
              </section>
              <div className="upload-queue-heading"><strong>已导入 {items.length} 张</strong>{items.length > 0 && <button type="button" onClick={resetWorkspace}>清空列表</button>}</div>
              {items.length ? <>
                <div className="upload-table" aria-live="polite">
                  <div className="upload-table-head"><span>文件信息</span><span>大小</span><span>状态</span><span>操作</span></div>
                  {pagedUploadItems.map((item) => <article className="upload-row" key={item.id}>
                    <div className="upload-file">{item.previewUrl ? <img src={item.previewUrl} alt="" /> : <span><FileImage size={20} aria-hidden="true" /></span>}<strong title={item.filename}>{item.filename}</strong></div>
                    <span>{formatBytes(item.fileSize)}</span>
                    <UploadStatus item={item} />
                    <button className="icon-button" type="button" aria-label={`移除 ${item.filename}`} onClick={() => removeItem(item.id)}><Trash2 size={16} aria-hidden="true" /></button>
                  </article>)}
                </div>
                <Pagination page={uploadPage} total={items.length} pageSize={PAGE_SIZE} onChange={setUploadPage} label="上传图片" />
              </> : <div className="queue-empty">尚未导入图片。选择图片后，格式和大小校验结果会显示在这里。</div>}
              <div className="sop-tip"><AlertCircle size={17} aria-hidden="true" />建议使用清晰、光线充足的原图，以获得稳定的识别与美化效果。</div>
            </>}

            {workflowStep === 2 && <>
              <header className="sop-stage-heading">
                <button className="back-link" type="button" onClick={() => setWorkflowStep(1)}><ChevronLeft size={16} />返回导入</button>
                <span>步骤 2 / 6</span>
                <h2>选择处理方案</h2>
                <p>为本次任务选择两套互斥审核标准和一套美化方案。</p>
              </header>
              <section className="plan-section">
                <div className="section-title"><div><span>01</span><div><h3>条件过滤标准</h3><p>必须且只能选择两套标准</p></div></div><b>{selectedStandardIds.length} / 2</b></div>
                <div className="plan-options">
                  {processingStandards.map((standard) => <label className={`plan-option ${selectedStandardIds.includes(standard.id) ? "selected" : ""}`} key={standard.id}>
                    <input type="checkbox" checked={selectedStandardIds.includes(standard.id)} onChange={() => toggleProcessingStandard(standard.id)} />
                    <span><strong>{standard.name}</strong><small>{standard.description}</small></span>
                    <i>{selectedStandardIds.includes(standard.id) ? <Check size={15} /> : null}</i>
                  </label>)}
                  {!processingStandards.length && <p className="inline-warning">尚未配置可用标准，请前往“标准管理”完成配置。</p>}
                </div>
              </section>
              <section className="plan-section">
                <div className="section-title"><div><span>02</span><div><h3>美化方案</h3><p>过滤通过后统一执行</p></div></div></div>
                <label className="select-field" htmlFor="beautifyProfile">美化标准
                  <select id="beautifyProfile" value={beautifyProfile} onChange={(event) => setBeautifyProfile(event.target.value)}>
                    <option value="" disabled>请选择美化标准</option>
                    {beautifyProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                  </select>
                  <small>{selectedBeautifyProfile?.description ?? "请选择本次任务的美化方案。"}</small>
                </label>
              </section>
              <section className="pipeline-note"><Sparkles size={20} aria-hidden="true" /><div><strong>正式处理流水线</strong><p>AI 初筛 → 标准过滤 → 独立美化 → 标签绑定 → 素材库匹配。识别结果会复用，不重复调用视觉模型。</p></div></section>
            </>}

            {workflowStep === 3 && <>
              <header className="sop-stage-heading">
                <button className="back-link" type="button" onClick={() => setWorkflowStep(2)}><ChevronLeft size={16} />返回方案</button>
                <span>步骤 3 / 6</span>
                <h2>执行前检查</h2>
                <p>确认后将锁定本次任务配置，并开始上传与自动处理。</p>
              </header>
              <div className="preflight-list">
                <PreflightRow icon={<Images size={20} />} title="图片清单" detail={`${items.length} 张图片已就绪`} valid={items.length > 0} action="返回修改" onAction={() => setWorkflowStep(1)} />
                <PreflightRow icon={<Check size={20} />} title="处理标准" detail={selectedStandards.map((item) => item.name).join("、") || "尚未选择"} valid={selectedStandardIds.length === 2} action="修改" onAction={() => setWorkflowStep(2)} />
                <PreflightRow icon={<Sparkles size={20} />} title="AI 与美化方案" detail={`AI 识别已启用 · ${selectedBeautifyProfile?.name ?? "尚未选择"}`} valid={Boolean(beautifyProfile)} action="修改" onAction={() => setWorkflowStep(2)} />
                <PreflightRow icon={<Database size={20} />} title="素材库匹配" detail="独立美化后自动匹配，未匹配项进入人工复核" valid action="查看素材库" onAction={() => setActiveWorkspace("library")} />
              </div>
              <div className="preflight-notice"><AlertCircle size={18} aria-hidden="true" /><div><strong>开始后配置将被锁定</strong><p>如需修改图片或处理方案，请在开始处理前返回对应步骤。</p></div></div>
            </>}

            {workflowStep === 4 && <>
              <header className="sop-stage-heading centered">
                <span>步骤 4 / 6</span>
                <h2>{job ? statusLabel(job.status) : "正在上传并创建任务"}</h2>
                <p>任务正在后台执行，可以保持页面打开查看实时进度。</p>
              </header>
              <div className="processing-hero">
                <span className="processing-icon"><Loader2 className="spin" size={34} aria-hidden="true" /></span>
                <strong>{job?.progress ?? Math.min(18, Math.round((uploadedCount / Math.max(items.length, 1)) * 18))}%</strong>
                <div className="processing-meter"><i style={{ width: `${job?.progress ?? Math.min(18, Math.round((uploadedCount / Math.max(items.length, 1)) * 18))}%` }} /></div>
                <p>{job ? `${job.processed}/${job.total} 已处理` : `${uploadedCount}/${items.length} 已上传`}</p>
              </div>
              <div className="processing-stages">
                {["上传校验", "AI 初筛", "标准过滤", "独立美化", "素材匹配", "汇总结果"].map((label, index) => {
                  const progress = job?.progress ?? (busy ? 8 : 0);
                  const threshold = [5, 18, 38, 58, 78, 96][index];
                  const done = progress >= threshold;
                  const active = !done && (index === 0 || progress >= [0, 5, 18, 38, 58, 78][index]);
                  return <div key={label} className={`${done ? "done" : ""} ${active ? "active" : ""}`}><span>{done ? <Check size={15} /> : index + 1}</span><strong>{label}</strong></div>;
                })}
              </div>
              {job && <div className="stage-counts centered-counts">{Object.entries(job.stage_counts).filter(([, count]) => count > 0).map(([stage, count]) => <span key={stage}>{stageCountLabel(stage)} {count}</span>)}</div>}
              {job && !isTerminalStatus(job.status) && <button className="cancel-job-button centered-cancel" type="button" onClick={() => void cancelCurrentJob()}><X size={15} aria-hidden="true" />取消当前任务</button>}
            </>}

            {workflowStep === 5 && <>
              <header className="sop-stage-heading result-heading-row"><div><span>步骤 5 / 6</span><h2>人工复核</h2><p>检查处理结果、修正待复核项，并按需要重试失败图片。</p></div>{results && <button className="primary-inline" type="button" onClick={() => setWorkflowStep(6)}>进入交付<ChevronRight size={16} /></button>}</header>
              {results ? <ResultsPanel results={results} averageScore={averageScore} resultFilter={resultFilter} visibleResults={visibleResults} selectedImage={selectedImage} page={resultPage} onFilterChange={(filter) => void changeResultPage(0, filter)} onPageChange={(page) => void changeResultPage(page)} onSelectImage={setSelectedImage} onReviewResolved={applyReviewResult} onRetry={(imageId) => void retryImage(imageId)} /> : <div className="queue-empty">结果正在汇总，完成后会自动进入复核。</div>}
            </>}

            {workflowStep === 6 && <>
              <header className="sop-stage-heading centered">
                <span>步骤 6 / 6</span>
                <h2>交付与归档</h2>
                <p>下载本次处理结果，任务配置与处理记录会保留在历史记录中。</p>
              </header>
              <div className="delivery-summary">
                <span className="delivery-icon"><Check size={32} aria-hidden="true" /></span>
                <p>处理流程已完成，可以下载交付文件。</p>
                <div><Metric label="总图片" value={results?.summary.total ?? 0} /><Metric label="保留并美化" value={results?.summary.selected ?? 0} tone="selected" /><Metric label="未通过标准" value={results?.summary.rejected ?? 0} tone="rejected" /><Metric label="合格未入选" value={results?.summary.not_selected ?? 0} /></div>
                <button className="primary-inline delivery-download" type="button" disabled={!results?.images.some((image) => image.enhanced_url ?? image.original_url)} onClick={() => results && downloadCurrentPage(results.images)}><ArrowDownToLine size={17} />下载当前结果</button>
                <button className="ghost-button" type="button" onClick={resetWorkspace}>创建新任务</button>
              </div>
            </>}
          </section>

          {workflowStep <= 3 && <aside className="task-summary">
            <h2>本次任务</h2>
            <dl>
              <div><dt>图片数量</dt><dd><strong>{items.length}</strong> 张{items.length > 0 && <small className="summary-file-status"><em>{items.length - failedCount} 张已校验</em>{failedCount > 0 && <b>{failedCount} 张需处理</b>}</small>}</dd></div>
              <div><dt>处理标准</dt><dd>{workflowStep === 1 ? "待选择" : selectedStandards.length ? selectedStandards.map((item) => item.name).join("、") : "待选择"}</dd></div>
              <div><dt>美化方案</dt><dd>{workflowStep === 1 ? "待选择" : selectedBeautifyProfile?.name ?? "待选择"}</dd></div>
              <div><dt>预计处理</dt><dd><Clock3 size={15} aria-hidden="true" />约 {estimatedMinutes}–{estimatedMinutes + 2} 分钟</dd></div>
            </dl>
            {workflowStep === 1 && <button className="summary-primary" type="button" disabled={!items.length} onClick={() => setWorkflowStep(2)}>继续选择方案<ChevronRight size={17} /></button>}
            {workflowStep === 2 && <button className="summary-primary" type="button" disabled={selectedStandardIds.length !== 2 || !beautifyProfile} onClick={() => setWorkflowStep(3)}>进入执行检查<ChevronRight size={17} /></button>}
            {workflowStep === 3 && <button className="summary-primary" type="button" disabled={!canCreateJob} onClick={() => void startJob()}>{busy ? <Loader2 className="spin" size={17} /> : <Sparkles size={17} />}确认并开始处理</button>}
          </aside>}
        </div>

        {historyOpen && <HistoryPanel history={history} loading={historyLoading} onRefresh={() => void loadHistory()} onOpen={(entry) => void openHistoryJob(entry)} />}
      </> : activeWorkspace === "library" ? <LibraryWorkspace onMessage={setMessage} /> : <ProfileWorkspace onMessage={setMessage} onProfilesChanged={reloadProcessingProfiles} />}
    </main>
  );
}

function PreflightRow({ icon, title, detail, valid, action, onAction }: { icon: ReactNode; title: string; detail: string; valid: boolean; action: string; onAction: () => void }) {
  return <section className="preflight-row">
    <span className="preflight-icon">{icon}</span>
    <div><h3>{title}</h3><p>{detail}</p></div>
    <span className={`preflight-status ${valid ? "valid" : "invalid"}`}>{valid ? <Check size={15} /> : <AlertCircle size={15} />}{valid ? "通过" : "待完善"}</span>
    <button type="button" onClick={onAction}>{action}<ChevronRight size={15} /></button>
  </section>;
}

function ResultsPanel({
  results,
  averageScore,
  resultFilter,
  visibleResults,
  selectedImage,
  page,
  onFilterChange,
  onPageChange,
  onSelectImage,
  onReviewResolved,
  onRetry
}: {
  results: JobResults;
  averageScore: number;
  resultFilter: ResultFilter;
  visibleResults: ResultImage[];
  selectedImage: ResultImage | null;
  page: number;
  onFilterChange: (value: ResultFilter) => void;
  onPageChange: (page: number) => void;
  onSelectImage: (image: ResultImage) => void;
  onReviewResolved: (imageId: string, result: SimilarityTaggingResult) => void;
  onRetry: (imageId: string) => void;
}) {
  return (
    <section className="results-panel">
      <div className="results-summary">
        <Metric label="总图片" value={results.summary.total} />
        <Metric label="保留并美化" value={results.summary.selected} tone="selected" />
        <Metric label="未通过标准" value={results.summary.rejected} tone="rejected" />
        <Metric label="合格未入选" value={results.summary.not_selected} />
        <Metric label="保留图平均分" value={averageScore} />
      </div>
      <div className="result-toolbar">
        <div className="tabs" role="tablist" aria-label="结果筛选">
          {[["all", "全部"], ["selected", "保留并美化"], ["rejected", "未通过标准"], ["not_selected", "合格未入选"], ["failed", "处理失败"]].map(([value, label]) => (
            <button key={value} className={resultFilter === value ? "active" : ""} type="button" role="tab" aria-selected={resultFilter === value} onClick={() => onFilterChange(value as ResultFilter)}>{label}</button>
          ))}
        </div>
        <button className="page-download-button" type="button" disabled={!visibleResults.some((image) => image.enhanced_url ?? image.original_url)} onClick={() => downloadCurrentPage(visibleResults)}><ArrowDownToLine size={15} aria-hidden="true" />下载本页</button>
      </div>
      <div className="result-layout">
        <div className="result-grid">
          {visibleResults.map((image) => <ResultCard key={image.image_id} image={image} active={selectedImage?.image_id === image.image_id} onOpen={() => onSelectImage(image)} />)}
          {!visibleResults.length && <p className="result-page-empty">当前筛选下还没有结果。</p>}
        </div>
        <aside className="detail-panel" aria-label="图片详情">{selectedImage ? <ImageDetail image={selectedImage} onReviewResolved={onReviewResolved} onRetry={onRetry} /> : <EmptyDetail />}</aside>
      </div>
      <Pagination page={page} total={results.result_total} pageSize={results.limit} onChange={onPageChange} label="处理结果" />
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
              <span>{entry.selected} 保留，{entry.rejected} 淘汰，{entry.not_selected} 合格未入选</span>
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
    ready: "已校验",
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

function formatSimilarityScore(similarity?: number | null) {
  return similarity == null ? "--" : `${(similarity * 100).toFixed(2)}%`;
}

function SimilarityScore({ similarity }: { similarity?: number | null }) {
  const value = formatSimilarityScore(similarity);

  return (
    <div className="similarity-score" aria-label={`AI匹配相似度 ${value}`}>
      <span>AI匹配相似度</span>
      <strong>{value}</strong>
    </div>
  );
}

function ResultCard({ image, active, onOpen }: { image: ResultImage; active: boolean; onOpen: () => void }) {
  const previewUrl = image.enhanced_url ?? image.original_url;
  const primaryReason = image.reject_codes?.[0] === "AI_FILTER_REJECTED"
    ? image.reasons[0]
    : image.reject_codes?.[0]
      ? rejectCodeLabel(image.reject_codes[0])
      : image.reasons[0] ?? "基础质量达标";

  return (
    <article className={`result-card ${active ? "active" : ""}`}>
      <button type="button" onClick={onOpen} aria-label={`查看 ${image.image_id} 详情`}>
        {previewUrl ? (
          <img src={previewUrl} alt={`${image.image_id} 预览`} loading="lazy" decoding="async" />
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
        <SimilarityScore similarity={image.tagging_result?.similarity} />
      </div>
      <p>{primaryReason}</p>
      {image.processing_standard_name && <p className="standard-match-note">处理标准：{image.processing_standard_name}</p>}
      {image.tagging_result?.decision === "matched" && image.tagging_result.tags.length ? (
        <div className="tag-row" aria-label="AI 自动标签">
          {image.tagging_result.tags.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      ) : image.tagging_result?.decision === "pending_review" ? <p className="unmatched-note">无法识别，等待人工复核</p>
        : image.tagging_result?.decision === "unmatched" ? <p className="unmatched-note">无法识别</p> : null}
    </article>
  );
}

function ImageDetail({ image, onReviewResolved, onRetry }: { image: ResultImage; onReviewResolved: (imageId: string, result: SimilarityTaggingResult) => void; onRetry: (imageId: string) => void }) {
  const [showEnhanced, setShowEnhanced] = useState(true);
  const [compare, setCompare] = useState(50);
  const imageUrl = showEnhanced && image.enhanced_url ? image.enhanced_url : image.original_url;
  const openUrl = image.enhanced_download_url ?? image.original_download_url
    ?? image.enhanced_url ?? image.original_url;

  return (
    <>
      <div className="detail-preview">
        {image.original_url && image.enhanced_url && showEnhanced ? (
          <div className="compare-viewer">
            <img src={image.original_url} alt={`${image.image_id} 原图`} decoding="async" />
            <img
              className="compare-enhanced"
              src={image.enhanced_url}
              alt={`${image.image_id} 美化图`}
              decoding="async"
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
          <img src={imageUrl} alt={`${image.image_id} 大图预览`} decoding="async" />
        ) : image.files_expired ? (
          <p>图片文件已过期，处理记录和标签仍保留。</p>
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
        <SimilarityScore similarity={image.tagging_result?.similarity} />
      </div>
      {image.decision === "failed" && <button className="retry-image-button" type="button" onClick={() => onRetry(image.image_id)}><RefreshCw size={15} aria-hidden="true" />重试这张图片</button>}
      {image.processing_standard_name && <div className="standard-match-detail"><strong>已启用标准：{image.processing_standard_name}</strong><p>{image.activation_reason}</p></div>}
      {(image.audit_dimensions?.length ?? 0) > 0 && (
        <section className="audit-dimensions" aria-label="过滤审核明细">
          <h3>过滤审核明细</h3>
          <div className="audit-dimension-list">
            {image.audit_dimensions?.map((dimension, index) => (
              <div className={`audit-dimension ${dimension.passed ? "passed" : "failed"}`} key={`${dimension.dimension}-${index}`}>
                <span>{dimension.passed ? "合格" : "不合格"}</span>
                <div><strong>{dimension.dimension}</strong><p>{dimension.reason}</p></div>
              </div>
            ))}
          </div>
        </section>
      )}

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
      <SimilarityMatch imageId={image.image_id} result={image.tagging_result} onResolved={onReviewResolved} />
    </>
  );
}

function SimilarityMatch({ imageId, result, onResolved }: { imageId: string; result?: ResultImage["tagging_result"]; onResolved: (imageId: string, result: SimilarityTaggingResult) => void }) {
  const [candidates, setCandidates] = useState<SimilarityCandidate[]>([]);
  const [selectedAssetId, setSelectedAssetId] = useState(result?.matched_asset_id ?? "");
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);

  useEffect(() => {
    setSelectedAssetId(result?.matched_asset_id ?? "");
    setCandidates([]);
    setReviewError(null);
    if (result?.decision !== "pending_review") return;
    let active = true;
    api.getTagReviews().then((reviews) => {
      if (!active) return;
      const review = reviews.find((entry) => entry.image_id === imageId);
      if (!review) return;
      const byPath = new Map<string, SimilarityCandidate>();
      review.candidates.forEach((candidate) => {
        const key = candidate.tags.join("、");
        if (!byPath.has(key)) byPath.set(key, candidate);
      });
      const options = Array.from(byPath.values());
      setCandidates(options);
      setSelectedAssetId((current) => current || review.matched_asset_id || options[0]?.asset_id || "");
    }).catch((error) => {
      if (active) setReviewError(error instanceof Error ? error.message : "候选素材读取失败");
    });
    return () => { active = false; };
  }, [imageId, result?.decision, result?.matched_asset_id]);

  async function decideReview(decision: "matched" | "unmatched") {
    setReviewBusy(true);
    setReviewError(null);
    try {
      const review = await api.decideTagReview(imageId, {
        decision,
        matched_asset_id: decision === "matched" ? selectedAssetId || result?.matched_asset_id : null
      });
      onResolved(imageId, {
        decision: review.decision,
        tags: review.tags,
        matched_asset_id: review.matched_asset_id,
        similarity: review.similarity_score,
        final_score: review.final_score,
        message: review.message
      });
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : "人工复核处理失败");
    } finally {
      setReviewBusy(false);
    }
  }

  if (!result) return null;
  return <section className={`similarity-panel ${result.decision}`}>
    <div className="similarity-heading"><h3>素材匹配</h3><span>{result.decision === "matched" ? "已匹配" : result.decision === "pending_review" ? "待复核" : "未匹配"}</span></div>
    <p>{result.message}</p>
    {result.tags.length ? <div className="path-tags">{result.tags.map((tag, index) => <span key={`${tag}-${index}`}>{tag}</span>)}</div> : null}
    {result.similarity != null ? <small>图片相似度 {Math.round(result.similarity * 100)}%</small> : null}
    {result.decision === "pending_review" && <div className="review-controls">
      {candidates.length > 1 && <label>选择候选标签组合<select value={selectedAssetId} onChange={(event) => setSelectedAssetId(event.target.value)}>{candidates.map((candidate) => <option key={candidate.asset_id} value={candidate.asset_id}>{candidate.tags.join("、")}（综合匹配 {Math.round(candidate.final_score * 100)}%）</option>)}</select></label>}
      <div className="review-actions"><button className="review-confirm" type="button" disabled={reviewBusy || (!selectedAssetId && !result.matched_asset_id)} onClick={() => void decideReview("matched")}>{reviewBusy ? <Loader2 className="spin" size={15} /> : <Check size={15} />}确认此标签</button><button type="button" disabled={reviewBusy} onClick={() => void decideReview("unmatched")}><X size={15} />设为未匹配</button></div>
      {reviewError && <p className="review-error">{reviewError}</p>}
    </div>}
  </section>;
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
      if (item) {
        try {
          await worker(item);
        } catch {
          // Individual upload errors are already reflected on their own rows.
        }
      }
    }
  });
  await Promise.all(runners);
}

function Pagination({ page, total, pageSize, onChange, label }: { page: number; total: number; pageSize: number; onChange: (page: number) => void; label: string }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return <nav className="pagination" aria-label={`${label}分页`}>
    <button type="button" aria-label="上一页" disabled={page <= 0} onClick={() => onChange(page - 1)}><ChevronLeft size={16} aria-hidden="true" /></button>
    <span>第 {page + 1} / {pageCount} 页 · 共 {total} 张</span>
    <button type="button" aria-label="下一页" disabled={page >= pageCount - 1} onClick={() => onChange(page + 1)}><ChevronRight size={16} aria-hidden="true" /></button>
  </nav>;
}

function stageCountLabel(stage: string): string {
  return {
    waiting: "等待",
    filtering: "过滤",
    beautifying: "美化",
    content_analysis: "标签绑定",
    matching: "匹配",
    completed: "完成",
    rejected: "淘汰",
    not_selected: "未入选",
    failed: "失败",
    cancelled: "取消"
  }[stage] ?? stage;
}

function downloadCurrentPage(images: ResultImage[]) {
  images.forEach((image, index) => {
    const url = image.enhanced_download_url ?? image.original_download_url
      ?? image.enhanced_url ?? image.original_url;
    if (!url) return;
    window.setTimeout(() => {
      const link = document.createElement("a");
      link.href = url;
      link.download = `${image.image_id}.jpg`;
      link.click();
    }, index * 80);
  });
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
