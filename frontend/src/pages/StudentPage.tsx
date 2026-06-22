import { useState, useEffect, useRef } from "react";
import {
  listQuestions,
  getQuestionDetail,
  uploadSubmission,
  startAnalysis,
  gradeSubmission,
  getStudentResult,
  getSubmitStatus,
  getStudentAnalysisResult,
  getStudentSubmissions,
  checkRoster,
  getSubmissionRecord,
  getQuestionFileUrl,
  getStudentPreviewUrl,
} from "../api";
import FloatingImageViewer from "../components/FloatingImageViewer";
import FileButton from "../components/FileButton";

interface Question {
  id: string;
  title: string;
  files?: { description: string; phase1_criteria: string; phase2_criteria: string; images: string[] };
}

interface GradeResult {
  姓名: string;
  学号: string;
  成绩: string;
  phase1_similarity: number;
  phase1_comment: string;
  phase2_criteria: number;
  phase2_comment: string;
  total_score: number;
  总评: string;
  图样表达: string;
  尺寸标注: string;
  尺寸公差: string;
  表面质量: string;
  形位公差: string;
}

interface Identity {
  name: string;
  id: string;
  isTest: boolean;
  className: string;
}

interface SubmissionSummary {
  question_id: string;
  question_title: string;
  grade: string;
  total_score: string;
  status: string;
  submitted_at: string;
}

const MAX_FILE_MB = 20;
const POLL_INTERVAL = 2000;
const POLL_TIMEOUT = 120000;

// 状态中文映射
const STATUS_LABELS: Record<string, string> = {
  converting: "文件转换中…",
  submitted: "已提交，排队等候分析",
  analyzing: "模型分析中…",
  done: "完成",
  error: "处理失败",
  grading: "评分中…",
};

export default function StudentPage() {
  // 身份
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [entryName, setEntryName] = useState("");
  const [entryId, setEntryId] = useState("");
  const [entryError, setEntryError] = useState("");
  const [checking, setChecking] = useState(false);

  // 题目 + 历史
  const [questions, setQuestions] = useState<Question[]>([]);
  const [submissions, setSubmissions] = useState<SubmissionSummary[]>([]);
  const [selectedQid, setSelectedQid] = useState<string | null>(null);
  const [question, setQuestion] = useState<Question | null>(null);

  // 提交
  const [result, setResult] = useState<GradeResult | null>(null);
  const [studentFilename, setStudentFilename] = useState("");
  const [questionTs, setQuestionTs] = useState(0);  // 题目附图缓存破坏
  const [submitTs, setSubmitTs] = useState(0);
  const [error, setError] = useState("");

  // 分步状态
  const [uploading, setUploading] = useState(false);       // 上传中（工程图读取中）
  const [uploaded, setUploaded] = useState(false);          // 上传成功
  const [submitStatus, setSubmitStatus] = useState("");     // analyzing / done / error / grading
  const [analysisData, setAnalysisData] = useState<any>(null);
  const [grading, setGrading] = useState(false);
  const [submitKey, setSubmitKey] = useState<{ name: string; id: string } | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const clearPolling = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  useEffect(() => () => clearPolling(), []);

  // 图片浮动预览
  const [lightboxSrc, setLightboxSrc] = useState("");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  // 右侧浮动图面板
  const [floatOpen, setFloatOpen] = useState(false);

  const openLightbox = (src: string) => { setLightboxSrc(src); setZoom(1); setPan({ x: 0, y: 0 }); };
  const closeLightbox = () => setLightboxSrc("");

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.min(5, Math.max(0.5, z - e.deltaY * 0.002)));
  };

  const onMouseDown = (e: React.MouseEvent) => {
    if (zoom <= 1) return;
    dragRef.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragRef.current) return;
    setPan({ x: dragRef.current.px + (e.clientX - dragRef.current.x), y: dragRef.current.py + (e.clientY - dragRef.current.y) });
  };
  const onMouseUp = () => { dragRef.current = null; };

  // 分析/评分结果出现时自动弹出浮动图
  useEffect(() => {
    if (selectedQid && studentFilename && (analysisData || result)) {
      setFloatOpen(true);
    }
  }, [selectedQid, studentFilename, !!analysisData, !!result]);

  const loadData = async () => {
    try { setQuestions(await listQuestions()); } catch { /* ignore */ }
  };

  const loadHistory = async (name: string, id: string) => {
    try {
      const data = await getStudentSubmissions(name, id);
      setSubmissions(data.submissions || []);
    } catch { setSubmissions([]); }
  };

  // ── 入口页 ──────────────────────────────────────────

  const handleEnter = async () => {
    const name = entryName.trim();
    const sid = entryId.trim();
    if (!name) { setEntryError("请填写姓名"); return; }
    if (!sid) { setEntryError("请填写学号"); return; }
    setChecking(true);
    setEntryError("");
    try {
      const res = await checkRoster(name, sid);
      if (!res.ok) {
        setEntryError(res.message || "不在班级名单中");
      } else {
        setIdentity({ name, id: sid, isTest: false, className: res.class_name || "" });
        await loadData();
        await loadHistory(name, sid);
      }
    } catch (e: any) {
      setEntryError(e.message);
    } finally {
      setChecking(false);
    }
  };

  const handleTestMode = async () => {
    setIdentity({ name: "测试", id: `test_${Date.now()}`, isTest: true, className: "" });
    await loadData();
  };

  const handleLogout = () => {
    clearPolling();
    setIdentity(null);
    setQuestions([]);
    setSubmissions([]);
    setSelectedQid(null);
    setQuestion(null);
    setResult(null);
    setAnalysisData(null);
    setSubmitKey(null);
    setSubmitStatus("");
    setError("");
  };

  // ── 主界面 ──────────────────────────────────────────

  const selectQuestion = async (qid: string) => {
    clearPolling();
    setSelectedQid(qid);
    setResult(null);
    setAnalysisData(null);
    setSubmitKey(null);
    setSubmitStatus("");
    setStudentFilename("");
    setUploaded(false);
    setUploading(false);
    setSubmitTs(0);
    setFloatOpen(false);
    setError("");
    try {
      const q = await getQuestionDetail(qid);
      setQuestion(q);
      setQuestionTs(Date.now());
      if (identity && !identity.isTest) {
        // 通过提交记录恢复全部状态
        try {
          const rec = await getSubmissionRecord(qid, identity.name, identity.id);
          setSubmitKey({ name: identity.name, id: identity.id });
          if (rec.student_filename) {
            setStudentFilename(rec.student_filename);
            setUploaded(true);
            setSubmitTs(Date.now());
          }
          // 有分析结果则恢复
          if (rec.status === "analyzed" || rec.status === "graded") {
            try {
              const a = await getStudentAnalysisResult(qid, identity.name, identity.id);
              if (a.analysis) setAnalysisData(a.analysis);
            } catch { /* 分析结果文件丢失 */ }
          } else if (rec.status === "uploaded" && rec.student_filename) {
            // 文件已上传但分析可能正在进行中，检查后台状态并恢复轮询
            try {
              const s = await getSubmitStatus(qid, identity.name, identity.id);
              if (s.step === "analyze" && (s.status === "queued" || s.status === "analyzing")) {
                setSubmitKey({ name: identity.name, id: identity.id });
                // 恢复轮询，等待分析完成
                resumeAnalysisPolling(qid, identity);
              }
            } catch { /* 状态查询失败，忽略 */ }
          }
          // 有成绩则恢复
          if (rec.status === "graded") {
            try {
              const r = await getStudentResult(qid, identity.id);
              setResult(r);
            } catch { setResult(null); }
          }
        } catch { /* 无提交记录 */ }
      }
    } catch { setError("加载题目失败"); }
  };

  const getStatusBadge = (qid: string) => {
    const sub = submissions.find(s => s.question_id === qid);
    if (!sub) return <span className="text-xs text-gray-400 ml-2">未提交</span>;
    if (sub.status === "uploaded") return <span className="text-xs text-blue-400 ml-2">已提交，待分析</span>;
    if (sub.status === "analyzed") return <span className="text-xs text-orange-400 ml-2">待评分</span>;
    const g = sub.grade || "";
    const color =
      g === "A+" || g === "A" ? "text-green-600" :
      g.startsWith("B") ? "text-blue-600" :
      g === "F" ? "text-red-600" : "text-yellow-600";
    return <span className={`text-xs ml-2 font-medium ${color}`}>{g} ({sub.total_score}分)</span>;
  };

  // ── 步骤1：上传文件 ──────────────────────────────────

  const handleUpload = async (selectedFile: File) => {
    if (!selectedQid || !identity) return;
    if (selectedFile.size > MAX_FILE_MB * 1024 * 1024) {
      setError(`文件过大（上限 ${MAX_FILE_MB}MB）`);
      return;
    }

    // 校验是否为真实 PDF（检查文件头 %PDF）
    const header = await selectedFile.slice(0, 4).text();
    if (header !== "%PDF") {
      setError("仅支持 PDF 格式文件，请上传真实的 PDF 文件");
      return;
    }

    setError("");
    setAnalysisData(null);
    setResult(null);
    setUploaded(false);
    setUploading(true);
    setStudentFilename("");
    setSubmitTs(0);
    clearPolling();

    const submitName = identity.isTest ? "测试" : identity.name;
    const submitId = identity.id;
    setSubmitKey({ name: submitName, id: submitId });

    // 捕获当前值，防止 identity 在 setTimeout 前变化
    const capturedIdentity = identity;
    const capturedQid = selectedQid;

    try {
      const data = await uploadSubmission(capturedQid, submitName, submitId, selectedFile,
        capturedIdentity.isTest ? "test" : "submit");
      if (!data.ok) throw new Error("上传失败");
      setStudentFilename(data.student_filename);

      setTimeout(() => {
        setUploading(false);
        setUploaded(true);
        setSubmitTs(Date.now());
        if (capturedIdentity && !capturedIdentity.isTest) loadHistory(capturedIdentity.name, capturedIdentity.id);
      }, 200);
    } catch (e: any) {
      setUploading(false);
      setError(e.message);
    }
  };

  // ── 步骤2：恢复分析轮询（selectQuestion 时复用）─────────

  const resumeAnalysisPolling = (qid: string, ident: Identity) => {
    const capturedQid = qid;
    const capturedKey = { name: ident.name, id: ident.id };
    const capturedIsTest = ident.isTest;

    setSubmitStatus("analyzing");
    clearPolling();

    let elapsed = 0;
    pollRef.current = setInterval(async () => {
      elapsed += POLL_INTERVAL;
      if (elapsed > POLL_TIMEOUT) {
        clearPolling();
        setSubmitStatus("");
        setError("分析超时，请重试");
        return;
      }
      try {
        const s = await getSubmitStatus(capturedQid, capturedKey.name, capturedKey.id);
        if (s.step === "analyze") {
          setSubmitStatus(s.status || "");
        }
        if (s.step === "analyze" && s.status === "done") {
          clearPolling();
          const r = await getStudentAnalysisResult(capturedQid, capturedKey.name, capturedKey.id);
          setAnalysisData(r.analysis);
          setSubmitStatus("");
          if (!capturedIsTest) loadHistory(capturedKey.name, capturedKey.id);
        } else if (s.step === "analyze" && s.status === "error") {
          clearPolling();
          setSubmitStatus("");
          setError(s.error_message || "处理失败");
        }
      } catch { /* 继续轮询 */ }
    }, POLL_INTERVAL);
  };

  // ── 步骤2：开始分析 ──────────────────────────────────

  const handleStartAnalysis = async () => {
    if (!selectedQid || !submitKey) return;

    setError("");
    setSubmitStatus("analyzing");
    clearPolling();

    // 捕获当前值，防止轮询中状态被清空
    const capturedQid = selectedQid;
    const capturedKey = submitKey;
    const capturedIsTest = identity?.isTest ?? false;

    try {
      const data = await startAnalysis(capturedQid, capturedKey.name, capturedKey.id,
        capturedIsTest ? "test" : "submit");
      if (!data.ok) throw new Error("提交失败");

      let elapsed = 0;
      pollRef.current = setInterval(async () => {
        elapsed += POLL_INTERVAL;
        if (elapsed > POLL_TIMEOUT) {
          clearPolling();
          setSubmitStatus("");
          setError("分析超时，请重试");
          return;
        }
        try {
          const s = await getSubmitStatus(capturedQid, capturedKey.name, capturedKey.id);
          // 只在当前步骤匹配时才更新 UI 状态，避免读到上一步的旧 done
          if (s.step === "analyze") {
            setSubmitStatus(s.status || "");
          }

          if (s.step === "analyze" && s.status === "done") {
            clearPolling();
            const r = await getStudentAnalysisResult(capturedQid, capturedKey.name, capturedKey.id);
            setAnalysisData(r.analysis);
            setSubmitStatus("");
            if (capturedIdentity && !capturedIsTest) loadHistory(capturedIdentity.name, capturedIdentity.id);
          } else if (s.step === "analyze" && s.status === "error") {
            clearPolling();
            setSubmitStatus("");
            setError(s.error_message || "处理失败");
          }
        } catch { /* 继续轮询 */ }
      }, POLL_INTERVAL);
    } catch (e: any) {
      setSubmitStatus("");
      setError(e.message);
    }
  };

  // ── 步骤2：评分 ──────────────────────────────────────

  const handleGrade = async () => {
    if (!analysisData) { setError("请先完成图面分析"); return; }
    if (!submitKey) { setError("分析数据异常，请重新上传分析"); return; }
    if (!selectedQid) return;

    setGrading(true);
    setError("");
    setSubmitStatus("grading");
    clearPolling();

    // 捕获当前值，防止轮询中状态被清空
    const capturedQid = selectedQid;
    const capturedKey = submitKey;
    const capturedIdentity = identity;
    const capturedIsTest = identity?.isTest ?? false;

    try {
      const data = await gradeSubmission(capturedQid, capturedKey.name, capturedKey.id,
        capturedIsTest ? "test" : "submit");
      if (!data.ok) throw new Error("提交失败");

      let elapsed = 0;
      pollRef.current = setInterval(async () => {
        elapsed += POLL_INTERVAL;
        if (elapsed > POLL_TIMEOUT) {
          clearPolling();
          setGrading(false);
          setSubmitStatus("");
          setError("评分超时，请重试");
          return;
        }
        try {
          const s = await getSubmitStatus(capturedQid, capturedKey.name, capturedKey.id);
          // 只在当前步骤匹配时才更新 UI 状态，避免读到上一步的旧 done
          if (s.step === "grade") {
            setSubmitStatus(s.status || "");
          }
          if (s.step === "grade" && s.status === "done") {
            clearPolling();
            const r = await getStudentResult(capturedQid, capturedKey.id);
            if (r) {
              setResult({
                姓名: capturedIdentity?.name || capturedKey.name,
                学号: capturedIdentity?.id || capturedKey.id,
                成绩: r.成绩,
                ...r,
              });
            }
            setGrading(false);
            setSubmitStatus("");
            if (capturedIdentity && !capturedIdentity.isTest) loadHistory(capturedIdentity.name, capturedIdentity.id);
          } else if (s.step === "grade" && s.status === "error") {
            clearPolling();
            setGrading(false);
            setSubmitStatus("");
            setError(s.error_message || "评分失败");
          }
        } catch { /* 继续轮询 */ }
      }, POLL_INTERVAL);
    } catch (e: any) {
      setGrading(false);
      setSubmitStatus("");
      setError(e.message);
    }
  };

  // ── 入口页 ──────────────────────────────────────────

  if (!identity) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="bg-white rounded-lg shadow p-8 w-full max-w-sm">
          <h1 className="text-xl font-bold text-center mb-6">工程图批阅系统</h1>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">姓名</label>
              <input type="text" value={entryName} onChange={(e) => setEntryName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleEnter()}
                className="w-full border rounded px-3 py-2" placeholder="请输入姓名" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">学号</label>
              <input type="text" value={entryId} onChange={(e) => setEntryId(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleEnter()}
                className="w-full border rounded px-3 py-2" placeholder="请输入学号" />
            </div>
            {entryError && <p className="text-red-500 text-sm">{entryError}</p>}
            <button onClick={handleEnter} disabled={checking}
              className="w-full bg-blue-600 text-white py-2 rounded hover:bg-blue-700 disabled:opacity-50">
              {checking ? "验证中…" : "进入"}
            </button>
            <div className="text-center">
              <button onClick={handleTestMode} className="text-sm text-gray-400 hover:text-blue-500">
                测试模式（无需姓名学号）
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── 主界面 ──────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-blue-700 text-white px-3 py-1.5 shadow sticky top-0 z-10">
        <div className="max-w-3xl mx-auto flex items-center justify-between text-sm">
          <button onClick={handleLogout} className="hover:underline opacity-70 hover:opacity-100">&larr;退出</button>
          <div>
            {identity.isTest ? (
              <span>测试模式</span>
            ) : (
              <span>
                {identity.name} ({identity.id})
                {identity.className ? <span className="ml-2 opacity-70">{identity.className}</span> : null}
              </span>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto p-4 space-y-6">
        {/* 题目列表 */}
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-lg font-semibold mb-3">选择题目</h2>
          <div className="flex flex-wrap gap-2">
            {questions.map((q) => (
              <button key={q.id} onClick={() => selectQuestion(q.id)}
                className={`px-4 py-2 rounded text-sm ${
                  selectedQid === q.id ? "bg-blue-600 text-white" : "bg-gray-100 hover:bg-gray-200"
                }`}>
                {q.id}: {q.title}
                {!identity.isTest && getStatusBadge(q.id)}
              </button>
            ))}
            {questions.length === 0 && <p className="text-gray-400 text-sm">暂无题目</p>}
          </div>
        </div>

        {/* 历史成绩 */}
        {!identity.isTest && submissions.length > 0 && (
          <div className="bg-white rounded-lg shadow p-4">
            <h2 className="text-lg font-semibold mb-3">我的作业</h2>
            <div className="space-y-2">
              {submissions.map((s) => (
                <div key={s.question_id}
                  className="flex items-center justify-between p-2 bg-gray-50 rounded cursor-pointer hover:bg-gray-100"
                  onClick={() => selectQuestion(s.question_id)}>
                  <span className="text-sm">{s.question_id}: {s.question_title}</span>
                  <span className={`text-sm font-bold ${
                    (s.grade || "") === "A+" || (s.grade || "") === "A" ? "text-green-600" :
                    (s.grade || "").startsWith("B") ? "text-blue-600" :
                    s.grade === "F" ? "text-red-600" : "text-yellow-600"
                  }`}>{s.grade}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 题目详情 */}
        {question && (
          <div className="bg-white rounded-lg shadow p-4">
            <h2 className="text-lg font-semibold mb-3">题目详情：{question.title}</h2>
            <div className="prose max-w-none mb-4">
              <p className="whitespace-pre-wrap text-gray-800">{question.files?.description}</p>
            </div>
            {question.files?.images.map((img) => (
              <img key={img} src={getQuestionFileUrl(question.id, img, questionTs)}
                alt="题目附图" className="max-w-full rounded border my-2" />
            ))}
          </div>
        )}

        {/* 提交作业 */}
        {selectedQid && (
          <div className="bg-white rounded-lg shadow p-4">
            <h2 className="text-lg font-semibold mb-3">
              {identity.isTest ? "测试提交" : "提交作业"}
            </h2>

            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">上传工程图 (PDF)</label>
              <FileButton
                accept=".pdf"
                onChange={(file) => handleUpload(file)}
                label="选择文件"
                fileName={studentFilename || undefined}
              />
              {error && <p className="text-red-500 mt-2 text-sm">{error}</p>}
            </div>

            {/* 上传中提示 */}
            {uploading && (
              <div className="mb-4 p-3 rounded bg-blue-50 border border-blue-200">
                <div className="flex items-center gap-2">
                  <svg className="animate-spin h-4 w-4 text-blue-600" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  <span className="text-sm text-blue-700">工程图读取中…</span>
                </div>
              </div>
            )}

            {/* 提交成功提示 */}
            {uploaded && !uploading && (
              <div className="mb-4 p-3 rounded bg-green-50 border border-green-200">
                <span className="text-sm text-green-700">提交成功</span>
              </div>
            )}

            {/* 分析/评分状态提示 */}
            {submitStatus && (
              <div className="mb-4 p-3 rounded bg-blue-50 border border-blue-200">
                <div className="flex items-center gap-2">
                  {submitStatus !== "error" && submitStatus !== "done" && (
                    <svg className="animate-spin h-4 w-4 text-blue-600" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  )}
                  <span className="text-sm text-blue-700">{STATUS_LABELS[submitStatus] || submitStatus}</span>
                </div>
              </div>
            )}

            {/* 学生图预览（提交后、分析前显示） */}
            {studentFilename && submitTs > 0 && selectedQid && !analysisData && !result && (
              <div className="mb-4">
                <p className="text-xs text-gray-500 mb-1">已上传的工程图（点击放大）</p>
                <img src={getStudentPreviewUrl(selectedQid, studentFilename, submitTs)}
                  alt="已上传工程图" className="max-w-full rounded border cursor-pointer hover:opacity-90" style={{ maxHeight: "300px" }}
                  onClick={() => openLightbox(getStudentPreviewUrl(selectedQid, studentFilename, submitTs))} />
              </div>
            )}

            {!analysisData ? (
              <button onClick={handleStartAnalysis} disabled={!uploaded || !!submitStatus}
                className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 disabled:opacity-50">
                {!uploaded ? "请先上传作业" : submitStatus ? "处理中…" : "开始分析"}
              </button>
            ) : result ? (
              <p className="text-green-600 font-medium text-sm">评分已完成</p>
            ) : (
              <button onClick={handleGrade} disabled={grading || !!submitStatus}
                className="bg-green-600 text-white px-6 py-2 rounded hover:bg-green-700 disabled:opacity-50">
                {grading || submitStatus ? "处理中…" : "提交评分"}
              </button>
            )}
            {/* 图面分析结果 */}

{analysisData && (
              <div className="mt-4 border rounded p-3 bg-gray-50 text-xs">
                <h3 className="text-sm font-semibold mb-3 text-green-700">图面分析完成</h3>
                {studentFilename && (
                  <div className="mb-3">
                    <p className="text-xs text-gray-500 mb-1">你的作业</p>
                    <img src={getStudentPreviewUrl(selectedQid!, studentFilename, submitTs)} alt="学生工程图" className="max-w-full rounded border cursor-pointer hover:opacity-90" style={{ maxHeight: "300px" }}
                      onClick={() => openLightbox(getStudentPreviewUrl(selectedQid!, studentFilename, submitTs))} />
                  </div>
                )}

                {analysisData.structure && (
                  <details className="mb-2" open>
                    <summary className="text-sm font-medium text-blue-700 cursor-pointer hover:underline mb-2">结构分析</summary>
                    {analysisData.structure.title_block && (
                      <table className="w-full border mb-2"><thead><tr className="bg-blue-50"><th colSpan={4} className="p-1 text-left text-blue-800">标题栏</th></tr></thead><tbody>
                        <tr className="border"><td className="p-1 text-gray-500 w-16">零件名称</td><td className="p-1">{analysisData.structure.title_block.part_name || "-"}</td><td className="p-1 text-gray-500 w-16">图号</td><td className="p-1">{analysisData.structure.title_block.drawing_number || "-"}</td></tr>
                        <tr className="border"><td className="p-1 text-gray-500">材料</td><td className="p-1">{analysisData.structure.title_block.material || "-"}</td><td className="p-1 text-gray-500">比例</td><td className="p-1">{analysisData.structure.title_block.scale || "-"}</td></tr>
                      </tbody></table>
                    )}
                    {analysisData.structure.overall_shape && (
                      <table className="w-full border mb-2"><thead><tr className="bg-blue-50"><th colSpan={4} className="p-1 text-left text-blue-800">整体形状</th></tr></thead><tbody>
                        <tr className="border"><td className="p-1 text-gray-500 w-16">类型</td><td className="p-1">{analysisData.structure.overall_shape.type || "-"}</td><td className="p-1 text-gray-500 w-16">对称性</td><td className="p-1">{analysisData.structure.overall_shape.symmetry || "-"}</td></tr>
                        <tr className="border"><td className="p-1 text-gray-500">外形尺寸</td><td className="p-1">{analysisData.structure.overall_shape.approx_dimensions || "-"}</td><td className="p-1 text-gray-500">材料标注</td><td className="p-1">{analysisData.structure.overall_shape.material_text || (analysisData.structure.overall_shape.has_material_label ? "有" : "无")}</td></tr>
                      </tbody></table>
                    )}
                    {analysisData.structure.views?.length > 0 && (
                      <table className="w-full border mb-2"><thead><tr className="bg-blue-50"><th colSpan={3} className="p-1 text-left text-blue-800">视图组成（{analysisData.structure.views.length} 个）</th></tr></thead><thead><tr className="bg-gray-50 border"><th className="p-1 text-left">名称</th><th className="p-1 text-left">类型</th><th className="p-1 text-left">说明</th></tr></thead><tbody>
                        {analysisData.structure.views.map((v: any, i: number) => (<tr key={i} className="border"><td className="p-1">{v.name}</td><td className="p-1">{v.type}</td><td className="p-1 text-gray-600">{v.description || "-"}</td></tr>))}
                      </tbody></table>
                    )}
                    {analysisData.structure.features?.length > 0 && (
                      <table className="w-full border mb-2"><thead><tr className="bg-blue-50"><th colSpan={5} className="p-1 text-left text-blue-800">结构特征（{analysisData.structure.features.length} 个）</th></tr></thead><thead><tr className="bg-gray-50 border"><th className="p-1 text-left">ID</th><th className="p-1 text-left">类型</th><th className="p-1 text-center w-10">数量</th><th className="p-1 text-left">位置</th><th className="p-1 text-left">备注</th></tr></thead><tbody>
                        {analysisData.structure.features.map((f: any, i: number) => (<tr key={i} className="border"><td className="p-1 font-mono">{f.id}</td><td className="p-1">{f.type}</td><td className="p-1 text-center">{f.count}</td><td className="p-1">{f.location || "-"}</td><td className="p-1 text-gray-600">{f.notes || "-"}</td></tr>))}
                      </tbody></table>
                    )}
                    {analysisData.structure.technical_notes && <p className="mb-2"><span className="text-gray-500">技术要求：</span>{analysisData.structure.technical_notes}</p>}
                  </details>
                )}

                {analysisData.quantitative && (
                  <details className="mb-2" open>
                    <summary className="text-sm font-medium text-blue-700 cursor-pointer hover:underline mb-2">量化分析</summary>
                    {analysisData.quantitative.general_notes && (
                      <table className="w-full border mb-2"><thead><tr className="bg-green-50"><th colSpan={6} className="p-1 text-left text-green-800">图纸通用信息</th></tr></thead><tbody>
                        <tr className="border"><td className="p-1 text-gray-500">比例</td><td className="p-1">{analysisData.quantitative.general_notes.scale || "-"}</td><td className="p-1 text-gray-500">未注公差</td><td className="p-1">{analysisData.quantitative.general_notes.general_tolerance || "-"}</td><td className="p-1 text-gray-500">热处理</td><td className="p-1">{analysisData.quantitative.general_notes.heat_treatment || "-"}</td></tr>
                        <tr className="border"><td className="p-1 text-gray-500">表面处理</td><td className="p-1">{analysisData.quantitative.general_notes.surface_treatment || "-"}</td><td className="p-1 text-gray-500">未注圆角</td><td className="p-1">{analysisData.quantitative.general_notes.unspecified_rounds || "-"}</td><td className="p-1 text-gray-500">未注倒角</td><td className="p-1">{analysisData.quantitative.general_notes.unspecified_chamfers || "-"}</td></tr>
                      </tbody></table>
                    )}
                    {analysisData.quantitative.dimensions?.length > 0 && (
                      <table className="w-full border mb-2"><thead><tr className="bg-green-50"><th colSpan={6} className="p-1 text-left text-green-800">尺寸标注（{analysisData.quantitative.dimensions.length} 个）</th></tr></thead><thead><tr className="bg-gray-50 border"><th className="p-1 text-left">ID</th><th className="p-1 text-left">类型</th><th className="p-1 text-right">数值</th><th className="p-1 text-left">公差</th><th className="p-1 text-left">关联特征</th><th className="p-1 text-left">说明</th></tr></thead><tbody>
                        {analysisData.quantitative.dimensions.map((d: any, i: number) => (<tr key={i} className="border"><td className="p-1 font-mono">{d.id}</td><td className="p-1">{d.type}</td><td className="p-1 text-right font-mono">{d.value}{d.unit && d.unit !== "无" ? d.unit : ""}</td><td className="p-1 font-mono">{d.tolerance || "-"}</td><td className="p-1 font-mono">{d.feature_ref || "-"}</td><td className="p-1 text-gray-600 max-w-xs truncate">{d.description || "-"}</td></tr>))}
                      </tbody></table>
                    )}
                    {analysisData.quantitative.surface_roughness?.length > 0 && (
                      <table className="w-full border mb-2"><thead><tr className="bg-green-50"><th colSpan={3} className="p-1 text-left text-green-800">表面粗糙度（{analysisData.quantitative.surface_roughness.length} 处）</th></tr></thead><thead><tr className="bg-gray-50 border"><th className="p-1 text-left">数值</th><th className="p-1 text-left">关联特征</th><th className="p-1 text-left">位置</th></tr></thead><tbody>
                        {analysisData.quantitative.surface_roughness.map((r: any, i: number) => (<tr key={i} className="border"><td className="p-1 font-mono">{r.value}</td><td className="p-1 font-mono">{r.feature_ref || "-"}</td><td className="p-1 text-gray-600">{r.location || "-"}</td></tr>))}
                      </tbody></table>
                    )}
                    {analysisData.quantitative.geometric_tolerances?.length > 0 && (
                      <table className="w-full border mb-2"><thead><tr className="bg-green-50"><th colSpan={6} className="p-1 text-left text-green-800">形位公差（{analysisData.quantitative.geometric_tolerances.length} 项）</th></tr></thead><thead><tr className="bg-gray-50 border"><th className="p-1 text-left">ID</th><th className="p-1 text-left">类型</th><th className="p-1 text-right">数值</th><th className="p-1 text-left">关联特征</th><th className="p-1 text-left">基准</th><th className="p-1 text-left">说明</th></tr></thead><tbody>
                        {analysisData.quantitative.geometric_tolerances.map((g: any, i: number) => (<tr key={i} className="border"><td className="p-1 font-mono">{g.id}</td><td className="p-1">{g.type}</td><td className="p-1 text-right font-mono">{g.value}{g.unit || "mm"}</td><td className="p-1 font-mono">{g.ref_feature || "-"}</td><td className="p-1 font-mono">{g.datum || "-"}</td><td className="p-1 text-gray-600 max-w-xs truncate">{g.description || "-"}</td></tr>))}
                      </tbody></table>
                    )}
                    {analysisData.quantitative.thread_specs?.length > 0 && (
                      <table className="w-full border mb-2"><thead><tr className="bg-green-50"><th colSpan={5} className="p-1 text-left text-green-800">螺纹规格（{analysisData.quantitative.thread_specs.length} 处）</th></tr></thead><thead><tr className="bg-gray-50 border"><th className="p-1 text-left">ID</th><th className="p-1 text-left">类型</th><th className="p-1 text-left">规格</th><th className="p-1 text-left">关联特征</th><th className="p-1 text-left">备注</th></tr></thead><tbody>
                        {analysisData.quantitative.thread_specs.map((t: any, i: number) => (<tr key={i} className="border"><td className="p-1 font-mono">{t.id}</td><td className="p-1">{t.type}</td><td className="p-1 font-mono">{t.spec}</td><td className="p-1 font-mono">{t.feature_ref || "-"}</td><td className="p-1 text-gray-600">{t.notes || "-"}</td></tr>))}
                      </tbody></table>
                    )}
                    {analysisData.quantitative.completeness_notes && <p className="text-orange-600"><span className="font-medium">完整度提示：</span>{analysisData.quantitative.completeness_notes}</p>}
                  </details>
                )}
              </div>
            )}
          </div>
        )}

        {/* 批阅结果 */}
        {result && (
          <div className="bg-white rounded-lg shadow p-4 border-l-4 border-blue-500 space-y-4">
            <h2 className="text-lg font-semibold">批阅结果</h2>
            <div className="flex items-center gap-4 p-3 bg-gray-50 rounded">
              <div>
                <span className="text-sm text-gray-600">最终等级</span>
                <div className={`text-3xl font-bold ${
                  result.成绩 === "F" ? "text-red-600" :
                  (result.成绩 || "") === "A+" ? "text-green-600" :
                  (result.成绩 || "").startsWith("A") ? "text-green-600" :
                  (result.成绩 || "").startsWith("B") ? "text-blue-600" :
                  (result.成绩 || "").startsWith("C") ? "text-yellow-600" :
                  "text-orange-600"
                }`}>{result.成绩}</div>
              </div>
              <div className="flex-1 text-right text-sm text-gray-500">
                阶段1 相似度<br/><span className="text-lg font-bold text-gray-800">{(result as any)["阶段1相似度"] || (result as any).phase1_similarity || "-"}%</span>
                <span className="mx-1 text-xs">√(</span>
                阶段2 评分<br/><span className="text-lg font-bold text-gray-800">{(result as any)["阶段2评分"] || (result as any).phase2_criteria || "-"}%</span>
                <span className="mx-1 text-xs">)</span>
                <span className="mx-2">=</span>
                总分<br/><span className="text-lg font-bold text-blue-600">{(result as any)["总分"] || (result as any).total_score || "-"}%</span>
              </div>
            </div>
            <div className="bg-yellow-50 rounded p-3">
              <h4 className="text-sm font-medium text-yellow-700">相似度评价</h4>
              <p className="text-sm mt-1 whitespace-pre-wrap">{(result as any)["相似度评价"] || (result as any).phase1_comment || "-"}</p>
            </div>
            <h4 className="text-sm font-medium text-gray-700 mt-2">量化评价</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-1">
              {["图样表达", "尺寸标注", "尺寸公差", "表面质量", "形位公差", "技术要求"].map((key) => (
                <div key={key} className="bg-gray-50 rounded p-3">
                  <h4 className="text-sm font-medium text-gray-500">{key}</h4>
                  <p className="text-sm mt-1 whitespace-pre-wrap">{(result as any)[key] || "-"}</p>
                </div>
              ))}
            </div>
            <div className="bg-blue-50 rounded p-3">
              <h4 className="text-sm font-medium text-blue-700">总评</h4>
              <p className="text-sm mt-1 whitespace-pre-wrap">{result.总评 || "-"}</p>
            </div>

            {(result as any)["教师评语"] ? (
              <div className="bg-green-50 rounded p-3 border border-green-200">
                <h4 className="text-sm font-medium text-green-700">教师评语</h4>
                <p className="text-sm mt-1 whitespace-pre-wrap">{(result as any)["教师评语"]}</p>
              </div>
            ) : null}
          </div>
        )}
      </main>

      {/* 右侧浮动图面板 */}
      {floatOpen && studentFilename && selectedQid && (
        <FloatingImageViewer
          src={getStudentPreviewUrl(selectedQid, studentFilename, submitTs)}
          title="我的作业"
          visible={true}
          onClose={() => setFloatOpen(false)}
          initialWidth={320}
          initialHeight={360}
          zIndex={40}
        />
      )}

      {/* 图片浮动预览 */}
      {lightboxSrc && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center"
          onClick={closeLightbox}
          onWheel={onWheel}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}>
          <button className="absolute top-4 right-4 text-white text-2xl hover:text-gray-300 z-10"
            onClick={closeLightbox}>&times;</button>
          <div className="absolute top-4 left-4 text-white text-sm bg-black/50 px-2 py-1 rounded z-10">
            {Math.round(zoom * 100)}%
          </div>
          <img src={lightboxSrc} alt="预览"
            className="max-w-[90vw] max-h-[90vh] object-contain select-none"
            style={{ transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`, cursor: zoom > 1 ? 'grab' : 'default' }}
            draggable={false}
            onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  );
}
