import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  checkLogin,
  teacherLogout,
  getTeacherQuestions,
  createQuestion,
  updateQuestion,
  deleteQuestion,
  getGrades,
  getQuestionDetail,
  getClasses,
  getClassStudents,
  createClass,
  deleteClass,
  downloadRosterTemplate,
  getScoringTemplates,
} from "../api";

interface Question {
  id: string;
  title: string;
  files?: any;
}

export default function TeacherDashboard() {
  const navigate = useNavigate();
  const [questions, setQuestions] = useState<Question[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [gradesView, setGradesView] = useState<string | null>(null);
  const [gradeData, setGradeData] = useState<any[]>([]);
  const [rosterView, setRosterView] = useState(false);
  const [classes, setClasses] = useState<{ class_name: string; count: number }[]>([]);
  const [selectedClass, setSelectedClass] = useState<string | null>(null);
  const [classStudents, setClassStudents] = useState<any[]>([]);
  const [newClassName, setNewClassName] = useState("");
  const [rosterFile, setRosterFile] = useState<File | null>(null);

  // form fields
  const [qid, setQid] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [phase1Criteria, setPhase1Criteria] = useState("");
  const [phase2Criteria, setPhase2Criteria] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [refPdf, setRefPdf] = useState<File | null>(null);

  const loadQuestions = useCallback(async () => {
    try {
      const data = await getTeacherQuestions();
      setQuestions(data);
    } catch (e: any) {
      if (e.message?.includes("401") || e.message?.includes("请先登录")) {
        navigate("/teacher");
      }
    }
  }, [navigate]);

  useEffect(() => {
    checkLogin().catch(() => navigate("/teacher"));
    loadQuestions();
  }, [navigate, loadQuestions]);

  const resetForm = () => {
    setQid("");
    setTitle("");
    setDescription("");
    setPhase1Criteria("");
    setPhase2Criteria("");
    setImage(null);
    setRefPdf(null);
    setEditingId(null);
    setShowForm(false);
  };

  const handleCreate = async () => {
    const fd = new FormData();
    fd.append("qid", qid);
    fd.append("title", title);
    fd.append("description", description);
    fd.append("phase1_criteria", phase1Criteria);
    fd.append("phase2_criteria", phase2Criteria);
    if (image) fd.append("image", image);
    if (refPdf) fd.append("reference_pdf", refPdf);
    try {
      await createQuestion(fd);
      resetForm();
      loadQuestions();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleUpdate = async () => {
    if (!editingId) return;
    const fd = new FormData();
    fd.append("title", title);
    fd.append("description", description);
    fd.append("phase1_criteria", phase1Criteria);
    fd.append("phase2_criteria", phase2Criteria);
    if (image) fd.append("image", image);
    if (refPdf) fd.append("reference_pdf", refPdf);
    try {
      await updateQuestion(editingId, fd);
      resetForm();
      loadQuestions();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleDelete = async (qid: string) => {
    if (!confirm(`确定删除题目 ${qid}？此操作不可恢复。`)) return;
    try {
      await deleteQuestion(qid);
      loadQuestions();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleEdit = async (q: Question) => {
    try {
      const detail = await getQuestionDetail(q.id);
      setEditingId(q.id);
      setQid(q.id);
      setTitle(q.title);
      setDescription(detail.files?.description || "");
      setPhase1Criteria(detail.files?.phase1_criteria || "");
      setPhase2Criteria(detail.files?.phase2_criteria || "");
      setImage(null);
      setRefPdf(null);
      setShowForm(true);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleViewGrades = async (qid: string) => {
    try {
      const data = await getGrades(qid);
      setGradeData(data.grades || []);
      setGradesView(qid);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const loadClasses = async () => {
    try {
      const data = await getClasses();
      setClasses(data.classes || []);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleOpenRoster = () => {
    setRosterView(true);
    loadClasses();
  };

  const handleViewClass = async (className: string) => {
    setSelectedClass(className);
    try {
      const data = await getClassStudents(className);
      setClassStudents(data.students || []);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleCreateClass = async () => {
    if (!newClassName || !rosterFile) {
      alert("请填写班级名称并上传 CSV 文件");
      return;
    }
    try {
      await createClass(newClassName, rosterFile);
      setNewClassName("");
      setRosterFile(null);
      loadClasses();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleDeleteClass = async (className: string) => {
    if (!confirm(`确定删除班级「${className}」？`)) return;
    try {
      await deleteClass(className);
      if (selectedClass === className) {
        setSelectedClass(null);
        setClassStudents([]);
      }
      loadClasses();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleLogout = async () => {
    await teacherLogout();
    navigate("/teacher");
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-blue-700 text-white p-4 shadow flex justify-between items-center">
        <h1 className="text-xl font-bold">教师后台</h1>
        <div className="flex gap-2">
          <button
            onClick={handleOpenRoster}
            className="bg-white/20 px-3 py-1 rounded hover:bg-white/30"
          >
            学生信息
          </button>
          <button
            onClick={() => navigate("/teacher/settings")}
            className="bg-white/20 px-3 py-1 rounded hover:bg-white/30"
          >
            设置
          </button>
          <button
            onClick={handleLogout}
            className="bg-white/20 px-3 py-1 rounded hover:bg-white/30"
          >
            登出
          </button>
        </div>
      </header>

      <main className="max-w-4xl mx-auto p-4 space-y-6">
        {/* Question List */}
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold">题目管理</h2>
            <button
              onClick={async () => {
                resetForm();
                try {
                  const templates = await getScoringTemplates();
                  setPhase1Criteria(templates.phase1 || "");
                  setPhase2Criteria(templates.phase2 || "");
                } catch (_) {}
                setShowForm(true);
              }}
              className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
            >
              新增题目
            </button>
          </div>
          {questions.length === 0 ? (
            <p className="text-gray-400">暂无题目，请新增</p>
          ) : (
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-gray-50 border-b">
                  <th className="text-left p-2">题号</th>
                  <th className="text-left p-2">标题</th>
                  <th className="text-right p-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {questions.map((q) => (
                  <tr key={q.id} className="border-b hover:bg-gray-50">
                    <td className="p-2 font-mono">{q.id}</td>
                    <td className="p-2">{q.title}</td>
                    <td className="p-2 text-right space-x-2">
                      <button onClick={() => handleEdit(q)} className="text-blue-600 hover:underline">编辑</button>
                      <button onClick={() => handleViewGrades(q.id)} className="text-green-600 hover:underline">成绩</button>
                      <button onClick={() => handleDelete(q.id)} className="text-red-600 hover:underline">删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Question Form Modal */}
        {showForm && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-auto">
              <h2 className="text-lg font-semibold mb-4">{editingId ? "编辑题目" : "新增题目"}</h2>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">题号</label>
                  <input
                    value={qid}
                    onChange={(e) => setQid(e.target.value)}
                    disabled={!!editingId}
                    className="w-full border rounded px-3 py-2 disabled:bg-gray-100"
                    placeholder="如：题1"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">标题</label>
                  <input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    className="w-full border rounded px-3 py-2"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">题目内容</label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={4}
                    className="w-full border rounded px-3 py-2"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">第一阶段评分标准（图形相似度）</label>
                  <textarea
                    value={phase1Criteria}
                    onChange={(e) => setPhase1Criteria(e.target.value)}
                    rows={4}
                    className="w-full border rounded px-3 py-2"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">第二阶段评分标准（批改要求）</label>
                  <textarea
                    value={phase2Criteria}
                    onChange={(e) => setPhase2Criteria(e.target.value)}
                    rows={6}
                    className="w-full border rounded px-3 py-2"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">题目附图</label>
                  <input type="file" accept="image/*" onChange={(e) => setImage(e.target.files?.[0] || null)} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">参考工程图 (PDF)</label>
                  <input type="file" accept=".pdf" onChange={(e) => setRefPdf(e.target.files?.[0] || null)} />
                </div>
              </div>
              <div className="flex justify-end gap-3 mt-6">
                <button onClick={resetForm} className="px-4 py-2 border rounded hover:bg-gray-50">取消</button>
                <button
                  onClick={editingId ? handleUpdate : handleCreate}
                  className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
                >
                  {editingId ? "保存修改" : "创建"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Grades Modal */}
        {gradesView && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-4xl mx-4 max-h-[90vh] overflow-auto">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-lg font-semibold">成绩列表 - {gradesView}</h2>
                <button onClick={() => setGradesView(null)} className="px-3 py-1 border rounded hover:bg-gray-50">关闭</button>
              </div>
              {gradeData.length === 0 ? (
                <p className="text-gray-400">暂无成绩</p>
              ) : (
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="bg-gray-50 border-b">
                      {Object.keys(gradeData[0]).map((k) => (
                        <th key={k} className="text-left p-2 whitespace-nowrap">{k}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {gradeData.map((row, i) => (
                      <tr key={i} className="border-b hover:bg-gray-50">
                        {Object.values(row).map((v: any, j) => (
                          <td key={j} className="p-2 max-w-xs truncate">{v}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}

        {/* Roster Modal — 独立班级管理 */}
        {rosterView && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-3xl mx-4 max-h-[90vh] overflow-auto">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-lg font-semibold">学生信息管理</h2>
                <button onClick={() => { setRosterView(false); setSelectedClass(null); setClassStudents([]); }} className="px-3 py-1 border rounded hover:bg-gray-50">关闭</button>
              </div>

              {/* 下载模版 */}
              <div className="mb-4">
                <button onClick={downloadRosterTemplate} className="text-sm text-blue-600 hover:underline border px-3 py-1 rounded">
                  下载模版 CSV
                </button>
                <span className="text-xs text-gray-400 ml-2">模版仅含表头（姓名,学号），请按此格式填写学生信息</span>
              </div>

              {/* 增加班级 */}
              <div className="border rounded p-4 mb-4 bg-gray-50">
                <h3 className="text-sm font-semibold mb-3">增加班级</h3>
                <div className="flex gap-3 items-end flex-wrap">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">班级名称</label>
                    <input
                      type="text"
                      value={newClassName}
                      onChange={(e) => setNewClassName(e.target.value)}
                      placeholder="如：机械1班"
                      className="border rounded px-3 py-2 w-40"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">学生名单 CSV</label>
                    <input
                      type="file"
                      accept=".csv"
                      onChange={(e) => setRosterFile(e.target.files?.[0] || null)}
                      className="w-full text-sm"
                    />
                  </div>
                  <button
                    onClick={handleCreateClass}
                    className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 text-sm"
                  >
                    提交
                  </button>
                </div>
              </div>

              {/* 班级列表 */}
              <div>
                <h3 className="text-sm font-semibold mb-2">已有班级</h3>
                {classes.length === 0 ? (
                  <p className="text-gray-400 text-sm">暂无班级，请上传学生名单</p>
                ) : (
                  <div className="space-y-2">
                    {classes.map((c) => (
                      <div key={c.class_name} className="border rounded">
                        <div className="flex items-center justify-between p-3 bg-white hover:bg-gray-50">
                          <button
                            onClick={() => handleViewClass(c.class_name)}
                            className="text-left flex-1 font-medium text-blue-700 hover:underline"
                          >
                            {c.class_name}
                            <span className="text-gray-400 text-sm ml-2">({c.count}人)</span>
                          </button>
                          <button
                            onClick={() => handleDeleteClass(c.class_name)}
                            className="text-red-500 hover:underline text-sm ml-2"
                          >
                            删除
                          </button>
                        </div>
                        {selectedClass === c.class_name && (
                          <div className="border-t p-3 bg-gray-50">
                            {classStudents.length === 0 ? (
                              <p className="text-gray-400 text-sm">无学生数据</p>
                            ) : (
                              <table className="w-full text-sm border-collapse">
                                <thead>
                                  <tr className="border-b">
                                    <th className="text-left p-1">姓名</th>
                                    <th className="text-left p-1">学号</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {classStudents.map((s: any, i: number) => (
                                    <tr key={i} className="border-b">
                                      <td className="p-1">{s.姓名}</td>
                                      <td className="p-1">{s.学号}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
