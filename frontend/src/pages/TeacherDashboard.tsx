import { useState, useEffect, useCallback, useRef } from "react";
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
  triggerAnalysis,
  getAnalysisResult,
  getTeacherPreviewUrl,
  batchGrade,
  editGrade,
  getTeacherStudentPreviewUrl,
  supplementSubmission,
  refreshGrades,
} from "../api";
import FloatingImageViewer from "../components/FloatingImageViewer";

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
  const [gradeColumns, setGradeColumns] = useState<string[]>([]);
  const [rosterView, setRosterView] = useState(false);
  const [classes, setClasses] = useState<{ class_name: string; count: number }[]>([]);
  const [selectedClass, setSelectedClass] = useState<string | null>(null);
  const [classStudents, setClassStudents] = useState<any[]>([]);
  const [newClassName, setNewClassName] = useState("");
  const [rosterFile, setRosterFile] = useState<File | null>(null);

  // 参考图分析状态
  const [analyzingQid, setAnalyzingQid] = useState<string | null>(null);
  const [analysisResults, setAnalysisResults] = useState<Record<string, any>>({});
  const [expandedAnalysis, setExpandedAnalysis] = useState<Record<string, boolean>>({});

  // 成绩管理
  const [selectedStudents, setSelectedStudents] = useState<Set<string>>(new Set());
  const [batchGrading, setBatchGrading] = useState(false);
  const [editingCell, setEditingCell] = useState<{ sid: string; col: string } | null>(null);
  const [editValue, setEditValue] = useState("");
  const [previewImage, setPreviewImage] = useState<string | null>(null);

  // 补充提交弹窗
  const [supplementModal, setSupplementModal] = useState(false);
  const [supplementName, setSupplementName] = useState("");
  const [supplementSid, setSupplementSid] = useState("");
  const [supplementFile, setSupplementFile] = useState<File | null>(null);
  const [supplementSubmitting, setSupplementSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // 查看作业弹窗
  const [reviewSid, setReviewSid] = useState<string | null>(null);
  const [reviewComment, setReviewComment] = useState("");
  const [reviewGrade, setReviewGrade] = useState("");

  // 浮动图面板
  const [floatQid, setFloatQid] = useState<string | null>(null);

  // 窗口拖动（仅查看作业弹窗需要，浮动图已收归 FloatingImageViewer）
  const reviewModalRef = useRef<HTMLDivElement>(null);
  const [modalPos, setModalPos] = useState<{ x: number; y: number } | null>(null);
  const modalMoveRef = useRef<{ mx: number; my: number; ox: number; oy: number } | null>(null);

  // 展开分析结果时自动显示浮动图
  useEffect(() => {
    const active = Object.entries(expandedAnalysis).find(([, v]) => v);
    if (active) {
      setFloatQid(active[0]);
    } else {
      setFloatQid(null);
    }
  }, [expandedAnalysis]);

  // form fields
  const [qid, setQid] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [phase1Criteria, setPhase1Criteria] = useState("");
  const [phase2Criteria, setPhase2Criteria] = useState("");
  const [knowledge, setKnowledge] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [refPdf, setRefPdf] = useState<File | null>(null);

  const loadQuestions = useCallback(async () => {
    try {
      const data = await getTeacherQuestions();
      setQuestions(data);
      // 加载已有分析结果
      for (const q of data) {
        if (q.files?.reference_pdf) {
          try {
            const res = await getAnalysisResult(q.id);
            if (res.ready && res.analysis) {
              setAnalysisResults((prev) => ({ ...prev, [q.id]: res.analysis }));
            }
          } catch (_) { /* 该题尚未分析 */ }
        }
      }
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

  // 轮询参考图分析结果
  useEffect(() => {
    if (!analyzingQid) return;
    const timer = setInterval(async () => {
      try {
        const res = await getAnalysisResult(analyzingQid);
        if (res.ready && res.analysis) {
          setAnalysisResults((prev) => ({ ...prev, [analyzingQid]: res.analysis }));
          setAnalyzingQid(null);
          clearInterval(timer);
        }
      } catch (_) { /* 继续轮询 */ }
    }, 3000);  // 每 3 秒查询一次
    return () => clearInterval(timer);
  }, [analyzingQid]);

  const resetForm = () => {
    setQid("");
    setTitle("");
    setDescription("");
    setPhase1Criteria("");
    setPhase2Criteria("");
    setKnowledge("");
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
    fd.append("knowledge", knowledge);
    if (image) fd.append("image", image);
    if (refPdf) fd.append("reference_pdf", refPdf);
    try {
      await createQuestion(fd);
      resetForm();
      loadQuestions();
      if (refPdf) setAnalyzingQid(qid);
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
    fd.append("knowledge", knowledge);
    if (image) fd.append("image", image);
    if (refPdf) fd.append("reference_pdf", refPdf);
    try {
      await updateQuestion(editingId, fd);
      resetForm();
      loadQuestions();
      if (refPdf) setAnalyzingQid(editingId);  // 换参考图后重新分析
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
      setKnowledge(detail.files?.knowledge || "");
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
      setGradeColumns(data.columns || []);
      setGradesView(qid);
      setSelectedStudents(new Set());
    } catch (e: any) {
      alert(e.message);
    }
  };

  const toggleSelect = (sid: string) => {
    setSelectedStudents((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid); else next.add(sid);
      return next;
    });
  };

  const toggleSelectAll = () => {
    const allIds = gradeData.map((r: any) => r["学号"]).filter(Boolean);
    if (selectedStudents.size === allIds.length) {
      setSelectedStudents(new Set());
    } else {
      setSelectedStudents(new Set(allIds));
    }
  };

  const handleBatchGrade = async () => {
    if (!gradesView || selectedStudents.size === 0) return;
    setBatchGrading(true);
    const ids = Array.from(selectedStudents);
    try {
      await batchGrade(gradesView, ids);
      // 立即更新本地状态为"评分中"，不等刷新
      setGradeData((prev) =>
        prev.map((r: any) =>
          ids.includes(r["学号"]) ? { ...r, _status: "grading" } : r
        )
      );
      setSelectedStudents(new Set());
    } catch (e: any) {
      alert(e.message);
    } finally {
      setBatchGrading(false);
    }
  };

  const handleSupplement = async () => {
    if (!gradesView || !supplementName.trim() || !supplementSid.trim() || !supplementFile) {
      alert("请填写姓名、学号并选择文件");
      return;
    }
    setSupplementSubmitting(true);
    try {
      await supplementSubmission(gradesView, supplementName.trim(), supplementSid.trim(), supplementFile);
      setSupplementModal(false);
      setSupplementName("");
      setSupplementSid("");
      setSupplementFile(null);
      handleViewGrades(gradesView); // 刷新成绩列表
    } catch (e: any) {
      alert(e.message);
    } finally {
      setSupplementSubmitting(false);
    }
  };

  const startEdit = (sid: string, col: string, value: string) => {
    setEditingCell({ sid, col });
    setEditValue(value);
  };

  const saveEdit = async () => {
    if (!editingCell || !gradesView) return;
    try {
      await editGrade(gradesView, editingCell.sid, { [editingCell.col]: editValue });
      setGradeData((prev) =>
        prev.map((r: any) =>
          r["学号"] === editingCell.sid ? { ...r, [editingCell.col]: editValue } : r
        )
      );
    } catch (e: any) {
      alert(e.message);
    }
    setEditingCell(null);
  };

  const GRADE_OPTIONS = ["A+", "A", "B+", "B", "C+", "C", "D+", "D", "F"];

  const handleOpenReview = (sid: string) => {
    if (!gradesView) return;
    const row = gradeData.find((r: any) => r["学号"] === sid);
    setReviewSid(sid);
    setReviewGrade(row?.["成绩"] || "");
    setReviewComment(row?.["教师评语"] || "");
    setSavedText("");
  };

  const [saving, setSaving] = useState(false);
  const [savedText, setSavedText] = useState("");

  const handleSave = async () => {
    if (!gradesView || !reviewSid) return;
    setSaving(true);
    try {
      const fields: Record<string, string> = {};
      if (reviewGrade) fields["成绩"] = reviewGrade;
      fields["教师评语"] = reviewComment;
      await editGrade(gradesView, reviewSid, fields);
      setGradeData((prev) =>
        prev.map((r: any) => {
          if (r["学号"] !== reviewSid) return r;
          const u = { ...r };
          if (reviewGrade) u["成绩"] = reviewGrade;
          u["教师评语"] = reviewComment;
          return u;
        })
      );
      setSavedText("已保存");
      setTimeout(() => setSavedText(""), 1500);
    } catch (e: any) {
      alert(e.message);
    } finally {
      setSaving(false);
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
                  <th className="text-center p-2">参考图分析</th>
                  <th className="text-right p-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {questions.map((q) => (
                  <tr key={q.id} className="border-b hover:bg-gray-50">
                    <td className="p-2 font-mono">{q.id}</td>
                    <td className="p-2">{q.title}</td>
                    <td className="p-2 text-center">
                      {analyzingQid === q.id ? (
                        <span className="text-yellow-600 text-xs animate-pulse">分析中…</span>
                      ) : analysisResults[q.id] ? (
                        <div className="flex items-center justify-center gap-1">
                          <button
                            onClick={() => setExpandedAnalysis((prev) => ({ ...prev, [q.id]: !prev[q.id] }))}
                            className="text-blue-600 hover:underline text-xs"
                          >
                            {expandedAnalysis[q.id] ? "收起" : "查看"}
                          </button>
                          <button
                            onClick={async () => {
                              setAnalyzingQid(q.id);
                              try { await triggerAnalysis(q.id); } catch (_) {}
                            }}
                            className="text-orange-500 hover:underline text-xs"
                            title="重新分析参考图"
                          >
                            重分析
                          </button>
                        </div>
                      ) : q.files?.reference_pdf ? (
                        <button
                          onClick={async () => {
                            setAnalyzingQid(q.id);
                            try { await triggerAnalysis(q.id); } catch (_) {}
                          }}
                          className="text-gray-500 hover:text-blue-600 text-xs"
                        >
                          分析
                        </button>
                      ) : (
                        <span className="text-gray-300 text-xs">-</span>
                      )}
                    </td>
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
          {/* 展开的分析结果 */}
          {Object.entries(analysisResults).map(([qid, analysis]) =>
            expandedAnalysis[qid] ? (
              <div key={qid} className="mt-3 border-t pt-3 text-xs">
                <h4 className="text-sm font-semibold mb-3">题{qid} 参考图分析结果</h4>

                {/* 参考工程图预览 */}
                {(() => {
                  const q = questions.find((x) => x.id === qid);
                  if (q?.files?.reference_pdf) {
                    return (
                      <div className="mb-3">
                        <p className="text-xs text-gray-500 mb-1">参考工程图</p>
                        <img
                          src={getTeacherPreviewUrl(qid, q.files.reference_pdf, Date.now())}
                          alt="参考工程图"
                          className="w-full rounded border"
                        />
                      </div>
                    );
                  }
                  return null;
                })()}

                {/* ===== 结构分析 ===== */}
                <details className="mb-3" open>
                  <summary className="text-sm font-medium text-blue-700 cursor-pointer hover:underline mb-2">结构分析</summary>

                  {/* 标题栏 */}
                  {analysis.structure?.title_block && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-blue-50"><th colSpan={4} className="p-1 text-left text-blue-800">标题栏</th></tr></thead>
                      <tbody>
                        <tr className="border">
                          <td className="p-1 text-gray-500 w-16">零件名称</td><td className="p-1">{analysis.structure.title_block.part_name || "-"}</td>
                          <td className="p-1 text-gray-500 w-16">图号</td><td className="p-1">{analysis.structure.title_block.drawing_number || "-"}</td>
                        </tr>
                        <tr className="border">
                          <td className="p-1 text-gray-500">材料</td><td className="p-1">{analysis.structure.title_block.material || "-"}</td>
                          <td className="p-1 text-gray-500">比例</td><td className="p-1">{analysis.structure.title_block.scale || "-"}</td>
                        </tr>
                      </tbody>
                    </table>
                  )}

                  {/* 整体形状 */}
                  {analysis.structure?.overall_shape && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-blue-50"><th colSpan={4} className="p-1 text-left text-blue-800">整体形状</th></tr></thead>
                      <tbody>
                        <tr className="border">
                          <td className="p-1 text-gray-500 w-16">类型</td><td className="p-1">{analysis.structure.overall_shape.type || "-"}</td>
                          <td className="p-1 text-gray-500 w-16">对称性</td><td className="p-1">{analysis.structure.overall_shape.symmetry || "-"}</td>
                        </tr>
                        <tr className="border">
                          <td className="p-1 text-gray-500">外形尺寸</td><td className="p-1">{analysis.structure.overall_shape.approx_dimensions || "-"}</td>
                          <td className="p-1 text-gray-500">材料标注</td><td className="p-1">{analysis.structure.overall_shape.material_text || (analysis.structure.overall_shape.has_material_label ? "有" : "无")}</td>
                        </tr>
                      </tbody>
                    </table>
                  )}

                  {/* 视图列表 */}
                  {analysis.structure?.views?.length > 0 && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-blue-50"><th colSpan={3} className="p-1 text-left text-blue-800">视图组成（{analysis.structure.views.length} 个）</th></tr></thead>
                      <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">名称</th><th className="p-1 text-left">类型</th><th className="p-1 text-left">说明</th></tr></thead>
                      <tbody>
                        {analysis.structure.views.map((v: any, i: number) => (
                          <tr key={i} className="border">
                            <td className="p-1">{v.name}</td><td className="p-1">{v.type}</td><td className="p-1 text-gray-600">{v.description || "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {/* 特征列表 */}
                  {analysis.structure?.features?.length > 0 && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-blue-50"><th colSpan={5} className="p-1 text-left text-blue-800">结构特征（{analysis.structure.features.length} 个）</th></tr></thead>
                      <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">ID</th><th className="p-1 text-left">类型</th><th className="p-1 text-center w-10">数量</th><th className="p-1 text-left">位置</th><th className="p-1 text-left">备注</th></tr></thead>
                      <tbody>
                        {analysis.structure.features.map((f: any, i: number) => (
                          <tr key={i} className="border">
                            <td className="p-1 font-mono">{f.id}</td><td className="p-1">{f.type}</td><td className="p-1 text-center">{f.count}</td><td className="p-1">{f.location || "-"}</td><td className="p-1 text-gray-600">{f.notes || "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {analysis.structure?.technical_notes && (
                    <p className="mb-2"><span className="text-gray-500">技术要求：</span>{analysis.structure.technical_notes}</p>
                  )}
                </details>

                {/* ===== 量化分析 ===== */}
                <details className="mb-3" open>
                  <summary className="text-sm font-medium text-blue-700 cursor-pointer hover:underline mb-2">量化分析</summary>

                  {/* 通用信息 */}
                  {analysis.quantitative?.general_notes && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-green-50"><th colSpan={6} className="p-1 text-left text-green-800">图纸通用信息</th></tr></thead>
                      <tbody>
                        <tr className="border">
                          <td className="p-1 text-gray-500">比例</td><td className="p-1">{analysis.quantitative.general_notes.scale || "-"}</td>
                          <td className="p-1 text-gray-500">未注公差</td><td className="p-1">{analysis.quantitative.general_notes.general_tolerance || "-"}</td>
                          <td className="p-1 text-gray-500">热处理</td><td className="p-1">{analysis.quantitative.general_notes.heat_treatment || "-"}</td>
                        </tr>
                        <tr className="border">
                          <td className="p-1 text-gray-500">表面处理</td><td className="p-1">{analysis.quantitative.general_notes.surface_treatment || "-"}</td>
                          <td className="p-1 text-gray-500">未注圆角</td><td className="p-1">{analysis.quantitative.general_notes.unspecified_rounds || "-"}</td>
                          <td className="p-1 text-gray-500">未注倒角</td><td className="p-1">{analysis.quantitative.general_notes.unspecified_chamfers || "-"}</td>
                        </tr>
                      </tbody>
                    </table>
                  )}

                  {/* 尺寸列表 */}
                  {analysis.quantitative?.dimensions?.length > 0 && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-green-50"><th colSpan={6} className="p-1 text-left text-green-800">尺寸标注（{analysis.quantitative.dimensions.length} 个）</th></tr></thead>
                      <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">ID</th><th className="p-1 text-left">类型</th><th className="p-1 text-right">数值</th><th className="p-1 text-left">公差</th><th className="p-1 text-left">关联特征</th><th className="p-1 text-left">说明</th></tr></thead>
                      <tbody>
                        {analysis.quantitative.dimensions.map((d: any, i: number) => (
                          <tr key={i} className="border">
                            <td className="p-1 font-mono">{d.id}</td>
                            <td className="p-1">{d.type}</td>
                            <td className="p-1 text-right font-mono">{d.value}{d.unit && d.unit !== "无" ? d.unit : ""}</td>
                            <td className="p-1 font-mono">{d.tolerance || "-"}</td>
                            <td className="p-1 font-mono">{d.feature_ref || "-"}</td>
                            <td className="p-1 text-gray-600 max-w-xs truncate">{d.description || "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {/* 粗糙度 */}
                  {analysis.quantitative?.surface_roughness?.length > 0 && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-green-50"><th colSpan={3} className="p-1 text-left text-green-800">表面粗糙度（{analysis.quantitative.surface_roughness.length} 处）</th></tr></thead>
                      <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">数值</th><th className="p-1 text-left">关联特征</th><th className="p-1 text-left">位置</th></tr></thead>
                      <tbody>
                        {analysis.quantitative.surface_roughness.map((r: any, i: number) => (
                          <tr key={i} className="border">
                            <td className="p-1 font-mono">{r.value}</td><td className="p-1 font-mono">{r.feature_ref || "-"}</td><td className="p-1 text-gray-600">{r.location || "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {/* 形位公差 */}
                  {analysis.quantitative?.geometric_tolerances?.length > 0 && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-green-50"><th colSpan={6} className="p-1 text-left text-green-800">形位公差（{analysis.quantitative.geometric_tolerances.length} 项）</th></tr></thead>
                      <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">ID</th><th className="p-1 text-left">类型</th><th className="p-1 text-right">数值</th><th className="p-1 text-left">关联特征</th><th className="p-1 text-left">基准</th><th className="p-1 text-left">说明</th></tr></thead>
                      <tbody>
                        {analysis.quantitative.geometric_tolerances.map((g: any, i: number) => (
                          <tr key={i} className="border">
                            <td className="p-1 font-mono">{g.id}</td><td className="p-1">{g.type}</td><td className="p-1 text-right font-mono">{g.value}{g.unit || "mm"}</td><td className="p-1 font-mono">{g.ref_feature || "-"}</td><td className="p-1 font-mono">{g.datum || "-"}</td><td className="p-1 text-gray-600 max-w-xs truncate">{g.description || "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {/* 螺纹 */}
                  {analysis.quantitative?.thread_specs?.length > 0 && (
                    <table className="w-full border mb-2">
                      <thead><tr className="bg-green-50"><th colSpan={5} className="p-1 text-left text-green-800">螺纹规格（{analysis.quantitative.thread_specs.length} 处）</th></tr></thead>
                      <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">ID</th><th className="p-1 text-left">类型</th><th className="p-1 text-left">规格</th><th className="p-1 text-left">关联特征</th><th className="p-1 text-left">备注</th></tr></thead>
                      <tbody>
                        {analysis.quantitative.thread_specs.map((t: any, i: number) => (
                          <tr key={i} className="border">
                            <td className="p-1 font-mono">{t.id}</td><td className="p-1">{t.type}</td><td className="p-1 font-mono">{t.spec}</td><td className="p-1 font-mono">{t.feature_ref || "-"}</td><td className="p-1 text-gray-600">{t.notes || "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {analysis.quantitative?.completeness_notes && (
                    <p className="text-orange-600"><span className="font-medium">完整度提示：</span>{analysis.quantitative.completeness_notes}</p>
                  )}
                </details>

                {/* 原始 JSON（折叠到底部，备查） */}
                <details>
                  <summary className="text-xs text-gray-400 cursor-pointer hover:underline">原始 JSON（调试用）</summary>
                  <pre className="text-xs bg-gray-100 p-2 rounded mt-1 overflow-auto max-h-48 whitespace-pre-wrap">
                    {JSON.stringify(analysis, null, 2)}
                  </pre>
                </details>
              </div>
            ) : null
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
                  <label className="block text-sm font-medium text-gray-700 mb-1">补充知识（辅助大模型理解图纸）</label>
                  <textarea
                    value={knowledge}
                    onChange={(e) => setKnowledge(e.target.value)}
                    rows={4}
                    className="w-full border rounded px-3 py-2"
                    placeholder="例如：零件材料为HT200、表面粗糙度Ra6.3、未注倒角C1等"
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
            <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-5xl mx-4 max-h-[90vh] overflow-auto">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-lg font-semibold">成绩列表 - {gradesView}</h2>
                <div className="flex gap-2">
                  <button
                    onClick={async () => {
                      if (refreshing) return;
                      setRefreshing(true);
                      try {
                        const data = await refreshGrades(gradesView!);
                        setGradeData(data.grades || []);
                        setGradeColumns(data.columns || []);
                        setSelectedStudents(new Set());
                      } catch (_) {}
                      setTimeout(() => setRefreshing(false), 100);
                    }}
                    disabled={refreshing}
                    className="px-3 py-1 border rounded hover:bg-gray-50 text-sm disabled:opacity-50"
                  >{refreshing ? "刷新中…" : "刷新"}</button>
                  <button
                    onClick={() => { setSupplementModal(true); setSupplementName(""); setSupplementSid(""); setSupplementFile(null); }}
                    className="px-3 py-1 bg-orange-500 text-white rounded hover:bg-orange-600 text-sm"
                  >
                    补充提交
                  </button>
                  <button
                    onClick={handleBatchGrade}
                    disabled={selectedStudents.size === 0 || batchGrading}
                    className="px-4 py-1 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50 text-sm"
                  >
                    {batchGrading ? "提交中…" : `批量评分 (${selectedStudents.size})`}
                  </button>
                  <button onClick={() => { setGradesView(null); setEditingCell(null); }} className="px-3 py-1 border rounded hover:bg-gray-50">关闭</button>
                </div>
              </div>
              {gradeData.length === 0 ? (
                <p className="text-gray-400">暂无成绩</p>
              ) : (
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="bg-gray-50 border-b">
                      <th className="text-left p-2 w-8">
                        <input type="checkbox"
                          checked={gradeData.filter((r: any) => r["学号"]).length > 0 && selectedStudents.size === gradeData.filter((r: any) => r["学号"]).length}
                          onChange={toggleSelectAll} />
                      </th>
                      <th className="text-left p-2 w-10">#</th>
                      <th className="text-left p-2 whitespace-nowrap">状态</th>
                      <th className="text-left p-2 whitespace-nowrap">查看作业</th>
                      {gradeColumns.map((k) => (
                        <th key={k} className="text-left p-2 whitespace-nowrap">{k}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {gradeData.map((row: any, i: number) => {
                      const sid = row["学号"] || "";
                      const status = row["_status"] || "";
                      const filename = row["_filename"] || "";
                      const isGraded = status === "graded";
                      const isFailed = status === "grade_failed" || status === "analyze_failed";
                      return (
                        <tr key={i} className={`border-b hover:bg-gray-50 ${isFailed ? "bg-red-50/50" : !isGraded ? "bg-yellow-50/50" : ""}`}>
                          <td className="p-2">
                            <input type="checkbox" checked={selectedStudents.has(sid)}
                              onChange={() => toggleSelect(sid)} />
                          </td>
                          <td className="p-2 text-gray-400">{i + 1}</td>
                          <td className="p-2">
                            {status === "graded" ? (
                              <span className="text-green-600 text-xs font-medium">已评分</span>
                            ) : status === "grading" ? (
                              <span className="text-purple-500 text-xs font-medium">评分中</span>
                            ) : status === "grade_failed" ? (
                              <span className="text-red-500 text-xs font-medium" title={row["_error"] || ""}>评分失败</span>
                            ) : status === "analyzing" ? (
                              <span className="text-purple-400 text-xs font-medium">分析中</span>
                            ) : status === "analyze_failed" ? (
                              <span className="text-red-400 text-xs font-medium" title={row["_error"] || ""}>分析失败</span>
                            ) : status === "analyzed" ? (
                              <span className="text-orange-500 text-xs font-medium">待评分</span>
                            ) : (
                              <span className="text-blue-500 text-xs font-medium">提交未评</span>
                            )}
                          </td>
                          <td className="p-2">
                            {filename ? (
                              <button onClick={() => handleOpenReview(sid)}
                                className="text-blue-600 hover:underline text-xs">查看作业</button>
                            ) : (
                              <span className="text-gray-300 text-xs">-</span>
                            )}
                          </td>
                          {gradeColumns.map((col: string, j: number) => {
                            const isEditing = editingCell?.sid === sid && editingCell?.col === col;
                            const editable = isGraded && ["成绩", "阶段1相似度", "阶段2评分", "总分"].includes(col);
                            return (
                              <td key={j} className="p-2 max-w-[120px] truncate"
                                onDoubleClick={() => editable && startEdit(sid, col, row[col] || "")}>
                                {isEditing ? (
                                  <input
                                    type="text"
                                    value={editValue}
                                    onChange={(e) => setEditValue(e.target.value)}
                                    onBlur={saveEdit}
                                    onKeyDown={(e) => { if (e.key === "Enter") saveEdit(); if (e.key === "Escape") setEditingCell(null); }}
                                    className="w-20 border rounded px-1 py-0.5 text-xs"
                                    autoFocus
                                  />
                                ) : (
                                  <span className={editable ? "cursor-pointer hover:bg-gray-100 rounded px-1" : ""}
                                    title={editable ? "双击编辑" : ""}>
                                    {row[col]}
                                  </span>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}

        {/* Supplement Submission Modal */}
        {supplementModal && gradesView && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[70]">
            <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md mx-4">
              <h3 className="text-lg font-semibold mb-4">补充提交 - 题{gradesView}</h3>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm text-gray-600 mb-1">姓名</label>
                  <input
                    type="text"
                    value={supplementName}
                    onChange={(e) => setSupplementName(e.target.value)}
                    className="w-full border rounded px-3 py-2 text-sm"
                    placeholder="学生姓名"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-600 mb-1">学号</label>
                  <input
                    type="text"
                    value={supplementSid}
                    onChange={(e) => setSupplementSid(e.target.value)}
                    className="w-full border rounded px-3 py-2 text-sm"
                    placeholder="学生学号"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-600 mb-1">作业文件</label>
                  <input
                    type="file"
                    accept=".pdf,.png,.jpg,.jpeg,.gif,.webp"
                    onChange={(e) => setSupplementFile(e.target.files?.[0] || null)}
                    className="w-full text-sm"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2 mt-4">
                <button
                  onClick={() => setSupplementModal(false)}
                  className="px-4 py-2 border rounded hover:bg-gray-50 text-sm"
                >
                  取消
                </button>
                <button
                  onClick={handleSupplement}
                  disabled={supplementSubmitting}
                  className="px-4 py-2 bg-orange-500 text-white rounded hover:bg-orange-600 disabled:opacity-50 text-sm"
                >
                  {supplementSubmitting ? "提交中…" : "提交"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 参考图浮动面板 */}
        {floatQid && (() => {
          const q = questions.find((x) => x.id === floatQid);
          if (!q?.files?.reference_pdf) return null;
          return (
            <FloatingImageViewer
              src={getTeacherPreviewUrl(floatQid, q.files.reference_pdf, Date.now())}
              title={`题${floatQid} 参考图`}
              visible={true}
              onClose={() => setFloatQid(null)}
              initialWidth={320}
              initialHeight={360}
              zIndex={40}
            />
          );
        })()}

        {/* 查看作业浮动图 */}
        {reviewSid && gradesView && (
          <FloatingImageViewer
            src={getTeacherStudentPreviewUrl(gradesView, reviewSid)}
            title="学生工程图"
            visible={true}
            onClose={() => setReviewSid(null)}
            initialWidth={340}
            initialHeight={380}
            zIndex={70}
          />
        )}

        {/* Student Drawing Preview */}
        {previewImage && (
          <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-[60]"
            onClick={() => setPreviewImage(null)}>
            <button className="absolute top-4 right-4 text-white text-2xl hover:text-gray-300 z-10"
              onClick={() => setPreviewImage(null)}>&times;</button>
            <img src={previewImage} alt="学生工程图"
              className="max-w-[90vw] max-h-[90vh] object-contain"
              onClick={(e) => e.stopPropagation()} />
          </div>
        )}

        {/* 查看作业弹窗 */}
        {reviewSid && gradesView && (() => {
          const row = gradeData.find((r: any) => r["学号"] === reviewSid);
          if (!row) return null;
          const isGraded = row["_status"] === "graded";
          const DIMS = ["图样表达", "尺寸标注", "尺寸公差", "表面质量", "形位公差"];
          return (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[65]"
              onClick={(e) => {
                if (e.target === e.currentTarget) setReviewSid(null);
              }}
              onMouseMove={(e) => {
                if (!modalMoveRef.current) return;
                setModalPos({
                  x: modalMoveRef.current.ox + (e.clientX - modalMoveRef.current.mx),
                  y: modalMoveRef.current.oy + (e.clientY - modalMoveRef.current.my),
                });
              }}
              onMouseUp={() => { modalMoveRef.current = null; }}>
              <div ref={reviewModalRef}
                className="bg-white rounded-lg shadow-xl p-6 w-full max-w-4xl mx-4 max-h-[90vh] overflow-auto"
                style={modalPos ? { position: "fixed", left: modalPos.x, top: modalPos.y, margin: 0 } : {}}>
                <div className="flex justify-between items-center mb-4 cursor-grab active:cursor-grabbing select-none"
                  onMouseDown={(e) => {
                    const el = reviewModalRef.current;
                    if (!el) return;
                    const rect = el.getBoundingClientRect();
                    setModalPos({ x: rect.left, y: rect.top });
                    modalMoveRef.current = { mx: e.clientX, my: e.clientY, ox: rect.left, oy: rect.top };
                  }}>
                  <h3 className="text-lg font-semibold">
                    {row["姓名"]} ({row["学号"]}) 的作业
                  </h3>
                  <div className="flex gap-2">
                    <button onClick={handleSave} disabled={saving}
                      onMouseDown={(e) => e.stopPropagation()}
                      className="px-4 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
                      {saving ? "保存中…" : "保存"}
                    </button>
                    {savedText && <span className="text-xs text-green-600">{savedText}</span>}
                    <button onClick={() => setReviewSid(null)}
                      onMouseDown={(e) => e.stopPropagation()}
                      className="px-3 py-1 border rounded hover:bg-gray-50 text-sm">关闭</button>
                  </div>
                </div>

{isGraded ? (
                  <div className="space-y-4 text-sm">
                    {/* 评级下拉 */}
                    <div className="flex items-center gap-3 bg-gray-50 rounded p-3">
                      <span className="text-gray-600">评级：</span>
                      <select
                        value={reviewGrade}
                        onChange={(e) => setReviewGrade(e.target.value)}
                        className="border rounded px-2 py-1 text-sm"
                      >
                        {GRADE_OPTIONS.map((g) => (
                          <option key={g} value={g}>{g}</option>
                        ))}
                      </select>
                      <span className="text-gray-400 text-xs ml-2">
                        阶段1: {row["阶段1相似度"] || "-"}% × 阶段2: {row["阶段2评分"] || "-"}% = 总分: {row["总分"] || "-"}%
                      </span>
                    </div>

                    {/* 阶段一评价 */}
                    <div>
                      <p className="font-medium text-gray-700">阶段一 · 相似度评价</p>
                      <p className="text-gray-600 mt-1">{row["相似度评价"] || "-"}</p>
                    </div>

                    {/* 阶段二评价 */}
                    <div>
                      <p className="font-medium text-gray-700">阶段二 · 量化评分</p>
                      <div className="grid grid-cols-2 gap-2 mt-1">
                        {DIMS.map((dim) => (
                          <div key={dim} className="bg-gray-50 rounded p-2">
                            <span className="text-gray-500 text-xs">{dim}</span>
                            <p className="text-gray-700 text-xs mt-0.5">{row[dim] || "-"}</p>
                          </div>
                        ))}
                      </div>
                      <p className="text-gray-600 mt-2">
                        <span className="font-medium text-gray-700">阶段二总评：</span>{row["总评"] || "-"}
                      </p>
                    </div>

                    {/* 教师评语 */}
                    <div>
                      <p className="font-medium text-gray-700 mb-1">教师评语</p>
                      <textarea
                        value={reviewComment}
                        onChange={(e) => setReviewComment(e.target.value)}
                        rows={4}
                        className="w-full border rounded px-3 py-2 text-sm"
                        placeholder="输入教师评语…"
                      />
                    </div>
                  </div>
                ) : (
                  <p className="text-gray-400 text-sm">该作业尚未评分</p>
                )}
              </div>
            </div>
          );
        })()}

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
