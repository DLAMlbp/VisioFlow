import {
  Check,
  CheckSquare,
  ChevronLeft,
  ChevronRight,
  ImagePlus,
  Images,
  Loader2,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  RotateCcw,
  Square,
  Tag,
  Trash2,
  UploadCloud,
  X
} from "lucide-react";
import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./services/api";
import type { LibraryAsset, LibraryAssetGroup, UploadItem } from "./types";
import { createClientId } from "./utils/id";

const MAX_LIBRARY_UPLOADS = 50;
const LIBRARY_PAGE_SIZE = 100;

export function LibraryWorkspace({ onMessage }: { onMessage: (message: string) => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const assetsSectionRef = useRef<HTMLElement | null>(null);
  const [groups, setGroups] = useState<LibraryAssetGroup[]>([]);
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadItems, setUploadItems] = useState<UploadItem[]>([]);
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [newGroupTags, setNewGroupTags] = useState("");
  const [editingTags, setEditingTags] = useState("");
  const [editingSortOrder, setEditingSortOrder] = useState(0);
  const [deletingGroupId, setDeletingGroupId] = useState<string | null>(null);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [deletingAssetId, setDeletingAssetId] = useState<string | null>(null);
  const [retryingFailed, setRetryingFailed] = useState(false);
  const [selectingAssets, setSelectingAssets] = useState(false);
  const [selectedAssetIds, setSelectedAssetIds] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [assetPage, setAssetPage] = useState(1);

  const selectedGroup = groups.find((group) => group.id === selectedGroupId) ?? null;
  const visibleAssets = useMemo(
    () => selectedGroupId ? assets.filter((asset) => asset.group_id === selectedGroupId) : assets,
    [assets, selectedGroupId]
  );
  const assetPageCount = Math.max(1, Math.ceil(visibleAssets.length / LIBRARY_PAGE_SIZE));
  const currentAssetPage = Math.min(assetPage, assetPageCount);
  const pageStartIndex = (currentAssetPage - 1) * LIBRARY_PAGE_SIZE;
  const pagedAssets = visibleAssets.slice(pageStartIndex, pageStartIndex + LIBRARY_PAGE_SIZE);
  const selectedAsset = visibleAssets.find((asset) => asset.id === selectedAssetId) ?? null;
  const activeGroupIds = useMemo(
    () => new Set(groups.filter((group) => group.status === "active").map((group) => group.id)),
    [groups]
  );
  const assetTotal = useMemo(
    () => groups.reduce((total, group) => total + group.asset_count, 0),
    [groups]
  );
  const activeCount = assets.filter(
    (asset) => asset.status === "active" && activeGroupIds.has(asset.group_id)
  ).length;
  const pendingCount = assets.filter((asset) => asset.status === "pending").length;
  const failedCount = visibleAssets.filter((asset) => asset.status === "failed").length;
  const canUpload = selectedGroup?.status === "active";

  useEffect(() => {
    void loadLibrary();
  }, []);

  useEffect(() => {
    if (!pendingCount) return;
    const timer = window.setInterval(() => void loadAssets(), 3000);
    return () => window.clearInterval(timer);
  }, [pendingCount]);

  useEffect(() => {
    setAssetPage(1);
    setSelectingAssets(false);
    setSelectedAssetIds(new Set());
  }, [selectedGroupId]);

  useEffect(() => {
    setAssetPage((current) => Math.min(current, assetPageCount));
  }, [assetPageCount]);

  useEffect(() => {
    if (!selectedAsset) return;
    const closePreview = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedAssetId(null);
    };
    window.addEventListener("keydown", closePreview);
    return () => window.removeEventListener("keydown", closePreview);
  }, [selectedAsset]);

  async function loadLibrary() {
    setLoading(true);
    try {
      const [nextGroups, nextAssets] = await Promise.all([
        api.getLibraryGroups(),
        api.getLibraryAssets()
      ]);
      setGroups(nextGroups);
      setAssets(nextAssets.items);
      setSelectedGroupId((current) => (
        current && nextGroups.some((group) => group.id === current) ? current : null
      ));
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "素材库加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function loadAssets() {
    try {
      setAssets((await api.getLibraryAssets()).items);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "素材状态刷新失败");
    }
  }

  function selectGroup(group: LibraryAssetGroup) {
    setSelectedGroupId(group.id);
    setAssetPage(1);
    setEditingTags(group.tags.join("，"));
    setEditingSortOrder(group.sort_order);
    setSelectedAssetId(null);
  }

  function changeAssetPage(nextPage: number) {
    setAssetPage(Math.min(Math.max(nextPage, 1), assetPageCount));
    window.requestAnimationFrame(() => {
      assetsSectionRef.current?.scrollIntoView({ block: "start" });
    });
  }

  async function createGroup() {
    const tags = parseTags(newGroupTags);
    if (!tags.length) return;
    try {
      const created = await api.createLibraryGroup({ tags });
      setCreatingGroup(false);
      setNewGroupTags("");
      await loadLibrary();
      setSelectedGroupId(created.id);
      setEditingTags(created.tags.join("，"));
      setEditingSortOrder(created.sort_order);
      onMessage("素材组已创建，可以上传多张参考图片");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "创建素材组失败");
    }
  }

  async function saveSelectedGroup() {
    if (!selectedGroup) return;
    const tags = parseTags(editingTags);
    if (!tags.length) {
      onMessage("请至少填写一个标签");
      return;
    }
    try {
      await api.updateLibraryGroup(selectedGroup.id, {
        tags,
        sort_order: editingSortOrder
      });
      await loadLibrary();
      onMessage("标签组合已更新，组内图片会共同使用这串标签");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "更新标签组合失败");
    }
  }

  async function toggleSelectedGroup() {
    if (!selectedGroup) return;
    try {
      await api.updateLibraryGroup(selectedGroup.id, {
        status: selectedGroup.status === "active" ? "disabled" : "active"
      });
      await loadLibrary();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "更新素材组状态失败");
    }
  }

  async function deleteSelectedGroup() {
    if (!selectedGroup) return;
    const assetCount = selectedGroup.asset_count;
    const consequence = assetCount
      ? `并永久删除组内全部 ${assetCount} 张图片（包括原图和缩略图）`
      : "";
    if (!window.confirm(`确认删除标签组合“${selectedGroup.tags.join("、")}”${consequence}？此操作无法恢复。`)) return;
    const groupId = selectedGroup.id;
    setDeletingGroupId(groupId);
    try {
      await api.deleteLibraryGroup(groupId);
      setSelectedGroupId(null);
      await loadLibrary();
      onMessage(assetCount ? `标签组合及组内 ${assetCount} 张图片已删除` : "标签组合已删除");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "删除素材组失败");
    } finally {
      setDeletingGroupId(null);
    }
  }

  function addFiles(files: FileList | File[]) {
    const available = MAX_LIBRARY_UPLOADS - uploadItems.length;
    const next = Array.from(files)
      .filter((file) => file.type.startsWith("image/"))
      .slice(0, available)
      .map<UploadItem>((file) => ({
        id: createClientId(),
        file,
        filename: file.name,
        fileSize: file.size,
        contentType: file.type || "image/jpeg",
        previewUrl: URL.createObjectURL(file),
        status: "ready",
        progress: 0
      }));
    setUploadItems((current) => [...current, ...next]);
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

  function removeUpload(itemId: string) {
    setUploadItems((current) => {
      const item = current.find((entry) => entry.id === itemId);
      if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
      return current.filter((entry) => entry.id !== itemId);
    });
  }

  function updateUpload(itemId: string, patch: Partial<UploadItem>) {
    setUploadItems((current) => current.map((item) => (
      item.id === itemId ? { ...item, ...patch } : item
    )));
  }

  async function uploadLibraryAssets() {
    if (!selectedGroup || !canUpload || !uploadItems.length) return;
    setUploading(true);
    let completed = 0;
    const completedIds = new Set<string>();
    for (const item of uploadItems) {
      try {
        if (!item.file) continue;
        updateUpload(item.id, { status: "presigning", progress: 1 });
        const presigned = await api.presignUpload({
          filename: item.file.name,
          content_type: item.file.type || "image/jpeg",
          file_size: item.file.size
        });
        updateUpload(item.id, { status: "uploading", progress: 2, objectKey: presigned.object_key });
        await api.uploadToStorage(
          presigned.upload_url,
          item.file,
          (progress) => updateUpload(item.id, { progress })
        );
        await api.createLibraryAsset({
          object_key: presigned.object_key,
          group_id: selectedGroup.id,
          original_filename: item.file.name
        });
        updateUpload(item.id, { status: "uploaded", progress: 100 });
        completedIds.add(item.id);
        completed += 1;
      } catch (error) {
        updateUpload(item.id, {
          status: "failed",
          error: error instanceof Error ? error.message : "上传失败"
        });
      }
    }
    setUploading(false);
    setUploadItems((current) => current.filter((item) => {
      if (!completedIds.has(item.id)) return true;
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      return false;
    }));
    await loadLibrary();
    if (completed) onMessage(`${completed} 张参考图片已加入该素材组并进入分析队列。`);
  }

  async function updateAssetStatus(asset: LibraryAsset) {
    try {
      if (asset.status === "failed") {
        await api.reindexLibraryAsset(asset.id);
        await loadAssets();
        onMessage("重复素材已重新分析，完成后会自动启用为可匹配素材。");
        return;
      }
      await api.updateLibraryAsset(asset.id, {
        status: asset.status === "disabled" ? "active" : "disabled"
      });
      await loadAssets();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "更新素材状态失败");
    }
  }

  async function moveAsset(asset: LibraryAsset, groupId: string) {
    if (groupId === asset.group_id) return;
    try {
      await api.updateLibraryAsset(asset.id, { group_id: groupId });
      await loadLibrary();
      onMessage("参考图片已移入新的标签组合");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "移动参考图片失败");
    }
  }

  async function deleteAsset(asset: LibraryAsset) {
    const filename = asset.original_filename ?? "这张素材";
    if (!window.confirm(`确认删除“${filename}”？原图会一并删除，且无法恢复。`)) return;
    setDeletingAssetId(asset.id);
    try {
      await api.deleteLibraryAsset(asset.id);
      if (selectedAssetId === asset.id) setSelectedAssetId(null);
      await loadLibrary();
      onMessage("素材已删除");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "删除素材失败");
    } finally {
      setDeletingAssetId(null);
    }
  }

  function toggleAssetSelection(assetId: string) {
    setSelectedAssetIds((current) => {
      const next = new Set(current);
      if (next.has(assetId)) next.delete(assetId);
      else next.add(assetId);
      return next;
    });
  }

  function toggleCurrentPageSelection() {
    const pageIds = pagedAssets.map((asset) => asset.id);
    const pageIsSelected = pageIds.every((assetId) => selectedAssetIds.has(assetId));
    setSelectedAssetIds((current) => {
      const next = new Set(current);
      pageIds.forEach((assetId) => pageIsSelected ? next.delete(assetId) : next.add(assetId));
      return next;
    });
  }

  function stopSelectingAssets() {
    setSelectingAssets(false);
    setSelectedAssetIds(new Set());
  }

  async function deleteSelectedAssets() {
    const assetIds = Array.from(selectedAssetIds);
    if (!assetIds.length || bulkDeleting) return;
    if (!window.confirm(`确认删除已选中的 ${assetIds.length} 张图片？原图会一并删除，且无法恢复。`)) return;
    await bulkDeleteAssets({ asset_ids: assetIds });
  }

  async function deleteAllVisibleAssets() {
    if (!visibleAssets.length || bulkDeleting) return;
    const scope = selectedGroup ? `素材组“${selectedGroup.tags.join("、")}”` : "素材库";
    if (!window.confirm(`确认删除${scope}内的全部 ${visibleAssets.length} 张图片？原图会一并删除，且无法恢复。`)) return;
    await bulkDeleteAssets({
      delete_all: true,
      ...(selectedGroupId ? { group_id: selectedGroupId } : {})
    });
  }

  async function bulkDeleteAssets(payload: { asset_ids?: string[]; delete_all?: boolean; group_id?: string }) {
    setBulkDeleting(true);
    try {
      const result = await api.bulkDeleteLibraryAssets(payload);
      if (selectedAssetId && !result.failed_asset_ids.includes(selectedAssetId)) {
        setSelectedAssetId(null);
      }
      setSelectedAssetIds(new Set(result.failed_asset_ids));
      setSelectingAssets(result.failed_count > 0);
      await loadLibrary();
      onMessage(result.failed_count
        ? `已删除 ${result.deleted_count} 张图片，${result.failed_count} 张因存储清理失败而保留，请重试。`
        : `已删除 ${result.deleted_count} 张图片。`);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "批量删除图片失败");
    } finally {
      setBulkDeleting(false);
    }
  }

  async function reindexAsset(assetId: string) {
    try {
      await api.reindexLibraryAsset(assetId);
      await loadAssets();
      onMessage("素材已进入重新分析队列");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "重新分析失败");
    }
  }

  async function reindexFailedAssets() {
    if (!failedCount || retryingFailed) return;
    const scope = selectedGroup ? "当前素材组" : "全部素材";
    if (!window.confirm(`确认将${scope}中的 ${failedCount} 张失败图片重新加入分析队列？`)) return;
    setRetryingFailed(true);
    try {
      const result = await api.reindexFailedLibraryAssets(selectedGroupId);
      await loadAssets();
      onMessage(result.queued_count
        ? `${result.queued_count} 张失败图片已重新进入分析队列。`
        : "当前范围没有需要重新分析的失败图片。");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "批量重新分析失败");
    } finally {
      setRetryingFailed(false);
    }
  }

  return (
    <section className="library-workspace" id="mainWorkspace">
      <header className="library-heading">
        <div>
          <span className="panel-kicker">REFERENCE LIBRARY</span>
          <h2>素材库管理</h2>
          <p>每个素材组由一串并列标签和多张参考图片组成；匹配任意参考图后继承整组标签。</p>
        </div>
        <div className="library-metrics" aria-label="素材库概况">
          <span><b>{groups.length}</b>素材组</span>
          <span><b>{assetTotal}</b>参考图片</span>
          <span><b>{activeCount}</b>可参与匹配</span>
          <span><b>{pendingCount}</b>分析中</span>
          <button className="tool-button" type="button" aria-label="刷新素材库" title="刷新素材库" onClick={() => void loadLibrary()}>
            <RefreshCw size={18} aria-hidden="true" />
          </button>
        </div>
      </header>

      <div className="library-layout">
        <aside className="library-groups-panel" aria-label="素材组列表">
          <div className="group-panel-heading">
            <div><strong>标签组合</strong><span>所有标签并列，无层级关系</span></div>
            <div className="group-panel-actions">
              <button
                className="group-delete-button"
                type="button"
                aria-label="删除选中的标签组合"
                title={!selectedGroup ? "请先选择标签组合" : selectedGroup.asset_count ? `删除标签组合及组内 ${selectedGroup.asset_count} 张图片` : "删除选中的标签组合"}
                disabled={!selectedGroup || deletingGroupId !== null}
                onClick={() => void deleteSelectedGroup()}
              >
                {deletingGroupId ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Trash2 size={16} aria-hidden="true" />}
              </button>
              <button className="tag-create-button" type="button" onClick={() => { setCreatingGroup(true); setNewGroupTags(""); }}>
                <Plus size={17} aria-hidden="true" />
                <span>新建素材组</span>
              </button>
            </div>
          </div>
          <button className={`group-all-row ${selectedGroupId === null ? "active" : ""}`} type="button" onClick={() => { setSelectedGroupId(null); setAssetPage(1); setSelectedAssetId(null); }}>
            <Images size={16} aria-hidden="true" /><span>全部素材</span><b>{assetTotal}</b>
          </button>

          <div className="library-group-list">
            {loading ? <div className="library-loading"><Loader2 className="spin" size={18} />正在读取素材组</div> : groups.length ? groups.map((group) => (
              <button
                className={`library-group-row ${selectedGroupId === group.id ? "active" : ""} ${group.status === "disabled" ? "disabled" : ""}`}
                type="button"
                key={group.id}
                onClick={() => selectGroup(group)}
              >
                <Tag size={15} aria-hidden="true" />
                <span className="library-group-tags">{group.tags.map((tag) => <i key={tag}>{tag}</i>)}</span>
                <b>{group.asset_count}</b>
              </button>
            )) : <p className="group-empty">还没有素材组，请先创建一串标签。</p>}
          </div>

          {creatingGroup && (
            <form className="inline-tag-form" onSubmit={(event) => { event.preventDefault(); void createGroup(); }}>
              <label htmlFor="newGroupTags">标签组合</label>
              <p className="tag-input-help">使用逗号或换行分隔多个标签</p>
              <textarea id="newGroupTags" autoFocus value={newGroupTags} onChange={(event) => setNewGroupTags(event.target.value)} maxLength={1000} placeholder="例如：客厅，现代风格，完工，明亮" />
              <div className="group-form-actions"><button type="submit" disabled={!parseTags(newGroupTags).length}><Check size={16} />创建</button><button type="button" onClick={() => setCreatingGroup(false)}><X size={16} />取消</button></div>
            </form>
          )}

          {selectedGroup && (
            <section className="selected-tag-tools">
              <div className="selected-group-preview">{selectedGroup.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
              <label htmlFor="editGroupTags">编辑整组标签</label>
              <textarea id="editGroupTags" value={editingTags} onChange={(event) => setEditingTags(event.target.value)} />
              <label htmlFor="editGroupSort">排序值</label>
              <input id="editGroupSort" className="tag-sort-input" type="number" min={0} max={100000} value={editingSortOrder} onChange={(event) => setEditingSortOrder(Math.max(0, Number(event.target.value) || 0))} />
              <div className="group-edit-actions"><button type="button" onClick={() => void saveSelectedGroup()}><Pencil size={15} />保存标签</button><button type="button" onClick={() => void toggleSelectedGroup()}><Power size={15} />{selectedGroup.status === "active" ? "停用整组" : "启用整组"}</button></div>
            </section>
          )}
        </aside>

        <div className="library-content">
          <section className="library-upload-band">
            <div className="upload-band-copy">
              <span><ImagePlus size={18} aria-hidden="true" /></span>
              <div><h3>{selectedGroup ? selectedGroup.tags.join(" · ") : "选择一个素材组"}</h3><p>上传的参考图片会完成校验、去重、图片向量生成和大模型内容特征识别。</p></div>
            </div>
            <div
              className={`library-dropzone ${dragActive ? "is-dragging" : ""} ${!canUpload ? "disabled" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); if (canUpload) setDragActive(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragActive(false)}
              onDrop={(event) => canUpload && onDrop(event)}
            >
              <UploadCloud size={22} aria-hidden="true" />
              <span>{canUpload ? "拖入多张参考图片，或从本机选择" : selectedGroup ? "该素材组已停用" : "请先在左侧选择素材组"}</span>
              <input ref={inputRef} type="file" accept="image/*" multiple onChange={onFileChange} />
              <button className="secondary-button" type="button" disabled={!canUpload} onClick={() => inputRef.current?.click()}>选择图片</button>
            </div>
            {uploadItems.length > 0 && <div className="library-upload-queue">{uploadItems.map((item) => <div key={item.id} className="queue-item">{item.previewUrl && <img src={item.previewUrl} alt={item.filename} />}<div><strong>{item.filename}</strong><span>{item.status === "failed" ? item.error : item.status === "uploaded" ? "已登记" : `${item.progress}%`}</span></div><button type="button" aria-label={`移除 ${item.filename}`} onClick={() => removeUpload(item.id)} disabled={uploading}><X size={15} /></button></div>)}</div>}
            <button className="primary-button library-upload-button" type="button" disabled={!canUpload || !uploadItems.length || uploading} onClick={() => void uploadLibraryAssets()}>{uploading ? <Loader2 className="spin" size={17} /> : <UploadCloud size={17} />}{uploading ? "正在上传并登记" : `上传 ${uploadItems.length || ""} 张参考图`}</button>
          </section>

          <section className="library-assets-section" ref={assetsSectionRef}>
            <div className="assets-heading">
              <div><h3>{selectedGroup ? "当前素材组" : "全部参考图片"}</h3><p>{visibleAssets.length ? `显示第 ${pageStartIndex + 1}-${pageStartIndex + pagedAssets.length} 张，共 ${visibleAssets.length} 张` : "0 张图片"}{selectedGroup ? `，共同标签：${selectedGroup.tags.join("、")}` : ""}</p></div>
              <div className="asset-bulk-actions">
                {selectingAssets ? <>
                  <span className="asset-selection-count">已选 <b>{selectedAssetIds.size}</b> 张</span>
                  <button className="asset-selection-button" type="button" onClick={toggleCurrentPageSelection} disabled={bulkDeleting}>
                    {pagedAssets.length > 0 && pagedAssets.every((asset) => selectedAssetIds.has(asset.id)) ? <CheckSquare size={16} aria-hidden="true" /> : <Square size={16} aria-hidden="true" />}
                    {pagedAssets.length > 0 && pagedAssets.every((asset) => selectedAssetIds.has(asset.id)) ? "取消本页" : "全选本页"}
                  </button>
                  <button className="bulk-delete-button" type="button" disabled={!selectedAssetIds.size || bulkDeleting} onClick={() => void deleteSelectedAssets()}>
                    {bulkDeleting ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Trash2 size={16} aria-hidden="true" />}
                    删除已选{selectedAssetIds.size ? ` (${selectedAssetIds.size})` : ""}
                  </button>
                  <button className="asset-selection-cancel" type="button" aria-label="退出图片选择" title="退出选择" disabled={bulkDeleting} onClick={stopSelectingAssets}><X size={17} aria-hidden="true" /></button>
                </> : <>
                  <button className="retry-failed-assets-button" type="button" disabled={!failedCount || retryingFailed || bulkDeleting} title={failedCount ? `重新分析当前范围内的 ${failedCount} 张失败图片` : "当前范围没有分析失败的图片"} onClick={() => void reindexFailedAssets()}>
                    {retryingFailed ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <RotateCcw size={16} aria-hidden="true" />}
                    {retryingFailed ? "正在加入队列" : `一键分析失败图片${failedCount ? ` (${failedCount})` : ""}`}
                  </button>
                  <button className="asset-selection-button" type="button" disabled={!visibleAssets.length || bulkDeleting} onClick={() => setSelectingAssets(true)}><CheckSquare size={16} aria-hidden="true" />选择图片</button>
                  <button className="bulk-delete-button" type="button" disabled={!visibleAssets.length || bulkDeleting} onClick={() => void deleteAllVisibleAssets()}>
                    {bulkDeleting ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Trash2 size={16} aria-hidden="true" />}
                    删除全部 ({visibleAssets.length})
                  </button>
                </>}
              </div>
            </div>
            {visibleAssets.length ? <div className="library-assets-browser">
              <div className="library-asset-grid">{pagedAssets.map((asset) => {
                const assetTags = groups.find((group) => group.id === asset.group_id)?.tags ?? asset.tags;
                return (
                  <article className={`library-asset ${selectedAssetId === asset.id ? "selected" : ""} ${selectedAssetIds.has(asset.id) ? "bulk-selected" : ""}`} key={asset.id}>
                    <div className="library-asset-preview">{asset.preview_url ? <button className="library-asset-image-button" type="button" aria-label={selectingAssets ? `${selectedAssetIds.has(asset.id) ? "取消选择" : "选择"}图片：${asset.original_filename ?? "素材图片"}` : `展示图片：${asset.original_filename ?? "素材图片"}`} onClick={() => selectingAssets ? toggleAssetSelection(asset.id) : setSelectedAssetId(asset.id)}><img src={asset.preview_url} alt={asset.original_filename ?? "素材图片"} loading="lazy" /></button> : <Images size={26} />}<span className={`asset-status ${asset.status}`}>{assetStatusLabel(asset.status)}</span>{selectingAssets && <button className={`asset-select-toggle ${selectedAssetIds.has(asset.id) ? "selected" : ""}`} type="button" aria-label={`${selectedAssetIds.has(asset.id) ? "取消选择" : "选择"} ${asset.original_filename ?? asset.id}`} onClick={() => toggleAssetSelection(asset.id)}>{selectedAssetIds.has(asset.id) ? <CheckSquare size={20} aria-hidden="true" /> : <Square size={20} aria-hidden="true" />}</button>}</div>
                    <div className="library-asset-copy"><strong title={asset.original_filename ?? asset.id}>{asset.original_filename ?? asset.id}</strong><div className="asset-tag-chips" aria-label={`全部标签，共 ${assetTags.length} 个`}>{assetTags.map((tag) => <span key={tag} title={tag}>{tag}</span>)}</div>{asset.error_message && <small>{asset.error_message}</small>}</div>
                    <div className={`library-asset-actions ${selectingAssets ? "selection-active" : ""}`}>
                      {selectingAssets ? <button className="asset-card-select-button" type="button" onClick={() => toggleAssetSelection(asset.id)}>{selectedAssetIds.has(asset.id) ? <Check size={15} aria-hidden="true" /> : <Square size={15} aria-hidden="true" />}{selectedAssetIds.has(asset.id) ? "已选择" : "选择图片"}</button> : <>
                      <select aria-label={`修改 ${asset.original_filename ?? asset.id} 所属素材组`} title="移动到其他标签组合" value={asset.group_id} onChange={(event) => void moveAsset(asset, event.target.value)}>{groups.filter((group) => group.status === "active").map((group) => <option key={group.id} value={group.id}>{group.tags.join("、")}</option>)}</select>
                      <button type="button" aria-label="重新分析素材" title="重新分析" onClick={() => void reindexAsset(asset.id)}><RotateCcw size={15} /></button>
                      <button type="button" aria-label={asset.status === "failed" ? "重新启用素材" : asset.status === "disabled" ? "启用素材" : "停用素材"} title={asset.status === "failed" ? "重新启用素材" : asset.status === "disabled" ? "启用素材" : "停用素材"} onClick={() => void updateAssetStatus(asset)} disabled={asset.status === "pending"}><Power size={15} /></button>
                      <button className="danger-icon-button" type="button" aria-label={`删除素材 ${asset.original_filename ?? asset.id}`} title="删除素材" onClick={() => void deleteAsset(asset)} disabled={deletingAssetId === asset.id}>{deletingAssetId === asset.id ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}</button>
                      </>}
                    </div>
                  </article>
                );
              })}</div>
              {assetPageCount > 1 && <nav className="library-pagination" aria-label="素材图片分页">
                <button type="button" aria-label="上一页" title="上一页" disabled={currentAssetPage === 1} onClick={() => changeAssetPage(currentAssetPage - 1)}><ChevronLeft size={18} aria-hidden="true" /></button>
                <span aria-live="polite">第 <b>{currentAssetPage}</b> / {assetPageCount} 页</span>
                <button type="button" aria-label="下一页" title="下一页" disabled={currentAssetPage === assetPageCount} onClick={() => changeAssetPage(currentAssetPage + 1)}><ChevronRight size={18} aria-hidden="true" /></button>
              </nav>}
            </div> : <div className="library-empty"><Images size={28} /><strong>{selectedGroup ? "这个素材组还没有参考图片" : "素材库还是空的"}</strong><p>{selectedGroup ? "为这串标签上传多张参考图片。" : "先创建素材组，再上传参考图片。"}</p></div>}
          </section>
        </div>
      </div>

      {selectedAsset?.preview_url && <div className="library-image-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedAssetId(null); }}>
        <section className="library-image-dialog" role="dialog" aria-modal="true" aria-labelledby="libraryImageDialogTitle">
          <header><div><strong id="libraryImageDialogTitle">{selectedAsset.original_filename ?? "素材图片"}</strong><span>完整图片</span></div><button type="button" aria-label="关闭图片" title="关闭" autoFocus onClick={() => setSelectedAssetId(null)}><X size={18} aria-hidden="true" /></button></header>
          <div className="library-image-dialog-view"><img src={selectedAsset.preview_url} alt={selectedAsset.original_filename ?? "素材图片"} /></div>
          <footer>{selectedAsset.tags.join(" · ")}</footer>
        </section>
      </div>}
    </section>
  );
}

function parseTags(value: string): string[] {
  const seen = new Set<string>();
  return value
    .split(/[，,\n]+/)
    .map((tag) => tag.trim())
    .filter((tag) => {
      const key = tag.toLocaleLowerCase();
      if (!tag || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 20);
}

function assetStatusLabel(status: LibraryAsset["status"]): string {
  return { pending: "分析中", active: "可匹配", failed: "失败", disabled: "已停用" }[status];
}
