import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { getSettings, updateSettings, checkLogin } from "../api";

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

function ModelCard({ cfg, active, onActivate, onChange }: {
  cfg: ModelConfig;
  active: boolean;
  onActivate: () => void;
  onChange: (c: ModelConfig) => void;
}) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState("");

  const set = (k: keyof ModelConfig, v: string | number) => onChange({ ...cfg, [k]: v });

  return (
    <div className={`border rounded-lg p-4 ${active ? "border-blue-400 bg-blue-50/30" : "border-gray-200"}`}>
      <div className="flex items-center justify-between mb-3">
        <input value={cfg.name} onChange={e => set("name", e.target.value)}
          className={`font-semibold bg-transparent border-b ${active ? "border-blue-300 text-blue-700" : "border-gray-200 text-gray-700"} outline-none`}
          placeholder="模型名称" style={{ width: "180px" }} />
        {active ? (
          <span className="text-xs text-blue-600 font-medium">当前使用</span>
        ) : (
          <button onClick={onActivate} className="text-xs bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700">启用</button>
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
          <input value={cfg.api_key} onChange={e => set("api_key", e.target.value)}
            className="w-full border rounded px-3 py-1.5 text-sm" />
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
  const [active, setActive] = useState(0);
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    checkLogin().catch(() => navigate("/teacher"));
    getSettings().then((s) => {
      setModels(s.models || []);
      setActive(s.llm_active || 0);
    }).catch(() => {});
  }, [navigate]);

  const handleSave = async () => {
    setSaving(true);
    setMsg("");
    try {
      const body: Record<string, any> = { models, llm_active: active };
      if (password) body.teacher_password = password;
      await updateSettings(body);
      setMsg("设置已保存");
      setPassword("");
    } catch (e: any) {
      setMsg("保存失败: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-blue-700 text-white p-4 shadow flex justify-between items-center">
        <h1 className="text-xl font-bold">系统设置</h1>
        <button onClick={() => navigate("/teacher/dashboard")}
          className="bg-white/20 px-3 py-1 rounded hover:bg-white/30">返回</button>
      </header>
      <main className="max-w-lg mx-auto p-4 space-y-4">
        {/* 大模型配置 */}
        <div className="bg-white rounded-lg shadow p-6 space-y-4">
          <h2 className="text-lg font-semibold">大模型配置</h2>
          {models.map((m, i) => (
            <ModelCard key={i} cfg={m} active={active === i}
              onActivate={() => setActive(i)}
              onChange={(c) => { const nm = [...models]; nm[i] = c; setModels(nm); }} />
          ))}
        </div>

        {/* 密码 */}
        <div className="bg-white rounded-lg shadow p-6 space-y-4">
          <h2 className="text-lg font-semibold">修改密码</h2>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">新密码</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              className="w-full border rounded px-3 py-2" placeholder="留空则不修改" />
          </div>
        </div>

        {/* 保存 */}
        <button onClick={handleSave} disabled={saving}
          className="w-full bg-blue-600 text-white py-2 rounded hover:bg-blue-700 disabled:opacity-50">
          {saving ? "保存中..." : "保存设置"}
        </button>
        {msg && <p className={`text-sm text-center ${msg.includes("失败") ? "text-red-500" : "text-green-600"}`}>{msg}</p>}
      </main>
    </div>
  );
}
