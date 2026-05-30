import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { getSettings, updateSettings, checkLogin, restartService, queryCurrentModel, getQueueStatus, clearQueue } from "../api";

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
  const [showKey, setShowKey] = useState(false);

  const set = (k: keyof ModelConfig, v: string | number) => onChange({ ...cfg, [k]: v });

  const maskKey = (key: string): string => {
    if (key.length <= 5) return key;
    return "•".repeat(key.length - 5) + key.slice(-5);
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
          <label className="block text-xs text-gray-500 mb-1">
            API Key
            <button type="button" onClick={() => setShowKey(!showKey)}
              className="ml-2 text-blue-500 hover:text-blue-700 font-normal">
              {showKey ? "隐藏" : "显示"}
            </button>
          </label>
          <input
            type="text"
            value={showKey ? cfg.api_key : maskKey(cfg.api_key)}
            onFocus={() => setShowKey(true)}
            onBlur={() => setShowKey(false)}
            onChange={e => { if (showKey) set("api_key", e.target.value); }}
            className="w-full border rounded px-3 py-1.5 text-sm font-mono"
            autoComplete="off"
          />
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
        <div className="flex items-center gap-2">
          <button onClick={onSave} disabled={saving}
            className="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700 disabled:opacity-50 text-xs">
            {saving ? "保存中…" : "保存"}
          </button>
          <button onClick={async () => { setTesting(true); setResult(""); setResult(await testConn(cfg)); setTesting(false); }}
            disabled={testing}
            className="bg-gray-100 text-gray-600 px-3 py-1 rounded hover:bg-gray-200 disabled:opacity-50 text-xs">
            {testing ? "检测中…" : "测试连接"}
          </button>
          {result && <span className={`text-xs ${result.startsWith("✓") ? "text-green-600" : "text-red-500"}`}>{result}</span>}
        </div>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const navigate = useNavigate();
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [originalModels, setOriginalModels] = useState<ModelConfig[]>([]);
  const [active, setActive] = useState(0);
  const [saving, setSaving] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [querying, setQuerying] = useState(false);
  const [modelQuery, setModelQuery] = useState<any>(null);
  const [queryError, setQueryError] = useState("");
  const [queueInfo, setQueueInfo] = useState<any>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    checkLogin().catch(() => navigate("/teacher"));
    getSettings().then((s) => {
      const ml = s.models || [];
      setModels(ml);
      setOriginalModels(JSON.parse(JSON.stringify(ml)));
      setActive(s.llm_active || 0);
    }).catch(() => {});
  }, [navigate]);

  const handleRestart = async () => {
    if (!confirm("确定要重启服务吗？正在处理的任务可能会中断。")) return;
    setRestarting(true);
    setMsg("");
    try {
      await restartService();
      setMsg("重启指令已发送，服务即将重启…");
    } catch (e: any) {
      setMsg("重启失败: " + e.message);
    } finally {
      setRestarting(false);
    }
  };

  const handleSaveModel = async (index: number) => {
    setSaving(true);
    setMsg("");
    try {
      const isActive = index === active;
      const origConcurrency = originalModels[index]?.concurrency ?? 1;
      const newConcurrency = models[index]?.concurrency ?? 1;
      const concurrencyChanged = origConcurrency !== newConcurrency;

      if (isActive && concurrencyChanged) {
        if (!confirm("并发数已修改，保存后需要重启服务才能生效。\n\n确定要保存并重启服务吗？")) {
          setSaving(false);
          return;
        }
      }

      await updateSettings({ models, llm_active: active });
      setOriginalModels(JSON.parse(JSON.stringify(models)));

      if (isActive && concurrencyChanged) {
        setMsg("设置已保存，正在重启服务…");
        try {
          await restartService();
        } catch (e: any) {
          setMsg("设置已保存，服务正在重启，请稍后刷新页面…");
        }
      } else {
        setMsg("设置已保存");
      }
    } catch (e: any) {
      setMsg("保存失败: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleAddModel = () => {
    const newModel: ModelConfig = {
      name: "",
      api_base: "",
      api_key: "",
      model: "",
      concurrency: 1,
    };
    setModels([...models, newModel]);
    if (models.length === 0) {
      setActive(0);
    }
  };

  const handleDeleteModel = (index: number) => {
    if (!confirm("确定要删除该模型配置吗？此操作不可恢复。")) return;

    const newModels = models.filter((_, i) => i !== index);
    const newOriginals = originalModels.filter((_, i) => i !== index);

    let newActive = active;
    if (index === active) {
      newActive = 0;
    } else if (index < active) {
      newActive = active - 1;
    }

    setModels(newModels);
    setOriginalModels(newOriginals);
    setActive(newActive);
  };

  const handleActivate = async (index: number) => {
    setActive(index);
    try {
      await updateSettings({ models, llm_active: index });
      setOriginalModels(JSON.parse(JSON.stringify(models)));
    } catch (e: any) {
      setMsg("切换模型失败: " + e.message);
    }
  };

  const handleQueryModel = async () => {
    setQuerying(true);
    setModelQuery(null);
    setQueryError("");
    try {
      const result = await queryCurrentModel();
      if (result.ok) {
        setModelQuery(result);
      } else {
        setQueryError(result.message || "查询失败");
      }
    } catch (e: any) {
      setQueryError("查询失败: " + e.message);
    } finally {
      setQuerying(false);
    }
  };

  const fetchQueueStatus = async () => {
    try {
      const info = await getQueueStatus();
      if (info.ok) setQueueInfo(info);
    } catch {}
  };

  // 自动轮询队列状态
  useEffect(() => {
    fetchQueueStatus();
    const timer = setInterval(fetchQueueStatus, 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-blue-700 text-white p-4 shadow flex justify-between items-center">
        <div className="flex items-center gap-6">
          <h1 className="text-xl font-bold">系统设置</h1>
          <nav className="flex gap-1">
            <button onClick={() => navigate("/teacher/settings")}
              className="px-3 py-1 rounded text-sm bg-white/20 font-medium">
              模型配置
            </button>
            <button onClick={() => navigate("/teacher/settings/password")}
              className="px-3 py-1 rounded text-sm hover:bg-white/20 transition-colors">
              修改密码
            </button>
          </nav>
        </div>
        <button onClick={() => navigate("/teacher/dashboard")}
          className="bg-white/20 px-3 py-1 rounded hover:bg-white/30">返回</button>
      </header>
      <main className="max-w-lg mx-auto p-4 space-y-4">
        {/* 大模型配置 */}
        <div className="bg-white rounded-lg shadow p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">大模型配置</h2>
            <button onClick={handleQueryModel} disabled={querying}
              className="text-xs bg-green-600 text-white px-3 py-1.5 rounded hover:bg-green-700 disabled:opacity-50">
              {querying ? "查询中…" : "查询当前模型"}
            </button>
          </div>

          {/* 查询结果 */}
          {queryError && (
            <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{queryError}</div>
          )}
          {modelQuery && (
            <div className="bg-green-50 border border-green-200 rounded p-4 text-sm space-y-1">
              <div className="flex items-center gap-2 mb-2">
                <span className="font-semibold text-green-800">{modelQuery.model}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${modelQuery.available ? "bg-green-200 text-green-800" : "bg-red-200 text-red-800"}`}>
                  {modelQuery.available ? "可用" : "不可用"}
                </span>
              </div>
              <div className="text-gray-600 grid grid-cols-2 gap-x-4 gap-y-1">
                <div><span className="text-gray-400">API 地址：</span>{modelQuery.api_base}</div>
                <div><span className="text-gray-400">查询方式：</span>{modelQuery.source === "retrieve" ? "模型检索" : modelQuery.source === "list" ? "列表匹配" : "未知"}</div>
                {modelQuery.model_info?.owned_by && (
                  <div><span className="text-gray-400">提供方：</span>{modelQuery.model_info.owned_by}</div>
                )}
                {modelQuery.model_info?.created && (
                  <div><span className="text-gray-400">创建时间：</span>{new Date(modelQuery.model_info.created * 1000).toLocaleDateString()}</div>
                )}
              </div>
              {!modelQuery.available && modelQuery.test_error && (
                <div className="text-red-600 mt-1">错误：{modelQuery.test_error}</div>
              )}
            </div>
          )}

          {models.length === 0 ? (
            <div className="text-center py-8 text-gray-400">
              <p className="mb-3">暂无模型配置，请添加模型</p>
              <button onClick={handleAddModel}
                className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 text-sm">
                + 添加模型
              </button>
            </div>
          ) : (
            <>
              {models.map((m, i) => (
                <ModelCard key={i} cfg={m} active={active === i}
                  onActivate={() => handleActivate(i)}
                  onChange={(c) => { const nm = [...models]; nm[i] = c; setModels(nm); }}
                  onSave={() => handleSaveModel(i)}
                  onDelete={() => handleDeleteModel(i)}
                  saving={saving} />
              ))}
              <button onClick={handleAddModel}
                className="w-full border-2 border-dashed border-gray-300 text-gray-400 py-3 rounded-lg hover:border-blue-400 hover:text-blue-500 transition-colors text-sm">
                + 添加模型
              </button>
            </>
          )}
        </div>

        {/* 重启服务 */}
        <div className="bg-white rounded-lg shadow p-6 space-y-4">
          <h2 className="text-lg font-semibold">系统管理</h2>
          <p className="text-sm text-gray-500">修改当前使用模型的并发数后，需要重启服务才能生效。</p>

          {/* 任务队列状态 */}
          {queueInfo && (
            <div className="bg-gray-50 rounded p-3 text-sm space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-medium text-gray-700">任务队列</span>
                <div className="flex items-center gap-2">
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
                      <span className={`w-1.5 h-1.5 rounded-full ${
                        item._status === "running" ? "bg-green-500 animate-pulse" : "bg-gray-300"
                      }`} />
                      <span className={`px-1.5 py-0.5 rounded font-medium text-white ${
                        item.type === "ref_analyze" ? "bg-purple-500" :
                        item.type === "batch_grade" ? "bg-orange-500" :
                        item.type === "grade" ? "bg-green-500" : "bg-blue-500"
                      }`}>
                        {item.type === "ref_analyze" ? "教师分析" :
                         item.type === "batch_grade" ? "批量评分" :
                         item.type === "grade" ? "评分" : "分析"}
                      </span>
                      <span className="text-gray-600">{item.qid && `题${item.qid}`}</span>
                      {item.name && <span className="text-gray-500">{item.name}</span>}
                      {item.name && item.student_id && <span className="text-gray-400">{item.student_id}</span>}
                      {item.count && <span className="text-gray-400">{item.count}人</span>}
                      <span className="ml-auto text-gray-400">
                        {item._status === "running" ? "执行中" : "排队"}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <button onClick={handleRestart} disabled={restarting}
            className="bg-red-600 text-white px-4 py-2 rounded hover:bg-red-700 disabled:opacity-50 text-sm">
            {restarting ? "重启中…" : "重启服务"}
          </button>
        </div>

        {msg && <p className={`text-sm text-center ${msg.includes("失败") ? "text-red-500" : "text-green-600"}`}>{msg}</p>}
      </main>
    </div>
  );
}
