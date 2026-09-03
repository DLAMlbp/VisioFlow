import { Copy, Loader2, Plus, Save, Trash2, WandSparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./services/api";
import type { ProcessingProfile, ProcessingStandard, ProfileOption, ProfilePreview } from "./types";

interface Props {
  onMessage: (message: string) => void;
  onProfilesChanged: () => Promise<void>;
  onConfigureAI: () => void;
}

type EditorType = "global" | "filter" | "beautify" | "redaction";

function redactionFlags(config: Record<string, unknown>) {
  const watermark = config.watermark_removal as Record<string, unknown> | undefined;
  const logos = config.logo_mosaic as Record<string, unknown> | undefined;
  return {
    removeWatermark: watermark?.enabled === true,
    mosaicLogo: logos?.enabled === true
  };
}

function withRedactionFlags(
  config: Record<string, unknown>,
  removeWatermark: boolean,
  mosaicLogo: boolean
) {
  const watermark = config.watermark_removal as Record<string, unknown> | undefined;
  const logos = config.logo_mosaic as Record<string, unknown> | undefined;
  return {
    ...config,
    watermark_removal: {
      ...(watermark ?? {}),
      enabled: removeWatermark,
      mode: "dangjia_bottom_left",
      roi: [0, 0.84, 0.48, 1],
      preserve_outside_roi: true
    },
    logo_mosaic: {
      ...(logos ?? {}),
      enabled: mosaicLogo,
      targets: ["dangjia_logo"],
      box_expansion: 0.08,
      mosaic_block_ratio: 0.16
    }
  };
}

export function ProfileWorkspace({ onMessage, onProfilesChanged, onConfigureAI }: Props) {
  const [type, setType] = useState<EditorType>("global");
  const [profiles, setProfiles] = useState<ProfileOption[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [classificationRule, setClassificationRule] = useState("");
  const [instruction, setInstruction] = useState("");
  const [priority, setPriority] = useState(100);
  const [isFallback, setIsFallback] = useState(false);
  const [version, setVersion] = useState<number | null>(null);
  const [preview, setPreview] = useState<ProfilePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [removeWatermark, setRemoveWatermark] = useState(false);
  const [mosaicLogo, setMosaicLogo] = useState(false);

  async function loadList(nextType = type, preferredId?: string | null) {
    setLoading(true);
    try {
      const items = nextType === "global"
        ? await api.getFilterProfiles()
        : nextType === "filter"
          ? await api.getProcessingStandards()
          : nextType === "beautify"
            ? await api.getBeautifyProfiles()
            : await api.getRedactionProfiles();
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
        setClassificationRule(detail.classification_rule);
        setInstruction(detail.filter_rule);
        setPriority(detail.priority);
        setIsFallback(Boolean(detail.is_fallback));
        setVersion(detail.version);
        setPreview({ description: detail.description, config: {}, unsupported: [], can_save: true });
        setRemoveWatermark(false);
        setMosaicLogo(false);
      } else {
        const detail = await api.getProcessingProfile(nextType === "global" ? "filter" : nextType, profileId);
        setSelectedId(detail.id);
        setName(detail.name);
        setClassificationRule("");
        setInstruction(detail.instruction);
        setPriority(100);
        setIsFallback(false);
        setVersion(detail.version);
        setPreview({ description: detail.description, config: detail.config, unsupported: [], can_save: true });
        const flags = redactionFlags(detail.config);
        setRemoveWatermark(nextType === "beautify" && flags.removeWatermark);
        setMosaicLogo(nextType === "beautify" && flags.mosaicLogo);
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
    if (copy && "classification_rule" in copy) {
      setClassificationRule(copy.classification_rule);
      setInstruction(copy.filter_rule);
      setPriority(copy.priority);
      setIsFallback(false);
    } else {
      setClassificationRule("");
      setInstruction(copy?.instruction ?? "");
      setPriority(100);
      setIsFallback(false);
    }
    setVersion(null);
    setPreview(copy ? { description: copy.description, config: "config" in copy ? copy.config : {}, unsupported: [], can_save: true } : null);
    const flags = copy && "config" in copy
      ? redactionFlags(copy.config)
      : { removeWatermark: false, mosaicLogo: false };
    setRemoveWatermark(type === "beautify" && flags.removeWatermark);
    setMosaicLogo(type === "beautify" && flags.mosaicLogo);
  }

  async function copyCurrent() {
    if (!selectedId) return;
    const detail = type === "filter"
      ? await api.getProcessingStandard(selectedId)
      : await api.getProcessingProfile(type === "global" ? "filter" : type, selectedId);
    startNew(detail);
  }

  async function generatePreview() {
    if (instruction.trim().length < 3 || (type === "filter" && classificationRule.trim().length < 3)) {
      onMessage(type === "filter" ? "分类标准和过滤规则都至少需要 3 个字" : "标准要求至少需要 3 个字");
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
        ? await api.previewProcessingStandard({ classification_rule: classificationRule.trim(), filter_rule: instruction.trim(), priority, is_fallback: isFallback })
        : await api.previewProcessingProfile(type === "global" ? "filter" : type, instruction.trim());
      setPreview(type === "beautify" ? {
        ...result,
        config: withRedactionFlags(result.config, removeWatermark, mosaicLogo)
      } : result);
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
            name: name.trim(), classification_rule: classificationRule.trim(), filter_rule: instruction.trim(),
            priority, is_fallback: isFallback, description: preview.description, expected_version: version ?? undefined
          })
        : await api.saveProcessingProfile(type === "global" ? "filter" : type, selectedId, {
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
      else await api.deleteProcessingProfile(type === "global" ? "filter" : type, selectedId);
      await onProfilesChanged();
      await loadList(type);
      onMessage("标准已停用");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void loadList(type); }, [type]);

  return <section className="profile-workspace" id="mainWorkspace" aria-label="标准管理">
    <header className="profile-heading"><div><span className="panel-kicker">PROCESSING RULES</span><h2>标准管理</h2><p>所有图片先执行全局过滤，再按唯一分类执行对应过滤规则，任务创建时锁定版本。</p></div>{type !== "global" && <button className="primary-button" type="button" onClick={() => startNew()}><Plus size={16} />新增标准</button>}</header>
    <div className="profile-tabs" role="tablist"><button role="tab" aria-selected={type === "global"} className={type === "global" ? "active" : ""} onClick={() => setType("global")}>全局过滤</button><button role="tab" aria-selected={type === "filter"} className={type === "filter" ? "active" : ""} onClick={() => setType("filter")}>分类过滤</button><button role="tab" aria-selected={type === "beautify"} className={type === "beautify" ? "active" : ""} onClick={() => setType("beautify")}>美化标准</button><button role="tab" aria-selected={type === "redaction"} className={type === "redaction" ? "active" : ""} onClick={() => setType("redaction")}>水印与Logo</button></div>
    <div className="profile-layout">
      <aside className="profile-list">{loading ? <p className="profile-muted"><Loader2 className="spin" size={16} />正在读取</p> : profiles.length ? profiles.map((item) => <button key={item.id} type="button" className={selectedId === item.id ? "active" : ""} onClick={() => void openProfile(type, item.id)}><strong>{item.name}</strong><span>版本 {item.version ?? 1}</span><p>{item.description}</p></button>) : <p className="profile-empty">暂无标准，请先新建。</p>}</aside>
      <section className="profile-editor">
        <div className="profile-editor-heading"><div><h3>{selectedId ? "编辑标准" : "新建标准"}</h3><p>{type === "global" ? "所有图片必须优先通过这一套规则，全局任一项失败即淘汰" : type === "filter" ? "模型先按分类标准选中本项，再执行这一项的专属过滤规则" : type === "beautify" ? "过滤通过后执行独立美化标准" : "定义水印放行、Logo遮挡素材及品牌地膜不合格阈值"}</p></div>{selectedId && type !== "global" && <div className="profile-icon-actions"><button type="button" aria-label="复制标准" onClick={() => void copyCurrent()}><Copy size={16} /></button><button type="button" aria-label="停用标准" onClick={() => void remove()}><Trash2 size={16} /></button></div>}</div>
        <label className="profile-field">标准名称<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
        {type === "filter" && <>
          <label className="profile-field">分类标准<textarea value={classificationRule} rows={4} maxLength={2000} onChange={(event) => { setClassificationRule(event.target.value); setPreview(null); }} placeholder="例如：图片呈现无明显施工、硬装完整且可使用的完工室内空间" /></label>
          <label className="fallback-toggle"><input type="checkbox" checked={isFallback} onChange={(event) => { setIsFallback(event.target.checked); setPreview(null); }} /><span><strong>设为兜底分类</strong><small>没有任何明确分类命中时使用；启用中的标准只能有一条兜底分类。</small></span></label>
        </>}
        <label className="profile-field">{type === "global" ? "全局过滤规则" : type === "filter" ? "对应分类专属过滤规则" : type === "beautify" ? "美化要求" : "水印与Logo处理要求"}<textarea value={instruction} rows={type === "global" || type === "redaction" ? 12 : 5} maxLength={2000} placeholder={type === "redaction" ? "例如：左下角水印允许通过并在通过后去除；保留当家文字，仅用小当图标遮挡APP；当家品牌地膜占比达到75%判定不合格。" : undefined} onChange={(event) => { setInstruction(event.target.value); setPreview(null); }} /></label>
        {type === "beautify" && <fieldset className="redaction-options">
          <legend>隐私保护与品牌遮挡</legend>
          <label>
            <input type="checkbox" checked={removeWatermark} onChange={(event) => {
              const enabled = event.target.checked;
              setRemoveWatermark(enabled);
              setPreview((current) => current ? {
                ...current,
                config: withRedactionFlags(current.config, enabled, mosaicLogo)
              } : current);
            }} />
            <span><strong>去除左下角水印</strong><small>仅处理左下角受保护区域，中央文案不会被删除。</small></span>
          </label>
          <label>
            <input type="checkbox" checked={mosaicLogo} onChange={(event) => {
              const enabled = event.target.checked;
              setMosaicLogo(enabled);
              setPreview((current) => current ? {
                ...current,
                config: withRedactionFlags(current.config, removeWatermark, enabled)
              } : current);
            }} />
            <span><strong>当家 APP Logo 自动打码</strong><small>检测衣服、背景布和桌布上的实体 Logo 并打强马赛克。</small></span>
          </label>
        </fieldset>}
        <button className="profile-generate" type="button" disabled={busy} onClick={() => void generatePreview()}>{busy ? <Loader2 className="spin" size={16} /> : <WandSparkles size={16} />}{busy ? "正在校验" : "校验规则"}</button>
        {preview && <div className="profile-preview"><strong>执行方案</strong><p className="profile-muted">{preview.description}</p>{preview.unsupported.length > 0 && <ul className="inline-warning">{preview.unsupported.map((item) => <li key={item}>{item}</li>)}</ul>}{type === "redaction" && <dl className="redaction-config-summary"><div><dt>地膜不合格阈值</dt><dd>{Math.round(Number((preview.config.branded_ground_film as { reject_coverage_gte?: number } | undefined)?.reject_coverage_gte ?? 0) * 100)}%</dd></div><div><dt>Logo处理</dt><dd>{String((preview.config.logo as { action?: string } | undefined)?.action ?? "关闭") === "overlay_asset" ? "保留当家，仅遮挡APP" : "马赛克"}</dd></div><div><dt>水印通过后</dt><dd>{String((preview.config.watermark as { post_action?: string } | undefined)?.post_action ?? "keep") === "remove" ? "自动去除" : "保留"}</dd></div></dl>}</div>}
        <div className="profile-save-row"><button className="primary-button" type="button" disabled={busy || !preview || !preview.can_save} onClick={() => void save()}><Save size={16} />保存标准</button></div>
      </section>
    </div>
  </section>;
}
