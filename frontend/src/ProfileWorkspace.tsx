import { Copy, Loader2, Plus, Save, Trash2, WandSparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./services/api";
import type { ProcessingProfile, ProcessingProfileType, ProfileOption, ProfilePreview } from "./types";

interface ProfileWorkspaceProps {
  onMessage: (message: string) => void;
  onProfilesChanged: () => Promise<void>;
}

export function ProfileWorkspace({ onMessage, onProfilesChanged }: ProfileWorkspaceProps) {
  const [type, setType] = useState<ProcessingProfileType>("filter");
  const [profiles, setProfiles] = useState<ProfileOption[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instruction, setInstruction] = useState("");
  const [version, setVersion] = useState<number | null>(null);
  const [preview, setPreview] = useState<ProfilePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  async function loadList(nextType = type, preferredId?: string | null) {
    setLoading(true);
    try {
      const items = nextType === "filter" ? await api.getFilterProfiles() : await api.getBeautifyProfiles();
      setProfiles(items);
      const nextId = preferredId === null ? null : preferredId ?? items[0]?.id ?? null;
      if (nextId) await openProfile(nextType, nextId);
      else startNew();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "读取标准失败");
    } finally {
      setLoading(false);
    }
  }

  async function openProfile(nextType: ProcessingProfileType, profileId: string) {
    setBusy(true);
    try {
      const detail = await api.getProcessingProfile(nextType, profileId);
      setSelectedId(detail.id);
      setName(detail.name);
      setInstruction(detail.instruction);
      setVersion(detail.version);
      setPreview({ description: detail.description, config: detail.config, unsupported: [], can_save: true });
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "读取标准详情失败");
    } finally {
      setBusy(false);
    }
  }

  function startNew(copy?: ProcessingProfile) {
    setSelectedId(null);
    setName(copy ? `${copy.name} 副本` : "");
    setInstruction(copy?.instruction ?? "");
    setVersion(null);
    setPreview(copy ? { description: copy.description, config: copy.config, unsupported: [], can_save: true } : null);
  }

  async function copyCurrent() {
    if (!selectedId) return;
    try {
      startNew(await api.getProcessingProfile(type, selectedId));
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "复制标准失败");
    }
  }

  async function generatePreview() {
    if (instruction.trim().length < 3) {
      onMessage("请先输入至少 3 个字的处理要求");
      return;
    }
    setBusy(true);
    try {
      setPreview(await api.previewProcessingProfile(type, instruction.trim()));
      onMessage("处理要求已解析，可以保存");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "解析处理要求失败");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!name.trim() || !preview) {
      onMessage("请填写名称并解析处理要求");
      return;
    }
    setBusy(true);
    try {
      const saved = await api.saveProcessingProfile(type, selectedId, {
        name: name.trim(),
        instruction: instruction.trim(),
        description: preview.description,
        config: preview.config,
        expected_version: version ?? undefined
      });
      await onProfilesChanged();
      await loadList(type, saved.id);
      onMessage(selectedId ? "标准已更新，新任务将使用新版本" : "标准已创建");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "保存标准失败");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selectedId || !window.confirm(`确认停用“${name}”？历史任务不会受影响。`)) return;
    setBusy(true);
    try {
      await api.deleteProcessingProfile(type, selectedId);
      await onProfilesChanged();
      await loadList(type);
      onMessage("标准已停用");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "停用标准失败");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void loadList(type); }, [type]);

  return (
    <section className="profile-workspace" id="mainWorkspace" aria-label="标准管理">
      <header className="profile-heading">
        <div><span className="panel-kicker">PROCESSING RULES</span><h2>标准管理</h2><p>用自然语言创建可复用的过滤和美化标准。</p></div>
        <button className="primary-button" type="button" onClick={() => startNew()}><Plus size={16} />新增标准</button>
      </header>

      <div className="profile-tabs" role="tablist" aria-label="标准类型">
        <button role="tab" aria-selected={type === "filter"} className={type === "filter" ? "active" : ""} onClick={() => setType("filter")}>过滤标准</button>
        <button role="tab" aria-selected={type === "beautify"} className={type === "beautify" ? "active" : ""} onClick={() => setType("beautify")}>美化标准</button>
      </div>

      <div className="profile-layout">
        <aside className="profile-list" aria-label={`${type === "filter" ? "过滤" : "美化"}标准列表`}>
          {loading ? <p className="profile-muted"><Loader2 className="spin" size={16} />正在读取</p> : profiles.length ? profiles.map((item) => (
            <button key={item.id} type="button" className={selectedId === item.id ? "active" : ""} onClick={() => void openProfile(type, item.id)}>
              <strong>{item.name}</strong><span>版本 {item.version ?? 1}</span><p>{item.description}</p>
            </button>
          )) : <p className="profile-empty">暂无标准，请新建后再处理图片。</p>}
        </aside>

        <section className="profile-editor" aria-label="标准编辑器">
          <div className="profile-editor-heading">
            <div><h3>{selectedId ? "编辑标准" : "新建标准"}</h3><p>{type === "filter" ? "用自然语言设置 AI 图片过滤要求" : "用自然语言设置 AI 图片美化要求"}</p></div>
            {selectedId && <div className="profile-icon-actions"><button type="button" title="复制" aria-label="复制标准" onClick={() => void copyCurrent()}><Copy size={16} /></button><button type="button" title="停用" aria-label="停用标准" onClick={() => void remove()}><Trash2 size={16} /></button></div>}
          </div>

          <label className="profile-field">标准名称<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder={type === "filter" ? "例如：工地照片严格筛选" : "例如：室内照片明亮增强"} /></label>
          <label className="profile-field">处理要求<textarea value={instruction} maxLength={2000} rows={6} onChange={(event) => { setInstruction(event.target.value); setPreview(null); }} placeholder={type === "filter" ? "例如：过滤宽度小于 1280、严重模糊和大面积过暗的图片" : "例如：适度提亮，压低高光，轻微降噪，保持颜色自然"} /></label>
          <button className="profile-generate" type="button" disabled={busy} onClick={() => void generatePreview()}>{busy ? <Loader2 className="spin" size={16} /> : <WandSparkles size={16} />}{busy ? "正在解析" : "解析处理要求"}</button>

          <div className="profile-save-row"><button className="primary-button" type="button" disabled={busy || !preview} onClick={() => void save()}><Save size={16} />保存标准</button></div>
        </section>
      </div>
    </section>
  );
}
