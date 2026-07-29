import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  checkLogin,
  teacherLogout,
  getTeacherQuestions,
  createQuestion,
  updateQuestion,
  deleteQuestion,
  getGrades,
  getSettings,
  getQuestionDetail,
  getClasses,
  getClassStudents,
  createClass,
  deleteClass,
  downloadRosterTemplate,
  resetStudentPassword,
  getScoringTemplates,
  triggerAnalysis,
  getAnalysisResult,
  downloadHomeworkZip,
  downloadGradesCsv,
  getTeacherPreviewUrl,
  batchGrade,
  batchClearGrades,
  editGrade,
  getTeacherStudentPreviewUrl,
  getStudentAnalysis,
  getStudentResult,
  supplementSubmission,
  refreshGrades,
  lookupRoster,
  getTemplates,
  getQuestionTemplate,
  updateQuestionTemplate,
  rejectSubmission,
} from "../api";
import FloatingImageViewer from "../components/FloatingImageViewer";
import FileButton from "../components/FileButton";

interface Question {
  id: string;
  title: string;
  files?: any;
}

export default function TeacherDashboard() {
  // 成绩表列名常量（与后端 CSV FIELDNAMES 保持一致）
  const COL = {
    班级: "班级", 姓名: "姓名", 学号: "学号",
    成绩: "成绩", 阶段1相似度: "阶段1相似度", 阶段2评分: "阶段2评分",
    总分: "总分", 相似度评价: "相似度评价", 阶段2评语: "阶段2评语",
    总评: "总评", 图样表达: "图样表达", 尺寸标注: "尺寸标注",
    尺寸公差: "尺寸公差", 表面质量: "表面质量", 形位公差: "形位公差",
    技术要求: "技术要求", 教师评语: "教师评语", 文件SHA256: "文件SHA256", 作弊: "作弊",
  } as const;

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
  const [analysisErrors, setAnalysisErrors] = useState<Record<string, string>>({});
  const [analysisModalQid, setAnalysisModalQid] = useState<string | null>(null);
  const pollStartTime = useRef<number>(0);  // 轮询开始时间，用于超时检测

  // 成绩管理
  const [selectedStudents, setSelectedStudents] = useState<Set<string>>(new Set());
  const [batchGrading, setBatchGrading] = useState(false);
  const [batchClearing, setBatchClearing] = useState(false);
  const [editingCell, setEditingCell] = useState<{ sid: string; col: string } | null>(null);
  const [editValue, setEditValue] = useState("");
  // 补充提交弹窗
  const [supplementModal, setSupplementModal] = useState(false);
  const [supplementFile, setSupplementFile] = useState<File | null>(null);
  const [supplementParsed, setSupplementParsed] = useState<{ name: string; sid: string; className: string } | null>(null);
  const [supplementParsing, setSupplementParsing] = useState(false);
  const [supplementSubmitting, setSupplementSubmitting] = useState(false);

  // 从文件名提取学号+姓名
  const parseFilename = (fname: string): { name: string; sid: string } | null => {
    const stem = fname.replace(/\.[^.]+$/, "");  // 去扩展名
    // 尝试分隔符拆分
    const parts = stem.split(/[_\-\s,，、]+/).filter(Boolean);
    const tryClassify = (a: string, b: string) => {
      const isIdLike = (s: string) => /^\d{4,15}$/.test(s) || (/\d/.test(s) && s.replace(/\D/g, "").length / s.length >= 0.7);
      const isNameLike = (s: string) => /[一-鿿]/.test(s) || (!/\d/.test(s) && s.length >= 2 && s.length <= 6);
      if (isIdLike(a) && isNameLike(b)) return { sid: a, name: b };
      if (isNameLike(a) && isIdLike(b)) return { sid: b, name: a };
      return null;
    };
    if (parts.length >= 2) {
      const r = tryClassify(parts[0], parts[1]);
      if (r) return r;
    }
    // 无分隔符：正则切分
    let m = stem.match(/^(\d+)([一-鿿].*)$/);
    if (m) return { sid: m[1], name: m[2] };
    m = stem.match(/^([一-鿿].*?)(\d+)$/);
    if (m) return { sid: m[2], name: m[1] };
    return null;
  };
  const [refreshing, setRefreshing] = useState(false);

  // 查看作业弹窗
  const [reviewSid, setReviewSid] = useState<string | null>(null);
  const [floatStudentFile, setFloatStudentFile] = useState<string | null>(null);
  const [reviewComment, setReviewComment] = useState("");
  const [reviewGrade, setReviewGrade] = useState("");
  const [templateModalQid, setTemplateModalQid] = useState<string | null>(null);
  const [templateModalContent, setTemplateModalContent] = useState("");
  const [templateModalSaving, setTemplateModalSaving] = useState(false);
  const [studentAnalysis, setStudentAnalysis] = useState<any>(null);
  const [studentGradeResult, setStudentGradeResult] = useState<any>(null);

  // 浮动图面板
  const [floatQid, setFloatQid] = useState<string | null>(null);

  // 帮助
  const [showHelp, setShowHelp] = useState(false);
  // 工具：重叠线清理（已移到设置页）

  // 窗口拖动（仅查看作业弹窗需要，浮动图已收归 FloatingImageViewer）
  const reviewModalRef = useRef<HTMLDivElement>(null);
  const [modalPos, setModalPos] = useState<{ x: number; y: number } | null>(null);
  const modalMoveRef = useRef<{ mx: number; my: number; ox: number; oy: number } | null>(null);

  // 弹窗查看分析结果时自动显示浮动图
  useEffect(() => {
    if (analysisModalQid) {
      setFloatQid(analysisModalQid);
    } else {
      setFloatQid(null);
    }
  }, [analysisModalQid]);

  // form fields
  const [qid, setQid] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [phase1Criteria, setPhase1Criteria] = useState("");
  const [phase2Criteria, setPhase2Criteria] = useState("");
  const [knowledge, setKnowledge] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [refPdf, setRefPdf] = useState<File | null>(null);
  const [submissionType, setSubmissionType] = useState("pdf");  // 学生提交文件类型：pdf / image
  const [requiredFrames, setRequiredFrames] = useState<string[]>([]); // DXF 答题图框列表
  const [qClasses, setQClasses] = useState<string[]>([]);     // 适用班别（复选框）
  const [deadline, setDeadline] = useState("");               // 提交截止时间
  const [existingImages, setExistingImages] = useState<string[]>([]);
  const [existingRefPdf, setExistingRefPdf] = useState<string | null>(null);
  const [templateType, setTemplateType] = useState("零件图识读模板.txt");
  const [templateContent, setTemplateContent] = useState("");
  const [templateLoaded, setTemplateLoaded] = useState(false);
  const [visibleToOthers, setVisibleToOthers] = useState(0);   // 0=仅限本人, 1=其他教师可见

  const imagePreviewUrl = useMemo(() => (image ? URL.createObjectURL(image) : null), [image]);
  useEffect(() => () => { if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl); }, [imagePreviewUrl]);

  const loadQuestions = useCallback(async () => {
    try {
      const data = await getTeacherQuestions();
      setQuestions(data);
      // 加载已有分析结果，检测进行中的分析
      for (const q of data) {
        const hasRef = q.files?.reference_pdf || q.files?.reference_dxf;
        if (hasRef) {
          try {
            const res = await getAnalysisResult(q.id);
            if (res.ready && res.analysis) {
              setAnalysisResults((prev) => ({ ...prev, [q.id]: res.analysis }));
            } else if (res.status === "analyzing") {
              // 任务确实在队列中，恢复轮询
              setAnalyzingQid(q.id);
              pollStartTime.current = Date.now();
            } else if (res.status === "error") {
              // 上次分析失败，记录错误信息
              setAnalysisErrors((prev) => ({ ...prev, [q.id]: res.error || "分析失败" }));
            }
            // status === "not_started" → 显示"分析"按钮，不做任何自动操作
          } catch (_) { /* 该题网络错误，忽略 */ }
        }
      }
    } catch (e: any) {
      if (e.message?.includes("401") || e.message?.includes("请先登录")) {
        navigate("/teacher");
      }
    }
  }, [navigate]);

  useEffect(() => {
    checkLogin().then((res: any) => {
      if (res?.name) sessionStorage.setItem("teacher_name", res.name);
      if (res?.username) sessionStorage.setItem("teacher_username", res.username);
    }).catch(() => navigate("/teacher"));
    loadQuestions();
    // 加载班别列表（供表单复选框使用）
    getClasses().then((data: any) => { setClasses(data.classes || []); }).catch(() => {});
    // 加载等级阈值配置
    getSettings().then((s) => {
      if (s.grade_thresholds) {
        const entries = Object.entries(s.grade_thresholds) as [string, number][];
        entries.sort((a, b) => b[1] - a[1]); // 分数从高到低
        setGradeThresholds(entries);
      }
    }).catch(() => {});
  }, [navigate, loadQuestions]);

  // 轮询参考图分析结果（最多 5 分钟，之后自动停止并提示）
  useEffect(() => {
    if (!analyzingQid) return;
    const POLL_TIMEOUT_MS = 5 * 60 * 1000;  // 5 分钟超时
    if (pollStartTime.current === 0) pollStartTime.current = Date.now();

    const timer = setInterval(async () => {
      // 超时检查
      if (Date.now() - pollStartTime.current > POLL_TIMEOUT_MS) {
        setAnalysisErrors((prev) => ({ ...prev, [analyzingQid]: "分析超时（超过 5 分钟），请检查模型服务后重试" }));
        setAnalyzingQid(null);
        pollStartTime.current = 0;
        clearInterval(timer);
        return;
      }
      try {
        const res = await getAnalysisResult(analyzingQid);
        if (res.ready && res.analysis) {
          setAnalysisResults((prev) => ({ ...prev, [analyzingQid]: res.analysis }));
          setAnalysisErrors((prev) => { const n = { ...prev }; delete n[analyzingQid]; return n; });
          setAnalyzingQid(null);
          pollStartTime.current = 0;
          clearInterval(timer);
        } else if (res.status === "error") {
          // 后端报告分析失败
          setAnalysisErrors((prev) => ({ ...prev, [analyzingQid]: res.error || "分析失败" }));
          setAnalyzingQid(null);
          pollStartTime.current = 0;
          clearInterval(timer);
        }
        // status === "analyzing" → 继续轮询
      } catch (_) { /* 继续轮询 */ }
    }, 3000);  // 每 3 秒查询一次
    return () => clearInterval(timer);
  }, [analyzingQid]);

  // 载入模板内容（新建时自动加载，编辑时由用户手动切换触发）
  const loadTemplateContent = (tplType?: string) => {
    const t = tplType || templateType;
    getTemplates().then((data: any) => {
      if (data.templates && data.templates[t]) {
        setTemplateContent(data.templates[t]);
        setTemplateLoaded(true);
      }
    }).catch(() => {});
  };

  // 新建题目：表单打开时自动加载默认模板
  useEffect(() => {
    if (!showForm || editingId) return;
    loadTemplateContent();
  }, [showForm, editingId]);

  // 模板类型变化时自动加载对应模板内容
  const skipTemplateLoadRef = useRef(false);
  useEffect(() => {
    if (!showForm) return;
    if (skipTemplateLoadRef.current) {
      skipTemplateLoadRef.current = false;
      return;
    }
    loadTemplateContent();
  }, [templateType]);

  // submission_type 切到 dxf 时自动切换模板下拉选项
  useEffect(() => {
    if (!showForm) return;
    if (submissionType === "dxf") {
      setTemplateType("DXF识读模板.txt");
    } else if (templateType === "DXF识读模板.txt") {
      setTemplateType("零件图识读模板.txt");
    }
  }, [submissionType]);

  const resetForm = () => {
    setQid("");
    setTitle("");
    setDescription("");
    setPhase1Criteria("");
    setPhase2Criteria("");
    setKnowledge("");
    setImage(null);
    setRefPdf(null);
    setSubmissionType("pdf");
    setRequiredFrames([]);
    setQClasses([]);
    setDeadline("");
    setExistingImages([]);
    setExistingRefPdf(null);
    setTemplateType("零件图识读模板.txt");
    setTemplateContent("");
    setTemplateLoaded(false);
    setVisibleToOthers(0);
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
    fd.append("submission_type", submissionType);
    fd.append("classes", qClasses.join(","));
    fd.append("deadline", deadline);
    fd.append("template_type", templateType);
    fd.append("template_content", templateContent);
    fd.append("visible_to_others", String(visibleToOthers));
    fd.append("required_frames", JSON.stringify(requiredFrames));
    if (image) fd.append("image", image);
    if (refPdf) fd.append("reference_pdf", refPdf);
    try {
      await createQuestion(fd);
      resetForm();
      loadQuestions();
      if (refPdf) { setAnalyzingQid(qid); pollStartTime.current = Date.now(); }
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
    fd.append("submission_type", submissionType);
    fd.append("classes", qClasses.join(","));
    fd.append("deadline", deadline);
    fd.append("template_type", templateType);
    fd.append("template_content", templateContent);
    fd.append("visible_to_others", String(visibleToOthers));
    fd.append("required_frames", JSON.stringify(requiredFrames));
    if (image) fd.append("image", image);
    if (refPdf) fd.append("reference_pdf", refPdf);
    try {
      await updateQuestion(editingId, fd);
      resetForm();
      loadQuestions();
      if (refPdf) { setAnalyzingQid(editingId); pollStartTime.current = Date.now(); }  // 换参考图后重新分析
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleEditTemplate = async (qid: string) => {
    try {
      const tpl = await getQuestionTemplate(qid);
      setTemplateModalContent(tpl.content || "");
    } catch (_) {
      setTemplateModalContent("");
    }
    setTemplateModalQid(qid);
  };

  const handleSaveTemplate = async () => {
    if (!templateModalQid) return;
    setTemplateModalSaving(true);
    try {
      await updateQuestionTemplate(templateModalQid, templateModalContent);
      setTemplateModalQid(null);
    } catch (e: any) {
      alert("保存失败: " + e.message);
    } finally {
      setTemplateModalSaving(false);
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
      skipTemplateLoadRef.current = true;  // 编辑模式下首次打开不覆盖题目模板
      setSubmissionType(detail.submission_type || "pdf");
      setQClasses(((q as any).classes || "").split(",").filter(Boolean));
      setDeadline((q as any).deadline || "");
      setVisibleToOthers((q as any).visible_to_others ?? 0);
      setRequiredFrames(Array.isArray((q as any).required_frames) ? (q as any).required_frames : []);
      setImage(null);
      setRefPdf(null);
      setExistingImages(detail.files?.images || []);
      setExistingRefPdf(detail.files?.reference_preview || detail.files?.reference_pdf || detail.files?.reference_dxf || null);
      // 加载题目的识读模板
      try {
        const tpl = await getQuestionTemplate(q.id);
        if (tpl.content) {
          setTemplateContent(tpl.content);
          setTemplateLoaded(true);
        }
      } catch (_) { /* 题目可能没有模板，使用默认 */ }
      setShowForm(true);
    } catch (e: any) {
      alert(e.message);
    }
  };

  // 当前查看的成绩表是否属于自己
  const [isGradeOwner, setIsGradeOwner] = useState(true);

  const handleViewGrades = async (qid: string) => {
    try {
      const data = await getGrades(qid);
      setGradeData(data.grades || []);
      setGradeColumns(data.columns || []);
      setGradesView(qid);
      setSelectedStudents(new Set());
      // 检查所有权
      const q = questions.find(x => x.id === qid);
      const myUsername = sessionStorage.getItem("teacher_username") || "";
      const owner = (q as any)?.teacher || "";
      setIsGradeOwner(!owner || owner === myUsername || !myUsername);
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
    const allIds = gradeData.map((r: any) => r[COL.学号]).filter(Boolean);
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
          ids.includes(r[COL.学号]) ? { ...r, _status: "grading" } : r
        )
      );
      setSelectedStudents(new Set());
    } catch (e: any) {
      alert(e.message);
    } finally {
      setBatchGrading(false);
    }
  };

  const handleBatchClear = async () => {
    if (!gradesView || selectedStudents.size === 0) return;
    if (!confirm(`确定要清除 ${selectedStudents.size} 名学生的评分记录和分析数据吗？此操作不可恢复。`)) return;
    setBatchClearing(true);
    try {
      const ids = Array.from(selectedStudents);
      await batchClearGrades(gradesView, ids);
      setGradeData((prev) =>
        prev.map((r: any) =>
          ids.includes(r[COL.学号])
            ? { ...r, [COL.成绩]: "", [COL.阶段1相似度]: "", [COL.阶段2评分]: "", [COL.总分]: "", [COL.相似度评价]: "", [COL.阶段2评语]: "", [COL.总评]: "", [COL.图样表达]: "", [COL.尺寸标注]: "", [COL.尺寸公差]: "", [COL.表面质量]: "", [COL.形位公差]: "", [COL.技术要求]: "", [COL.教师评语]: "", [COL.作弊]: "", _status: "uploaded" }
            : r
        )
      );
      setSelectedStudents(new Set());
    } catch (e: any) {
      alert(e.message);
    } finally {
      setBatchClearing(false);
    }
  };

  // 选择文件后解析文件名并查询班级
  const handleSupplementFile = async (file: File) => {
    // 根据题目类型校验文件格式
    const q = questions.find(x => x.id === gradesView);
    const subType = (q as any)?.submission_type || "pdf";
    const ext = file.name.split(".").pop()?.toLowerCase();
    const header = await file.slice(0, 4).text();
    if (subType === "dxf") {
      if (ext !== "dxf") {
        alert("本题要求提交 DXF 文件，请上传 .dxf 格式文件");
        setSupplementFile(null);
        return;
      }
    } else if (header !== "%PDF" && subType !== "image") {
      alert("仅支持 PDF 格式文件，请上传真实的 PDF 文件");
      setSupplementFile(null);
      return;
    }
    setSupplementFile(file);
    setSupplementParsed(null);
    setSupplementParsing(true);
    try {
      const parsed = parseFilename(file.name);
      if (!parsed) {
        setSupplementParsing(false);
        return;  // 无法解析，让教师在下方手动输入
      }
      const res = await lookupRoster(parsed.name, parsed.sid);
      setSupplementParsed({
        name: parsed.name,
        sid: parsed.sid,
        className: (res as any).found ? (res as any).class : "",
      });
    } catch {
      // 查询失败，仍然显示解析结果（无班级）
    } finally {
      setSupplementParsing(false);
    }
  };

  const handleSupplement = async () => {
    if (!gradesView || !supplementFile || !supplementParsed) {
      alert("请选择文件并确认学生信息");
      return;
    }
    setSupplementSubmitting(true);
    try {
      await supplementSubmission(gradesView, supplementParsed.name, supplementParsed.sid, supplementFile);
      setSupplementModal(false);
      setSupplementFile(null);
      setSupplementParsed(null);
      handleViewGrades(gradesView);
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

  // 等级阈值从后端 API 加载（/api/teacher/settings → grade_thresholds）
  const [gradeThresholds, setGradeThresholds] = useState<[number, string][]>([]);

  const computeTotal = (p1: number, p2: number) => Math.round(Math.sqrt(p1 * p2) * 10) / 10;

  const computeGrade = (total: number) => {
    for (const [threshold, grade] of gradeThresholds) {
      if (total >= threshold) return grade;
    }
    return "F";
  };

  const saveEdit = async () => {
    if (!editingCell || !gradesView) return;
    try {
      const col = editingCell.col;
      const isPhaseScore = col === COL.阶段1相似度 || col === COL.阶段2评分;
      let fields: Record<string, string> = { [col]: editValue };

      if (isPhaseScore) {
        // 重新计算总分和评级
        const row = gradeData.find((r: any) => r[COL.学号] === editingCell.sid);
        const p1 = parseFloat(col === COL.阶段1相似度 ? editValue : (row?.[COL.阶段1相似度] || "0"));
        const p2 = parseFloat(col === COL.阶段2评分 ? editValue : (row?.[COL.阶段2评分] || "0"));
        if (!isNaN(p1) && !isNaN(p2)) {
          const total = computeTotal(p1, p2);
          const grade = computeGrade(total);
          fields[COL.总分] = String(total);
          fields[COL.成绩] = grade;
        }
      }

      await editGrade(gradesView, editingCell.sid, fields);
      setGradeData((prev) =>
        prev.map((r: any) =>
          r[COL.学号] === editingCell.sid ? { ...r, ...Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, v])) } : r
        )
      );
    } catch (e: any) {
      alert(e.message);
    }
    setEditingCell(null);
  };

  const GRADE_OPTIONS = ["A+", "A", "B+", "B", "C+", "C", "D+", "D", "F"];

  // DXF 答题图框选项（ACI颜色索引：1红, 2黄, 3绿, 5蓝, 6洋红）
  const FRAME_OPTIONS = ["主视图", "俯视图", "左视图", "其他视图1", "其他视图2"];

  const handleOpenReview = async (sid: string) => {
    if (!gradesView) return;
    const row = gradeData.find((r: any) => r[COL.学号] === sid);
    const name = row?.[COL.姓名] || "";
    setReviewSid(sid);
    setFloatStudentFile(sid);  // 同时显示浮动图
    setReviewGrade(row?.[COL.成绩] || "");
    setReviewComment(row?.[COL.教师评语] || "");
    setSavedText("");
    setStudentAnalysis(null);
    setStudentGradeResult(null);
    if (name) {
      try {
        const res = await getStudentAnalysis(gradesView, sid, name);
        if (res.ready && res.analysis) {
          setStudentAnalysis(res.analysis);
        }
      } catch {}
      try {
        const gr = await getStudentResult(gradesView, sid);
        if (gr) setStudentGradeResult(gr);
      } catch {}
    }
  };

  // 打印：克隆目标内容到 body 下 → 隐藏其他元素 → 调浏览器打印 → 清理
  const handlePrint = (targetId: string) => {
    const el = document.getElementById(targetId);
    if (!el) return;
    // 展开所有折叠内容
    el.querySelectorAll("details:not([open])").forEach((d) => d.setAttribute("open", ""));
    // 克隆内容，移除 no-print 元素
    const clone = el.cloneNode(true) as HTMLElement;
    clone.querySelectorAll(".no-print").forEach((n) => n.remove());
    clone.classList.add("print-clone");
    // 创建临时容器挂到 body 下
    const wrapper = document.createElement("div");
    wrapper.id = "print-wrapper";
    wrapper.appendChild(clone);
    document.body.appendChild(wrapper);
    window.print();
    document.body.removeChild(wrapper);
  };

  const [saving, setSaving] = useState(false);
  const [savedText, setSavedText] = useState("");

  const handleSave = async () => {
    if (!gradesView || !reviewSid) return;
    setSaving(true);
    try {
      const fields: Record<string, string> = {};
      if (reviewGrade) fields[COL.成绩] = reviewGrade;
      fields[COL.教师评语] = reviewComment;
      await editGrade(gradesView, reviewSid, fields);
      setGradeData((prev) =>
        prev.map((r: any) => {
          if (r[COL.学号] !== reviewSid) return r;
          const u = { ...r };
          if (reviewGrade) u[COL.成绩] = reviewGrade;
          u[COL.教师评语] = reviewComment;
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
    <>
      <style>{`
        @media print {
          body > *:not(#print-wrapper) { display: none !important; }
          #print-wrapper {
            display: block !important;
            position: static !important;
            width: 100% !important;
            padding: 16px;
            background: white !important;
          }
          .print-clone {
            max-height: none !important;
            max-width: 100% !important;
            overflow: visible !important;
            width: 100% !important;
          }
          .print-clone details { display: block !important; }
        }
      `}</style>
    <div className="min-h-screen bg-gray-50">
      <header className="bg-blue-700 text-white p-4 shadow flex justify-between items-center">
        <h1 className="text-xl font-bold">{sessionStorage.getItem("teacher_name") || "教师"}老师的工作台</h1>
        <div className="flex items-center gap-3">
          <button
            onClick={handleOpenRoster}
            className="bg-white/20 px-3 py-1 rounded hover:bg-white/30"
          >
            学生信息
          </button>
          <button
            onClick={() => setShowHelp(true)}
            className="bg-white/20 px-3 py-1 rounded hover:bg-white/30"
          >
            帮助
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
                  <th className="text-left p-2">教师</th>
                  <th className="text-left p-2">下载作业</th>
                  <th className="text-left p-2">截止时间</th>
                  <th className="text-center p-2">参考图分析</th>
                  <th className="text-right p-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {questions.map((q) => {
                  const myUsername = sessionStorage.getItem("teacher_username") || "";
                  const isOwner = !(q as any).teacher || (q as any).teacher === myUsername || !myUsername;
                  return (
                  <tr key={q.id} className={`border-b hover:bg-gray-50 ${!isOwner ? "opacity-50 bg-gray-100" : ""}`}>
                    <td className="p-2 font-mono">{q.id}</td>
                    <td className="p-2">{q.title}</td>
                    <td className="p-2 text-sm text-gray-600">{ (q as any).teacher || "-" }</td>
                    <td className="p-2">
                      <div className="flex flex-wrap gap-1">
                        {(() => {
                          const classes = ((q as any).classes || "").split(",").map(c => c.trim()).filter(Boolean);
                          const links: JSX.Element[] = [];
                          const doDownload = (cls?: string) => {
                            downloadHomeworkZip(q.id, cls).catch(e => alert(e.message));
                          };
                          // 每个班一个下载按钮
                          for (const cls of classes) {
                            links.push(
                              <button key={cls}
                                onClick={() => doDownload(cls)}
                                className="inline-block bg-blue-50 text-blue-700 px-2 py-0.5 rounded text-xs hover:bg-blue-100 whitespace-nowrap cursor-pointer"
                                title={`下载「${cls}」作业`}
                              >
                                {cls}
                              </button>
                            );
                          }
                          // 全部作业
                          links.push(
                            <button key="_all"
                              onClick={() => doDownload()}
                              className="inline-block bg-green-50 text-green-700 px-2 py-0.5 rounded text-xs hover:bg-green-100 whitespace-nowrap cursor-pointer"
                              title="下载全部作业"
                            >
                              全部作业
                            </button>
                          );
                          return links;
                        })()}
                      </div>
                    </td>
                    <td className="p-2 text-sm text-gray-600">
                      {isOwner ? (
                        <span className="cursor-pointer hover:text-blue-600"
                          onClick={() => {
                            if (!isOwner) return;
                            const input = prompt("修改截止时间（ISO格式，如 2026-07-28T18:00）", (q as any).deadline || "");
                            if (input !== null) {
                              const fd = new FormData();
                              fd.append("title", q.title);
                              fd.append("description", q.files?.description || "");
                              fd.append("phase1_criteria", q.files?.phase1_criteria || "");
                              fd.append("phase2_criteria", q.files?.phase2_criteria || "");
                              fd.append("submission_type", (q as any).submission_type || "pdf");
                              fd.append("deadline", input);
                              fd.append("required_frames", JSON.stringify((q as any).required_frames || []));
                              updateQuestion(q.id, fd).then(() => loadQuestions()).catch(e => alert(e.message));
                            }
                          }}
                          title="点击修改截止时间">
                          {(q as any).deadline ? new Date((q as any).deadline).toLocaleString("zh-CN", {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "-"}
                        </span>
                      ) : (
                        <span>{(q as any).deadline ? new Date((q as any).deadline).toLocaleString("zh-CN", {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "-"}</span>
                      )}
                    </td>
                    <td className="p-2 text-center">
                      {analyzingQid === q.id ? (
                        <span className="text-yellow-600 text-xs animate-pulse">分析中…</span>
                      ) : analysisErrors[q.id] ? (
                        <div className="flex items-center justify-center gap-1">
                          <span className="text-red-500 text-xs" title={analysisErrors[q.id]}>失败</span>
                          <button
                            onClick={async () => {
                              setAnalysisErrors((prev) => { const n = { ...prev }; delete n[q.id]; return n; });
                              setAnalyzingQid(q.id);
                              pollStartTime.current = Date.now();
                              try { await triggerAnalysis(q.id); } catch (_) {}
                            }}
                            className="text-orange-500 hover:underline text-xs"
                            title={analysisErrors[q.id]}
                          >
                            重试
                          </button>
                        </div>
                      ) : analysisResults[q.id] ? (
                        <div className="flex items-center justify-center gap-1">
                          <button
                            onClick={() => setAnalysisModalQid(q.id)}
                            className="text-blue-600 hover:underline text-xs"
                          >
                            查看
                          </button>
                          <button
                            onClick={async () => {
                              setAnalyzingQid(q.id);
                              pollStartTime.current = Date.now();
                              try { await triggerAnalysis(q.id); } catch (_) {}
                            }}
                            className="text-orange-500 hover:underline text-xs"
                            title="重新分析参考图"
                          >
                            重分析
                          </button>
                        </div>
                      ) : (q.files?.reference_pdf || q.files?.reference_dxf) ? (
                        <button
                          onClick={async () => {
                            setAnalysisErrors((prev) => { const n = { ...prev }; delete n[q.id]; return n; });
                            setAnalyzingQid(q.id);
                            pollStartTime.current = Date.now();
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
                      {isOwner ? (
                        <>
                          <button onClick={() => handleEdit(q)} className="text-blue-600 hover:underline">编辑</button>
                          <button onClick={() => handleDelete(q.id)} className="text-red-600 hover:underline">删除</button>
                        </>
                      ) : (
                        <span className="text-gray-300 text-xs">只读</span>
                      )}
                      <button onClick={() => handleViewGrades(q.id)} className="text-green-600 hover:underline">查看作业</button>
                      <button onClick={() => downloadGradesCsv(q.id).catch(e => alert(e.message))} className="text-amber-600 hover:underline">下载成绩</button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* 参考图分析结果弹窗 */}
        {analysisModalQid && (() => {
          const qid = analysisModalQid;
          const analysis = analysisResults[qid];
          if (!analysis) return null;
          const q = questions.find((x) => x.id === qid);
          return (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[65]"
              onClick={(e) => { if (e.target === e.currentTarget) setAnalysisModalQid(null); }}>
              <div id="print-ref-analysis" className="bg-white rounded-lg shadow-xl p-6 w-full max-w-4xl mx-4 max-h-[90vh] overflow-auto">
                <div className="flex justify-between items-center mb-4 no-print">
                  <h3 className="text-lg font-semibold">题{qid} 参考图分析结果</h3>
                  <div className="flex items-center gap-2">
                    <button onClick={() => handlePrint("print-ref-analysis")}
                      className="px-3 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200">打印</button>
                    <button onClick={() => setAnalysisModalQid(null)}
                      className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
                  </div>
                </div>

                {/* 模型和 Token */}
                {analysis._model && (
                  <div className="bg-gray-50 border rounded px-3 py-2 mb-3 text-xs text-gray-500 flex items-center gap-4 flex-wrap">
                    <span>模型：<span className="font-medium text-gray-700">{analysis._model}</span></span>
                    {analysis._usage && (
                      <span>用量：<span className="font-mono text-gray-600">{analysis._usage.total_tokens?.toLocaleString()} tokens</span>
                        <span className="text-gray-300 ml-1">(提示{analysis._usage.prompt_tokens?.toLocaleString()}+生成{analysis._usage.completion_tokens?.toLocaleString()})</span>
                      </span>
                    )}
                  </div>
                )}

                {/* 参考工程图预览（DXF 优先显示含尺寸的 PNG 渲染图） */}
                {(q?.files?.reference_pdf || q?.files?.reference_dxf) && (
                  <div className="mb-3">
                    <p className="text-xs text-gray-500 mb-1">参考工程图</p>
                    <img src={getTeacherPreviewUrl(qid,
                      q.files.reference_preview || q.files.reference_pdf || q.files.reference_dxf, Date.now())}
                      alt="参考工程图" className="w-full rounded border" />
                  </div>
                )}

                {/* ── DXF 分析数据（ezdxf 提取） ── */}
                {analysis.entities && analysis.entity_counts ? (
                  <div className="space-y-2 text-xs">
                    <div className="p-2 bg-blue-50 rounded border border-blue-100">
                      <p className="text-xs text-blue-500 mb-1 font-medium">DXF 实体统计</p>
                      <div className="grid grid-cols-4 gap-1">
                        {Object.entries(analysis.entity_counts as Record<string,number>).map(([k, v]) => (
                          <span key={k} className="text-gray-700">
                            <span className="text-gray-400">{k}:</span> <strong>{v}</strong>
                          </span>
                        ))}
                      </div>
                    </div>

                    {analysis.dimensions && (analysis.dimensions as any[]).length > 0 && (
                      <table className="w-full border">
                        <thead><tr className="bg-green-50"><th colSpan={3} className="p-1 text-left text-green-800">尺寸标注（{(analysis.dimensions as any[]).length} 个）</th></tr></thead>
                        <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">类型</th><th className="p-1 text-left">文字</th><th className="p-1 text-left">测量值</th></tr></thead>
                        <tbody>{(analysis.dimensions as any[]).map((d: any, i: number) => (
                          <tr key={i} className="border"><td className="p-1">{d.type || "-"}</td><td className="p-1 font-mono">{d.text || "-"}</td><td className="p-1 font-mono">{d.measurement != null ? d.measurement : "-"}</td></tr>
                        ))}</tbody>
                      </table>
                    )}

                    {analysis.texts && (analysis.texts as any[]).length > 0 && (
                      <div className="p-2 bg-yellow-50 rounded border border-yellow-100">
                        <p className="text-xs text-yellow-600 mb-1 font-medium">文字内容（{(analysis.texts as any[]).length} 条）</p>
                        <div className="space-y-1 max-h-32 overflow-auto">
                          {(analysis.texts as any[]).slice(0, 30).map((t: any, i: number) => (
                            <p key={i} className="text-gray-700 text-xs">{t.content}</p>
                          ))}
                        </div>
                      </div>
                    )}

                    {analysis.layers && Object.keys(analysis.layers).length > 0 && (
                      <table className="w-full border">
                        <thead><tr className="bg-purple-50"><th colSpan={4} className="p-1 text-left text-purple-800">图层（{Object.keys(analysis.layers).length} 个）</th></tr></thead>
                        <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">名称</th><th className="p-1 text-left">颜色</th><th className="p-1 text-left">线型</th><th className="p-1 text-left">线宽</th></tr></thead>
                        <tbody>{Object.entries(analysis.layers as Record<string,any>).map(([name, info]: [string, any]) => (
                          <tr key={name} className="border"><td className="p-1">{name}</td><td className="p-1 font-mono">{info.color}</td><td className="p-1">{info.linetype || "-"}</td><td className="p-1 font-mono">{info.lineweight > 0 ? info.lineweight + "mm" : "-"}</td></tr>
                        ))}</tbody>
                      </table>
                    )}

                    {analysis.bounds && (
                      <p className="text-xs text-gray-400">范围: X[{analysis.bounds.min_x} ~ {analysis.bounds.max_x}] Y[{analysis.bounds.min_y} ~ {analysis.bounds.max_y}]</p>
                    )}

                    {/* ── 参考图图框/视图信息 ── */}
                    {analysis.frames && (analysis.frames as any[]).length > 0 && (
                      <details className="mt-2">
                        <summary className="text-xs text-purple-700 cursor-pointer hover:underline font-medium">
                          图框检测（{(analysis.frames as any[]).length} 个）
                          {(() => {
                            const _af: string[] = q?.required_frames || [];
                            return _af.length > 0 ? <span className="ml-1 text-purple-600 font-bold">答题: {_af.join("、")}</span> : null;
                          })()}
                        </summary>
                        <div className="mt-1 space-y-2">
                          {(() => {
                            const _af: string[] = q?.required_frames || [];
                            return (analysis.frames as any[]).map((f: any, fi: number) => {
                              const b = f.bbox || {};
                              const isAnswer = _af.length > 0 && _af.includes(f.name);
                              const viewEnt = analysis.views?.[f.name];
                              return (
                                <div key={fi} className={`p-2 rounded border ${isAnswer ? 'bg-purple-50 border-purple-300 ring-1 ring-purple-200' : 'bg-gray-50 border-gray-200'}`}>
                                  <p className="text-xs font-medium mb-1">
                                    {isAnswer && <span>📌 </span>}{f.name}
                                    <span className="text-gray-400 ml-2">颜色 {f.color}</span>
                                    {isAnswer && <span className="ml-2 text-xs text-purple-600 font-semibold">答题视图</span>}
                                  </p>
                                  <p className="text-xs text-gray-500">
                                    范围: X[{b.min_x?.toFixed(1)}~{b.max_x?.toFixed(1)}] Y[{b.min_y?.toFixed(1)}~{b.max_y?.toFixed(1)}]
                                  </p>
                                  {/* 答题视图才显示统计 + 详细尺寸 */}
                                  {isAnswer && viewEnt && (
                                    <div className="mt-1 space-y-1">
                                      <div className="grid grid-cols-4 gap-x-2 gap-y-0.5 text-xs text-gray-600">
                                        {Object.entries(viewEnt).map(([vk, vv]) => {
                                          if (!Array.isArray(vv) || vk === "dimensions") return null;
                                          return (
                                            <span key={vk}>
                                              <span className="text-gray-400">{vk}:</span> <strong>{(vv as any[]).length}</strong>
                                            </span>
                                          );
                                        })}
                                      </div>
                                      {/* 答题视图的尺寸标注 */}
                                      {Array.isArray(viewEnt.dimensions) && viewEnt.dimensions.length > 0 && (
                                        <details>
                                          <summary className="text-xs text-green-700 cursor-pointer hover:underline font-medium mt-1">
                                            尺寸标注（{viewEnt.dimensions.length} 个）
                                          </summary>
                                          <table className="w-full border mt-1">
                                            <thead><tr className="bg-gray-50 border"><th className="p-0.5 text-left text-xs">类型</th><th className="p-0.5 text-left text-xs">文字</th><th className="p-0.5 text-left text-xs">测量值</th></tr></thead>
                                            <tbody>{viewEnt.dimensions.map((d: any, di: number) => (
                                              <tr key={di} className="border"><td className="p-0.5 text-xs">{d.type || "-"}</td><td className="p-0.5 font-mono text-xs">{d.text || "-"}</td><td className="p-0.5 font-mono text-xs">{d.measurement != null ? d.measurement : "-"}</td></tr>
                                            ))}</tbody>
                                          </table>
                                        </details>
                                      )}
                                    </div>
                                  )}
                                </div>
                              );
                            });
                          })()}
                        </div>
                      </details>
                    )}

                    {analysis.view_overlap_ratios && (() => {
                      const af: string[] = q?.required_frames || [];
                      const views = af.length > 0 ? af : Object.keys(analysis.view_overlap_ratios);
                      return (
                      <div className="bg-purple-50 rounded p-2 mt-2">
                        <p className="text-xs text-purple-700 font-medium mb-1">重叠率 <span class="font-normal text-gray-400">(原始−clean)÷clean</span></p>
                        <p className="text-xs text-gray-400 mb-1">0%=无重叠，越大重叠越多</p>
                        <div className="space-y-0.5 text-xs text-gray-600">
                          {views.map((vname: string) => {
                            const vdata = analysis.view_overlap_ratios[vname];
                            if (!vdata) return null;
                            return (
                            <div key={vname} className="flex flex-wrap gap-x-3">
                              <span className="font-medium text-purple-800 min-w-[5rem]">{vname}</span>
                              {["solid", "dashed", "centerline", "total"].map((cat: string) => {
                                const d = vdata[cat];
                                if (!d) return null;
                                return (
                                  <span key={cat}>
                                    {cat === "total" ? "合计" : cat}: {d.overlap_ratio === 0 ? "无重叠" : `${(d.overlap_ratio * 100).toFixed(1)}%`}
                                    <span className="text-gray-300 ml-0.5">({d.raw_len}→{d.clean_len})</span>
                                  </span>
                                );
                              })}
                            </div>
                            );
                          })}
                        </div>
                      </div>
                      );
                    })()}
                  </div>
                ) : (
                  <>

                {/* 工程图概述 */}
                {analysis.工程图概述 && (
                  <div className="mb-3 p-3 bg-blue-50 rounded border border-blue-100 text-sm">
                    <p className="text-xs text-blue-500 mb-1 font-medium">工程图概述</p>
                    <p className="text-gray-700 whitespace-pre-wrap">{analysis.工程图概述}</p>
                  </div>
                )}

                {/* 基本信息 */}
                {analysis.基本信息 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-blue-50"><th colSpan={4} className="p-1 text-left text-blue-800">基本信息</th></tr></thead>
                    <tbody>
                      <tr className="border"><td className="p-1 text-gray-500 w-16">名称</td><td className="p-1">{analysis.基本信息.零件名称 || analysis.基本信息.装配体名称 || analysis.基本信息.组合体类型 || "-"}</td><td className="p-1 text-gray-500 w-16">材料</td><td className="p-1">{analysis.基本信息.材料 || "-"}</td></tr>
                      <tr className="border"><td className="p-1 text-gray-500">比例</td><td className="p-1">{analysis.基本信息.比例 || "-"}</td><td className="p-1 text-gray-500">类型</td><td className="p-1">{analysis.基本信息.组合体类型 || analysis.基本信息.零件总数 ? `零件总数: ${analysis.基本信息.零件总数}` : "-"}</td></tr>
                    </tbody>
                  </table>
                )}

                {/* 几何特征 */}
                {Array.isArray(analysis.几何特征) && analysis.几何特征.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-blue-50"><th colSpan={3} className="p-1 text-left text-blue-800">几何特征（{analysis.几何特征.length} 个）</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">名称</th><th className="p-1 text-left">尺寸</th><th className="p-1 text-left">所在视图</th></tr></thead>
                    <tbody>{analysis.几何特征.map((f: any, i: number) => (<tr key={i} className="border"><td className="p-1">{f.名称}</td><td className="p-1 font-mono">{f.尺寸 || "-"}</td><td className="p-1 text-gray-600">{Array.isArray(f.所在视图) ? f.所在视图.join("、") : f.所在视图 || "-"}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 各视图信息（组合体三视图） */}
                {Array.isArray(analysis.各视图信息) && analysis.各视图信息.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-blue-50"><th colSpan={4} className="p-1 text-left text-blue-800">各视图信息</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1">视图名</th><th className="p-1">可见特征</th><th className="p-1">隐藏线</th><th className="p-1">对称性</th></tr></thead>
                    <tbody>{analysis.各视图信息.map((v: any, i: number) => (<tr key={i} className="border"><td className="p-1 font-medium">{v.视图名}</td><td className="p-1 text-gray-600">{Array.isArray(v.可见特征) ? v.可见特征.join("、") : "-"}</td><td className="p-1 text-gray-600">{Array.isArray(v.隐藏线) ? v.隐藏线.join("、") : "-"}</td><td className="p-1">{v.对称性 || "-"}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 尺寸 */}
                {Array.isArray(analysis.尺寸) && analysis.尺寸.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-green-50"><th colSpan={2} className="p-1 text-left text-green-800">尺寸标注（{analysis.尺寸.length} 个）</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">类别</th><th className="p-1 text-left">数值</th></tr></thead>
                    <tbody>{analysis.尺寸.map((d: any, i: number) => (<tr key={i} className="border"><td className="p-1">{d.类别 || "-"}</td><td className="p-1 font-mono">{d.数值}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 尺寸公差 */}
                {Array.isArray(analysis.尺寸公差) && analysis.尺寸公差.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-green-50"><th colSpan={2} className="p-1 text-left text-green-800">尺寸公差（{analysis.尺寸公差.length} 项）</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">公称尺寸</th><th className="p-1 text-left">公差</th></tr></thead>
                    <tbody>{analysis.尺寸公差.map((d: any, i: number) => (<tr key={i} className="border"><td className="p-1 font-mono">{d.公称尺寸}</td><td className="p-1 font-mono">{d.公差}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 几何公差 */}
                {Array.isArray(analysis.几何公差) && analysis.几何公差.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-green-50"><th colSpan={3} className="p-1 text-left text-green-800">几何公差（{analysis.几何公差.length} 项）</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">类别</th><th className="p-1 text-left">数值</th><th className="p-1 text-left">基准</th></tr></thead>
                    <tbody>{analysis.几何公差.map((g: any, i: number) => (<tr key={i} className="border"><td className="p-1">{g.类别}</td><td className="p-1 font-mono">{g.数值}</td><td className="p-1 font-mono">{g.基准 || "-"}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 表面粗糙度 */}
                {Array.isArray(analysis.表面粗糙度) && analysis.表面粗糙度.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-green-50"><th colSpan={2} className="p-1 text-left text-green-800">表面粗糙度（{analysis.表面粗糙度.length} 处）</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1 text-left">类别</th><th className="p-1 text-left">数值</th></tr></thead>
                    <tbody>{analysis.表面粗糙度.map((r: any, i: number) => (<tr key={i} className="border"><td className="p-1">{r.类别 || "Ra"}</td><td className="p-1 font-mono">{r.数值}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 零件清单（装配图） */}
                {Array.isArray(analysis.零件清单) && analysis.零件清单.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-purple-50"><th colSpan={4} className="p-1 text-left text-purple-800">零件清单（{analysis.零件清单.length} 个）</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1">序号</th><th className="p-1">名称</th><th className="p-1">数量</th><th className="p-1">材料</th></tr></thead>
                    <tbody>{analysis.零件清单.map((p: any, i: number) => (<tr key={i} className="border"><td className="p-1">{p.序号}</td><td className="p-1">{p.名称}</td><td className="p-1 text-center">{p.数量}</td><td className="p-1">{p.材料 || "-"}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 配合关系（装配图） */}
                {Array.isArray(analysis.配合关系) && analysis.配合关系.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-purple-50"><th colSpan={3} className="p-1 text-left text-purple-800">配合关系</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1">类型</th><th className="p-1">零件组合</th><th className="p-1">配合尺寸</th></tr></thead>
                    <tbody>{analysis.配合关系.map((p: any, i: number) => (<tr key={i} className="border"><td className="p-1">{p.类型}</td><td className="p-1">{p.零件组合}</td><td className="p-1 font-mono">{p.配合尺寸 || "-"}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 技术要求 */}
                {analysis.技术要求 && (
                  <div className="mb-3 p-3 bg-yellow-50 rounded border border-yellow-100 text-xs">
                    <p className="text-yellow-600 mb-1 font-medium">技术要求</p>
                    <p className="text-gray-700 whitespace-pre-wrap">{analysis.技术要求}</p>
                  </div>
                )}

                {/* 装配技术要求 */}
                {analysis.装配技术要求 && (
                  <div className="mb-3 p-3 bg-yellow-50 rounded border border-yellow-100 text-xs">
                    <p className="text-yellow-600 mb-1 font-medium">装配技术要求</p>
                    <p className="text-gray-700 whitespace-pre-wrap">{analysis.装配技术要求}</p>
                  </div>
                )}

                {/* 外形尺寸（装配图） */}
                {Array.isArray(analysis.外形尺寸) && analysis.外形尺寸.length > 0 && (
                  <table className="w-full border mb-3 text-xs">
                    <thead><tr className="bg-purple-50"><th colSpan={2} className="p-1 text-left text-purple-800">外形尺寸</th></tr></thead>
                    <thead><tr className="bg-gray-50 border"><th className="p-1">类别</th><th className="p-1">数值</th></tr></thead>
                    <tbody>{analysis.外形尺寸.map((d: any, i: number) => (<tr key={i} className="border"><td className="p-1">{d.类别}</td><td className="p-1 font-mono">{d.数值}</td></tr>))}</tbody>
                  </table>
                )}

                {/* 原始 JSON */}
                <details>
                  <summary className="text-xs text-gray-400 cursor-pointer hover:underline">原始 JSON（调试用）</summary>
                  <pre className="text-xs bg-gray-100 p-2 rounded mt-1 overflow-auto max-h-48 whitespace-pre-wrap">{JSON.stringify(analysis, null, 2)}</pre>
                </details>
                </>
              )}
              </div>
            </div>
          );
        })()}

        {/* Question Form Modal */}
        {showForm && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-auto">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold">{editingId ? "编辑题目" : "新增题目"}</h2>
                <button onClick={resetForm}
                  className="text-gray-400 hover:text-gray-600 text-xl leading-none px-2 py-1 rounded hover:bg-gray-100"
                  title="关闭不保存">✕</button>
              </div>
              <div className="space-y-3">
                {!editingId && (
                  <p className="text-xs text-gray-400">题号自动生成（如 260727-001），无需手动输入</p>
                )}
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
                  <label className="block text-sm font-medium text-gray-700 mb-1">学生提交文件类型</label>
                  <div className="flex gap-4">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input type="radio" name="submission_type" value="pdf"
                        checked={submissionType === "pdf"} onChange={(e) => setSubmissionType(e.target.value)} />
                      <span className="text-sm">PDF 文件</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input type="radio" name="submission_type" value="image"
                        checked={submissionType === "image"} onChange={(e) => setSubmissionType(e.target.value)} />
                      <span className="text-sm">图片文件</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input type="radio" name="submission_type" value="dxf"
                        checked={submissionType === "dxf"} onChange={(e) => setSubmissionType(e.target.value)} />
                      <span className="text-sm">DXF 文件</span>
                    </label>
                  </div>
                </div>

                {/* DXF 答题图框设置（仅 DXF 类型时显示） */}
                {submissionType === "dxf" && (
                  <div className="border rounded p-3 bg-blue-50/30">
                    <label className="block text-sm font-medium text-gray-700 mb-2">答题图框（DXF）</label>
                    <p className="text-xs text-gray-400 mb-2">选择学生需要绘制的图框，留空=无图框（不限绘制区域）。图框线粗 1.0mm</p>
                    <div className="flex flex-wrap gap-3">
                      {FRAME_OPTIONS.map((frame, i) => {
                        const colors = ["bg-red-400", "bg-yellow-400", "bg-green-400", "bg-blue-400", "bg-fuchsia-400"];
                        return (
                          <label key={frame} className="flex items-center gap-1.5 cursor-pointer text-sm">
                            <input type="checkbox" checked={requiredFrames.includes(frame)}
                              onChange={(e) => {
                                if (e.target.checked) setRequiredFrames([...requiredFrames, frame]);
                                else setRequiredFrames(requiredFrames.filter(x => x !== frame));
                              }}
                              className="w-4 h-4" />
                            <span className={`inline-block w-3 h-3 rounded-full ${colors[i]}`} />
                            {frame}
                          </label>
                        );
                      })}
                    </div>
                    <p className="text-xs text-gray-400 mt-1.5">
                      空列表=无图框（不限区域）；至少选一个=有图框，仅检查选中图框内的绘制内容
                    </p>
                  </div>
                )}

                <div>
                  <div className="flex gap-4">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input type="radio" name="visible_to_others" value={0}
                        checked={visibleToOthers === 0} onChange={() => setVisibleToOthers(0)} />
                      <span className="text-sm">仅限本人</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input type="radio" name="visible_to_others" value={1}
                        checked={visibleToOthers === 1} onChange={() => setVisibleToOthers(1)} />
                      <span className="text-sm">其他教师可见</span>
                    </label>
                  </div>
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
                {/* 识读模板 */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">识读模板</label>
                  <div className="flex gap-2 mb-2">
                    <select value={templateType}
                      onChange={(e) => { setTemplateType(e.target.value); loadTemplateContent(e.target.value); }}
                      className="border rounded px-3 py-1.5 text-sm">
                      <option value="零件图识读模板.txt">零件图</option>
                      <option value="装配图识读模板.txt">装配图</option>
                      <option value="平面图识读模板.txt">平面图</option>
                      <option value="组合体三视图识读模板.txt">组合体三视图</option>
                      <option value="DXF识读模板.txt">DXF</option>
                    </select>
                    {!editingId && (
                      <button type="button" onClick={async () => {
                        try {
                          const data = await getTemplates();
                          if (data.templates && data.templates[templateType]) {
                            setTemplateContent(data.templates[templateType]);
                          }
                        } catch (_) {}
                      }}
                        className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded hover:bg-gray-200">
                        重置为默认
                      </button>
                    )}
                  </div>
                  <textarea
                    value={templateContent}
                    onChange={(e) => { setTemplateContent(e.target.value); setTemplateLoaded(true); }}
                    rows={10}
                    className="w-full border rounded px-3 py-2 text-sm font-mono"
                    placeholder="选择模板类型后自动加载..."
                  />
                  <p className="text-xs text-gray-400 mt-0.5">
                    LLM 根据此模板从工程图中提取信息。创建后可随时修改。
                  </p>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">适用班别</label>
                    {classes.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {classes.map((c) => (
                          <label key={c.class_name} className="flex items-center gap-1.5 cursor-pointer text-sm">
                            <input type="checkbox" checked={qClasses.includes(c.class_name)}
                              onChange={(e) => {
                                if (e.target.checked) setQClasses([...qClasses, c.class_name]);
                                else setQClasses(qClasses.filter(x => x !== c.class_name));
                              }}
                              className="w-4 h-4" />
                            {c.class_name}
                          </label>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-gray-400">暂无班别数据，请先在学生信息中导入班级</p>
                    )}
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">创建教师</label>
                    <input type="text" value={sessionStorage.getItem("teacher_name") || sessionStorage.getItem("teacher_username") || ""}
                      disabled className="w-full border rounded px-3 py-1.5 text-sm bg-gray-100 text-gray-600"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">提交截止时间</label>
                  <input type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)}
                    className="w-full border rounded px-3 py-1.5 text-sm" />
                  <p className="text-xs text-gray-400 mt-0.5">留空则不限制提交时间</p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">题目附图</label>
                  <FileButton
                    accept="image/*"
                    onChange={(file) => setImage(file)}
                    label="选择图片"
                    fileName={image?.name}
                  />
                  <div className="mt-2">
                    {image && imagePreviewUrl ? (
                      <img src={imagePreviewUrl} alt="题目附图预览" className="w-full rounded border" />
                    ) : existingImages.length > 0 ? (
                      <img src={getTeacherPreviewUrl(editingId!, existingImages[0])} alt="题目附图" className="w-full rounded border" />
                    ) : (
                      <p className="text-xs text-gray-400 py-4 text-center border rounded bg-gray-50">请提交文件</p>
                    )}
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    参考工程图 ({submissionType === "dxf" ? "DXF" : "PDF"})
                  </label>
                  <FileButton
                    accept={submissionType === "dxf" ? ".dxf" : ".pdf"}
                    onChange={(file) => setRefPdf(file)}
                    label={submissionType === "dxf" ? "选择DXF" : "选择PDF"}
                    fileName={refPdf?.name}
                  />
                  <div className="mt-2">
                    {refPdf ? (
                      <div className="text-xs text-blue-600 py-3 px-3 bg-blue-50 rounded border border-blue-200">
                        已选择新文件：{refPdf.name}（保存后生效）
                      </div>
                    ) : existingRefPdf ? (
                      <img src={getTeacherPreviewUrl(editingId!, existingRefPdf)} alt="参考工程图" className="w-full rounded border" />
                    ) : (
                      <p className="text-xs text-gray-400 py-4 text-center border rounded bg-gray-50">请提交文件</p>
                    )}
                  </div>
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
                    onClick={() => { setSupplementModal(true); setSupplementFile(null); setSupplementParsed(null); }}
                    disabled={!isGradeOwner}
                    className="px-3 py-1 bg-orange-500 text-white rounded hover:bg-orange-600 text-sm disabled:opacity-50"
                  >
                    补充提交
                  </button>
                  <button
                    onClick={handleBatchGrade}
                    disabled={!isGradeOwner || selectedStudents.size === 0 || batchGrading}
                    className="px-4 py-1 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50 text-sm"
                  >
                    {batchGrading ? "提交中…" : `批量评分 (${selectedStudents.size})`}
                  </button>
                  <button
                    onClick={handleBatchClear}
                    disabled={!isGradeOwner || selectedStudents.size === 0 || batchClearing}
                    className="px-4 py-1 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 text-sm"
                  >
                    {batchClearing ? "清除中…" : `清除评分 (${selectedStudents.size})`}
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
                          checked={gradeData.filter((r: any) => r[COL.学号]).length > 0 && selectedStudents.size === gradeData.filter((r: any) => r[COL.学号]).length}
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
                      const sid = row[COL.学号] || "";
                      const rowHasGrade = !!(row[COL.成绩] || row[COL.总分]);
                      const status = row["_status"] || (rowHasGrade ? "graded" : "");
                      const filename = row["_filename"] || "";
                      const isGraded = rowHasGrade;
                      const isFailed = status === "grade_failed" || status === "analyze_failed";
                      const isSubmitted = status === "submitted";
                      return (
                        <tr key={i} className={`border-b hover:bg-gray-50 ${isFailed ? "bg-red-50/50" : isSubmitted ? "bg-blue-50/50" : !isGraded ? "bg-yellow-50/50" : ""}`}>
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
                            ) : status === "graded" ? (
                              <span className="text-green-600 text-xs font-medium">已评分</span>
                            ) : (
                              <span className="text-blue-500 text-xs font-medium">提交未评</span>
                            )}
                          </td>
                          <td className="p-2">
                            <div className="flex gap-1 items-center">
                              {filename ? (
                                <button onClick={() => handleOpenReview(sid)}
                                  className="text-blue-600 hover:underline text-xs">查看</button>
                              ) : (
                                <span className="text-gray-300 text-xs">-</span>
                              )}
                              {isGradeOwner && (
                                <button onClick={async () => {
                                  if (!confirm(`确定要打回 ${row[COL.姓名] || sid} 的作业吗？`)) return;
                                  try {
                                    await rejectSubmission(gradesView!, sid);
                                    handleViewGrades(gradesView!);
                                  } catch (e: any) { alert(e.message); }
                                }}
                                  className="text-orange-500 hover:underline text-xs ml-2">打回</button>
                              )}
                            </div>
                          </td>
                          {gradeColumns.map((col: string, j: number) => {
                            const isEditing = editingCell?.sid === sid && editingCell?.col === col;
                            const editable = isGraded && isGradeOwner && [COL.阶段1相似度, COL.阶段2评分].includes(col);
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
                  <label className="block text-sm text-gray-600 mb-1">作业文件</label>
                  <FileButton
                    accept={(questions.find(x => x.id === gradesView) as any)?.submission_type === "dxf" ? ".dxf" :
                            (questions.find(x => x.id === gradesView) as any)?.submission_type === "image" ? "image/*" : ".pdf"}
                    onChange={(file) => handleSupplementFile(file)}
                    label="选择作业文件"
                    fileName={supplementFile?.name}
                  />
                  <p className="text-xs text-gray-400 mt-1">文件名需包含学号和姓名，如 2024001_张三{(questions.find(x => x.id === gradesView) as any)?.submission_type === "dxf" ? ".dxf" : ".pdf"}</p>
                </div>

                {/* 解析结果确认面板 */}
                {supplementParsing && (
                  <div className="text-sm text-blue-600 py-2">正在识别学生信息…</div>
                )}
                {supplementFile && !supplementParsing && !supplementParsed && (
                  <div className="bg-yellow-50 rounded p-3 text-sm text-yellow-700">
                    无法从文件名自动识别学号和姓名，请确保文件名包含学号和姓名（如 2024001_张三.pdf）。
                  </div>
                )}
                {supplementParsed && (
                  <div className="bg-green-50 rounded p-3 space-y-1.5 text-sm">
                    <div className="flex justify-between">
                      <span className="text-gray-500">班别</span>
                      <span className="font-medium">{supplementParsed.className || "未找到"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-500">学号</span>
                      <span className="font-medium font-mono">{supplementParsed.sid}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-500">姓名</span>
                      <span className="font-medium">{supplementParsed.name}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-gray-500">文件</span>
                      <span className="text-gray-600 truncate max-w-[200px]">{supplementFile?.name || ""}</span>
                    </div>
                  </div>
                )}
              </div>
              <div className="flex justify-end gap-2 mt-4">
                <button
                  onClick={() => { setSupplementModal(false); setSupplementFile(null); setSupplementParsed(null); }}
                  className="px-4 py-2 border rounded hover:bg-gray-50 text-sm"
                >
                  取消
                </button>
                <button
                  onClick={handleSupplement}
                  disabled={supplementSubmitting || !supplementParsed}
                  className="px-4 py-2 bg-orange-500 text-white rounded hover:bg-orange-600 disabled:opacity-50 text-sm"
                >
                  {supplementSubmitting ? "提交中…" : "确认提交"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 参考图浮动面板 */}
        {floatQid && (() => {
          const q = questions.find((x) => x.id === floatQid);
          const refFile = q?.files?.reference_preview || q?.files?.reference_pdf || q?.files?.reference_dxf;
          if (!refFile) return null;
          return (
            <FloatingImageViewer
              src={getTeacherPreviewUrl(floatQid, refFile, Date.now())}
              title={`题${floatQid} 参考图`}
              visible={true}
              onClose={() => setFloatQid(null)}
              initialWidth={320}
              initialHeight={360}
              zIndex={75}
            />
          );
        })()}

        {/* 查看作业浮动图（独立于弹窗，关闭不影响弹窗） */}
        {floatStudentFile && gradesView && (
          <FloatingImageViewer
            src={getTeacherStudentPreviewUrl(gradesView, floatStudentFile)}
            title="学生工程图"
            visible={true}
            onClose={() => setFloatStudentFile(null)}
            initialWidth={340}
            initialHeight={380}
            zIndex={70}
          />
        )}

        {/* 查看作业弹窗 */}
        {reviewSid && gradesView && (() => {
          const row = gradeData.find((r: any) => r[COL.学号] === reviewSid);
          if (!row) return null;
          const isGraded = !!(row[COL.成绩] || row[COL.总分] || row[COL.阶段1相似度]);
          const DIMS = ["图样表达", "尺寸标注", "尺寸公差", "表面质量", "形位公差", "技术要求"];
          // 当前题目的答题图框列表
          const curQuestion = questions.find((q: any) => q.id === gradesView);
          const answerFrames: string[] = curQuestion?.required_frames || [];
          return (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[65]"
              onClick={(e) => {
                if (e.target === e.currentTarget) { setReviewSid(null); setFloatStudentFile(null); }
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
                id="print-review-hw"
                className="bg-white rounded-lg shadow-xl p-6 w-full max-w-4xl mx-4 max-h-[90vh] overflow-auto"
                style={modalPos ? { position: "fixed", left: modalPos.x, top: modalPos.y, margin: 0 } : {}}>
                <div className="flex justify-between items-center mb-4 cursor-grab active:cursor-grabbing select-none no-print"
                  onMouseDown={(e) => {
                    const el = reviewModalRef.current;
                    if (!el) return;
                    const rect = el.getBoundingClientRect();
                    setModalPos({ x: rect.left, y: rect.top });
                    modalMoveRef.current = { mx: e.clientX, my: e.clientY, ox: rect.left, oy: rect.top };
                  }}>
                  <h3 className="text-lg font-semibold">
                    {row[COL.姓名]} ({row[COL.学号]}) 的作业
                  </h3>
                  <div className="flex gap-2">
                    <button onClick={() => handlePrint("print-review-hw")}
                      onMouseDown={(e) => e.stopPropagation()}
                      className="px-3 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200">打印</button>
                    {isGradeOwner && (
                      <button onClick={handleSave} disabled={saving}
                        onMouseDown={(e) => e.stopPropagation()}
                        className="px-4 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 text-sm">
                        {saving ? "保存中…" : "保存"}
                      </button>
                    )}
                    {!isGradeOwner && <span className="text-xs text-gray-400">只读</span>}
                    {savedText && <span className="text-xs text-green-600">{savedText}</span>}
                    <button onClick={() => { setReviewSid(null); setFloatStudentFile(null); }}
                      onMouseDown={(e) => e.stopPropagation()}
                      className="px-3 py-1 border rounded hover:bg-gray-50 text-sm">关闭</button>
                  </div>
                </div>

                {/* 学生工程图 */}
                <div className="border rounded-lg overflow-hidden bg-gray-100 mb-4">
                  <img
                    src={`${getTeacherStudentPreviewUrl(gradesView, reviewSid)}?t=${Date.now()}`}
                    alt="学生工程图"
                    className="w-full"
                    onError={(e) => {
                      const el = e.target as HTMLImageElement;
                      el.style.display = "none";
                      // 显示提示占位
                      const placeholder = el.nextElementSibling;
                      if (placeholder && placeholder.classList.contains("img-error-placeholder")) {
                        (placeholder as HTMLElement).style.display = "flex";
                      }
                    }}
                  />
                  <div className="img-error-placeholder hidden items-center justify-center py-16 text-gray-400 text-sm">
                    该学生尚未提交工程图文件
                  </div>
                </div>

                {/* 模型和 Token 用量 */}
                {(row._model || studentAnalysis?._model) && (
                  <div className="bg-gray-50 border rounded px-3 py-2 mb-3 text-xs text-gray-500 flex items-center gap-4 flex-wrap">
                    <span>模型：<span className="font-medium text-gray-700">{row._model || studentAnalysis?._model}</span></span>
                    {studentAnalysis?._usage && (
                      <span>分析：<span className="font-mono text-gray-600">{studentAnalysis._usage.total_tokens?.toLocaleString()} tokens</span></span>
                    )}
                  </div>
                )}

                {/* 学生图面分析结果 */}
                {studentAnalysis && (
                  <div className="border-t pt-3 mb-4">
                    <details className="mb-2" open>
                      <summary className="text-sm font-medium text-blue-700 cursor-pointer hover:underline mb-2">
                        {studentAnalysis.entities ? "DXF 提取数据" : "工程图识读结果"}
                      </summary>
                      <div className="text-xs space-y-1">

                        {/* ── DXF 分析 ── */}
                        {studentAnalysis.entities && studentAnalysis.entity_counts ? (
                          <div className="space-y-1">
                            <div className="p-2 bg-blue-50 rounded border border-blue-100">
                              <p className="text-xs text-blue-500 mb-1 font-medium">实体统计</p>
                              <div className="grid grid-cols-4 gap-1">
                                {Object.entries(studentAnalysis.entity_counts as Record<string,number>).map(([k, v]) => (
                                  <span key={k} className="text-gray-700"><span className="text-gray-400">{k}:</span> <strong>{v}</strong></span>
                                ))}
                              </div>
                            </div>
                            {studentAnalysis.dimensions && (studentAnalysis.dimensions as any[]).length > 0 && (
                              <table className="w-full border"><thead><tr className="bg-green-50"><th colSpan={3} className="p-1 text-left text-green-800">尺寸标注（{(studentAnalysis.dimensions as any[]).length} 个）</th></tr></thead>
                                <thead><tr className="bg-gray-50 border"><th className="p-1">类型</th><th className="p-1">文字</th><th className="p-1">测量值</th></tr></thead>
                                <tbody>{(studentAnalysis.dimensions as any[]).map((d: any, i: number) => (<tr key={i} className="border"><td className="p-1">{d.type || "-"}</td><td className="p-1 font-mono">{d.text || "-"}</td><td className="p-1 font-mono">{d.measurement != null ? d.measurement : "-"}</td></tr>))}</tbody></table>
                            )}
                            {studentAnalysis.texts && (studentAnalysis.texts as any[]).length > 0 && (
                              <div className="p-2 bg-yellow-50 rounded border border-yellow-100">
                                <p className="text-xs text-yellow-600 mb-1 font-medium">文字（{(studentAnalysis.texts as any[]).length} 条）</p>
                                <div className="space-y-0.5 max-h-24 overflow-auto">
                                  {(studentAnalysis.texts as any[]).slice(0, 15).map((t: any, i: number) => (<p key={i} className="text-gray-700">{t.content}</p>))}
                                </div>
                              </div>
                            )}
                            {studentAnalysis.bounds && (
                              <p className="text-gray-400">范围: X[{studentAnalysis.bounds.min_x}~{studentAnalysis.bounds.max_x}] Y[{studentAnalysis.bounds.min_y}~{studentAnalysis.bounds.max_y}]</p>
                            )}
                            {/* ── 图框/视图信息 ── */}
                            {studentAnalysis.frames && (studentAnalysis.frames as any[]).length > 0 && (
                              <details className="mt-2">
                                <summary className="text-xs text-purple-700 cursor-pointer hover:underline font-medium">
                                  图框检测（{(studentAnalysis.frames as any[]).length} 个）
                                  {answerFrames.length > 0 && (
                                    <span className="ml-1 text-purple-600 font-bold">答题: {answerFrames.join("、")}</span>
                                  )}
                                </summary>
                                <div className="mt-1 space-y-2">
                                  {(studentAnalysis.frames as any[]).map((f: any, fi: number) => {
                                    const b = f.bbox || {};
                                    const isAnswer = answerFrames.length > 0 && answerFrames.includes(f.name);
                                    const viewEnt = studentAnalysis.views?.[f.name];
                                    return (
                                      <div key={fi} className={`p-2 rounded border ${isAnswer ? 'bg-purple-50 border-purple-300 ring-1 ring-purple-200' : 'bg-gray-50 border-gray-200'}`}>
                                        <p className="text-xs font-medium mb-1">
                                          {isAnswer && <span>📌 </span>}{f.name}
                                          <span className="text-gray-400 ml-2">颜色 {f.color}</span>
                                          {isAnswer && <span className="ml-2 text-xs text-purple-600 font-semibold">答题视图</span>}
                                        </p>
                                        <p className="text-xs text-gray-500">
                                          范围: X[{b.min_x?.toFixed(1)}~{b.max_x?.toFixed(1)}] Y[{b.min_y?.toFixed(1)}~{b.max_y?.toFixed(1)}]
                                        </p>
                                        {/* 答题视图才显示统计 + 详细尺寸 */}
                                        {isAnswer && viewEnt && (
                                          <div className="mt-1 space-y-1">
                                            {/* 图元计数 */}
                                            <div className="grid grid-cols-4 gap-x-2 gap-y-0.5 text-xs text-gray-600">
                                              {Object.entries(viewEnt).map(([vk, vv]) => {
                                                if (!Array.isArray(vv) || vk === "dimensions") return null;
                                                return (
                                                  <span key={vk}>
                                                    <span className="text-gray-400">{vk}:</span> <strong>{(vv as any[]).length}</strong>
                                                  </span>
                                                );
                                              })}
                                            </div>
                                            {/* 答题视图的尺寸标注 */}
                                            {Array.isArray(viewEnt.dimensions) && viewEnt.dimensions.length > 0 && (
                                              <details>
                                                <summary className="text-xs text-green-700 cursor-pointer hover:underline font-medium mt-1">
                                                  尺寸标注（{viewEnt.dimensions.length} 个）
                                                </summary>
                                                <table className="w-full border mt-1">
                                                  <thead><tr className="bg-gray-50 border"><th className="p-0.5 text-left text-xs">类型</th><th className="p-0.5 text-left text-xs">文字</th><th className="p-0.5 text-left text-xs">测量值</th></tr></thead>
                                                  <tbody>{viewEnt.dimensions.map((d: any, di: number) => (
                                                    <tr key={di} className="border"><td className="p-0.5 text-xs">{d.type || "-"}</td><td className="p-0.5 font-mono text-xs">{d.text || "-"}</td><td className="p-0.5 font-mono text-xs">{d.measurement != null ? d.measurement : "-"}</td></tr>
                                                  ))}</tbody>
                                                </table>
                                              </details>
                                            )}
                                          </div>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              </details>
                            )}
                          </div>
                        ) : (
                          <>
                        {studentAnalysis.工程图概述 && (
                          <div className="p-2 bg-blue-50 rounded border border-blue-100 mb-2">
                            <p className="text-xs text-blue-500 mb-1 font-medium">工程图概述</p>
                            <p className="text-gray-700 whitespace-pre-wrap">{studentAnalysis.工程图概述}</p>
                          </div>
                        )}
                        {studentAnalysis.基本信息 && (
                          <table className="w-full border mb-1"><thead><tr className="bg-blue-50"><th colSpan={4} className="p-1 text-left text-blue-800">基本信息</th></tr></thead>
                            <tbody><tr className="border"><td className="p-1 text-gray-500 w-14">名称</td><td className="p-1">{studentAnalysis.基本信息.零件名称 || studentAnalysis.基本信息.装配体名称 || "-"}</td><td className="p-1 text-gray-500 w-14">材料</td><td className="p-1">{studentAnalysis.基本信息.材料 || "-"}</td></tr>
                            <tr className="border"><td className="p-1 text-gray-500">比例</td><td className="p-1">{studentAnalysis.基本信息.比例 || "-"}</td><td className="p-1 text-gray-500">类型</td><td className="p-1">{studentAnalysis.基本信息.组合体类型 || "-"}</td></tr></tbody></table>)}
                        {Array.isArray(studentAnalysis.几何特征) && studentAnalysis.几何特征.length > 0 && (
                          <table className="w-full border mb-1"><thead><tr className="bg-blue-50"><th colSpan={3} className="p-1 text-left text-blue-800">几何特征（{studentAnalysis.几何特征.length} 个）</th></tr></thead>
                            <thead><tr className="bg-gray-50 border"><th className="p-1">名称</th><th className="p-1">尺寸</th><th className="p-1">视图</th></tr></thead>
                            <tbody>{studentAnalysis.几何特征.map((f: any, i: number) => (<tr key={i} className="border"><td className="p-1">{f.名称}</td><td className="p-1 font-mono">{f.尺寸 || "-"}</td><td className="p-1 text-gray-600">{Array.isArray(f.所在视图) ? f.所在视图.join("、") : "-"}</td></tr>))}</tbody></table>)}
                        {Array.isArray(studentAnalysis.尺寸) && studentAnalysis.尺寸.length > 0 && (
                          <table className="w-full border mb-1"><thead><tr className="bg-green-50"><th colSpan={2} className="p-1 text-left text-green-800">尺寸（{studentAnalysis.尺寸.length} 个）</th></tr></thead>
                            <thead><tr className="bg-gray-50 border"><th className="p-1">类别</th><th className="p-1">数值</th></tr></thead>
                            <tbody>{studentAnalysis.尺寸.map((d: any, i: number) => (<tr key={i} className="border"><td className="p-1">{d.类别}</td><td className="p-1 font-mono">{d.数值}</td></tr>))}</tbody></table>)}
                        {Array.isArray(studentAnalysis.尺寸公差) && studentAnalysis.尺寸公差.length > 0 && (
                          <table className="w-full border mb-1"><thead><tr className="bg-green-50"><th colSpan={2} className="p-1 text-left text-green-800">尺寸公差（{studentAnalysis.尺寸公差.length} 项）</th></tr></thead>
                            <thead><tr className="bg-gray-50 border"><th className="p-1">公称尺寸</th><th className="p-1">公差</th></tr></thead>
                            <tbody>{studentAnalysis.尺寸公差.map((d: any, i: number) => (<tr key={i} className="border"><td className="p-1 font-mono">{d.公称尺寸}</td><td className="p-1 font-mono">{d.公差}</td></tr>))}</tbody></table>)}
                        {Array.isArray(studentAnalysis.几何公差) && studentAnalysis.几何公差.length > 0 && (
                          <table className="w-full border mb-1"><thead><tr className="bg-green-50"><th colSpan={3} className="p-1 text-left text-green-800">几何公差（{studentAnalysis.几何公差.length} 项）</th></tr></thead>
                            <thead><tr className="bg-gray-50 border"><th className="p-1">类别</th><th className="p-1">数值</th><th className="p-1">基准</th></tr></thead>
                            <tbody>{studentAnalysis.几何公差.map((g: any, i: number) => (<tr key={i} className="border"><td className="p-1">{g.类别}</td><td className="p-1 font-mono">{g.数值}</td><td className="p-1 font-mono">{g.基准 || "-"}</td></tr>))}</tbody></table>)}
                        {Array.isArray(studentAnalysis.表面粗糙度) && studentAnalysis.表面粗糙度.length > 0 && (
                          <table className="w-full border mb-1"><thead><tr className="bg-green-50"><th colSpan={2} className="p-1 text-left text-green-800">表面粗糙度（{studentAnalysis.表面粗糙度.length} 处）</th></tr></thead>
                            <thead><tr className="bg-gray-50 border"><th className="p-1">类别</th><th className="p-1">数值</th></tr></thead>
                            <tbody>{studentAnalysis.表面粗糙度.map((r: any, i: number) => (<tr key={i} className="border"><td className="p-1">{r.类别 || "Ra"}</td><td className="p-1 font-mono">{r.数值}</td></tr>))}</tbody></table>)}
                        {studentAnalysis.技术要求 && (
                          <div className="p-2 bg-yellow-50 rounded border border-yellow-100"><p className="text-yellow-600 mb-1 font-medium">技术要求</p><p className="text-gray-700 whitespace-pre-wrap">{studentAnalysis.技术要求}</p></div>
                        )}
                        </>
                      )}
                      </div>
                    </details>
                  </div>
                )}

{isGraded ? (
                  <div className="space-y-4 text-sm">
                    {/* 评级下拉 */}
                    <div className="flex items-center gap-3 bg-gray-50 rounded p-3">
                      <span className="text-gray-600">评级：</span>
                      <select
                        value={reviewGrade}
                        onChange={(e) => setReviewGrade(e.target.value)}
                        disabled={!isGradeOwner}
                        className="border rounded px-2 py-1 text-sm disabled:bg-gray-100"
                      >
                        {GRADE_OPTIONS.map((g) => (
                          <option key={g} value={g}>{g}</option>
                        ))}
                      </select>
                      <span className="text-gray-400 text-xs ml-2">
                        阶段1: {row[COL.阶段1相似度] || "-"}% × 阶段2: {row[COL.阶段2评分] || "-"}% → √(P1×P2) = 总分: {row[COL.总分] || "-"}%
                      </span>
                    </div>

                    {/* 阶段一评价 */}
                    <div>
                      <p className="font-medium text-gray-700">阶段一 · 相似度评价</p>
                      <p className="text-gray-600 mt-1">{row[COL.相似度评价] || "-"}</p>
                    </div>

                    {/* 重叠率 */}
                    {(() => {
                      const ov = studentAnalysis?.view_overlap_ratios;
                      if (!ov) return null;
                      const af: string[] = curQuestion?.required_frames || [];
                      const views = af.length > 0 ? af : Object.keys(ov);
                      return (
                        <div className="bg-purple-50 rounded p-3">
                          <p className="font-medium text-purple-700 text-sm mb-2">重叠率 <span class="font-normal text-gray-400">(原始−clean)÷clean</span></p>
                          <p className="text-xs text-gray-400 mb-1">0%=无重叠，越大重叠越多</p>
                          <div className="space-y-1 text-xs">
                            {views.map((vname: string) => {
                              const vdata = ov[vname];
                              if (!vdata) return null;
                              return (
                                <div key={vname} className="border border-purple-200 rounded p-1.5 bg-white">
                                  <p className="font-medium text-purple-800 mb-0.5">📌 {vname}</p>
                                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-gray-600">
                                    {["solid", "dashed", "centerline", "total"].map((cat: string) => {
                                      const d = vdata[cat];
                                      if (!d || d.overlap_ratio === undefined) return null;
                                      return (
                                        <span key={cat}>
                                          {cat === "total" ? "合计" : cat}: {d.overlap_ratio === 0 ? "无重叠" : `${(d.overlap_ratio * 100).toFixed(1)}%`}
                                          <span className="text-gray-300 ml-0.5">({d.raw_len}→{d.clean_len})</span>
                                        </span>
                                      );
                                    })}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      );
                    })()}

                    {/* 重合度 */}
                    {studentGradeResult?.view_coincidence && Object.keys(studentGradeResult.view_coincidence).length > 0 && (
                      <div className="bg-purple-50 rounded p-3">
                        <p className="font-medium text-purple-700 text-sm mb-2">重合度 <span class="font-normal text-gray-400">∩÷参考图</span></p>
                          <p className="text-xs text-gray-400 mb-1">100%=完全重合，越小表示漏画越多</p>
                        <div className="space-y-1 text-xs">
                          {Object.entries(studentGradeResult.view_coincidence).map(([vname, vdata]: [string, any]) => {
                            const t = vdata.total;
                            return (
                              <div key={vname} className="border border-purple-200 rounded p-1.5 bg-white">
                                <p className="font-medium text-purple-800 mb-0.5">📌 {vname}</p>
                                <div className="flex flex-wrap gap-x-3 text-gray-600">
                                  <span>重合: <strong>{(t.coincidence*100).toFixed(1)}%</strong></span>
                                  <span>非重合: <strong>{(t.extra_ratio*100).toFixed(1)}%</strong></span>
                                  <span className="text-gray-400">偏移: ({vdata.align_offset.dx.toFixed(1)}, {vdata.align_offset.dy.toFixed(1)})</span>
                                </div>
                                <div className="flex flex-wrap gap-x-3 text-gray-400 mt-0.5">
                                  {["solid","dashed","centerline"].map((cat) => {
                                    const c = vdata[cat];
                                    if (!c) return null;
                                    return (
                                      <span key={cat}>
                                        {cat}: 重合={((c.coincidence||0)*100).toFixed(1)}% 非重合={((c.extra_ratio||0)*100).toFixed(1)}%
                                      </span>
                                    );
                                  })}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* 阶段二评价 */}
                    <div>
                      <p className="font-medium text-gray-700">阶段二 · 量化评分</p>
                      <p className="text-gray-600 mt-1">{row[COL.阶段2评语] || "-"}</p>
                      <div className="grid grid-cols-2 gap-2 mt-1">
                        {DIMS.map((dim) => (
                          <div key={dim} className="bg-gray-50 rounded p-2">
                            <span className="text-gray-500 text-xs">{dim}</span>
                            <p className="text-gray-700 text-xs mt-0.5">{row[dim] || "-"}</p>
                          </div>
                        ))}
                      </div>
                      <p className="text-gray-600 mt-2">
                        <span className="font-medium text-gray-700">阶段二总评：</span>{row[COL.总评] || "-"}
                      </p>
                    </div>

                    {/* 教师评语 */}
                    <div>
                      <p className="font-medium text-gray-700 mb-1">教师评语</p>
                      <textarea
                        value={reviewComment}
                        onChange={(e) => setReviewComment(e.target.value)}
                        disabled={!isGradeOwner}
                        rows={4}
                        className="w-full border rounded px-3 py-2 text-sm disabled:bg-gray-100"
                        placeholder={isGradeOwner ? "输入教师评语…" : "只读"}
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
                    <FileButton
                      accept=".csv"
                      onChange={(file) => setRosterFile(file)}
                      label="选择CSV"
                      fileName={rosterFile?.name}
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
                                    <th className="p-1"></th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {classStudents.map((s: any, i: number) => (
                                    <tr key={i} className="border-b">
                                      <td className="p-1">{s.姓名}</td>
                                      <td className="p-1">{s.学号}</td>
                                      <td className="p-1">
                                        <button
                                          onClick={async () => {
                                            if (!confirm(`确定将 ${s.姓名}(${s.学号}) 的密码重置为 cad123 吗？`)) return;
                                            try {
                                              await resetStudentPassword(selectedClass!, s.学号);
                                              alert("密码已重置为 cad123");
                                            } catch (e: any) { alert("重置失败: " + e.message); }
                                          }}
                                          className="text-xs text-orange-600 hover:underline"
                                        >重置密码</button>
                                      </td>
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

      {/* 编辑模板弹窗 */}
      {templateModalQid && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-2xl mx-4 max-h-[90vh] overflow-auto">
            <h2 className="text-lg font-semibold mb-2">编辑识读模板 — 题{templateModalQid}</h2>
            <p className="text-xs text-gray-400 mb-4">修改仅影响此题目的分析和评分，不影响全局默认模板</p>
            <textarea
              value={templateModalContent}
              onChange={(e) => setTemplateModalContent(e.target.value)}
              rows={22}
              className="w-full border rounded px-3 py-2 text-sm font-mono"
            />
            <div className="flex justify-end gap-3 mt-4">
              <button onClick={() => setTemplateModalQid(null)}
                className="px-4 py-2 border rounded hover:bg-gray-50">取消</button>
              <button onClick={handleSaveTemplate} disabled={templateModalSaving}
                className="bg-purple-600 text-white px-4 py-2 rounded hover:bg-purple-700 disabled:opacity-50">
                {templateModalSaving ? "保存中…" : "保存模板"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ========== 使用帮助弹窗 ========== */}
      {showHelp && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-8 bg-black/40" onClick={() => setShowHelp(false)}>
          <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="sticky top-0 bg-white border-b px-6 py-4 flex justify-between items-center">
              <h2 className="text-xl font-bold">工程图批阅系统 — 使用帮助</h2>
              <button onClick={() => setShowHelp(false)} className="text-gray-400 hover:text-gray-600 text-2xl leading-none">&times;</button>
            </div>
            <div className="px-6 py-4 space-y-6 text-sm leading-relaxed">

              <section>
                <h3 className="font-bold text-base mb-2">一、系统概述</h3>
                <p>工程图批阅系统用于教师布置工程图作业、学生提交图纸、LLM 大模型自动评分。支持 PDF、图片、DXF 三种提交类型。</p>
              </section>

              <section>
                <h3 className="font-bold text-base mb-2">二、初始密码</h3>
                <table className="w-full border text-left text-sm">
                  <thead><tr className="bg-gray-50"><th className="p-2 border">角色</th><th className="p-2 border">初始密码</th><th className="p-2 border">说明</th></tr></thead>
                  <tbody>
                    <tr><td className="p-2 border">教师</td><td className="p-2 border font-mono">MechCAD</td><td className="p-2 border text-gray-600">首次登录后可在"设置 → 个人信息 → 修改密码"中修改</td></tr>
                    <tr><td className="p-2 border">学生</td><td className="p-2 border font-mono">cad123</td><td className="p-2 border text-gray-600">首次登录强制修改密码（至少6位）；教师可在班级名单管理中重置学生密码</td></tr>
                  </tbody>
                </table>
              </section>

              <section>
                <h3 className="font-bold text-base mb-2">三、题目录入</h3>
                <ol className="list-decimal pl-5 space-y-1">
                  <li>在"题目管理"区域点击<strong>新建题目</strong></li>
                  <li>输入<strong>标题</strong>（题号自动生成，格式 年月日-序号，如 260727-001）</li>
                  <li>选择<strong>提交类型</strong>：PDF 文件 / 图片文件 / DXF 文件</li>
                  <li>设置<strong>权限</strong>：仅限本人 / 其他教师可见</li>
                  <li>填写<strong>适用班别</strong>（逗号分隔，如 机械1班,机械2班）</li>
                  <li>填写<strong>题目内容</strong>、<strong>阶段一评分标准</strong>（图形相似度）、<strong>阶段二评分标准</strong>（量化标注）</li>
                  <li>选择<strong>识读模板</strong>（零件图/装配图/平面图/组合体三视图/DXF）</li>
                  <li>可上传<strong>题目附图</strong>（题目示意图）和<strong>参考工程图</strong>（标准答案，PDF 或 DXF）</li>
                  <li>设置<strong>提交截止时间</strong>（过期后学生无法提交）</li>
                  <li>点击<strong>提交</strong></li>
                </ol>
                <p className="mt-2 text-gray-600">提示：上传参考工程图后系统会自动触发 LLM 分析（PDF）/ DXF 数据提取；DXF 题目无需 LLM 识读，直接提取结构化数据。</p>
              </section>

              <section>
                <h3 className="font-bold text-base mb-2">四、参考图分析</h3>
                <p>创建题目并上传参考工程图后，系统自动分析。在题目列表的"参考图分析"列可查看状态：</p>
                <ul className="list-disc pl-5 space-y-1 mt-1">
                  <li><span className="text-yellow-600 font-medium">分析中…</span> — 正在处理，请等待</li>
                  <li><span className="text-blue-600 font-medium">查看</span> — 分析完成，可查看结构化数据</li>
                  <li><span className="text-gray-500 font-medium">分析</span> — 尚未分析，点击启动</li>
                  <li><span className="text-red-500 font-medium">失败</span> — 分析出错，可点击"重试"</li>
                </ul>
                <p className="mt-2 text-gray-600">提示：DXF 参考图无需 LLM，直接提取实体/尺寸/图层数据并渲染预览图（含尺寸版本 + 无尺寸版本）。</p>
              </section>

              <section>
                <h3 className="font-bold text-base mb-2">五、学生录入</h3>
                <ol className="list-decimal pl-5 space-y-1">
                  <li>在"题目管理"右侧点击<strong>班级管理</strong></li>
                  <li>点击<strong>新增班级</strong>，输入班级名称（如 机械1班）</li>
                  <li>点击该班级的<strong>查看名单</strong></li>
                  <li>下载<strong>学生名单模板</strong>（CSV 格式，表头：姓名,学号）</li>
                  <li>按模板填写学生信息，上传 CSV 导入</li>
                  <li>也可手动逐条添加学生</li>
                </ol>
                <p className="mt-2 text-gray-600">提示：学生信息保存在 data/StudentInfo/班级名.csv，密码哈希存在 data/StudentAuth/班级名.json。</p>
              </section>

              <section>
                <h3 className="font-bold text-base mb-2">六、学生初始密码与登录</h3>
                <ul className="list-disc pl-5 space-y-1">
                  <li>初始密码：<code className="bg-gray-100 px-1 rounded">cad123</code></li>
                  <li>首次登录强制修改密码（至少 6 位）</li>
                  <li>学生登录需填写：姓名 + 学号 + 密码</li>
                  <li>教师可在"班级管理 → 查看名单"中<strong>重置学生密码</strong>为 cad123</li>
                  <li>学生端登录后可在设置（⚙）中自行修改密码</li>
                </ul>
              </section>

              <section>
                <h3 className="font-bold text-base mb-2">七、成绩管理</h3>
                <ol className="list-decimal pl-5 space-y-1">
                  <li>在题目列表点击<strong>成绩</strong>进入成绩表</li>
                  <li><strong>批量评分</strong>：选择学生 → 批量评分 → 系统依次调用 LLM 打分</li>
                  <li><strong>单个评分</strong>：点击学生行右侧的评分按钮</li>
                  <li><strong>查看详情</strong>：点击已评分学生的分数 → 弹出评分明细（Phase1 + Phase2）</li>
                  <li><strong>编辑分数</strong>：双击成绩单元格直接修改（仅题目创建者可编辑）</li>
                  <li><strong>补充提交</strong>：教师可为缺交学生代为上传文件</li>
                  <li>评分公式：总分 = √(Phase1 × Phase2)，按阈值映射为 A+ ~ F 等级</li>
                </ol>
              </section>

              <section>
                <h3 className="font-bold text-base mb-2">八、设置页面</h3>
                <p>点击顶部"设置"进入系统配置：</p>
                <ul className="list-disc pl-5 space-y-1 mt-1">
                  <li><strong>个人信息</strong> — 查看姓名/用户名，修改登录密码</li>
                  <li><strong>模型配置</strong> — 添加/切换 LLM 模型（支持多模型并发）</li>
                  <li><strong>LLM 参数</strong> — 温度、最大 token、超时等</li>
                  <li><strong>图像处理</strong> — 分析用图分辨率、缩略图质量</li>
                  <li><strong>评分等级</strong> — A+ ~ D 的分数阈值</li>
                  <li><strong>分析模板</strong> — LLM 识读提示词和评分引导语</li>
                  <li><strong>评分模板</strong> — 新建题目时预填的阶段一/二默认标准</li>
                  <li><strong>系统管理</strong> — 模型测试、队列状态、清空队列、服务重启</li>
                </ul>
              </section>

              <section>
                <h3 className="font-bold text-base mb-2">九、常见问题</h3>
                <dl className="space-y-3">
                  <div>
                    <dt className="font-medium">Q: 参考图分析一直显示"分析中"？</dt>
                    <dd className="text-gray-600">检查 LLM 模型是否正常连接（设置 → 系统管理 → 模型测试）。PDF 文件损坏或格式不支持也会导致分析失败，错误信息会显示在页面上。</dd>
                  </div>
                  <div>
                    <dt className="font-medium">Q: DXF 预览图看不到标注？</dt>
                    <dd className="text-gray-600">系统会生成含尺寸和无尺寸两个版本。蓝色标注在含尺寸版本中显示。如仍看不到，检查 DXF 文件本身是否有标注实体。</dd>
                  </div>
                  <div>
                    <dt className="font-medium">Q: 学生看不到题目？</dt>
                    <dd className="text-gray-600">确认题目"适用班别"包含该学生所属班级，且未过截止时间。</dd>
                  </div>
                  <div>
                    <dt className="font-medium">Q: 其他教师看不到我的题目？</dt>
                    <dd className="text-gray-600">编辑题目，将"权限管理"设为"其他教师可见"。否则仅本人可见。</dd>
                  </div>
                  <div>
                    <dt className="font-medium">Q: 教师默认密码是什么？如何修改？</dt>
                    <dd className="text-gray-600">默认密码 MechCAD。登录后在"设置 → 个人信息 → 修改密码"中修改。</dd>
                  </div>
                  <div>
                    <dt className="font-medium">Q: 学生忘记密码怎么办？</dt>
                    <dd className="text-gray-600">教师可在"班级管理 → 查看名单"中重置学生密码为 cad123。</dd>
                  </div>
                </dl>
              </section>

            </div>
          </div>
        </div>
      )}

    </div>
    </>
  );
}
