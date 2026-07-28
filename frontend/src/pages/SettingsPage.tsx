import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { getSettings, updateSettings, checkLogin, restartService, queryCurrentModel, testVision, getQueueStatus, clearQueue, getTeacherProfile, updateTeacherProfile, teacherChangePassword } from "../api";

interface ModelConfig {
  name: string;
  api_base: string;
  api_key: string;
  model: string;
  concurrency: number;
}

async function testConn(cfg: ModelConfig): Promise<string> {
  try {
    const r = await fetch("/api/teacher/settings/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
      credentials: "include",
    }).then(r => r.json());
    return r.ok ? `✓ ${r.message}` : `✗ ${r.message}`;
  } catch (e: any) {
    return `✗ 请求失败: ${e.message}`;
  }
}

function ModelCard({ cfg, active, onActivate, onChange, onSave, onDelete, saving }: {
  cfg: ModelConfig;
  active: boolean;
  onActivate: () => void;
  onChange: (c: ModelConfig) => void;
  onSave: () => void;
  onDelete: () => void;
  saving: boolean;
}) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState("");
  const [visionTesting, setVisionTesting] = useState(false);
  const [visionResult, setVisionResult] = useState("");
  const [visionReply, setVisionReply] = useState("");
  const [newKey, setNewKey] = useState("");

  const set = (k: keyof ModelConfig, v: string | number) => onChange({ ...cfg, [k]: v });

  const handleSetKey = () => {
    const key = newKey.trim();
    if (!key) return;
    set("api_key", key);
    setNewKey("");
  };

  return (
    <div className={`border rounded-lg p-4 ${active ? "border-blue-400 bg-blue-50/30" : "border-gray-200"}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <input value={cfg.name} onChange={e => set("name", e.target.value)}
            className={`font-semibold bg-transparent border-b ${active ? "border-blue-300 text-blue-700" : "border-gray-200 text-gray-700"} outline-none`}
            placeholder="模型名称" style={{ width: "160px" }} />
          <button onClick={onDelete}
            className="text-xs text-red-500 hover:text-red-700 hover:underline whitespace-nowrap"
            title="删除此模型配置">
            删除
          </button>
        </div>
        {active ? (
          <span className="text-xs text-blue-600 font-medium whitespace-nowrap">当前使用</span>
        ) : (
          <button onClick={onActivate} className="text-xs bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700 whitespace-nowrap">启用</button>
        )}
      </div>
      <div className="space-y-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">API 地址</label>
          <input value={cfg.api_base} onChange={e => set("api_base", e.target.value)}
            className="w-full border rounded px-3 py-1.5 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">API Key</label>
          {cfg.api_key ? (
            <div className="flex items-center gap-2">
              <span className="flex-1 border rounded px-3 py-1.5 text-sm font-mono bg-gray-50 text-gray-400 select-none"
                title="API Key 不可查看，仅可替换">
                {cfg.api_key}
              </span>
            </div>
          ) : null}
          <div className="flex items-center gap-2 mt-1">
            <input
              type="password"
              value={newKey}
              onChange={e => setNewKey(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleSetKey(); }}
              placeholder={cfg.api_key ? "输入新 Key 替换" : "输入 API Key"}
              className="flex-1 border rounded px-3 py-1.5 text-sm font-mono"
              autoComplete="off"
            />
            <button type="button" onClick={handleSetKey}
              className="text-xs bg-gray-100 px-3 py-1.5 rounded hover:bg-gray-200 whitespace-nowrap">
              {cfg.api_key ? "替换" : "设置"}
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">模型名称</label>
            <input value={cfg.model} onChange={e => set("model", e.target.value)}
              className="w-full border rounded px-3 py-1.5 text-sm" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">并发数</label>
            <input type="number" min={1} max={5} value={cfg.concurrency}
              onChange={e => set("concurrency", Math.max(1, Math.min(5, parseInt(e.target.value) || 1)))}
              className="w-full border rounded px-3 py-1.5 text-sm" />
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={onSave} disabled={saving}
            className="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700 disabled:opacity-50 text-xs">
            {saving ? "保存中…" : "保存"}
          </button>
          <button onClick={async () => { setTesting(true); setResult(""); setResult(await testConn(cfg)); setTesting(false); }}
            disabled={testing}
            className="bg-gray-100 text-gray-600 px-3 py-1 rounded hover:bg-gray-200 disabled:opacity-50 text-xs">
            {testing ? "检测中…" : "测试连接"}
          </button>
          <button onClick={async () => {
            setVisionTesting(true); setVisionResult(""); setVisionReply("");
            try {
              const r = await testVision(cfg);
              if (r.ok) {
                setVisionResult(r.passed ? "✓ 读图通过" : "✗ 读图失败");
                setVisionReply(r.reply || "");
              } else {
                setVisionResult("✗ " + (r.message || "测试失败"));
              }
            } catch (e: any) {
              setVisionResult("✗ 请求失败: " + e.message);
            }
            setVisionTesting(false);
          }}
            disabled={visionTesting}
            className="bg-orange-100 text-orange-700 px-3 py-1 rounded hover:bg-orange-200 disabled:opacity-50 text-xs">
            {visionTesting ? "测试中…" : "测试读图"}
          </button>
          {result && <span className={`text-xs ${result.startsWith("✓") ? "text-green-600" : "text-red-500"}`}>{result}</span>}
        </div>
        {visionResult && (
          <div className={`text-xs rounded p-2 mt-1 ${visionResult.includes("通过") ? "bg-green-50 text-green-700" : "bg-red-50 text-red-600"}`}>
            <p className="font-medium">{visionResult}</p>
            {visionReply && <p className="mt-1 text-gray-600 line-clamp-3">{visionReply}</p>}
            {visionResult.includes("失败") && !visionReply && (
              <p className="mt-1 text-gray-500">该模型可能不支持图像识别（vision），建议使用 qwen-vl 或 gpt-4o 等多模态模型</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ------ 标签页定义 ------
const TABS = [
  { key: "profile", label: "个人信息" },
  { key: "models", label: "模型配置" },
  { key: "llm", label: "LLM 参数" },
  { key: "image", label: "图像处理" },
  { key: "grade", label: "等级阈值" },
  { key: "analysis", label: "识读模板管理" },
  { key: "scoring", label: "评分模板" },
  { key: "tools", label: "工具" },
  { key: "system", label: "系统管理" },
];

// ------ 识读模板管理标签页 ------
const TEMPLATE_NAMES = ["零件图识读模板.txt", "装配图识读模板.txt", "平面图识读模板.txt", "组合体三视图识读模板.txt", "DXF识读模板.txt"];
const TEMPLATE_LABELS: Record<string, string> = {
  "零件图识读模板.txt": "零件图",
  "装配图识读模板.txt": "装配图",
  "平面图识读模板.txt": "平面图",
  "组合体三视图识读模板.txt": "组合体三视图",
  "DXF识读模板.txt": "DXF",
};

function AnalysisTemplatesTab({ saving, setMsg }: { saving: boolean; setMsg: (m: string) => void }) {
  const [templates, setTemplates] = useState<Record<string, string>>({});
  const [activeTpl, setActiveTpl] = useState(TEMPLATE_NAMES[0]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch("/api/teacher/templates", { credentials: "include" })
      .then(r => r.json())
      .then(data => {
        if (data.templates) {
          setTemplates(data.templates);
        }
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, []);

  const saveCurrent = async () => {
    try {
      await fetch(`/api/teacher/templates/${encodeURIComponent(activeTpl)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: templates[activeTpl] || "" }),
        credentials: "include",
      });
      setMsg("模板已保存");
    } catch (e: any) {
      setMsg("保存失败: " + e.message);
    }
  };

  const sectionClass = "bg-white rounded-lg shadow p-6 space-y-4";
  const inputClass = "w-full border rounded px-3 py-2 text-sm font-mono";

  return (
    <div className={sectionClass}>
      <h2 className="text-lg font-semibold">识读模板管理</h2>
      <p className="text-xs text-gray-400">
        修改全局模板会影响新创建的题目默认值。已有题目的模板在题目编辑中单独修改。
      </p>

      {/* 模板类型切换 */}
      <div className="flex gap-1 flex-wrap border-b pb-2">
        {TEMPLATE_NAMES.map(name => (
          <button key={name} onClick={() => setActiveTpl(name)}
            className={`px-3 py-1 rounded text-sm whitespace-nowrap ${activeTpl === name ? "bg-blue-600 text-white font-medium" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
            {TEMPLATE_LABELS[name] || name}
          </button>
        ))}
      </div>

      {/* 当前选中模板的编辑区 */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          {TEMPLATE_LABELS[activeTpl] || activeTpl}
          <span className="text-gray-400 font-normal ml-2 text-xs">— 全局默认模板</span>
        </label>
        <textarea rows={18} value={templates[activeTpl] || ""}
          onChange={e => setTemplates({ ...templates, [activeTpl]: e.target.value })}
          className={inputClass} />
      </div>

      <button onClick={saveCurrent} disabled={saving || !loaded}
        className="bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
        {saving ? "保存中…" : "保存模板"}
      </button>
    </div>
  );
}


export default function SettingsPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState("models");

  // 模型配置
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [originalModels, setOriginalModels] = useState<ModelConfig[]>([]);
  const [active, setActive] = useState(0);

  // 通用
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  // 学生会话超时
  const [studentTimeout, setStudentTimeout] = useState(5);

  // 重叠线清理工具
  const [cleanFile, setCleanFile] = useState<File | null>(null);
  const [cleanProcessing, setCleanProcessing] = useState(false);
  const [cleanResult, setCleanResult] = useState<{ previewId: string; downloadUrl: string } | null>(null);
  const [cleanError, setCleanError] = useState("");

  // LLM 参数
  const [llmParams, setLlmParams] = useState<Record<string, any>>({});
  // 图像参数
  const [imageParams, setImageParams] = useState<Record<string, any>>({});
  // DXF 参数
  const [dxfParams, setDxfParams] = useState<Record<string, any>>({});
  // 等级阈值
  const [gradeThresholds, setGradeThresholds] = useState<Record<string, number>>({});
  // 提示词模板
  const [promptTemplates, setPromptTemplates] = useState<Record<string, string>>({});
  // 评分模板
  const [scoringTemplates, setScoringTemplates] = useState<Record<string, string>>({});

  // 模型查询
  // 个人信息
  const [profileName, setProfileName] = useState("");
  const [profileUsername, setProfileUsername] = useState("");
  const [profileOldPwd, setProfileOldPwd] = useState("");
  const [profileNewPwd, setProfileNewPwd] = useState("");
  const [profileNewPwd2, setProfileNewPwd2] = useState("");

  const [querying, setQuerying] = useState(false);
  const [modelQuery, setModelQuery] = useState<any>(null);
  const [queryError, setQueryError] = useState("");
  // 队列
  const [queueInfo, setQueueInfo] = useState<any>(null);
  // 重启
  const [restarting, setRestarting] = useState(false);
  // 加载全部设置
  const loadAllSettings = async () => {
    try {
      const s = await getSettings();
      const ml = s.models || [];
      setModels(ml);
      setOriginalModels(JSON.parse(JSON.stringify(ml)));
      setActive(s.llm_active || 0);
      setLlmParams(s.llm_params || {});
      setImageParams(s.image_params || {});
      setDxfParams(s.dxf_params || {});
      setGradeThresholds(s.grade_thresholds || {});
      setPromptTemplates(s.prompt_templates || {});
      setScoringTemplates(s.scoring_templates || {});
      setStudentTimeout(s.student_timeout_minutes ?? 5);
    } catch {}
  };

  useEffect(() => {
    checkLogin().catch(() => navigate("/teacher"));
    loadAllSettings();
    // 加载教师个人信息
    getTeacherProfile().then((p: any) => {
      setProfileName(p["姓名"] || sessionStorage.getItem("teacher_name") || "");
      setProfileUsername(p["用户名"] || sessionStorage.getItem("teacher_username") || "");
    }).catch(() => {});
  }, [navigate]);

  // 保存通用函数
  const saveSection = async (section: string, data: Record<string, any>) => {
    setSaving(true); setMsg("");
    try {
      await updateSettings({ [section]: data });
      setMsg("设置已保存");
    } catch (e: any) {
      setMsg("保存失败: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  // 模型保存
  const handleSaveModel = async (index: number) => {
    setSaving(true); setMsg("");
    try {
      const isActive = index === active;
      const origConcurrency = originalModels[index]?.concurrency ?? 1;
      const newConcurrency = models[index]?.concurrency ?? 1;
      const concurrencyChanged = origConcurrency !== newConcurrency;

      if (isActive && concurrencyChanged) {
        if (!confirm("并发数已修改，保存后需要重启服务才能生效。\n\n确定要保存并重启服务吗？")) {
          setSaving(false); return;
        }
      }
      await updateSettings({ models, llm_active: active });
      setOriginalModels(JSON.parse(JSON.stringify(models)));
      if (isActive && concurrencyChanged) {
        setMsg("设置已保存，正在重启服务…");
        try { await restartService(); } catch { setMsg("设置已保存，服务正在重启，请稍后刷新页面…"); }
      } else {
        setMsg("设置已保存");
      }
    } catch (e: any) {
      setMsg("保存失败: " + e.message);
    } finally { setSaving(false); }
  };

  const handleAddModel = () => {
    const newModel: ModelConfig = { name: "", api_base: "", api_key: "", model: "", concurrency: 1 };
    setModels([...models, newModel]);
    if (models.length === 0) setActive(0);
  };

  const handleDeleteModel = (index: number) => {
    if (!confirm("确定要删除该模型配置吗？此操作不可恢复。")) return;
    const newModels = models.filter((_, i) => i !== index);
    const newOriginals = originalModels.filter((_, i) => i !== index);
    let newActive = active;
    if (index === active) newActive = 0;
    else if (index < active) newActive = active - 1;
    setModels(newModels); setOriginalModels(newOriginals); setActive(newActive);
  };

  const handleActivate = async (index: number) => {
    setActive(index);
    try {
      await updateSettings({ models, llm_active: index });
      setOriginalModels(JSON.parse(JSON.stringify(models)));
    } catch (e: any) { setMsg("切换模型失败: " + e.message); }
  };

  const handleQueryModel = async () => {
    setQuerying(true); setModelQuery(null); setQueryError("");
    try {
      const result = await queryCurrentModel();
      if (result.ok) setModelQuery(result);
      else setQueryError(result.message || "查询失败");
    } catch (e: any) { setQueryError("查询失败: " + e.message); }
    finally { setQuerying(false); }
  };

  const handleRestart = async () => {
    if (!confirm("确定要重启服务吗？正在处理的任务可能会中断。")) return;
    setRestarting(true); setMsg("");
    try { await restartService(); setMsg("重启指令已发送，服务即将重启…"); }
    catch (e: any) { setMsg("重启失败: " + e.message); }
    finally { setRestarting(false); }
  };

  const fetchQueueStatus = async () => {
    try { const info = await getQueueStatus(); if (info.ok) setQueueInfo(info); } catch {}
  };

  useEffect(() => { fetchQueueStatus(); const timer = setInterval(fetchQueueStatus, 5000); return () => clearInterval(timer); }, []);

  // ------ 渲染辅助 ------
  const inputClass = "w-full border rounded px-3 py-1.5 text-sm";
  const labelClass = "block text-xs text-gray-500 mb-1";
  const sectionClass = "bg-white rounded-lg shadow p-6 space-y-4";

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-blue-700 text-white p-4 shadow">
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-xl font-bold">系统设置</h1>
          <button onClick={() => navigate("/teacher/dashboard")}
            className="bg-white/20 px-3 py-1 rounded hover:bg-white/30 flex-shrink-0">返回</button>
        </div>
        <nav className="flex gap-1 flex-wrap">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`px-3 py-1 rounded text-sm whitespace-nowrap ${tab === t.key ? "bg-white/20 font-medium" : "hover:bg-white/20 transition-colors"}`}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="max-w-3xl mx-auto p-4 space-y-4">

        {/* ========== 模型配置 ========== */}
        {tab === "models" && (
          <div className={sectionClass}>
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">大模型配置</h2>
              <button onClick={handleQueryModel} disabled={querying}
                className="text-xs bg-green-600 text-white px-3 py-1.5 rounded hover:bg-green-700 disabled:opacity-50">
                {querying ? "查询中…" : "查询当前模型"}
              </button>
            </div>
            {queryError && <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{queryError}</div>}
            {modelQuery && (
              <div className="bg-green-50 border border-green-200 rounded p-4 text-sm space-y-1">
                <div className="flex items-center gap-2 mb-2">
                  <span className="font-semibold text-green-800">{modelQuery.model}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${modelQuery.available ? "bg-green-200 text-green-800" : "bg-red-200 text-red-800"}`}>
                    {modelQuery.available ? "可用" : "不可用"}
                  </span>
                  {modelQuery.vision_ok !== undefined && (
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${modelQuery.vision_ok ? "bg-green-200 text-green-800" : "bg-yellow-200 text-yellow-800"}`}>
                      {modelQuery.vision_ok ? "可读图" : "不支持读图"}
                    </span>
                  )}
                </div>
              </div>
            )}
            {models.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <p className="mb-3">暂无模型配置，请添加模型</p>
                <button onClick={handleAddModel} className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 text-sm">+ 添加模型</button>
              </div>
            ) : (
              <>
                {models.map((m, i) => (
                  <ModelCard key={i} cfg={m} active={active === i}
                    onActivate={() => handleActivate(i)}
                    onChange={(c) => { const nm = [...models]; nm[i] = c; setModels(nm); }}
                    onSave={() => handleSaveModel(i)}
                    onDelete={() => handleDeleteModel(i)} saving={saving} />
                ))}
                <button onClick={handleAddModel}
                  className="w-full border-2 border-dashed border-gray-300 text-gray-400 py-3 rounded-lg hover:border-blue-400 hover:text-blue-500 transition-colors text-sm">
                  + 添加模型
                </button>
              </>
            )}
          </div>
        )}

        {/* ========== LLM 参数 ========== */}
        {tab === "llm" && (
          <div className={sectionClass}>
            <h2 className="text-lg font-semibold">LLM 调用参数</h2>
            <p className="text-xs text-gray-400">控制模型输出风格和资源消耗，修改后下次 LLM 调用立即生效</p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>temperature — 生成随机性 (0~2)</label>
                <input type="number" step="0.01" min={0} max={2} value={llmParams.temperature ?? 0.1}
                  onChange={e => setLlmParams({ ...llmParams, temperature: parseFloat(e.target.value) || 0 })}
                  className={inputClass} />
                <p className="text-xs text-gray-400 mt-0.5">越低越确定，工程图批改建议 0.1</p>
              </div>
              <div>
                <label className={labelClass}>max_tokens — 最大输出 token 数</label>
                <input type="number" min={256} max={16384} value={llmParams.max_tokens ?? 4096}
                  onChange={e => setLlmParams({ ...llmParams, max_tokens: parseInt(e.target.value) || 4096 })}
                  className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>client_timeout — API 超时（秒）</label>
                <input type="number" min={10} max={600} value={llmParams.client_timeout ?? 120}
                  onChange={e => setLlmParams({ ...llmParams, client_timeout: parseInt(e.target.value) || 120 })}
                  className={inputClass} />
              </div>
              <div className="flex items-end pb-1">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={llmParams.enable_thinking ?? false}
                    onChange={e => setLlmParams({ ...llmParams, enable_thinking: e.target.checked })}
                    className="w-4 h-4" />
                  <span className="text-sm">enable_thinking — 启用思考模式</span>
                </label>
              </div>
            </div>
            <button onClick={() => saveSection("llm_params", llmParams)} disabled={saving}
              className="bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
              {saving ? "保存中…" : "保存 LLM 参数"}
            </button>
          </div>
        )}

        {/* ========== 图像处理 ========== */}
        {tab === "image" && (
          <div className={sectionClass}>
            <h2 className="text-lg font-semibold">图像处理参数</h2>
            <p className="text-xs text-gray-400">控制发给 LLM 的图片质量和 token 消耗。A3@200DPI≈3300px</p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelClass}>analysis_max_size — 分析图长边最大像素</label>
                <input type="number" min={512} max={8192} value={imageParams.analysis_max_size ?? 3508}
                  onChange={e => setImageParams({ ...imageParams, analysis_max_size: parseInt(e.target.value) || 3508 })}
                  className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>analysis_dpi — PDF 渲染 DPI</label>
                <input type="number" min={72} max={600} value={imageParams.analysis_dpi ?? 150}
                  onChange={e => setImageParams({ ...imageParams, analysis_dpi: parseInt(e.target.value) || 150 })}
                  className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>phase1_max_size — 阶段一缩略图长边像素</label>
                <input type="number" min={256} max={2048} value={imageParams.phase1_max_size ?? 768}
                  onChange={e => setImageParams({ ...imageParams, phase1_max_size: parseInt(e.target.value) || 768 })}
                  className={inputClass} />
                <p className="text-xs text-gray-400 mt-0.5">相似度对比用缩略图，降低 token 消耗</p>
              </div>
              <div>
                <label className={labelClass}>phase1_jpeg_quality — 阶段一 JPEG 质量</label>
                <input type="number" min={10} max={100} value={imageParams.phase1_jpeg_quality ?? 55}
                  onChange={e => setImageParams({ ...imageParams, phase1_jpeg_quality: parseInt(e.target.value) || 55 })}
                  className={inputClass} />
              </div>
              <div>
                <label className={labelClass}>analysis_jpeg_quality — 分析图 JPEG 质量</label>
                <input type="number" min={10} max={100} value={imageParams.analysis_jpeg_quality ?? 85}
                  onChange={e => setImageParams({ ...imageParams, analysis_jpeg_quality: parseInt(e.target.value) || 85 })}
                  className={inputClass} />
              </div>
            </div>
            <button onClick={() => saveSection("image_params", imageParams)} disabled={saving}
              className="bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
              {saving ? "保存中…" : "保存图像参数"}
            </button>

            {/* DXF 处理参数 */}
            <div className="border-t pt-4 mt-4">
              <h3 className="text-md font-semibold mb-2">DXF 处理参数</h3>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={labelClass}>preview_dpi — 预览图 DPI</label>
                  <input type="number" min={72} max={600} value={dxfParams.preview_dpi ?? 150}
                    onChange={e => setDxfParams({ ...dxfParams, preview_dpi: parseInt(e.target.value) || 150 })}
                    className={inputClass} />
                </div>
                <div>
                  <label className={labelClass}>preview_max_size — 预览图长边最大像素</label>
                  <input type="number" min={512} max={4096} value={dxfParams.preview_max_size ?? 2048}
                    onChange={e => setDxfParams({ ...dxfParams, preview_max_size: parseInt(e.target.value) || 2048 })}
                    className={inputClass} />
                </div>
                <div>
                  <label className={labelClass}>preview_linewidth_scale — 预览图线宽缩放</label>
                  <input type="number" step="0.1" min={0.1} max={5} value={dxfParams.preview_linewidth_scale ?? 1.0}
                    onChange={e => setDxfParams({ ...dxfParams, preview_linewidth_scale: parseFloat(e.target.value) || 1.0 })}
                    className={inputClass} />
                </div>
              </div>
              <button onClick={() => saveSection("dxf_params", dxfParams)} disabled={saving}
                className="bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 text-sm mt-4">
                {saving ? "保存中…" : "保存 DXF 参数"}
              </button>
            </div>
          </div>
        )}

        {/* ========== 等级阈值 ========== */}
        {tab === "grade" && (
          <div className={sectionClass}>
            <h2 className="text-lg font-semibold">评分等级阈值</h2>
            <p className="text-xs text-gray-400">总分 ≥ 阈值即对应等级，低于最低阈值则为 F。修改后所有评分立即生效</p>
            <div className="grid grid-cols-4 gap-3">
              {["A+", "A", "B+", "B", "C+", "C", "D+", "D"].map(g => (
                <div key={g}>
                  <label className={labelClass}>{g}</label>
                  <input type="number" step="0.01" min={0} max={100}
                    value={gradeThresholds[g] ?? 0}
                    onChange={e => setGradeThresholds({ ...gradeThresholds, [g]: parseFloat(e.target.value) || 0 })}
                    className={inputClass} />
                </div>
              ))}
            </div>
            <button onClick={() => saveSection("grade_thresholds", gradeThresholds)} disabled={saving}
              className="bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
              {saving ? "保存中…" : "保存等级阈值"}
            </button>
          </div>
        )}

        {/* ========== 识读模板管理 ========== */}
        {tab === "analysis" && (
          <AnalysisTemplatesTab saving={saving} setMsg={setMsg} />
        )}

        {/* ========== 评分模板 ========== */}
        {tab === "scoring" && (
          <div className={sectionClass}>
            <h2 className="text-lg font-semibold">评分模板</h2>
            <p className="text-xs text-gray-400">评分流程中的引导语、修正提示词，以及新建题目时的默认评分标准</p>

            <h3 className="text-sm font-medium text-gray-700 mt-4">评分引导语</h3>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                评分引导语
                <span className="text-gray-400 font-normal ml-2 text-xs">— 引导 LLM 综合对比学生图和参考图</span>
              </label>
              <textarea rows={3} value={promptTemplates["grading_guide"] || ""}
                onChange={e => setPromptTemplates({ ...promptTemplates, grading_guide: e.target.value })}
                className="w-full border rounded px-3 py-2 text-sm font-mono" />
            </div>

            <h3 className="text-sm font-medium text-gray-700 mt-6">新建题目默认评分标准</h3>
            <p className="text-xs text-gray-400">创建新题目时自动填入的评分标准，教师可在每题中覆盖</p>
            {[
              ["phase1", "阶段一默认标准（图形相似度）"],
              ["phase2", "阶段二默认标准（量化标注）"],
            ].map(([key, label]) => (
              <div key={key}>
                <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
                <textarea rows={5} value={scoringTemplates[key] || ""}
                  onChange={e => setScoringTemplates({ ...scoringTemplates, [key]: e.target.value })}
                  className="w-full border rounded px-3 py-2 text-sm" />
              </div>
            ))}
            <button onClick={async () => {
              setSaving(true); setMsg("");
              try {
                // 评分引导语存在 prompt_templates 里，评分标准存在 scoring_templates 里
                await updateSettings({ prompt_templates: promptTemplates, scoring_templates: scoringTemplates });
                setMsg("设置已保存");
              } catch (e: any) { setMsg("保存失败: " + e.message); }
              finally { setSaving(false); }
            }} disabled={saving}
              className="bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
              {saving ? "保存中…" : "保存评分模板"}
            </button>
          </div>
        )}

        {/* ========== 工具 ========== */}
        {tab === "tools" && (
          <div className={sectionClass}>
            <h3 className="text-lg font-semibold mb-4">重叠线清理工具</h3>
            <p className="text-sm text-gray-500 mb-4">
              上传 DXF 文件，系统自动预处理（多段线爆炸）并合并重叠的线段、圆、圆弧，
              保留标注/公差/文字等实体不变。处理后可下载清理后的 DXF 文件并预览效果。
            </p>

            <div className="flex items-center gap-3 mb-4">
              <input type="file" accept=".dxf" id="clean-file-input" className="hidden"
                onChange={(e) => {
                  setCleanFile(e.target.files?.[0] || null);
                  setCleanResult(null);
                  setCleanError("");
                }} />
              <button onClick={() => document.getElementById("clean-file-input")?.click()}
                className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
                选择文件
              </button>
              <span className="text-sm text-gray-500">{cleanFile?.name || "未选择文件"}</span>
              <button
                disabled={!cleanFile || cleanProcessing}
                onClick={async () => {
                  if (!cleanFile) return;
                  setCleanProcessing(true);
                  setCleanError("");
                  setCleanResult(null);
                  try {
                    const fd = new FormData();
                    fd.append("file", cleanFile);
                    const res = await fetch("/api/teacher/tools/clean-overlap", {
                      method: "POST", body: fd, credentials: "include",
                    });
                    if (!res.ok) {
                      const err = await res.text();
                      throw new Error(err || "处理失败");
                    }
                    const previewId = res.headers.get("X-Preview-ID") || "";
                    const blob = await res.blob();
                    const downloadUrl = URL.createObjectURL(blob);
                    setCleanResult({ previewId, downloadUrl });
                  } catch (e: any) {
                    setCleanError(e.message);
                  } finally {
                    setCleanProcessing(false);
                  }
                }}
                className="px-4 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700 disabled:opacity-50"
              >
                {cleanProcessing ? "处理中…" : "开始清理"}
              </button>
            </div>

            {cleanError && <p className="text-red-500 text-sm mb-3">{cleanError}</p>}

            {cleanResult && (
              <div className="space-y-3">
                <p className="text-green-600 text-sm font-medium">✅ 清理完成</p>
                {cleanResult.previewId && (
                  <img src={`/api/teacher/tools/clean-preview/${cleanResult.previewId}`}
                    alt="清理后预览" className="w-full rounded border max-w-xl" />
                )}
                <a href={cleanResult.downloadUrl}
                  download={cleanFile?.name?.replace(".dxf", "_clean.dxf") || "cleaned.dxf"}
                  className="inline-block px-4 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700">
                  下载清理后的 DXF
                </a>
              </div>
            )}
          </div>
        )}

        {/* ========== 系统管理 ========== */}
        {tab === "system" && (
          <div className={sectionClass}>
            <h2 className="text-lg font-semibold">系统管理</h2>

            {/* 任务队列 */}
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-2">任务队列</h3>
              {queueInfo && (
                <div className="bg-gray-50 rounded p-3 text-sm space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-400">自动刷新</span>
                    <button
                      onClick={async () => {
                        if (!confirm(`确定要清空 ${queueInfo.queued_count} 个等待中的任务吗？正在执行的任务不受影响。`)) return;
                        try { await clearQueue(); fetchQueueStatus(); } catch {}
                      }}
                      disabled={queueInfo.queued_count === 0}
                      className="text-xs bg-red-100 text-red-600 px-2 py-0.5 rounded hover:bg-red-200 disabled:opacity-40 disabled:cursor-not-allowed">
                      清空队列{queueInfo.queued_count > 0 ? ` (${queueInfo.queued_count})` : ""}
                    </button>
                  </div>
                  <div className="flex gap-4 text-xs text-gray-500">
                    <span>并发: {queueInfo.concurrency}</span>
                    <span className="text-green-600">执行中: {queueInfo.running_count}</span>
                    <span className="text-orange-500">排队: {queueInfo.queued_count}</span>
                  </div>
                  {queueInfo.items && queueInfo.items.length > 0 && (
                    <div className="space-y-1 max-h-40 overflow-y-auto">
                      {queueInfo.items.map((item: any, i: number) => (
                        <div key={i} className="flex items-center gap-2 text-xs bg-white rounded px-2 py-1">
                          <span className={`w-1.5 h-1.5 rounded-full ${item._status === "running" ? "bg-green-500 animate-pulse" : "bg-gray-300"}`} />
                          <span className={`px-1.5 py-0.5 rounded font-medium text-white ${item.type === "ref_analyze" ? "bg-purple-500" : item.type === "batch_grade" ? "bg-orange-500" : item.type === "grade" ? "bg-green-500" : "bg-blue-500"}`}>
                            {item.type === "ref_analyze" ? "教师分析" : item.type === "batch_grade" ? "批量评分" : item.type === "grade" ? "评分" : "分析"}
                          </span>
                          <span className="text-gray-600">{item.qid && `题${item.qid}`}</span>
                          {item.name && <span className="text-gray-500">{item.name}</span>}
                          <span className="ml-auto text-gray-400">{item._status === "running" ? "执行中" : "排队"}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 会话超时 */}
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-2">学生会话超时</h3>
              <p className="text-xs text-gray-400 mb-2">学生登录后无操作超时自动退出（分钟）。</p>
              <div className="flex items-center gap-2">
                <input type="number" min="1" max="1440" value={studentTimeout}
                  onChange={e => setStudentTimeout(parseInt(e.target.value) || 5)}
                  className="w-20 border rounded px-2 py-1 text-sm" />
                <span className="text-sm text-gray-500">分钟</span>
                <button onClick={async () => {
                  setSaving(true); setMsg("");
                  try {
                    await updateSettings({ student_timeout_minutes: studentTimeout });
                    setMsg("已保存");
                  } catch (e: any) { setMsg("保存失败: " + e.message); }
                  finally { setSaving(false); }
                }} disabled={saving}
                  className="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
                  {saving ? "保存中…" : "保存"}
                </button>
              </div>
            </div>

            <hr className="my-4" />

            {/* 重启 */}
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-2">重启服务</h3>
              <p className="text-xs text-gray-400 mb-2">修改当前使用模型的并发数后，需要重启服务才能生效。</p>
              <button onClick={handleRestart} disabled={restarting}
                className="bg-red-600 text-white px-4 py-2 rounded hover:bg-red-700 disabled:opacity-50 text-sm">
                {restarting ? "重启中…" : "重启服务"}
              </button>
            </div>
          </div>
        )}

        {/* ========== 个人信息 ========== */}
        {tab === "profile" && (
          <div className={sectionClass}>
            <h2 className="text-lg font-semibold mb-4">个人信息</h2>
            <div className="grid grid-cols-2 gap-4 max-w-sm mb-4">
              <div>
                <label className={labelClass}>姓名</label>
                <input type="text" value={profileName} disabled
                  className={inputClass + " bg-gray-100 text-gray-500 cursor-not-allowed"} />
              </div>
              <div>
                <label className={labelClass}>用户名</label>
                <input type="text" value={profileUsername} disabled
                  className={inputClass + " bg-gray-100 text-gray-500 cursor-not-allowed"} />
              </div>
            </div>

            <h3 className="text-lg font-semibold">修改密码</h3>
            <div className="space-y-3 max-w-sm">
              <div>
                <label className={labelClass}>旧密码</label>
                <input type="password" value={profileOldPwd} onChange={e => setProfileOldPwd(e.target.value)}
                  className={inputClass} placeholder="请输入当前密码" />
              </div>
              <div>
                <label className={labelClass}>新密码</label>
                <input type="password" value={profileNewPwd} onChange={e => setProfileNewPwd(e.target.value)}
                  className={inputClass} placeholder="请输入新密码（至少6位）" />
              </div>
              <div>
                <label className={labelClass}>确认新密码</label>
                <input type="password" value={profileNewPwd2} onChange={e => setProfileNewPwd2(e.target.value)}
                  className={inputClass} placeholder="请再次输入新密码" />
              </div>
              <button onClick={async () => {
                if (!profileOldPwd) { setMsg("请输入旧密码"); return; }
                if (!profileNewPwd || profileNewPwd.length < 6) { setMsg("新密码至少6位"); return; }
                if (profileNewPwd !== profileNewPwd2) { setMsg("两次输入的新密码不一致"); return; }
                if (profileOldPwd === profileNewPwd) { setMsg("新密码不能与旧密码相同"); return; }
                setSaving(true); setMsg("");
                try {
                  await teacherChangePassword(profileOldPwd, profileNewPwd);
                  setProfileOldPwd(""); setProfileNewPwd(""); setProfileNewPwd2("");
                  setMsg("密码已修改");
                } catch (e: any) { setMsg("修改失败: " + e.message); }
                finally { setSaving(false); }
              }} disabled={saving}
                className="bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
                {saving ? "保存中…" : "修改密码"}
              </button>
            </div>
          </div>
        )}

        {msg && <p className={`text-sm text-center ${msg.includes("失败") ? "text-red-500" : "text-green-600"}`}>{msg}</p>}

      </main>
    </div>
  );
}
