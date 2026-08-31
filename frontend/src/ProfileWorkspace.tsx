import { Copy, Loader2, Plus, Save, Trash2, WandSparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./services/api";
import type { ProcessingProfile, ProcessingStandard, ProfileOption, ProfilePreview } from "./types";

interface Props {
  onMessage: (message: string) => void;
  onProfilesChanged: () => Promise<void>;
  onConfigureAI: () => void;
}

type EditorType = "filter" | "beautify" | "completion";

export function ProfileWorkspace({ onMessage, onProfilesChanged, onConfigureAI }: Props) {
  const [type, setType] = useState<EditorType>("filter");
  const [profiles, setProfiles] = useState<ProfileOption[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [activationRule, setActivationRule] = useState("");
  const [instruction, setInstruction] = useState("");
  const [priority, setPriority] = useState(100);
  const [version, setVersion] = useState<number | null>(null);
  const [preview, setPreview] = useState<ProfilePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  async function loadList(nextType = type, preferredId?: string | null) {
    setLoading(true);
    try {
      const items = nextType === "filter"
        ? await api.getProcessingStandards()
        : nextType === "completion"
          ? await api.getCompletionProfiles()
          : await api.getBeautifyProfiles();
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

  async function openProfile(nextType: EditorType, profileId: string) {
    setBusy(true);
    try {
      if (nextType === "filter") {
        const detail = await api.getProcessingStandard(profileId);
        setSelectedId(detail.id);
        setName(detail.name);
        setActivationRule(detail.activation_rule);
        setInstruction(detail.filter_rule);
        setPriority(detail.priority);
        setVersion(detail.version);
        setPreview({ description: detail.description, config: {}, unsupported: [], can_save: true });
      } else {
        const detail = await api.getProcessingProfile(nextType, profileId);
        setSelectedId(detail.id);
        setName(detail.name);
        setActivationRule("");
        setInstruction(detail.instruction);
        setPriority(100);
        setVersion(detail.version);
        setPreview({ description: detail.description, config: detail.config, unsupported: [], can_save: true });
      }
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "读取标准详情失败");
    } finally {
      setBusy(false);
    }
  }

  function startNew(copy?: ProcessingStandard | ProcessingProfile) {
    setSelectedId(null);
    setName(copy ? `${copy.name} 副本` : "");
    if (copy && "activation_rule" in copy) {
      setActivationRule(copy.activation_rule);
      setInstruction(copy.filter_rule);
      setPriority(copy.priority);
    } else {
      setActivationRule("");
      setInstruction(copy?.instruction ?? "");
      setPriority(100);
    }
    setVersion(null);
    setPreview(copy ? { description: copy.description, config: "config" in copy ? copy.config : {}, unsupported: [], can_save: true } : null);
  }

  async function copyCurrent() {
    if (!selectedId) return;
    const detail = type === "filter"
      ? await api.getProcessingStandard(selectedId)
      : await api.getProcessingProfile(type, selectedId);
    startNew(detail);
  }

  async function generatePreview() {
    if (instruction.trim().length < 3 || (type === "filter" && activationRule.trim().length < 3)) {
      onMessage(type === "filter" ? "启动规则和过滤规则都至少需要 3 个字" : "标准要求至少需要 3 个字");
      return;
    }
    setBusy(true);
    try {
      if (type === "beautify") {
        const modelConfig = await api.getAIModelConfig();
        if (!modelConfig.enabled || !modelConfig.api_key_configured) {
          onMessage("美化规则需要通过 AI 校验，请先启用 AI 并填写 API Key");
          onConfigureAI();
          return;
        }
      }
      const result = type === "filter"
        ? await api.previewProcessingStandard({ activation_rule: activationRule.trim(), filter_rule: instruction.trim(), priority })
        : await api.previewProcessingProfile(type, instruction.trim());
      setPreview(result);
      onMessage("标准已校验，可以保存");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "校验标准失败");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!name.trim() || !preview) {
      onMessage("请填写名称并先校验规则");
      return;
    }
    setBusy(true);
    try {
      const saved = type === "filter"
        ? await api.saveProcessingStandard(selectedId, {
            name: name.trim(), activation_rule: activationRule.trim(), filter_rule: instruction.trim(),
            priority, description: preview.description, expected_version: version ?? undefined
          })
        : await api.saveProcessingProfile(type, selectedId, {
            name: name.trim(), instruction: instruction.trim(), description: preview.description,
            config: preview.config, expected_version: version ?? undefined
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
      if (type === "filter") await api.deleteProcessingStandard(selectedId);
      else await api.deleteProcessingProfile(type, selectedId);
      await onProfilesChanged();
      await loadList(type);
      onMessage("标准已停用");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void loadList(type); }, [type]);

  return <section className="profile-workspace" id="mainWorkspace" aria-label="标准管理">
    <header className="profile-heading"><div><span className="panel-kicker">PROCESSING RULES</span><h2>标准管理</h2><p>完工分类、分支过滤和美化标准分别维护，任务创建时锁定版本。</p></div><button className="primary-button" type="button" onClick={() => startNew()}><Plus size={16} />新增标准</button></header>
    <div className="profile-tabs" role="tablist"><button role="tab" aria-selected={type === "completion"} className={type === "completion" ? "active" : ""} onClick={() => setType("completion")}>完工分类标准</button><button role="tab" aria-selected={type === "filter"} className={type === "filter" ? "active" : ""} onClick={() => setType("filter")}>分支过滤标准</button><button role="tab" aria-selected={type === "beautify"} className={type === "beautify" ? "active" : ""} onClick={() => setType("beautify")}>美化标准</button></div>
    <div className="profile-layout">
      <aside className="profile-list">{loading ? <p className="profile-muted"><Loader2 className="spin" size={16} />正在读取</p> : profiles.length ? profiles.map((item) => <button key={item.id} type="button" className={selectedId === item.id ? "active" : ""} onClick={() => void openProfile(type, item.id)}><strong>{item.name}</strong><span>版本 {item.version ?? 1}</span><p>{item.description}</p></button>) : <p className="profile-empty">暂无标准，请先新建。</p>}</aside>
      <section className="profile-editor">
        <div className="profile-editor-heading"><div><h3>{selectedId ? "编辑标准" : "新建标准"}</h3><p>{type === "filter" ? "后端路由后强制执行所选分支" : type === "completion" ? "只定义完工状态的可观察事实判定规则" : "过滤通过后执行独立美化标准"}</p></div>{selectedId && <div className="profile-icon-actions">{type !== "filter" && <button type="button" aria-label="复制标准" onClick={() => void copyCurrent()}><Copy size={16} /></button>}<button type="button" aria-label="停用标准" onClick={() => void remove()}><Trash2 size={16} /></button></div>}</div>
        <label className="profile-field">标准名称<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
        {type === "filter" && <>
          <label className="profile-field">启动规则<textarea value={activationRule} rows={4} maxLength={2000} onChange={(event) => { setActivationRule(event.target.value); setPreview(null); }} placeholder="例如：图片主体是商品，且背景接近纯白" /></label>
        </>}
        <label className="profile-field">{type === "filter" ? "过滤规则" : type === "completion" ? "完工分类规则" : "美化要求"}<textarea value={instruction} rows={5} maxLength={2000} onChange={(event) => { setInstruction(event.target.value); setPreview(null); }} /></label>
        <button className="profile-generate" type="button" disabled={busy} onClick={() => void generatePreview()}>{busy ? <Loader2 className="spin" size={16} /> : <WandSparkles size={16} />}{busy ? "正在校验" : "校验规则"}</button>
        {preview && <p className="profile-muted">{preview.description}</p>}
        <div className="profile-save-row"><button className="primary-button" type="button" disabled={busy || !preview} onClick={() => void save()}><Save size={16} />保存标准</button></div>
      </section>
    </div>
  </section>;
}
