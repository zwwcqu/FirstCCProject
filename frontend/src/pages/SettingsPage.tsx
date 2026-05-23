import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { getSettings, updateSettings, checkLogin } from "../api";

export default function SettingsPage() {
  const navigate = useNavigate();
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    checkLogin().catch(() => navigate("/teacher"));
    getSettings().then((s) => {
      setApiBase(s.llm_api_base || "");
      setApiKey(s.llm_api_key || "");
      setModel(s.llm_model || "");
    }).catch(() => {});
  }, [navigate]);

  const handleSave = async () => {
    setSaving(true);
    setMsg("");
    try {
      const body: Record<string, string> = {};
      if (apiBase) body.llm_api_base = apiBase;
      if (apiKey) body.llm_api_key = apiKey;
      if (model) body.llm_model = model;
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
        <button
          onClick={() => navigate("/teacher/dashboard")}
          className="bg-white/20 px-3 py-1 rounded hover:bg-white/30"
        >
          返回
        </button>
      </header>
      <main className="max-w-lg mx-auto p-4">
        <div className="bg-white rounded-lg shadow p-6 space-y-4">
          <h2 className="text-lg font-semibold">大模型配置</h2>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">API 地址</label>
            <input
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              className="w-full border rounded px-3 py-2"
              placeholder="http://127.0.0.1:1234/v1"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
            <input
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full border rounded px-3 py-2"
              placeholder="sk-xxx"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full border rounded px-3 py-2"
              placeholder="qwen2.5-vl-7b-instruct"
            />
          </div>

          <hr />
          <h2 className="text-lg font-semibold">修改密码</h2>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">新密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full border rounded px-3 py-2"
              placeholder="留空则不修改"
            />
          </div>

          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full bg-blue-600 text-white py-2 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? "保存中..." : "保存设置"}
          </button>
          {msg && <p className={`text-sm text-center ${msg.includes("失败") ? "text-red-500" : "text-green-600"}`}>{msg}</p>}
        </div>
      </main>
    </div>
  );
}
