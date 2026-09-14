import {
  AlertCircle,
  AlertTriangle,
  ArrowDownToLine,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  CircleX,
  Clock3,
  Database,
  Eye,
  FileImage,
  History,
  Images,
  Loader2,
  LogOut,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Tag,
  Trash2,
  UploadCloud,
  Users,
  X
} from "lucide-react";
import { ChangeEvent, DragEvent, PointerEvent as ReactPointerEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./services/api";
import { LibraryWorkspace } from "./LibraryWorkspace";
import { ProfileWorkspace } from "./ProfileWorkspace";
import brandLogo from "./assets/image-processing-logo.svg";
import type {
  AIImageTags,
  AIModelConfig,
  AuthUser,
  Decision,
  ImageMetrics,
  JobHistoryItem,
  JobHistoryResponse,
  JobProgress,
  JobResults,
  ProfileOption,
  ResultImage,
  ResultFilter,
  ClassificationContentAnalysis,
  SimilarityTaggingResult,
  UploadItem
} from "./types";
import { decisionLabel, isTerminalStatus, processingReasons, rejectCodeLabel, statusLabel } from "./utils/decision";
import { downloadResultArchive, getDownloadableResultCount } from "./utils/download";
import { createClientId } from "./utils/id";
import {
  imagePixelDisposition,
  MAX_DECODE_IMAGE_PIXELS,
  MAX_PROCESSING_IMAGE_PIXELS,
  readImageDimensions
} from "./utils/imagePixels";
import { workflowStageState } from "./utils/workflowProgress";

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
  { id: 5, title: "结果确认", description: "检查与重试" },
  { id: 6, title: "交付归档", description: "下载与留档" }
];

interface AppProps {
  user: AuthUser;
  onLogout: () => void;
  onManageUsers: () => void;
}

function App({ user, onLogout, onManageUsers }: AppProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const itemsRef = useRef<UploadItem[]>([]);
  const operationVersionRef = useRef(0);
  const historyRequestVersionRef = useRef(0);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [processingStandards, setProcessingStandards] = useState<ProfileOption[]>([]);
  const [globalFilterProfiles, setGlobalFilterProfiles] = useState<ProfileOption[]>([]);
  const [beautifyProfiles, setBeautifyProfiles] = useState<ProfileOption[]>([]);
  const [beautifyProfile, setBeautifyProfile] = useState("");
  const [redactionProfiles, setRedactionProfiles] = useState<ProfileOption[]>([]);
  const [redactionProfile, setRedactionProfile] = useState("");
  const [watermarkProcessingEnabled, setWatermarkProcessingEnabled] = useState(true);
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
  const [historyPage, setHistoryPage] = useState(0);
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
  const [modelConfigError, setModelConfigError] = useState<string | null>(null);
  const [activeWorkspace, setActiveWorkspace] = useState<"processing" | "library" | "profiles">("processing");
  const [workflowStep, setWorkflowStep] = useState<WorkflowStep>(1);

  const uploadedCount = items.filter((item) => item.status === "uploaded").length;
  const failedCount = items.filter((item) => item.status === "failed").length;
  const hasFallbackStandard = processingStandards.some((item) => item.is_fallback);
  const hasGlobalFilter = globalFilterProfiles.length === 1;
  const canCreateJob = items.some((item) => item.file)
    && processingStandards.length > 0
    && hasGlobalFilter
    && hasFallbackStandard
    && (!beautifyEnabled || Boolean(beautifyProfile))
    && Boolean(redactionProfile)
    && !busy;
  const selectedBeautifyProfile = beautifyProfiles.find((profile) => profile.id === beautifyProfile);
  const selectedRedactionProfile = redactionProfiles.find((profile) => profile.id === redactionProfile);
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

  async function reloadProcessingProfiles() {
    const [globalFilters, standards, beautify, redaction] = await Promise.all([
      api.getFilterProfiles(),
      api.getProcessingStandards(),
      api.getBeautifyProfiles(),
      api.getRedactionProfiles()
    ]);
    setGlobalFilterProfiles(globalFilters);
    setProcessingStandards(standards);
    setBeautifyProfiles(beautify);
    setRedactionProfiles(redaction);
    setBeautifyProfile((current) => (
      beautify.some((item) => item.id === current) ? current : (beautify[0]?.id ?? "")
    ));
    setRedactionProfile((current) => (
      redaction.some((item) => item.id === current) ? current : (redaction[0]?.id ?? "")
    ));
  }

  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;
    let retryAttempt = 0;
    let startupError: string | null = null;

    const loadStartupData = async () => {
      try {
        await reloadProcessingProfiles();
        if (cancelled) return;
        if (startupError) {
          setMessage((current) => current === startupError ? null : current);
        }
        startupError = null;
        retryAttempt = 0;
      } catch (error) {
        if (cancelled) return;
        startupError = error instanceof Error ? error.message : "正式后端初始化失败";
        setMessage(startupError);
        const retryDelay = Math.min(30_000, 1_000 * (2 ** retryAttempt));
        retryAttempt += 1;
        retryTimer = window.setTimeout(() => void loadStartupData(), retryDelay);
      }
    };

    void loadStartupData();
    return () => {
      cancelled = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
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
          void loadHistory(historyPage, operationVersion);
        }
      } catch (error) {
        if (!isCurrentOperation(operationVersion)) return;
        setMessage(error instanceof Error ? error.message : "查询任务进度失败");
      }
    }, delay);

    return () => window.clearTimeout(timer);
  }, [historyPage, job, resultFilter, resultPage]);

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
      current
        ? nextResults.images.find((image) => image.image_id === current.image_id) ?? null
        : null
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

  async function addFiles(fileList: FileList | File[]) {
    const incoming = Array.from(fileList);
    const availableSlots = MAX_IMAGES - items.length;
    const accepted = incoming.slice(0, availableSlots).filter((file) => file.type.startsWith("image/"));
    const oversized = accepted.find((file) => file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024);

    if (oversized) {
      setMessage(`${oversized.name} 超过 ${MAX_IMAGE_SIZE_MB}MB，已跳过。`);
    }

    const sizeAccepted = accepted.filter((file) => file.size <= MAX_IMAGE_SIZE_MB * 1024 * 1024);
    const pixelAccepted: File[] = [];
    let downscaleCount = 0;
    let rejectedByPixels: { file: File; width: number; height: number } | null = null;
    let unreadable: File | null = null;
    for (const file of sizeAccepted) {
      try {
        const { width, height } = await readImageDimensions(file);
        const disposition = imagePixelDisposition(width, height);
        if (disposition === "reject") {
          rejectedByPixels ??= { file, width, height };
          continue;
        }
        if (disposition === "downscale") downscaleCount += 1;
        pixelAccepted.push(file);
      } catch {
        unreadable ??= file;
      }
    }

    const nextItems = pixelAccepted.map<UploadItem>((file) => ({
        id: createClientId(),
        file,
        filename: file.name,
        fileSize: file.size,
        contentType: file.type || "application/octet-stream",
        previewUrl: URL.createObjectURL(file),
        status: "ready",
        progress: 0
      }));

    if (rejectedByPixels) {
      const pixels = rejectedByPixels.width * rejectedByPixels.height;
      setMessage(
        `${rejectedByPixels.file.name} 为 ${rejectedByPixels.width}×${rejectedByPixels.height}`
        + `（${pixels.toLocaleString()} 像素），超过 ${(MAX_DECODE_IMAGE_PIXELS / 1_000_000).toFixed(0)}MP 硬安全上限，已跳过。`
      );
    } else if (unreadable) {
      setMessage(`${unreadable.name} 无法读取图片尺寸，已跳过。`);
    } else if (downscaleCount > 0) {
      setMessage(
        `${downscaleCount} 张图片超过 ${(MAX_PROCESSING_IMAGE_PIXELS / 1_000_000).toFixed(0)}MP，`
        + "处理时会自动按比例缩小，原图保持不变。"
      );
    }

    if (incoming.length > availableSlots) {
      setMessage(`最多支持 ${MAX_IMAGES} 张图片，本次已按上限加入。`);
    }

    setItems((current) => [...current, ...nextItems]);
  }

  async function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) await addFiles(event.target.files);
    event.target.value = "";
  }

  async function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    await addFiles(event.dataTransfer.files);
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
    setWatermarkProcessingEnabled(false);
    setWorkflowStep(1);
  }

  function isCurrentOperation(operationVersion: number) {
    return operationVersion === operationVersionRef.current;
  }

  async function loadHistory(page = historyPage, operationVersion?: number) {
    const requestVersion = ++historyRequestVersionRef.current;
    setHistoryLoading(true);
    try {
      const nextHistory = await api.getHistory(50, page * 50);
      if (
        requestVersion !== historyRequestVersionRef.current
        || (operationVersion !== undefined && !isCurrentOperation(operationVersion))
      ) return;
      setHistory(nextHistory);
    } catch (error) {
      if (
        requestVersion !== historyRequestVersionRef.current
        || (operationVersion !== undefined && !isCurrentOperation(operationVersion))
      ) return;
      setMessage(error instanceof Error ? error.message : "加载历史记录失败");
    } finally {
      if (
        requestVersion === historyRequestVersionRef.current
        && (operationVersion === undefined || isCurrentOperation(operationVersion))
      ) setHistoryLoading(false);
    }
  }

  function toggleHistory() {
    const nextOpen = !historyOpen;
    setHistoryOpen(nextOpen);
    if (nextOpen) {
      setHistoryPage(0);
      void loadHistory(0);
      window.setTimeout(() => document.querySelector<HTMLElement>(".history-panel")?.scrollIntoView({ behavior: "smooth" }), 0);
    }
  }

  function changeHistoryPage(page: number) {
    setHistoryPage(page);
    void loadHistory(page);
  }

  async function openModelConfig() {
    setModelConfigOpen(true);
    setModelConfigLoading(true);
    setModelConfig(null);
    setModelApiKey("");
    setModelConfigError(null);
    try {
      setModelConfig(await api.getAIModelConfig());
    } catch (error) {
      setModelConfigError(error instanceof Error ? error.message : "加载 AI 配置失败");
    } finally {
      setModelConfigLoading(false);
    }
  }

  async function saveModelConfig() {
    if (!modelConfig) return;
    setModelConfigSaving(true);
    setModelConfigError(null);
    try {
      const updated = await api.updateAIModelConfig({
        enabled: modelConfig.enabled,
        ...(modelApiKey.trim() ? { api_key: modelApiKey.trim() } : {})
      });
      setModelConfig(updated);
      setModelApiKey("");
      setModelConfigOpen(false);
      setMessage("AI 配置已保存，将用于新任务。");
    } catch (error) {
      setModelConfigError(error instanceof Error ? error.message : "保存 AI 配置失败");
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
        beautify_profile: beautifyEnabled ? beautifyProfile : undefined,
        redaction_profile: redactionProfile,
        filter_enabled: filterEnabled,
        beautify_enabled: beautifyEnabled,
        watermark_processing_enabled: watermarkProcessingEnabled,
        similarity_enabled: similarityEnabled,
        unmatched_standard_policy: "reject",
        enhance_level: 1,
        files: uploadable.map((item) => ({
          filename: item.filename,
          content_type: item.contentType,
          file_size: item.fileSize
        }))
      });
      const successfulIds: string[] = [];
      const uploadErrors: string[] = [];
      const registrations = uploadable.map((item, index) => ({ item, registered: batch.items[index] }));
      await runConcurrent(registrations, UPLOAD_CONCURRENCY, async ({ item, registered }) => {
        if (!registered) {
          const message = `${item.filename}：上传凭据缺失`;
          uploadErrors.push(message);
          updateItem(item.id, { status: "failed", error: message }, operationVersion);
          return;
        }
        try {
          const uploadedId = await uploadOne(item, registered, operationVersion);
          if (uploadedId) successfulIds.push(uploadedId);
        } catch (error) {
          uploadErrors.push(error instanceof Error ? error.message : "图片上传失败");
        }
      });
      if (!isCurrentOperation(operationVersion)) return;
      if (!successfulIds.length) {
        throw new Error(uploadErrors[0] ?? "没有成功上传的图片，无法创建任务");
      }
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
          <img className="brand-mark" src={brandLogo} alt="VisioFlow" />
          <div>
            <h1>VisioFlow</h1>
          </div>
        </div>
        <nav className="workspace-switch" aria-label="工作区">
          <button className={activeWorkspace === "processing" ? "active" : ""} type="button" aria-current={activeWorkspace === "processing" ? "page" : undefined} onClick={() => setActiveWorkspace("processing")}><Sparkles size={16} aria-hidden="true" />图片处理</button>
          <button className={activeWorkspace === "library" ? "active" : ""} type="button" aria-current={activeWorkspace === "library" ? "page" : undefined} onClick={() => setActiveWorkspace("library")}><Database size={16} aria-hidden="true" />素材库</button>
          <button className={activeWorkspace === "profiles" ? "active" : ""} type="button" aria-current={activeWorkspace === "profiles" ? "page" : undefined} onClick={() => setActiveWorkspace("profiles")}><SlidersHorizontal size={16} aria-hidden="true" />标准管理</button>
        </nav>
        <div className="topbar-actions">
          <span className="mode-pill">正式模式</span>
          <span className="current-user"><strong>{user.display_name}</strong><small>{user.role === "admin" ? "管理员" : "操作员"}</small></span>
          {user.role === "admin" && <button className="model-config-button" type="button" onClick={onManageUsers}><Users size={17} aria-hidden="true" /><span>账号管理</span></button>}
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
          <button className="tool-button" type="button" aria-label="退出登录" title="退出登录" onClick={onLogout}>
            <LogOut size={18} aria-hidden="true" />
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
            {modelConfigLoading ? <div className="model-config-loading"><Loader2 className="spin" size={20} aria-hidden="true" />正在读取配置</div> : !modelConfig ? <div className="model-config-load-error" role="alert"><AlertCircle size={20} aria-hidden="true" /><span>{modelConfigError ?? "加载 AI 配置失败"}</span><button className="ghost-button" type="button" onClick={() => void openModelConfig()}>重新加载</button></div> : <>
              <label className="config-toggle"><input type="checkbox" checked={modelConfig.enabled} onChange={(event) => { setModelConfig({ ...modelConfig, enabled: event.target.checked }); setModelConfigError(null); }} /><span>启用 AI 分类、规则过滤与美化规划</span></label>
              <label className="config-field">API Key<input type="password" value={modelApiKey} onChange={(event) => { setModelApiKey(event.target.value); setModelConfigError(null); }} placeholder={modelConfig.api_key_configured ? "已配置，留空则保持不变" : "请输入 API Key"} autoComplete="new-password" /></label>
              <a className="config-key-link" href="https://router.keenlight.ai/home" target="_blank" rel="noreferrer">获取</a>
              <p className="config-note">API Key 仅保存在服务端且不会在页面回显；模型配置会冻结到新任务记录中。</p>
              {modelConfigError && <div className="model-config-error" role="alert"><AlertCircle size={17} aria-hidden="true" /><span>{modelConfigError}</span></div>}
              <div className="model-config-actions"><button className="ghost-button" type="button" onClick={() => setModelConfigOpen(false)} disabled={modelConfigSaving}>取消</button><button className="primary-button" type="button" onClick={() => void saveModelConfig()} disabled={modelConfigSaving}>{modelConfigSaving && <Loader2 className="spin" size={16} aria-hidden="true" />}{modelConfigSaving ? "保存中" : "保存配置"}</button></div>
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
                <p>支持 JPG、JPEG、PNG、WebP，单张不超过 {MAX_IMAGE_SIZE_MB}MB，最多 {MAX_IMAGES} 张；超过 12MP 自动缩放，硬上限 25MP</p>
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
                <h2>确认处理方案</h2>
                <p>系统先对所有图片执行全局过滤，再逐图分类并执行唯一对应的专属过滤规则。</p>
              </header>
              <section className="plan-section">
                <div className="section-title"><div><span>01</span><div><h3>全局审核与分类过滤</h3><p>任务创建时锁定全局标准和全部分类标准版本</p></div></div><b>分类 {processingStandards.length} 项</b></div>
                <div className="automatic-standard-band">
                  <div><Check size={18} aria-hidden="true" /><span><strong>{globalFilterProfiles[0]?.name ?? "尚未配置全局过滤标准"}</strong><small>所有图片优先执行全局规则；通过后只命中一条分类并执行对应规则。</small></span></div>
                  <ul>
                    {processingStandards.map((standard) => <li key={standard.id}><span>{standard.name}</span>{standard.is_fallback && <b>兜底分类</b>}</li>)}
                  </ul>
                </div>
                {!hasGlobalFilter && <p className="inline-warning">必须且只能启用一套全局过滤标准，请先在“标准管理”中配置。</p>}
                {!processingStandards.length && <p className="inline-warning">没有启用中的过滤标准，请先在“标准管理”中配置。</p>}
                {processingStandards.length > 0 && !hasFallbackStandard && <p className="inline-warning">缺少兜底分类，请在“标准管理”中将一条标准设为兜底分类。</p>}
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
              <section className="plan-section">
                <div className="section-title"><div><span>03</span><div><h3>水印与Logo</h3><p>水印放行、Logo遮挡和品牌地膜阈值</p></div></div></div>
                <label className="select-field" htmlFor="redactionProfile">水印与Logo标准
                  <select id="redactionProfile" value={redactionProfile} onChange={(event) => setRedactionProfile(event.target.value)}>
                    <option value="" disabled>请选择水印与Logo标准</option>
                    {redactionProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                  </select>
                  <small>{selectedRedactionProfile ? `${selectedRedactionProfile.description}。左下角水印以本次任务开关为准，开启后仅遮挡英文 APP。` : "请先在标准管理中创建水印与Logo标准。"}</small>
                </label>
                <label className="watermark-task-toggle">
                  <span><strong>处理左下角拍摄水印</strong><small>{watermarkProcessingEnabled ? "已开启：识别英文 APP 并使用透明小当图标遮挡" : "已关闭：保留水印，不执行水印处理"}</small></span>
                  <input
                    type="checkbox"
                    role="switch"
                    checked={watermarkProcessingEnabled}
                    disabled={busy}
                    onChange={(event) => setWatermarkProcessingEnabled(event.target.checked)}
                    aria-label="处理左下角拍摄水印"
                  />
                </label>
                {!redactionProfiles.length && <p className="inline-warning">没有启用中的水印与Logo标准，请先在“标准管理”中配置。</p>}
              </section>
              <section className="pipeline-note"><Sparkles size={20} aria-hidden="true" /><div><strong>逐图连续流水线</strong><p>唯一分类 → 地膜阈值筛选 → 对应过滤 → {watermarkProcessingEnabled ? "定位水印APP并透明覆盖 → " : ""}美化 → Logo处理与编码 → 内容与向量 → 素材匹配。图片之间互不等待。</p></div></section>
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
                <PreflightRow icon={<Check size={20} />} title="全局与分类过滤" detail={`${globalFilterProfiles[0]?.name ?? "未配置全局标准"} · ${processingStandards.length} 条分类标准`} valid={hasGlobalFilter && processingStandards.length > 0 && hasFallbackStandard} action="查看" onAction={() => setWorkflowStep(2)} />
                <PreflightRow icon={<Sparkles size={20} />} title="AI 与美化方案" detail={`AI 识别已启用 · ${selectedBeautifyProfile?.name ?? "尚未选择"}`} valid={Boolean(beautifyProfile)} action="修改" onAction={() => setWorkflowStep(2)} />
                <PreflightRow icon={<ShieldCheck size={20} />} title="水印与Logo标准" detail={`${selectedRedactionProfile?.name ?? "尚未选择"} · 水印处理${watermarkProcessingEnabled ? "开启" : "关闭"}`} valid={Boolean(redactionProfile)} action="修改" onAction={() => setWorkflowStep(2)} />
                <PreflightRow icon={<Database size={20} />} title="素材库匹配" detail="独立美化后自动判定：达到采用线继承标签，否则不打标签" valid action="查看素材库" onAction={() => setActiveWorkspace("library")} />
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
                {["上传校验", "标准分类", "规则过滤", "美化与净化", "内容与向量", "素材匹配", "汇总结果"].map((label, index) => {
                  const state = job
                    ? workflowStageState(job, index)
                    : { done: false, active: busy && index === 0 };
                  const { done, active } = state;
                  return <div key={label} className={`${done ? "done" : ""} ${active ? "active" : ""}`}><span>{done ? <Check size={15} /> : index + 1}</span><strong>{label}</strong></div>;
                })}
              </div>
              {job && <div className="stage-counts centered-counts">{Object.entries(job.stage_counts).filter(([, count]) => count > 0).map(([stage, count]) => <span key={stage}>{stageCountLabel(stage)} {count}</span>)}</div>}
              {job && !isTerminalStatus(job.status) && <button className="cancel-job-button centered-cancel" type="button" onClick={() => void cancelCurrentJob()}><X size={15} aria-hidden="true" />取消当前任务</button>}
            </>}

            {workflowStep === 5 && <>
              <header className="sop-stage-heading result-heading-row"><div><span>步骤 5 / 6</span><h2>结果确认</h2><p>检查自动处理结果，并按需要重试失败图片。</p></div>{results && <button className="primary-inline" type="button" onClick={() => setWorkflowStep(6)}>进入交付<ChevronRight size={16} /></button>}</header>
              {results?.failure && <section className="action-panel failed-action"><header><AlertTriangle size={18} aria-hidden="true" /><div><strong>任务已在“{failureNodeLabel(results.failure.node)}”节点停止</strong><span>{results.failure.message}</span></div></header><p>错误码：{results.failure.code}{results.failure.upstream_status_code ? ` · 上游 HTTP ${results.failure.upstream_status_code}` : ""}{results.failure.duration_ms != null ? ` · 耗时 ${(results.failure.duration_ms / 1000).toFixed(1)} 秒` : ""}</p></section>}
              {results ? <ResultsPanel results={results} averageScore={averageScore} resultFilter={resultFilter} visibleResults={visibleResults} selectedImage={selectedImage} page={resultPage} onFilterChange={(filter) => void changeResultPage(0, filter)} onPageChange={(page) => void changeResultPage(page)} onSelectImage={setSelectedImage} onRetry={(imageId) => void retryImage(imageId)} onRedactionSaved={async () => { await loadResultPage(results.job_id, operationVersionRef.current, resultPage, resultFilter); setMessage("Logo遮挡框已人工复核并重新生成图片"); }} /> : <div className="queue-empty">结果正在汇总，完成后会自动进入结果确认。</div>}
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
                <div className="delivery-metrics"><Metric label="总图片" value={results?.summary.total ?? 0} /><Metric label="保留并美化" value={results?.summary.selected ?? 0} tone="selected" /><Metric label="过滤未通过" value={results?.summary.rejected ?? 0} tone="rejected" /><Metric label="合格未入选" value={results?.summary.not_selected ?? 0} /></div>
                <DownloadAction
                  jobId={results?.job_id ?? ""}
                  className="primary-inline delivery-download"
                  label="下载全部美化图片"
                  iconSize={17}
                  expectedCount={results?.summary.selected ?? 0}
                />
                <button className="ghost-button" type="button" onClick={resetWorkspace}>创建新任务</button>
              </div>
            </>}
          </section>

          {workflowStep <= 3 && <aside className="task-summary">
            <h2>本次任务</h2>
            <dl>
              <div><dt>图片数量</dt><dd><strong>{items.length}</strong> 张{items.length > 0 && <small className="summary-file-status"><em>{items.length - failedCount} 张已校验</em>{failedCount > 0 && <b>{failedCount} 张需处理</b>}</small>}</dd></div>
              <div><dt>分类与过滤</dt><dd>{workflowStep === 1 ? "自动配置" : `全局审核 + ${processingStandards.length} 条分类`}</dd></div>
              <div><dt>美化方案</dt><dd>{workflowStep === 1 ? "待选择" : selectedBeautifyProfile?.name ?? "待选择"}</dd></div>
              <div><dt>水印与Logo</dt><dd>{workflowStep === 1 ? "待选择" : `${selectedRedactionProfile?.name ?? "待选择"} · 水印${watermarkProcessingEnabled ? "开启" : "关闭"}`}</dd></div>
              <div><dt>预计处理</dt><dd><Clock3 size={15} aria-hidden="true" />约 {estimatedMinutes}–{estimatedMinutes + 2} 分钟</dd></div>
            </dl>
            {workflowStep === 1 && <button className="summary-primary" type="button" disabled={!items.length} onClick={() => setWorkflowStep(2)}>继续选择方案<ChevronRight size={17} /></button>}
            {workflowStep === 2 && <button className="summary-primary" type="button" disabled={!hasGlobalFilter || !processingStandards.length || !hasFallbackStandard || !beautifyProfile || !redactionProfile} onClick={() => setWorkflowStep(3)}>进入执行检查<ChevronRight size={17} /></button>}
            {workflowStep === 3 && <button className="summary-primary" type="button" disabled={!canCreateJob} onClick={() => void startJob()}>{busy ? <Loader2 className="spin" size={17} /> : <Sparkles size={17} />}确认并开始处理</button>}
          </aside>}
        </div>

        {historyOpen && <HistoryPanel history={history} page={historyPage} loading={historyLoading} onRefresh={() => void loadHistory()} onPageChange={changeHistoryPage} onOpen={(entry) => void openHistoryJob(entry)} />}
      </> : activeWorkspace === "library" ? <LibraryWorkspace onMessage={setMessage} /> : <ProfileWorkspace onMessage={setMessage} onProfilesChanged={reloadProcessingProfiles} onConfigureAI={() => void openModelConfig()} />}
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
  onRetry,
  onRedactionSaved
}: {
  results: JobResults;
  averageScore: number;
  resultFilter: ResultFilter;
  visibleResults: ResultImage[];
  selectedImage: ResultImage | null;
  page: number;
  onFilterChange: (value: ResultFilter) => void;
  onPageChange: (page: number) => void;
  onSelectImage: (image: ResultImage | null) => void;
  onRetry: (imageId: string) => void;
  onRedactionSaved: () => Promise<void>;
}) {
  if (selectedImage) {
    return (
      <section className="results-panel detail-mode">
        <ImageDetail
          image={selectedImage}
          jobId={results.job_id}
          onBack={() => onSelectImage(null)}
          onRetry={onRetry}
          onRedactionSaved={onRedactionSaved}
        />
      </section>
    );
  }

  return (
    <section className="results-panel">
      <div className="results-summary">
        <Metric label="总图片" value={results.summary.total} />
        <Metric label="保留并美化" value={results.summary.selected} tone="selected" />
        <Metric label="过滤未通过" value={results.summary.rejected} tone="rejected" />
        <Metric label="合格未入选" value={results.summary.not_selected} />
        <Metric label="保留图平均分" value={averageScore} />
      </div>
      <div className="result-toolbar">
        <div className="tabs" role="tablist" aria-label="结果筛选">
          {[["all", "全部"], ["review", "建议复核"], ["selected", "保留并美化"], ["rejected", "过滤未通过"], ["not_selected", "合格未入选"], ["failed", "处理失败"]].map(([value, label]) => (
            <button key={value} className={resultFilter === value ? "active" : ""} type="button" role="tab" aria-selected={resultFilter === value} onClick={() => onFilterChange(value as ResultFilter)}>{label}</button>
          ))}
        </div>
        <DownloadAction
          jobId={results.job_id}
          imageIds={visibleResults.map((image) => image.image_id)}
          className="page-download-button"
          label="下载本页"
          iconSize={15}
          expectedCount={getDownloadableResultCount(visibleResults)}
        />
      </div>
      <div className="result-grid">
        {visibleResults.map((image) => <ResultCard key={image.image_id} image={image} active={false} onOpen={() => onSelectImage(image)} />)}
        {!visibleResults.length && <p className="result-page-empty">当前筛选下还没有结果。</p>}
      </div>
      <Pagination page={page} total={results.result_total} pageSize={results.limit} onChange={onPageChange} label="处理结果" />
    </section>
  );
}

function HistoryPanel({
  history,
  page,
  loading,
  onRefresh,
  onPageChange,
  onOpen
}: {
  history: JobHistoryResponse | null;
  page: number;
  loading: boolean;
  onRefresh: () => void;
  onPageChange: (page: number) => void;
  onOpen: (entry: JobHistoryItem) => void;
}) {
  return (
    <section className="history-panel" aria-label="任务历史记录">
      <div className="history-heading">
        <div>
          <h2>历史记录</h2>
          <p>共 {history?.total ?? 0} 个图片处理任务，每页 50 条</p>
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
      <Pagination page={page} total={history?.total ?? 0} pageSize={50} onChange={onPageChange} label="历史记录" unit="个任务" />
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

function SimilarityScore({ result }: { result?: SimilarityTaggingResult }) {
  const hasContentScore = result?.feature_score != null;
  const value = formatSimilarityScore(
    hasContentScore ? result?.final_score : result?.similarity
  );
  const label = hasContentScore ? "综合匹配度" : "图片相似度";

  return (
    <div className="similarity-score" aria-label={`${label} ${value}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ResultCard({ image, active, onOpen }: { image: ResultImage; active: boolean; onOpen: () => void }) {
  const previewUrl = image.enhanced_url ?? image.original_url;
  const taggingResult = image.library_tags ?? image.tagging_result;
  const preciseReasons = processingReasons(image);
  const primaryReason = image.reject_codes?.[0] === "AI_FILTER_REJECTED"
    ? preciseReasons[0]
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
        <span className={`decision-badge ${image.decision}`}>{resultStatusTitle(image, taggingResult)}</span>
      </button>
      <div className="result-meta">
        <div>
          <strong>{image.image_id}</strong>
          <span>{image.decision === "selected" ? "美化图已生成" : "原图保留供查看"}</span>
        </div>
        <SimilarityScore result={taggingResult} />
      </div>
      <span className={`pipeline-stage-badge ${image.pipeline_stage}`}>{pipelineStageLabel(image.pipeline_stage)}</span>
      <p>{primaryReason}</p>
      {image.classification ? (
        <p className="classification-note">
          <span>命中分类标准</span>
          <strong>{image.classification.standard_name}</strong>
          <em>置信度 {Math.round(image.classification.confidence * 100)}%</em>
          {image.classification.review_required && <b>建议复核</b>}
        </p>
      ) : image.completion && <p className="completion-note"><strong>{image.completion.label === "completed" ? "完工" : "非完工"}</strong><span>{Math.round(image.completion.confidence * 100)}%</span>{image.completion.review_required && <em>建议复核</em>}</p>}
      {image.processing_standard_name && image.processing_standard_name !== image.classification?.standard_name && <p className="standard-match-note">执行过滤标准：{image.processing_standard_name}</p>}
      {taggingResult?.decision === "matched" && taggingResult.tags.length ? (
        <div className="tag-row" aria-label="素材库匹配标签">
          {taggingResult.tags.map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      ) : taggingResult?.decision === "unmatched" ? <p className="unmatched-note">未匹配到素材，暂无标签</p> : null}
    </article>
  );
}

function ImageDetail({ image, jobId, onBack, onRetry, onRedactionSaved }: { image: ResultImage; jobId: string; onBack: () => void; onRetry: (imageId: string) => void; onRedactionSaved: () => Promise<void> }) {
  const [showEnhanced, setShowEnhanced] = useState(true);
  const [showRedactionOverlay, setShowRedactionOverlay] = useState(false);
  const [editingLogoBoxes, setEditingLogoBoxes] = useState(false);
  const [savingLogoBoxes, setSavingLogoBoxes] = useState(false);
  const [logoEditError, setLogoEditError] = useState<string | null>(null);
  const [compare, setCompare] = useState(50);
  const [auditExpanded, setAuditExpanded] = useState(false);
  const [beautifyExpanded, setBeautifyExpanded] = useState(false);
  const [processingExpanded, setProcessingExpanded] = useState(false);
  const [recognitionExpanded, setRecognitionExpanded] = useState(false);
  const imageUrl = showEnhanced && image.enhanced_url ? image.enhanced_url : image.original_url;
  const openUrl = image.enhanced_download_url ?? image.original_download_url
    ?? image.enhanced_url ?? image.original_url;
  const taggingResult = image.library_tags ?? image.tagging_result;
  const auditDimensions = image.audit_dimensions ?? [];
  const preciseReasons = processingReasons(image);
  const failedAuditCount = auditDimensions.filter((dimension) => !dimension.passed).length;
  const auditRegionId = `audit-dimensions-${image.image_id}`;
  const beautifyRegionId = `beautify-details-${image.image_id}`;
  const processingRegionId = `processing-details-${image.image_id}`;
  const recognitionRegionId = `ai-content-recognition-${image.image_id}`;
  const hasRedactionOverlay = Boolean(
    image.beautify?.redaction?.watermark?.roi_px
      || image.beautify?.redaction?.logos?.boxes
  );
  const logoAudit = image.beautify?.redaction?.logos;
  const groundFilmAudit = image.beautify?.redaction?.screening?.branded_ground_film;
  const groundFilmCoverage = typeof groundFilmAudit?.coverage_ratio === "number"
    ? groundFilmAudit.coverage_ratio
    : null;
  const logoReviewAvailable = logoAudit?.manual_review_available === true && Boolean(image.enhanced_url);
  const logoStatus = typeof logoAudit?.status === "string" ? logoAudit.status : "";
  const logoWarning = logoStatus === "failed_safe"
    ? "Logo 自动识别不可用：当前结果未伪装为已处理，请手动画框后保存。"
    : logoStatus === "not_detected"
      ? "未自动发现当家 Logo；如有漏检，可以进入人工复核手动画框。"
      : null;

  useEffect(() => {
    setAuditExpanded(false);
    setBeautifyExpanded(false);
    setProcessingExpanded(false);
    setRecognitionExpanded(false);
    setShowRedactionOverlay(false);
    setEditingLogoBoxes(false);
    setLogoEditError(null);
  }, [image.image_id]);

  async function saveLogoBoxes(boxes: LogoBox[]) {
    setSavingLogoBoxes(true);
    setLogoEditError(null);
    try {
      await api.updateLogoRedaction(jobId, image.image_id, boxes);
      await onRedactionSaved();
      setEditingLogoBoxes(false);
      setShowRedactionOverlay(true);
    } catch (error) {
      setLogoEditError(error instanceof Error ? error.message : "Logo 框保存失败");
      throw error;
    } finally {
      setSavingLogoBoxes(false);
    }
  }

  return (
    <div className="result-detail-workspace">
      <section className="formal-image-stage" aria-label="图片预览">
        <header>
          <button type="button" onClick={onBack}><ChevronLeft size={17} aria-hidden="true" />返回结果列表</button>
          <span title={image.image_id}><FileImage size={15} aria-hidden="true" />{image.image_id}</span>
        </header>
        <div className="formal-image-canvas">
          <div className="formal-image-frame">
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
            {showEnhanced && showRedactionOverlay && image.beautify?.redaction && !editingLogoBoxes && (
              <RedactionOverlay redaction={image.beautify.redaction} />
            )}
            {showEnhanced && editingLogoBoxes && image.beautify?.redaction && (
              <LogoBoxEditor
                redaction={image.beautify.redaction}
                saving={savingLogoBoxes}
                onSave={saveLogoBoxes}
                onCancel={() => { setEditingLogoBoxes(false); setLogoEditError(null); }}
              />
            )}
          </div>
        </div>
        <footer className="preview-actions">
          <button type="button" className={!showEnhanced ? "active" : ""} onClick={() => setShowEnhanced(false)}><Eye size={16} aria-hidden="true" />原图</button>
          <button type="button" className={showEnhanced ? "active" : ""} onClick={() => setShowEnhanced(true)} disabled={!image.enhanced_url}><Sparkles size={16} aria-hidden="true" />美化图</button>
          {hasRedactionOverlay && <button type="button" className={showRedactionOverlay ? "active" : ""} onClick={() => setShowRedactionOverlay((current) => !current)}><ScanSearch size={16} aria-hidden="true" />处理区域</button>}
          {logoReviewAvailable && <button type="button" className={editingLogoBoxes ? "active" : ""} onClick={() => { setShowEnhanced(true); setEditingLogoBoxes(true); setShowRedactionOverlay(false); }}><SlidersHorizontal size={16} aria-hidden="true" />复核 Logo 框</button>}
          {openUrl ? <a href={openUrl} target="_blank" rel="noreferrer"><ArrowDownToLine size={16} aria-hidden="true" />打开文件</a> : <span />}
        </footer>
        {logoWarning && <p className="redaction-status-warning"><AlertTriangle size={15} aria-hidden="true" />{logoWarning}</p>}
        {groundFilmAudit?.detected && <p className="redaction-status-warning"><AlertTriangle size={15} aria-hidden="true" />品牌地膜画面占比 {groundFilmCoverage === null ? "待确认" : `${Math.round(groundFilmCoverage * 100)}%`} · {groundFilmAudit.reason ?? "请核对识别结果"}</p>}
        {logoEditError && <p className="redaction-status-warning"><CircleX size={15} aria-hidden="true" />{logoEditError}</p>}
      </section>

      <aside className="formal-result-inspector" aria-label="结果详情">
        <header className="formal-result-heading">
          <div>
            <span className={`decision-badge ${image.decision}`}>{resultStatusTitle(image, taggingResult)}</span>
            <h2>{image.image_id}</h2>
            <p>{resultStatusDetail(image, taggingResult)}</p>
          </div>
          <SimilarityScore result={taggingResult} />
        </header>

      {image.decision === "rejected" ? (
        <RejectedResult reasons={preciseReasons} />
      ) : image.decision === "failed" ? (
        <FailedResult reasons={preciseReasons} onRetry={() => onRetry(image.image_id)} />
      ) : (
        <SimilarityMatch result={taggingResult} />
      )}

      <OutcomeSummary image={image} result={taggingResult} />

      {image.classification ? (
        <div className="classification-detail">
          <div><span>命中分类标准</span><strong>{image.classification.standard_name}</strong><em>分类置信度 {Math.round(image.classification.confidence * 100)}%</em></div>
          {image.classification.review_required && <b>建议人工复核</b>}
        </div>
      ) : image.completion && <div className="completion-detail"><strong>{image.completion.label === "completed" ? "完工" : "非完工"} · {Math.round(image.completion.confidence * 100)}%</strong><p>{image.completion.reason}</p>{image.completion.review_required && <span>建议人工复核</span>}</div>}
      {(image.classification?.content_analysis || image.ai_tags) && (
        <AIContentRecognitionDetail
          analysis={image.classification?.content_analysis}
          legacyResult={image.ai_tags}
          expanded={recognitionExpanded}
          regionId={recognitionRegionId}
          onToggle={() => setRecognitionExpanded((current) => !current)}
        />
      )}
      {auditDimensions.length > 0 && (
        <DetailDisclosure
          className="audit-dimensions"
          title="过滤审核明细"
          summary={`${auditDimensions.length} 项${failedAuditCount > 0 ? ` · ${failedAuditCount} 项未通过` : " · 全部通过"}`}
          expanded={auditExpanded}
          regionId={auditRegionId}
          onToggle={() => setAuditExpanded((current) => !current)}
        >
          <div className="audit-dimension-list">
            {auditDimensions.map((dimension, index) => (
              <div className={`audit-dimension ${dimension.passed ? "passed" : "failed"}`} key={`${dimension.dimension}-${index}`}>
                <span>{dimension.passed ? "合格" : "不合格"}</span>
                <div><strong>{dimension.dimension}</strong><p>{dimension.reason}</p></div>
              </div>
            ))}
          </div>
        </DetailDisclosure>
      )}
      {image.beautify && (
        <BeautifyAuditDetail
          beautify={image.beautify}
          metrics={image.metrics}
          enhancedMetrics={image.enhanced_metrics}
          expanded={beautifyExpanded}
          regionId={beautifyRegionId}
          onToggle={() => setBeautifyExpanded((current) => !current)}
        />
      )}

      <DetailDisclosure
        className="processing-explanation"
        title="完整处理记录"
        summary="技术信息"
        expanded={processingExpanded}
        regionId={processingRegionId}
        onToggle={() => setProcessingExpanded((current) => !current)}
      >
        <dl className="technical-record">
          <div><dt>内部图片 ID</dt><dd>{image.image_id}</dd></div>
          <div><dt>当前阶段</dt><dd>{pipelineStageLabel(image.pipeline_stage)}</dd></div>
          <div><dt>分类</dt><dd>{stageStatusLabel(image.classification_status)}</dd></div>
          <div><dt>过滤</dt><dd>{stageStatusLabel(image.filter_status)}</dd></div>
          <div><dt>美化</dt><dd>{stageStatusLabel(image.beautify_status)}</dd></div>
          <div><dt>素材匹配</dt><dd>{matchOutcomeLabel(taggingResult, image.match_status)}</dd></div>
        </dl>
        {preciseReasons.length > 0 && (
          <div className="processing-explanation-content">
            <ul>{preciseReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          </div>
        )}
      </DetailDisclosure>
      </aside>
    </div>
  );
}

export type LogoBox = [number, number, number, number];

function RedactionOverlay({ redaction }: { redaction: NonNullable<NonNullable<ResultImage["beautify"]>["redaction"]> }) {
  const watermark = redaction.watermark;
  const logos = redaction.logos;
  const size = numericArray(watermark.image_size, 2) ?? numericArray(logos.image_size, 2);
  if (!size || size[0] <= 0 || size[1] <= 0) return null;
  const roi = numericArray(watermark.roi_px, 4);
  const boxes = Array.isArray(logos.boxes)
    ? logos.boxes.map((box) => numericArray(box, 4)).filter((box): box is number[] => box !== null)
    : [];
  const confidences = Array.isArray(logos.confidences) ? logos.confidences.map(Number) : [];
  return (
    <svg className="redaction-preview-overlay" viewBox={`0 0 ${size[0]} ${size[1]}`} preserveAspectRatio="xMidYMid meet" aria-label="图像处理区域">
      {roi && <rect className="watermark-roi" x={roi[0]} y={roi[1]} width={roi[2] - roi[0]} height={roi[3] - roi[1]} />}
      {boxes.map((box, index) => <g key={`${box.join("-")}-${index}`}>
        <rect className="logo-box" x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]} />
        <text className="logo-confidence" x={box[0]} y={Math.max(18, box[1] - 6)}>{Number.isFinite(confidences[index]) ? `Logo ${Math.round(confidences[index] * 100)}%` : "Logo"}</text>
      </g>)}
    </svg>
  );
}

type BoxInteraction = {
  kind: "draw" | "move" | "resize";
  index: number;
  startX: number;
  startY: number;
  original: LogoBox;
};

export function LogoBoxEditor({ redaction, saving, onSave, onCancel }: {
  redaction: NonNullable<NonNullable<ResultImage["beautify"]>["redaction"]>;
  saving: boolean;
  onSave: (boxes: LogoBox[]) => Promise<void>;
  onCancel: () => void;
}) {
  const logos = redaction.logos;
  const watermark = redaction.watermark;
  const sizeValue = numericArray(logos.image_size, 2) ?? numericArray(watermark.image_size, 2);
  const width = sizeValue?.[0] ?? 0;
  const height = sizeValue?.[1] ?? 0;
  const initialBoxes = Array.isArray(logos.boxes)
    ? logos.boxes.map((box) => numericArray(box, 4)).filter((box): box is number[] => box !== null).map((box) => box as LogoBox)
    : [];
  const initialKey = JSON.stringify(initialBoxes);
  const [boxes, setBoxes] = useState<LogoBox[]>(initialBoxes);
  const [selected, setSelected] = useState<number | null>(initialBoxes.length ? 0 : null);
  const [drawing, setDrawing] = useState(false);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const interaction = useRef<BoxInteraction | null>(null);

  useEffect(() => {
    const next = JSON.parse(initialKey) as LogoBox[];
    setBoxes(next);
    setSelected(next.length ? 0 : null);
  }, [initialKey]);

  if (width <= 0 || height <= 0) return null;

  function imagePoint(event: ReactPointerEvent<SVGElement>): [number, number] {
    const svg = svgRef.current;
    if (!svg) return [0, 0];
    const rect = svg.getBoundingClientRect();
    const scale = Math.min(rect.width / width, rect.height / height);
    const offsetX = (rect.width - width * scale) / 2;
    const offsetY = (rect.height - height * scale) / 2;
    return [
      Math.max(0, Math.min(width, (event.clientX - rect.left - offsetX) / scale)),
      Math.max(0, Math.min(height, (event.clientY - rect.top - offsetY) / scale)),
    ];
  }

  function beginDraw(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drawing || event.target !== event.currentTarget) {
      if (event.target === event.currentTarget) setSelected(null);
      return;
    }
    const [x, y] = imagePoint(event);
    const index = boxes.length;
    const box: LogoBox = [Math.round(x), Math.round(y), Math.round(x), Math.round(y)];
    setBoxes((current) => [...current, box]);
    setSelected(index);
    interaction.current = { kind: "draw", index, startX: x, startY: y, original: box };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function beginMove(event: ReactPointerEvent<SVGRectElement>, index: number) {
    event.stopPropagation();
    const [x, y] = imagePoint(event);
    setSelected(index);
    interaction.current = { kind: "move", index, startX: x, startY: y, original: boxes[index] };
    svgRef.current?.setPointerCapture(event.pointerId);
  }

  function beginResize(event: ReactPointerEvent<SVGCircleElement>, index: number) {
    event.stopPropagation();
    const [x, y] = imagePoint(event);
    setSelected(index);
    interaction.current = { kind: "resize", index, startX: x, startY: y, original: boxes[index] };
    svgRef.current?.setPointerCapture(event.pointerId);
  }

  function movePointer(event: ReactPointerEvent<SVGSVGElement>) {
    const active = interaction.current;
    if (!active) return;
    const [x, y] = imagePoint(event);
    setBoxes((current) => current.map((box, index) => {
      if (index !== active.index) return box;
      if (active.kind === "draw") {
        return [
          Math.round(Math.min(active.startX, x)),
          Math.round(Math.min(active.startY, y)),
          Math.round(Math.max(active.startX, x)),
          Math.round(Math.max(active.startY, y)),
        ];
      }
      if (active.kind === "resize") {
        return [
          active.original[0],
          active.original[1],
          Math.round(Math.max(active.original[0] + 8, Math.min(width, x))),
          Math.round(Math.max(active.original[1] + 8, Math.min(height, y))),
        ];
      }
      const boxWidth = active.original[2] - active.original[0];
      const boxHeight = active.original[3] - active.original[1];
      const x0 = Math.max(0, Math.min(width - boxWidth, active.original[0] + x - active.startX));
      const y0 = Math.max(0, Math.min(height - boxHeight, active.original[1] + y - active.startY));
      return [Math.round(x0), Math.round(y0), Math.round(x0 + boxWidth), Math.round(y0 + boxHeight)];
    }));
  }

  function endPointer(event: ReactPointerEvent<SVGSVGElement>) {
    const active = interaction.current;
    interaction.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (active?.kind === "draw") {
      setBoxes((current) => current.filter((box, index) => index !== active.index || (box[2] - box[0] >= 8 && box[3] - box[1] >= 8)));
      setDrawing(false);
    }
  }

  function deleteSelected() {
    if (selected === null) return;
    setBoxes((current) => current.filter((_, index) => index !== selected));
    setSelected(null);
  }

  const handleRadius = Math.max(8, Math.min(width, height) * 0.009);
  return (
    <div className="redaction-editor-shell">
      <svg
        ref={svgRef}
        className={`redaction-preview-overlay redaction-box-editor ${drawing ? "drawing" : ""}`}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        aria-label="Logo遮挡框编辑器"
        onPointerDown={beginDraw}
        onPointerMove={movePointer}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
      >
        {boxes.map((box, index) => <g key={`${index}-${box.join("-")}`}>
          <rect
            className={`logo-box editable ${selected === index ? "selected" : ""}`}
            x={box[0]}
            y={box[1]}
            width={box[2] - box[0]}
            height={box[3] - box[1]}
            onPointerDown={(event) => beginMove(event, index)}
          />
          <text className="logo-confidence" x={box[0]} y={Math.max(18, box[1] - 6)}>Logo {index + 1}</text>
          {selected === index && <circle className="resize-handle" cx={box[2]} cy={box[3]} r={handleRadius} onPointerDown={(event) => beginResize(event, index)} />}
        </g>)}
      </svg>
      <div className="redaction-editor-toolbar" role="toolbar" aria-label="Logo 框编辑操作">
        <span>{boxes.length} 个框</span>
        <button type="button" className={drawing ? "active" : ""} onClick={() => setDrawing((current) => !current)} disabled={saving}>手动画框</button>
        <button type="button" onClick={deleteSelected} disabled={saving || selected === null}><Trash2 size={14} aria-hidden="true" />删除选中</button>
        <button type="button" onClick={onCancel} disabled={saving}>取消</button>
        <button type="button" className="primary" onClick={() => void onSave(boxes).catch(() => undefined)} disabled={saving}>{saving ? <Loader2 className="spin" size={14} /> : <Check size={14} />}保存并重新生成</button>
      </div>
    </div>
  );
}

function numericArray(value: unknown, length: number): number[] | null {
  if (!Array.isArray(value) || value.length !== length) return null;
  const numbers = value.map(Number);
  return numbers.every(Number.isFinite) ? numbers : null;
}

function RejectedResult({ reasons }: { reasons: string[] }) {
  return (
    <section className="action-panel rejected-action">
      <header><CircleX size={18} aria-hidden="true" /><div><strong>未通过过滤要求</strong><span>{reasons.length ? `${reasons.length} 项关键问题需要检查` : "过滤标准未通过"}</span></div></header>
      {reasons.length > 0 && <ul>{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
    </section>
  );
}

function FailedResult({ reasons, onRetry }: { reasons: string[]; onRetry: () => void }) {
  return (
    <section className="action-panel failed-action">
      <header><AlertTriangle size={18} aria-hidden="true" /><div><strong>图片处理失败</strong><span>{reasons[0] ?? "处理服务未完成这张图片"}</span></div></header>
      <button className="retry-action" type="button" onClick={onRetry}><RefreshCw size={16} aria-hidden="true" />重试这张图片</button>
    </section>
  );
}

function failureNodeLabel(node: string): string {
  return ({
    dispatch: "任务分发",
    preprocess_queue: "预处理排队",
    preprocess: "上传校验与预处理",
    classification: "标准分类",
    filtering: "规则过滤",
    classification_and_filtering: "分类与过滤",
    ranking: "结果排序",
    beautify_planning: "AI 美化规划",
    beautifying: "逐图美化",
    content_analysis: "内容分析",
    embedding: "向量生成",
    matching: "素材匹配",
    job_deadline: "整任务时限"
  } as Record<string, string>)[node] ?? node;
}

function OutcomeSummary({ image, result }: { image: ResultImage; result?: SimilarityTaggingResult }) {
  const failedAuditCount = (image.audit_dimensions ?? []).filter((dimension) => !dimension.passed).length;
  const filterValue = failedAuditCount > 0
    ? `${failedAuditCount} 项未通过`
    : image.filter_status === "completed" || image.decision === "selected" || image.decision === "not_selected"
      ? "全部通过"
      : stageStatusLabel(image.filter_status);
  const filterTone = failedAuditCount > 0 || image.decision === "rejected" ? "danger" : filterValue === "全部通过" ? "success" : "muted";
  const beautifyValue = beautifyOutcomeLabel(image);
  const beautifyTone = image.decision === "failed" ? "danger" : image.decision === "rejected" || beautifyValue === "未执行" ? "muted" : "success";
  const matchValue = matchOutcomeLabel(result, image.match_status);
  const matchTone = result?.decision === "matched" ? "success" : "muted";
  const classificationValue = classificationOutcomeLabel(image);
  const items = [
    { label: "分类", value: classificationValue, tone: "success", icon: <ScanSearch size={15} aria-hidden="true" /> },
    { label: "过滤", value: filterValue, tone: filterTone, icon: failedAuditCount > 0 ? <CircleX size={15} aria-hidden="true" /> : <CheckCircle2 size={15} aria-hidden="true" /> },
    { label: "美化", value: beautifyValue, tone: beautifyTone, icon: <Sparkles size={15} aria-hidden="true" /> },
    { label: "素材匹配", value: matchValue, tone: matchTone, icon: <Tag size={15} aria-hidden="true" /> }
  ];
  return <section className="outcome-summary" aria-label="处理结果摘要">{items.map((item) => <div className={item.tone} key={item.label}>{item.icon}<span>{item.label}</span><strong title={item.value}>{item.value}</strong></div>)}</section>;
}

function AIContentRecognitionDetail({
  analysis,
  legacyResult,
  expanded,
  onToggle,
  regionId
}: {
  analysis?: ClassificationContentAnalysis | null;
  legacyResult?: AIImageTags;
  expanded: boolean;
  onToggle: () => void;
  regionId: string;
}) {
  if (!analysis && legacyResult) {
    return (
      <LegacyAIContentRecognitionDetail
        result={legacyResult}
        expanded={expanded}
        regionId={regionId}
        onToggle={onToggle}
      />
    );
  }
  if (!analysis) return null;

  const recognizedCount = analysis.subjects.length + analysis.objects.length;
  const summary = `${recognizedCount} 项主体/物体 · 识别置信度 ${Math.round(analysis.confidence * 100)}%`;
  const attributeEntries = Object.entries(analysis.attributes).filter(([, values]) => values.length > 0);
  const overview = buildDetailedRecognitionOverview({
    summary: analysis.summary,
    scene: analysis.scene,
    spaces: analysis.spaces,
    view: analysis.view,
    conditions: analysis.visible_conditions
  });

  return (
    <DetailDisclosure
      className="classification-content"
      title="AI 内容识别"
      summary={summary}
      expanded={expanded}
      regionId={regionId}
      onToggle={onToggle}
    >
      <div className="recognition-summary">
        <span>识别概览</span>
        <strong>{overview}</strong>
      </div>

      <dl className="recognition-facts">
        <div><dt>内容类型</dt><dd>{analysis.content_type}</dd></div>
        <div><dt>可见场景</dt><dd>{analysis.scene}</dd></div>
        <div><dt>拍摄视角</dt><dd>{analysis.view}</dd></div>
        <div><dt>识别置信度</dt><dd>{Math.round(analysis.confidence * 100)}%</dd></div>
      </dl>

      <RecognitionChips title="可见空间或区域" items={analysis.spaces} emptyText="未识别到明确空间或区域" />
      <RecognitionChips title="主要主体" items={analysis.subjects} emptyText="未识别到明确主体" />
      <RecognitionChips title="可见物体" items={analysis.objects} emptyText="未识别到明确物体" />

      {analysis.visible_conditions.length > 0 && (
        <RecognitionEvidence title="可见状态" items={analysis.visible_conditions} tone="neutral" />
      )}

      {attributeEntries.length > 0 && (
        <section className="recognition-group recognition-attributes">
          <h3>视觉属性</h3>
          <dl>
            {attributeEntries.map(([name, values]) => (
              <div key={name}><dt>{name}</dt><dd>{values.join("、")}</dd></div>
            ))}
          </dl>
        </section>
      )}

      <div className="recognition-evidence-grid">
        <RecognitionEvidence title="支持当前分类的证据" items={analysis.supporting_evidence} tone="supporting" emptyText="未返回独立的支持证据" />
        <RecognitionEvidence title="相反证据" items={analysis.conflicting_evidence} tone="conflicting" emptyText="未发现明显相反证据" />
        <RecognitionEvidence title="当前画面未呈现的证据" items={analysis.missing_evidence} tone="missing" emptyText="没有需要补充的缺失证据" />
        <RecognitionEvidence title="不确定项" items={analysis.uncertainties} tone="uncertain" emptyText="没有需要说明的不确定项" />
      </div>

      <section className="recognition-group recognition-ocr">
        <h3>图片文字识别</h3>
        {analysis.ocr_text.length > 0 ? (
          <blockquote>{analysis.ocr_text.join(" / ")}</blockquote>
        ) : (
          <p className="recognition-empty">未识别到清晰文字</p>
        )}
      </section>
    </DetailDisclosure>
  );
}

function LegacyAIContentRecognitionDetail({
  result,
  expanded,
  onToggle,
  regionId
}: {
  result: AIImageTags;
  expanded: boolean;
  onToggle: () => void;
  regionId: string;
}) {
  const recognitionConfidenceValue = result.content_confidence
    ?? (result.source === "library" ? null : result.confidence);
  const recognitionConfidence = recognitionConfidenceValue == null
    ? null
    : Math.round(recognitionConfidenceValue * 100);
  const matchConfidence = result.source === "library" && result.confidence != null
    ? Math.round(result.confidence * 100)
    : null;
  const statusLabel = result.status === "completed" ? "识别完成" : result.status === "failed" ? "识别失败" : "正在识别";
  const summary = recognitionConfidence == null ? statusLabel : `${statusLabel} · 内容置信度 ${recognitionConfidence}%`;
  const normalizedTags = Array.from(new Set(result.tags.filter(Boolean)));
  const normalizedTagKey = [...normalizedTags].sort().join("\u0000");
  const categoryEntries = Object.entries(result.categories).filter(([, items]) => {
    const values = Array.from(new Set(items.filter(Boolean)));
    return values.length > 0 && [...values].sort().join("\u0000") !== normalizedTagKey;
  });
  const overview = result.summary
    ? buildDetailedRecognitionOverview({
        summary: result.summary,
        scene: result.scene,
        spaces: result.space ? [result.space] : [],
        view: result.view,
        conditions: result.condition ? [result.condition] : []
      })
    : result.error_message
      ?? (result.status === "pending" ? "AI 正在分析图片内容" : "本次识别未返回内容概览");
  const features = Object.entries(result.features ?? {}).filter(([, values]) => values.length > 0);
  const attributes = Object.entries(result.attributes ?? {}).filter(([, values]) => values.length > 0);
  const subjects = result.subjects ?? [];
  const objects = result.objects ?? [];
  const ocrText = result.ocr_text ?? [];

  return (
    <DetailDisclosure
      className={`classification-content legacy-recognition ${result.status}`}
      title="AI 内容识别"
      summary={summary}
      expanded={expanded}
      regionId={regionId}
      onToggle={onToggle}
    >
      <div className={`recognition-summary ${result.status}`}>
        <span>识别概览</span>
        <strong>{overview}</strong>
      </div>

      <dl className="recognition-facts">
        <div><dt>内容类型</dt><dd>{result.content_type || "未明确"}</dd></div>
        <div><dt>内容置信度</dt><dd>{recognitionConfidence == null ? "--" : `${recognitionConfidence}%`}</dd></div>
        <div><dt>可见主体/物体</dt><dd>{subjects.length + objects.length} 项</dd></div>
        <div><dt>风险提示</dt><dd>{result.risks.length} 项</dd></div>
      </dl>

      {(result.scene || result.space || result.view || result.condition) && (
        <section className="recognition-group recognition-narrative">
          <h3>场景与现场状态</h3>
          <dl>
            {result.scene && <div><dt>可见场景</dt><dd>{result.scene}</dd></div>}
            {result.space && <div><dt>空间区域</dt><dd>{result.space}</dd></div>}
            {result.view && <div><dt>拍摄视角</dt><dd>{result.view}</dd></div>}
            {result.condition && <div><dt>现场状态</dt><dd>{result.condition}</dd></div>}
          </dl>
        </section>
      )}

      <RecognitionChips title="主要主体" items={subjects} emptyText="未识别到明确主体" />
      <RecognitionChips title="可见物体、材料与工具" items={objects} emptyText="未识别到明确物体" />
      {features.length > 0 && <RecognitionMapping title="现场内容特征" entries={features} />}
      {attributes.length > 0 && <RecognitionMapping title="视觉属性" entries={attributes} />}

      {normalizedTags.length > 0 && (
        <RecognitionChips title="素材库匹配标签" items={normalizedTags} emptyText="未匹配到素材库标签" />
      )}
      {result.candidate_tags.length > 0 && (
        <RecognitionChips title="待人工确认标签" items={result.candidate_tags} emptyText="没有待确认标签" />
      )}
      {categoryEntries.length > 0 && (
        <RecognitionMapping title="素材标签分类" entries={categoryEntries} />
      )}
      {result.risks.length > 0 && (
        <RecognitionEvidence title="风险提示" items={result.risks} tone="conflicting" />
      )}
      <section className="recognition-group recognition-ocr">
        <h3>图片文字识别</h3>
        {ocrText.length > 0 ? (
          <blockquote>{ocrText.join(" / ")}</blockquote>
        ) : (
          <p className="recognition-empty">未识别到清晰文字</p>
        )}
      </section>
      {(matchConfidence != null || result.status !== "completed") && (
        <dl className="recognition-meta">
          <div><dt>识别状态</dt><dd>{statusLabel}</dd></div>
          {matchConfidence != null && <div><dt>素材匹配置信度</dt><dd>{matchConfidence}%</dd></div>}
        </dl>
      )}
      {result.error_message && result.error_message !== overview && (
        <p className="recognition-error"><AlertTriangle size={14} aria-hidden="true" />{result.error_message}</p>
      )}
    </DetailDisclosure>
  );
}

function RecognitionMapping({ title, entries }: { title: string; entries: Array<[string, string[]]> }) {
  return (
    <section className="recognition-group recognition-attributes">
      <h3>{title}</h3>
      <dl>
        {entries.map(([name, values]) => (
          <div key={name}><dt>{name}</dt><dd>{values.join("、")}</dd></div>
        ))}
      </dl>
    </section>
  );
}

function buildDetailedRecognitionOverview({
  summary,
  scene,
  spaces,
  view,
  conditions
}: {
  summary?: string | null;
  scene?: string | null;
  spaces: string[];
  view?: string | null;
  conditions: string[];
}) {
  const base = summary?.trim() ?? "";
  const context: string[] = [];
  const normalizedSpaces = Array.from(new Set(spaces.map((item) => item.trim()).filter(Boolean)));
  const normalizedConditions = Array.from(new Set(conditions.map((item) => item.trim()).filter(Boolean)));

  if (scene?.trim() && !base.includes(scene.trim())) context.push(scene.trim());
  if (normalizedSpaces.length > 0 && !normalizedSpaces.every((item) => base.includes(item))) {
    context.push(`主要空间为${normalizedSpaces.join("、")}`);
  }
  if (view?.trim() && !base.includes(view.trim())) context.push(`画面采用${view.trim()}`);

  const sentences = [base];
  if (context.length > 0) sentences.push(context.join("，"));
  const additionalConditions = normalizedConditions.filter((item) => !base.includes(item));
  if (additionalConditions.length > 0) {
    sentences.push(`现场可见状态：${additionalConditions.join("；")}`);
  }

  return sentences
    .filter(Boolean)
    .map((sentence) => /[。！？]$/.test(sentence) ? sentence : `${sentence}。`)
    .join("");
}

function RecognitionChips({ title, items, emptyText }: { title: string; items: string[]; emptyText: string }) {
  return (
    <section className="recognition-group recognition-chip-group">
      <h3>{title}</h3>
      {items.length > 0 ? (
        <div>{items.map((item, index) => <span key={`${item}-${index}`}>{item}</span>)}</div>
      ) : (
        <p className="recognition-empty">{emptyText}</p>
      )}
    </section>
  );
}

function RecognitionEvidence({
  title,
  items,
  tone,
  emptyText
}: {
  title: string;
  items: string[];
  tone: "neutral" | "supporting" | "conflicting" | "missing" | "uncertain";
  emptyText?: string;
}) {
  return (
    <section className={`recognition-evidence ${tone}`}>
      <h3>{title}</h3>
      {items.length > 0 ? (
        <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
      ) : (
        <p className="recognition-empty">{emptyText}</p>
      )}
    </section>
  );
}

function DetailDisclosure({
  children,
  className,
  expanded,
  onToggle,
  regionId,
  summary,
  title
}: {
  children: ReactNode;
  className?: string;
  expanded: boolean;
  onToggle: () => void;
  regionId: string;
  summary: string;
  title: string;
}) {
  return (
    <section className={`detail-disclosure${className ? ` ${className}` : ""}`} aria-label={title}>
      <button
        className="detail-disclosure-toggle"
        type="button"
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={onToggle}
      >
        <span className="detail-disclosure-copy"><strong>{title}</strong><small>{summary}</small></span>
        <ChevronDown size={18} aria-hidden="true" />
      </button>
      {expanded && <div className="detail-disclosure-content" id={regionId}>{children}</div>}
    </section>
  );
}

function BeautifyAuditDetail({
  beautify,
  metrics,
  enhancedMetrics,
  expanded,
  onToggle,
  regionId
}: {
  beautify: NonNullable<ResultImage["beautify"]>;
  metrics: ImageMetrics;
  enhancedMetrics?: ImageMetrics;
  expanded: boolean;
  onToggle: () => void;
  regionId: string;
}) {
  const checkLabels = {
    exposure: "曝光",
    color: "色彩",
    noise: "噪声",
    sharpening: "锐化"
  } as const;
  const acceptance = beautify.acceptance;
  const acceptanceLabel = acceptance?.status === "fallback"
    ? "已安全回退"
    : acceptance?.status === "failed"
      ? "验收未通过"
      : acceptance?.status === "passed"
        ? "验收通过"
        : "等待执行";
  const parameters = Object.entries(beautify.effective_parameters).filter(([, value]) => (
    typeof value === "boolean" ? value : typeof value === "number" && value !== 0 && value !== 1
  ));

  return (
    <DetailDisclosure
      className="beautify-audit"
      title="美化验收详情"
      summary={acceptanceLabel}
      expanded={expanded}
      regionId={regionId}
      onToggle={onToggle}
    >
      <p className="beautify-audit-reason">{beautify.reason ?? "尚未生成美化规划说明"}</p>
      <div className="beautify-checks">
          {acceptance?.checks.map((check) => (
            <span className={check.passed ? "passed" : "failed"} key={check.name}>
              {checkLabels[check.name]} {check.passed ? "通过" : "风险"}
            </span>
          ))}
        </div>
        {parameters.length > 0 && (
          <dl className="beautify-parameters">
            {parameters.map(([name, value]) => <div key={name}><dt>{beautifyParameterLabel(name)}</dt><dd>{String(value)}</dd></div>)}
          </dl>
        )}
        {beautify.corrections.length > 0 && <p className="beautify-corrections">{beautify.corrections.join("；")}</p>}
        {acceptance?.fallback_reason && <p className="beautify-fallback">{acceptance.fallback_reason}</p>}
        <div className="metrics-compare">
          <MetricList title="美化前" metrics={metrics} />
          <MetricList title="美化后" metrics={enhancedMetrics} />
        </div>
    </DetailDisclosure>
  );
}

function beautifyParameterLabel(name: string) {
  return ({
    brightness: "亮度",
    contrast: "对比度",
    color: "色彩",
    sharpness: "锐化",
    auto_white_balance: "自动白平衡",
    white_balance_strength: "白平衡强度",
    shadow_lift: "暗部提升",
    highlight_recovery: "高光保护",
    denoise_strength: "降噪",
    local_tone_strength: "局部层次",
    glare_reduction_strength: "反光抑制",
    local_clarity_strength: "局部清晰度"
  } as Record<string, string>)[name] ?? name;
}

function SimilarityMatch({ result }: { result?: ResultImage["tagging_result"] }) {
  if (!result) return null;
  if (result.decision === "matched") {
    return (
      <section className="action-panel matched-action">
        <header><Tag size={18} aria-hidden="true" /><div><strong>素材标签已匹配</strong><span>{result.message}</span></div></header>
        {result.tags.length > 0 && <div className="final-tags">{result.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
        <MatchScoreComposition result={result} />
      </section>
    );
  }

  if (result.decision === "unmatched") {
    return (
      <section className="action-panel unmatched-action">
        <header><ScanSearch size={18} aria-hidden="true" /><div><strong>没有可信的相似素材</strong><span>{result.message}</span></div></header>
        <MatchScoreComposition result={result} />
      </section>
    );
  }

  return null;
}

function MatchScoreComposition({ result }: { result: SimilarityTaggingResult }) {
  const scores: Array<readonly [string, number]> = [];
  if (result.similarity != null) scores.push(["图片向量", result.similarity]);
  if (result.feature_score != null) scores.push(["内容特征", result.feature_score]);
  if (result.final_score != null) scores.push(["综合匹配", result.final_score]);
  if (result.auto_threshold != null) scores.push(["综合分采用线", result.auto_threshold]);
  if (result.feature_auto_threshold != null) scores.push(["内容特征采用线", result.feature_auto_threshold]);
  if (!scores.length) return null;
  return <div className="score-composition" aria-label="匹配分数组成">{scores.map(([label, value]) => <span key={label}><small>{label}</small><strong>{formatSimilarityScore(value)}</strong></span>)}</div>;
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

function Pagination({ page, total, pageSize, onChange, label, unit = "张" }: { page: number; total: number; pageSize: number; onChange: (page: number) => void; label: string; unit?: string }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return <nav className="pagination" aria-label={`${label}分页`}>
    <button type="button" aria-label="上一页" disabled={page <= 0} onClick={() => onChange(page - 1)}><ChevronLeft size={16} aria-hidden="true" /></button>
    <span>第 {page + 1} / {pageCount} 页 · 共 {total} {unit}</span>
    <button type="button" aria-label="下一页" disabled={page >= pageCount - 1} onClick={() => onChange(page + 1)}><ChevronRight size={16} aria-hidden="true" /></button>
  </nav>;
}

function stageCountLabel(stage: string): string {
  return {
    waiting: "等待",
    classifying: "分类",
    filtering: "过滤",
    beautify_planning: "美化规划",
    beautifying: "美化",
    content_analysis: "内容与向量",
    matching: "匹配",
    completed: "完成",
    rejected: "淘汰",
    not_selected: "未入选",
    failed: "失败",
    cancelled: "取消"
  }[stage] ?? stage;
}

function pipelineStageLabel(stage: string): string {
  return {
    waiting: "等待校验",
    classifying: "分类识别",
    filtering: "规则过滤",
    beautify_planning: "美化规划",
    beautifying: "图片美化",
    content_analysis_and_embedding: "内容与向量并行处理",
    matching: "素材库匹配",
    completed: "处理完成",
    rejected: "过滤未通过",
    not_selected: "历史任务未入选",
    failed: "处理失败",
    cancelled: "已取消"
  }[stage] ?? stage;
}

function stageStatusLabel(status?: string | null): string {
  return {
    pending: "等待",
    queued: "已排队",
    processing: "处理中",
    completed: "完成",
    failed: "失败",
    skipped: "未执行"
  }[status ?? ""] ?? "未执行";
}

function resultStatusTitle(image: ResultImage, result?: SimilarityTaggingResult): string {
  if (image.decision === "selected") return result?.decision === "matched" ? "处理完成" : "已保留并美化";
  if (image.decision === "rejected") {
    return isInputValidationRejection(image) ? "输入校验失败" : "过滤未通过";
  }
  if (image.decision === "failed") return "处理失败";
  if (image.decision === "not_selected") return "合格未入选";
  return decisionLabel(image.decision);
}

function resultStatusDetail(image: ResultImage, result?: SimilarityTaggingResult): string {
  if (image.decision === "rejected") {
    return isInputValidationRejection(image)
      ? "图片未进入内容过滤、美化与素材匹配"
      : "图片保留供复核，未进入后续美化与素材匹配";
  }
  if (image.decision === "failed") return "当前图片未完成处理，可以单独重新执行";
  if (result?.decision === "matched") return "已匹配素材并继承素材库标签";
  if (result?.decision === "unmatched") return "已完成美化，但没有可信的素材标签";
  return pipelineStageLabel(image.pipeline_stage);
}

function isInputValidationRejection(image: ResultImage): boolean {
  return image.reject_codes?.some((code) => (
    code === "INVALID_IMAGE" || code === "IMAGE_TOO_LARGE" || code === "IMAGE_TOO_SMALL"
  )) ?? false;
}

function beautifyOutcomeLabel(image: ResultImage): string {
  if (image.decision === "rejected") return "未执行";
  const acceptance = image.beautify?.acceptance?.status;
  if (acceptance === "passed") return "验收通过";
  if (acceptance === "fallback") return "安全回退";
  if (acceptance === "failed" || image.decision === "failed") return "执行失败";
  if (image.enhanced_url) return "已生成";
  return stageStatusLabel(image.beautify_status);
}

function classificationOutcomeLabel(image: ResultImage): string {
  if (image.completion) return image.completion.label === "completed" ? "完工" : "非完工";
  const standardName = image.classification?.standard_name ?? "";
  if (standardName.includes("非完工")) return "非完工";
  if (standardName.includes("完工")) return "完工";
  return stageStatusLabel(image.classification_status);
}

function matchOutcomeLabel(result?: SimilarityTaggingResult, status?: string | null): string {
  if (result?.decision === "matched") return "已匹配";
  if (result?.decision === "unmatched") return "未匹配";
  return stageStatusLabel(status);
}

function DownloadAction({ jobId, imageIds, className, label, iconSize, expectedCount }: {
  jobId: string;
  imageIds?: string[];
  className: string;
  label: string;
  iconSize: number;
  expectedCount?: number;
}) {
  const total = expectedCount ?? 0;
  const [feedback, setFeedback] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  async function startDownload() {
    setDownloading(true);
    setFeedback(null);
    try {
      const archive = await api.downloadSelectedResultsArchive(jobId, imageIds);
      downloadResultArchive(archive, `${jobId}_enhanced_images.zip`);
      setFeedback(`已开始下载 ${total} 张美化图片压缩包。`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "准备下载文件失败。");
    } finally {
      setDownloading(false);
    }
  }

  return <div className="download-action">
    <button
      className={className}
      type="button"
      disabled={!jobId || !total || downloading}
      onClick={() => void startDownload()}
    >{downloading
      ? <Loader2 className="spin" size={iconSize} aria-hidden="true" />
      : <ArrowDownToLine size={iconSize} aria-hidden="true" />}
      {downloading ? "正在准备下载" : label}
    </button>
    {feedback && <span className="download-feedback success" role="status">{feedback}</span>}
  </div>;
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
