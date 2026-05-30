import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { changePassword, checkLogin } from "../api";

export default function PasswordPage() {
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    checkLogin().catch(() => navigate("/teacher"));
  }, [navigate]);

  const handleSave = async () => {
    if (!currentPassword) {
      setMsg("请输入当前密码");
      return;
    }
    if (!newPassword) {
      setMsg("请输入新密码");
      return;
    }
    if (currentPassword === newPassword) {
      setMsg("新密码不能与当前密码相同");
      return;
    }
    setSaving(true);
    setMsg("");
    try {
      await changePassword(currentPassword, newPassword);
      setMsg("密码已修改");
      setCurrentPassword("");
      setNewPassword("");
    } catch (e: any) {
      setMsg(e.message || "修改失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-blue-700 text-white p-4 shadow flex justify-between items-center">
        <div className="flex items-center gap-6">
          <h1 className="text-xl font-bold">系统设置</h1>
          <nav className="flex gap-1">
            <button onClick={() => navigate("/teacher/settings")}
              className="px-3 py-1 rounded text-sm hover:bg-white/20 transition-colors">
              模型配置
            </button>
            <button onClick={() => navigate("/teacher/settings/password")}
              className="px-3 py-1 rounded text-sm bg-white/20 font-medium">
              修改密码
            </button>
          </nav>
        </div>
        <button onClick={() => navigate("/teacher/dashboard")}
          className="bg-white/20 px-3 py-1 rounded hover:bg-white/30">返回</button>
      </header>
      <main className="max-w-lg mx-auto p-4">
        <div className="bg-white rounded-lg shadow p-6 space-y-4">
          <h2 className="text-lg font-semibold">修改密码</h2>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">当前密码</label>
            <input type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)}
              className="w-full border rounded px-3 py-2" placeholder="输入当前密码" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">新密码</label>
            <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
              className="w-full border rounded px-3 py-2" placeholder="输入新密码" />
          </div>
          <button onClick={handleSave} disabled={saving}
            className="w-full bg-blue-600 text-white py-2 rounded hover:bg-blue-700 disabled:opacity-50">
            {saving ? "保存中..." : "修改密码"}
          </button>
          {msg && <p className={`text-sm text-center ${msg.includes("失败") || msg.includes("错误") || msg.includes("不能") ? "text-red-500" : "text-green-600"}`}>{msg}</p>}
        </div>
      </main>
    </div>
  );
}
