import { useState, useEffect } from "react";
import { listQuestions, getQuestionDetail, analyzeSubmission, gradeSubmission, getStudentResult, getQuestionFileUrl, getTeacherPreviewUrl, getStudentPreviewUrl } from "../api";

interface Question {
  id: string;
  title: string;
  files?: { description: string; requirements: string; images: string[]; reference_pdf: string | null };
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

export default function StudentPage() {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [selectedQid, setSelectedQid] = useState<string | null>(null);
  const [question, setQuestion] = useState<Question | null>(null);
  const [name, setName] = useState("");
  const [studentId, setStudentId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<GradeResult | null>(null);
  const [studentFilename, setStudentFilename] = useState("");
  const [submitTs, setSubmitTs] = useState(0);
  const [mode, setMode] = useState<"test" | "submit">("test");
  const [error, setError] = useState("");

  // 新版分步流程状态
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisData, setAnalysisData] = useState<any>(null);   // 图面分析结果
  const [grading, setGrading] = useState(false);                  // 评分中
  const [submitKey, setSubmitKey] = useState<{ name: string; id: string } | null>(null);  // 分析时生成的提交标识，评分时复用

  useEffect(() => {
    listQuestions().then(setQuestions).catch(console.error);
  }, []);

  const selectQuestion = async (qid: string) => {
    setSelectedQid(qid);
    setResult(null);
    setError("");
    try {
      const q = await getQuestionDetail(qid);
      setQuestion(q);
    } catch {
      setError("加载题目失败");
    }
  };

  const checkExisting = async () => {
    if (!selectedQid || !studentId) return;
    try {
      const r = await getStudentResult(selectedQid, studentId);
      setResult(r);
    } catch {
      setResult(null);
    }
  };

  // 输入校验（正式提交模式）
  const MAX_NAME_LEN = 50;
  const MAX_ID_LEN = 30;
  const MAX_FILE_MB = 20;

  const validate = (): string | null => {
    if (!selectedQid) return "请选择题目";
    if (!file) return "请上传工程图文件";
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      return `文件过大（上限 ${MAX_FILE_MB}MB），请压缩后重新上传`;
    }
    if (mode === "submit") {
      if (!name.trim()) return "请填写姓名";
      if (name.length > MAX_NAME_LEN) return `姓名不能超过${MAX_NAME_LEN}个字符`;
      if (!studentId.trim()) return "请填写学号";
      if (studentId.length > MAX_ID_LEN) return `学号不能超过${MAX_ID_LEN}个字符`;
    }
    return null;
  };

  // 第一步：上传 + 图面分析
  const handleAnalyze = async () => {
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }

    setAnalyzing(true);
    setError("");
    setAnalysisData(null);
    setResult(null);
    try {
      const submitName = mode === "test" ? "测试" : name;
      const submitId = mode === "test" ? `test_${Date.now()}` : studentId;
      setSubmitKey({ name: submitName, id: submitId });  // 保存标识，评分时复用
      const data = await analyzeSubmission(selectedQid!, submitName, submitId, file!, mode);
      setAnalysisData(data.analysis);
      setStudentFilename(data.student_filename || "");
      setSubmitTs(Date.now());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAnalyzing(false);
    }
  };

  // 第二步：提交评分
  const handleGrade = async () => {
    if (!analysisData) {
      setError("请先完成图面分析");
      return;
    }
    if (!submitKey) {
      setError("分析数据异常，请重新上传分析");
      return;
    }
    setGrading(true);
    setError("");
    try {
      const data = await gradeSubmission(selectedQid!, submitKey.name, submitKey.id, mode);
      if (data.result) {
        setResult({
          姓名: name,
          学号: studentId,
          成绩: data.grade,
          ...data.result,
        });
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGrading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-blue-700 text-white p-4 shadow">
        <h1 className="text-xl font-bold">工程图批阅系统 - 学生端</h1>
      </header>
      <main className="max-w-3xl mx-auto p-4 space-y-6">
        {/* Question Selection */}
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-lg font-semibold mb-3">选择题目</h2>
          <div className="flex flex-wrap gap-2">
            {questions.map((q) => (
              <button
                key={q.id}
                onClick={() => selectQuestion(q.id)}
                className={`px-4 py-2 rounded ${
                  selectedQid === q.id ? "bg-blue-600 text-white" : "bg-gray-100 hover:bg-gray-200"
                }`}
              >
                {q.id}: {q.title}
              </button>
            ))}
            {questions.length === 0 && <p className="text-gray-400">暂无题目</p>}
          </div>
        </div>

        {/* Question Detail */}
        {question && (
          <div className="bg-white rounded-lg shadow p-4">
            <h2 className="text-lg font-semibold mb-3">题目详情：{question.title}</h2>
            <div className="prose max-w-none mb-4">
              <p className="whitespace-pre-wrap text-gray-800">{question.files?.description}</p>
            </div>
            {question.files?.images.map((img) => (
              <img
                key={img}
                src={getQuestionFileUrl(question.id, img)}
                alt="题目附图"
                className="max-w-full rounded border my-2"
              />
            ))}
            {question.files?.reference_pdf && (
              <p className="text-sm text-gray-500 mt-2">
                参考工程图已上传（提交后将与参考图对比批阅）
              </p>
            )}
          </div>
        )}

        {/* Submission Form */}
        {selectedQid && (
          <div className="bg-white rounded-lg shadow p-4">
            <h2 className="text-lg font-semibold mb-3">提交作业</h2>
            {/* 模式选择——放在最前面 */}
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">模式</label>
              <div className="flex gap-4">
                <label className="flex items-center gap-1 cursor-pointer">
                  <input type="radio" name="mode" checked={mode==="test"} onChange={()=>{setMode("test"); setResult(null);}}/>
                  <span className="text-sm">测试</span>
                </label>
                <label className="flex items-center gap-1 cursor-pointer">
                  <input type="radio" name="mode" checked={mode==="submit"} onChange={()=>{setMode("submit"); setResult(null);}}/>
                  <span className="text-sm">提交作业</span>
                </label>
              </div>
              <p className="text-xs text-gray-400 mt-1">
                {mode==="test" ? "无需姓名学号，不保存成绩" : "需填写姓名学号，保存成绩"}
              </p>
            </div>
            {/* 姓名学号——测试模式禁用 */}
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">姓名</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onBlur={mode==="submit" ? checkExisting : undefined}
                  disabled={mode==="test"}
                  className="w-full border rounded px-3 py-2 disabled:bg-gray-100 disabled:text-gray-400"
                  placeholder={mode==="test" ? "测试模式无需填写" : ""}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">学号</label>
                <input
                  type="text"
                  value={studentId}
                  onChange={(e) => setStudentId(e.target.value)}
                  onBlur={mode==="submit" ? checkExisting : undefined}
                  disabled={mode==="test"}
                  className="w-full border rounded px-3 py-2 disabled:bg-gray-100 disabled:text-gray-400"
                  placeholder={mode==="test" ? "测试模式无需填写" : ""}
                />
              </div>
            </div>
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">上传工程图 (PDF/图片)</label>
              <input
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.gif,.webp"
                onChange={(e) => { setFile(e.target.files?.[0] || null); setAnalysisData(null); setSubmitKey(null); setResult(null); }}
                className="w-full"
              />
            </div>

            {/* 第一步：图面分析 */}
            {!analysisData ? (
              <button
                onClick={handleAnalyze}
                disabled={analyzing}
                className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {analyzing ? "模型分析中…" : "上传并分析"}
              </button>
            ) : (
              /* 第二步：提交评分 */
              <button
                onClick={handleGrade}
                disabled={grading}
                className="bg-green-600 text-white px-6 py-2 rounded hover:bg-green-700 disabled:opacity-50"
              >
                {grading ? "评分中…" : "提交评分"}
              </button>
            )}
            {error && <p className="text-red-500 mt-2">{error}</p>}

            {/* 图面分析结果预览 */}
            {analysisData && (
              <div className="mt-4 border rounded p-3 bg-gray-50 text-xs">
                <h3 className="text-sm font-semibold mb-3 text-green-700">图面分析完成</h3>

                {/* 参考图 vs 学生图对比 */}
                {(question?.files?.reference_pdf || studentFilename) && (
                  <div className="grid grid-cols-2 gap-3 mb-3">
                    {question?.files?.reference_pdf && (
                      <div>
                        <p className="text-xs text-gray-500 mb-1">参考工程图</p>
                        <img src={getTeacherPreviewUrl(selectedQid!, question.files.reference_pdf, submitTs)} alt="参考图" className="w-full rounded border" />
                      </div>
                    )}
                    {studentFilename && (
                      <div>
                        <p className="text-xs text-gray-500 mb-1">你的作业</p>
                        <img src={getStudentPreviewUrl(selectedQid!, studentFilename, submitTs)} alt="学生工程图" className="w-full rounded border" />
                      </div>
                    )}
                  </div>
                )}

                {/* ===== 结构分析 ===== */}
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

                {/* ===== 量化分析 ===== */}
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

        {/* Grade Result */}
        {result && (
          <div className="bg-white rounded-lg shadow p-4 border-l-4 border-blue-500 space-y-4">
            <h2 className="text-lg font-semibold">批阅结果</h2>

            {/* Final Grade */}
            <div className="flex items-center gap-4 p-3 bg-gray-50 rounded">
              <div>
                <span className="text-sm text-gray-600">最终等级</span>
                <div className={`text-3xl font-bold ${
                  result.成绩 === "F" ? "text-red-600" :
                  result.成绩 === "A+" ? "text-green-600" :
                  result.成绩?.startsWith("A") ? "text-green-600" :
                  result.成绩?.startsWith("B") ? "text-blue-600" :
                  result.成绩?.startsWith("C") ? "text-yellow-600" :
                  "text-orange-600"
                }`}>{result.成绩}</div>
              </div>
              <div className="flex-1 text-center text-sm text-gray-500">
                阶段1 相似度<br/><span className="text-lg font-bold text-gray-800">{(result as any).phase1_similarity}%</span>
                <span className="mx-2">×</span>
                阶段2 评分<br/><span className="text-lg font-bold text-gray-800">{(result as any).phase2_criteria}%</span>
                <span className="mx-2">=</span>
                总分<br/><span className="text-lg font-bold text-blue-600">{(result as any).total_score}%</span>
              </div>
            </div>

            {/* Phase 1 Comment */}
            <div className="bg-yellow-50 rounded p-3">
              <h4 className="text-sm font-medium text-yellow-700">阶段1：相似度评价</h4>
              <p className="text-sm mt-1 whitespace-pre-wrap">{(result as any).phase1_comment || "-"}</p>
            </div>

            {/* Phase 2 Details */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {["图样表达", "尺寸标注", "尺寸公差", "表面质量", "形位公差"].map((key) => (
                <div key={key} className="bg-gray-50 rounded p-3">
                  <h4 className="text-sm font-medium text-gray-500">{key}</h4>
                  <p className="text-sm mt-1 whitespace-pre-wrap">{(result as any)[key] || "-"}</p>
                </div>
              ))}
            </div>
            <div className="bg-green-50 rounded p-3">
              <h4 className="text-sm font-medium text-green-700">阶段2：综合评价</h4>
              <p className="text-sm mt-1 whitespace-pre-wrap">{(result as any).phase2_comment || "-"}</p>
            </div>

            {/* Overall Comment */}
            <div className="bg-blue-50 rounded p-3">
              <h4 className="text-sm font-medium text-blue-700">总评</h4>
              <p className="text-sm mt-1 whitespace-pre-wrap">{result.总评}</p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
