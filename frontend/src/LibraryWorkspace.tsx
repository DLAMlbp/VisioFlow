import {
  Check,
  ChevronDown,
  ChevronRight,
  FolderPlus,
  ImagePlus,
  Images,
  Loader2,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  RotateCcw,
  Trash2,
  UploadCloud,
  X
} from "lucide-react";
import { ChangeEvent, CSSProperties, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./services/api";
import type { LibraryAsset, LibraryTagNode, UploadItem } from "./types";

const MAX_LIBRARY_UPLOADS = 50;

export function LibraryWorkspace({ onMessage }: { onMessage: (message: string) => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [tree, setTree] = useState<LibraryTagNode[]>([]);
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadItems, setUploadItems] = useState<UploadItem[]>([]);
  const [addingParentId, setAddingParentId] = useState<string | null | undefined>(undefined);
  const [newTagName, setNewTagName] = useState("");
  const [editingName, setEditingName] = useState("");
  const [editingSortOrder, setEditingSortOrder] = useState(0);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);

  const flatNodes = useMemo(() => flattenTree(tree), [tree]);
  const selectedNode = flatNodes.find((node) => node.id === selectedNodeId) ?? null;
  const selectedPath = selectedNode ? findPath(tree, selectedNode.id) : [];
  const selectedIsLeaf = Boolean(selectedNode && !selectedNode.children.some((child) => child.status === "active"));
  const enabledNodeIds = useMemo(() => collectEnabledNodeIds(tree), [tree]);
  const leafOptions = flatNodes.filter((node) => enabledNodeIds.has(node.id) && !node.children.some((child) => child.status === "active"));
  const visibleAssets = useMemo(() => {
    if (!selectedPath.length) return assets;
    return assets.filter((asset) => selectedPath.every((part, index) => asset.tag_path[index] === part));
  }, [assets, selectedPath]);
  const selectedAsset = visibleAssets.find((asset) => asset.id === selectedAssetId) ?? null;
  const activeCount = assets.filter((asset) => asset.status === "active" && enabledNodeIds.has(asset.leaf_tag_node_id)).length;
  const pendingCount = assets.filter((asset) => asset.status === "pending").length;

  useEffect(() => {
    void loadLibrary();
  }, []);

  useEffect(() => {
    if (!pendingCount) return;
    const timer = window.setInterval(() => void loadAssets(), 3000);
    return () => window.clearInterval(timer);
  }, [pendingCount]);

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
      const [nextTree, nextAssets] = await Promise.all([api.getLibraryTagTree(), api.getLibraryAssets()]);
      setTree(nextTree);
      setAssets(nextAssets.items);
      setExpandedIds(new Set(flattenTree(nextTree).filter((node) => node.children.length).map((node) => node.id)));
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

  function selectNode(node: LibraryTagNode) {
    setSelectedNodeId(node.id);
    setEditingName(node.name);
    setEditingSortOrder(node.sort_order);
    if (node.children.length) {
      setExpandedIds((current) => new Set(current).add(node.id));
    }
  }

  function toggleExpanded(nodeId: string) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  }

  async function createTag() {
    const name = newTagName.trim();
    if (!name) return;
    try {
      const created = await api.createLibraryTagNode({ name, parent_id: addingParentId ?? null });
      setNewTagName("");
      setAddingParentId(undefined);
      setSelectedNodeId(created.id);
      await loadLibrary();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "创建标签失败");
    }
  }

  async function saveSelectedNode() {
    if (!selectedNode || !editingName.trim()) return;
    try {
      await api.updateLibraryTagNode(selectedNode.id, {
        name: editingName.trim(),
        sort_order: editingSortOrder
      });
      await loadLibrary();
      onMessage("标签名称已更新");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "更新标签失败");
    }
  }

  async function toggleSelectedNode() {
    if (!selectedNode) return;
    try {
      await api.updateLibraryTagNode(selectedNode.id, {
        status: selectedNode.status === "active" ? "disabled" : "active"
      });
      await loadLibrary();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "更新标签状态失败");
    }
  }

  async function deleteSelectedNode() {
    if (!selectedNode) return;
    if (selectedNode.children.length) {
      onMessage("该标签包含下级标签，请先处理下级标签后再删除");
      return;
    }
    if (selectedNode.asset_count) {
      onMessage(`该标签关联 ${selectedNode.asset_count} 张素材，请先将素材移到其他标签后再删除`);
      return;
    }
    if (!window.confirm(`确认删除标签“${selectedNode.name}”？`)) return;
    try {
      await api.deleteLibraryTagNode(selectedNode.id);
      setSelectedNodeId(null);
      await loadLibrary();
      onMessage("标签已删除");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "删除标签失败");
    }
  }

  function addFiles(files: FileList | File[]) {
    const available = MAX_LIBRARY_UPLOADS - uploadItems.length;
    const next = Array.from(files)
      .filter((file) => file.type.startsWith("image/"))
      .slice(0, available)
      .map<UploadItem>((file) => ({
        id: crypto.randomUUID(),
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
    setUploadItems((current) => current.map((item) => item.id === itemId ? { ...item, ...patch } : item));
  }

  async function uploadLibraryAssets() {
    if (!selectedNode || !selectedIsLeaf || !uploadItems.length) return;
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
        await api.uploadToStorage(presigned.upload_url, item.file, (progress) => updateUpload(item.id, { progress }));
        await api.createLibraryAsset({
          object_key: presigned.object_key,
          leaf_tag_node_id: selectedNode.id,
          original_filename: item.file.name
        });
        updateUpload(item.id, { status: "uploaded", progress: 100 });
        completedIds.add(item.id);
        completed += 1;
      } catch (error) {
        updateUpload(item.id, { status: "failed", error: error instanceof Error ? error.message : "上传失败" });
      }
    }
    setUploading(false);
    setUploadItems((current) => current.filter((item) => {
      if (!completedIds.has(item.id)) return true;
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      return false;
    }));
    await loadLibrary();
    if (completed) onMessage(`${completed} 张素材已进入分析队列，不会经过过滤或美化。`);
  }

  async function updateAssetStatus(asset: LibraryAsset) {
    try {
      await api.updateLibraryAsset(asset.id, { status: asset.status === "disabled" ? "active" : "disabled" });
      await loadAssets();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "更新素材状态失败");
    }
  }

  async function moveAsset(asset: LibraryAsset, leafTagNodeId: string) {
    if (leafTagNodeId === asset.leaf_tag_node_id) return;
    try {
      await api.updateLibraryAsset(asset.id, { leaf_tag_node_id: leafTagNodeId });
      await loadLibrary();
      onMessage("素材标签路径已更新");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "调整素材路径失败");
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

  return (
    <section className="library-workspace" id="mainWorkspace">
      <header className="library-heading">
        <div>
          <span className="panel-kicker">REFERENCE LIBRARY</span>
          <h2>素材库管理</h2>
          <p>用标签路径整理参考图片。业务图片只会匹配这里已启用的素材。</p>
        </div>
        <div className="library-metrics" aria-label="素材库概况">
          <span><b>{assets.length}</b>全部素材</span>
          <span><b>{activeCount}</b>可参与匹配</span>
          <span><b>{pendingCount}</b>分析中</span>
          <button className="tool-button" type="button" aria-label="刷新素材库" title="刷新素材库" onClick={() => void loadLibrary()}>
            <RefreshCw size={18} aria-hidden="true" />
          </button>
        </div>
      </header>

      <div className="library-layout">
        <aside className="tag-tree-panel" aria-label="素材标签树">
          <div className="tree-panel-heading">
            <div><strong>标签路径</strong><span>支持任意层级</span></div>
            <button className="tag-create-button" type="button" aria-label="新增根标签" onClick={() => { setAddingParentId(null); setNewTagName(""); }}>
              <FolderPlus size={17} aria-hidden="true" />
              <span>新增根标签</span>
            </button>
          </div>
          <button className={`tree-all-row ${selectedNodeId === null ? "active" : ""}`} type="button" onClick={() => setSelectedNodeId(null)}>
            <Images size={16} aria-hidden="true" /><span>全部素材</span><b>{assets.length}</b>
          </button>
          <div className="tag-tree">
            {loading ? <div className="library-loading"><Loader2 className="spin" size={18} />正在读取标签</div> : tree.length ? tree.map((node) => (
              <TagTreeRow key={node.id} node={node} selectedId={selectedNodeId} expandedIds={expandedIds} onSelect={selectNode} onToggle={toggleExpanded} />
            )) : <p className="tree-empty">还没有标签，先创建一个根标签。</p>}
          </div>

          {addingParentId !== undefined && (
            <form className="inline-tag-form" onSubmit={(event) => { event.preventDefault(); void createTag(); }}>
              <label htmlFor="newTagName">{addingParentId ? "子标签名称" : "根标签名称"}</label>
              <div><input id="newTagName" autoFocus value={newTagName} onChange={(event) => setNewTagName(event.target.value)} maxLength={120} /><button type="submit" aria-label="保存标签" disabled={!newTagName.trim()}><Check size={16} /></button><button type="button" aria-label="取消新增" onClick={() => setAddingParentId(undefined)}><X size={16} /></button></div>
            </form>
          )}

          {selectedNode && (
            <section className="selected-tag-tools">
              <div className="selected-path">{selectedPath.join(" / ")}</div>
              <label htmlFor="editTagName">标签名称</label>
              <div className="rename-row"><input id="editTagName" value={editingName} onChange={(event) => setEditingName(event.target.value)} /><button type="button" aria-label="保存标签设置" title="保存标签设置" onClick={() => void saveSelectedNode()}><Pencil size={15} /></button></div>
              <label htmlFor="editTagSort">排序值</label>
              <input id="editTagSort" className="tag-sort-input" type="number" min={0} max={100000} value={editingSortOrder} onChange={(event) => setEditingSortOrder(Math.max(0, Number(event.target.value) || 0))} />
              <div className="tree-actions"><button type="button" onClick={() => { setAddingParentId(selectedNode.id); setNewTagName(""); }} disabled={selectedNode.status !== "active"}><Plus size={15} />添加下一级</button><button type="button" onClick={() => void toggleSelectedNode()}><Power size={15} />{selectedNode.status === "active" ? "停用" : "启用"}</button><button type="button" aria-label="删除标签" title={selectedNode.children.length ? "该标签包含下级标签" : selectedNode.asset_count ? `该标签关联 ${selectedNode.asset_count} 张素材` : "删除标签"} onClick={() => void deleteSelectedNode()}><Trash2 size={15} /></button></div>
            </section>
          )}
        </aside>

        <div className="library-content">
          <section className="library-upload-band">
            <div className="upload-band-copy">
              <span><ImagePlus size={18} aria-hidden="true" /></span>
              <div><h3>{selectedPath.length ? selectedPath.join(" / ") : "选择最末一级标签"}</h3><p>素材原图直接入库，只做解码校验、去重、内容分析和向量生成，不做过滤或美化。</p></div>
            </div>
            <div
              className={`library-dropzone ${dragActive ? "is-dragging" : ""} ${!selectedIsLeaf ? "disabled" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); if (selectedIsLeaf) setDragActive(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragActive(false)}
              onDrop={(event) => selectedIsLeaf && onDrop(event)}
            >
              <UploadCloud size={22} aria-hidden="true" />
              <span>{selectedIsLeaf ? "拖入图片，或从本机选择" : selectedNode ? "该标签还有下一级，请选择末级标签" : "请先在左侧选择末级标签"}</span>
              <input ref={inputRef} type="file" accept="image/*" multiple onChange={onFileChange} />
              <button className="secondary-button" type="button" disabled={!selectedIsLeaf} onClick={() => inputRef.current?.click()}>选择图片</button>
            </div>
            {uploadItems.length > 0 && <div className="library-upload-queue">{uploadItems.map((item) => <div key={item.id} className="queue-item">{item.previewUrl && <img src={item.previewUrl} alt={item.filename} />}<div><strong>{item.filename}</strong><span>{item.status === "failed" ? item.error : item.status === "uploaded" ? "已登记" : `${item.progress}%`}</span></div><button type="button" aria-label={`移除 ${item.filename}`} onClick={() => removeUpload(item.id)} disabled={uploading}><X size={15} /></button></div>)}</div>}
            <button className="primary-button library-upload-button" type="button" disabled={!selectedIsLeaf || !uploadItems.length || uploading} onClick={() => void uploadLibraryAssets()}>{uploading ? <Loader2 className="spin" size={17} /> : <UploadCloud size={17} />}{uploading ? "正在上传并登记" : `上传 ${uploadItems.length || ""} 张素材`}</button>
          </section>

          <section className="library-assets-section">
            <div className="assets-heading"><div><h3>{selectedPath.length ? selectedPath[selectedPath.length - 1] : "全部素材"}</h3><p>{visibleAssets.length} 张图片</p></div></div>
            {visibleAssets.length ? <div className="library-assets-browser">
              <div className="library-asset-grid">{visibleAssets.map((asset) => (
                <article className={`library-asset ${selectedAssetId === asset.id ? "selected" : ""}`} key={asset.id}>
                  <div className="library-asset-preview">{asset.preview_url ? <button className="library-asset-image-button" type="button" aria-label={`展示图片：${asset.original_filename ?? "素材图片"}`} onClick={() => setSelectedAssetId(asset.id)}><img src={asset.preview_url} alt={asset.original_filename ?? "素材图片"} loading="lazy" /></button> : <Images size={26} />}<span className={`asset-status ${asset.status}`}>{assetStatusLabel(asset.status)}</span></div>
                  <div className="library-asset-copy"><strong title={asset.original_filename ?? asset.id}>{asset.original_filename ?? asset.id}</strong><p>{asset.tag_path.join(" / ")}</p>{asset.error_message && <small>{asset.error_message}</small>}</div>
                  <div className="library-asset-actions"><select aria-label={`修改 ${asset.original_filename ?? asset.id} 所属标签`} title="调整素材标签路径" value={asset.leaf_tag_node_id} onChange={(event) => void moveAsset(asset, event.target.value)}>{leafOptions.map((node) => <option key={node.id} value={node.id}>{findPath(tree, node.id).join(" / ")}</option>)}</select><button type="button" aria-label="重新分析素材" title="重新分析" onClick={() => void reindexAsset(asset.id)}><RotateCcw size={15} /></button><button type="button" aria-label={asset.status === "disabled" ? "启用素材" : "停用素材"} title={asset.status === "disabled" ? "启用素材" : "停用素材"} onClick={() => void updateAssetStatus(asset)} disabled={asset.status === "pending" || asset.status === "failed"}><Power size={15} /></button></div>
                </article>
              ))}</div>
            </div> : <div className="library-empty"><Images size={28} /><strong>当前路径还没有素材</strong><p>选择末级标签后上传参考图片。</p></div>}
          </section>
        </div>
      </div>

      {selectedAsset?.preview_url && <div className="library-image-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedAssetId(null); }}>
        <section className="library-image-dialog" role="dialog" aria-modal="true" aria-labelledby="libraryImageDialogTitle">
          <header><div><strong id="libraryImageDialogTitle">{selectedAsset.original_filename ?? "素材图片"}</strong><span>完整图片</span></div><button type="button" aria-label="关闭图片" title="关闭" autoFocus onClick={() => setSelectedAssetId(null)}><X size={18} aria-hidden="true" /></button></header>
          <div className="library-image-dialog-view"><img src={selectedAsset.preview_url} alt={selectedAsset.original_filename ?? "素材图片"} /></div>
          <footer>{selectedAsset.tag_path.join(" / ")}</footer>
        </section>
      </div>}

    </section>
  );
}

function TagTreeRow({ node, selectedId, expandedIds, onSelect, onToggle }: { node: LibraryTagNode; selectedId: string | null; expandedIds: Set<string>; onSelect: (node: LibraryTagNode) => void; onToggle: (nodeId: string) => void }) {
  const expanded = expandedIds.has(node.id);
  return <div className={`tree-branch ${node.status === "disabled" ? "disabled" : ""}`}>
    <div className={`tree-row ${selectedId === node.id ? "active" : ""}`} style={{ "--tree-depth": node.depth } as CSSProperties}>
      <button className="tree-toggle" type="button" aria-label={expanded ? `收起 ${node.name}` : `展开 ${node.name}`} onClick={() => onToggle(node.id)} disabled={!node.children.length}>{node.children.length ? expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} /> : <span />}</button>
      <button className="tree-select" type="button" onClick={() => onSelect(node)}><span>{node.name}</span><b>{node.asset_count}</b></button>
    </div>
    {expanded && node.children.map((child) => <TagTreeRow key={child.id} node={child} selectedId={selectedId} expandedIds={expandedIds} onSelect={onSelect} onToggle={onToggle} />)}
  </div>;
}

function flattenTree(nodes: LibraryTagNode[]): LibraryTagNode[] {
  return nodes.flatMap((node) => [node, ...flattenTree(node.children)]);
}

function findPath(nodes: LibraryTagNode[], targetId: string, parents: string[] = []): string[] {
  for (const node of nodes) {
    const path = [...parents, node.name];
    if (node.id === targetId) return path;
    const childPath = findPath(node.children, targetId, path);
    if (childPath.length) return childPath;
  }
  return [];
}

function collectEnabledNodeIds(nodes: LibraryTagNode[], ancestorsEnabled = true): Set<string> {
  const result = new Set<string>();
  for (const node of nodes) {
    const enabled = ancestorsEnabled && node.status === "active";
    if (enabled) result.add(node.id);
    for (const id of collectEnabledNodeIds(node.children, enabled)) result.add(id);
  }
  return result;
}

function assetStatusLabel(status: LibraryAsset["status"]): string {
  return { pending: "分析中", active: "可匹配", failed: "失败", disabled: "已停用" }[status];
}
