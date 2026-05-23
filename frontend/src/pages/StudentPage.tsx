import { useState, useEffect } from "react";
import { listQuestions, getQuestionDetail, submitHomework, getStudentResult, getQuestionFileUrl, getTeacherPreviewUrl, getStudentPreviewUrl } from "../api";

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
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<GradeResult | null>(null);
  const [refFilenames, setRefFilenames] = useState<string[]>([]);
  const [studentFilename, setStudentFilename] = useState("");
  const [submitTs, setSubmitTs] = useState(0);
  const [mode, setMode] = useState<"test" | "submit">("test");
  const [error, setError] = useState("");

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

  const handleSubmit = async () => {
    if (!selectedQid || !file) {
      setError("请选择题目并上传文件");
      return;
    }
    if (mode === "submit" && (!name || !studentId)) {
      setError("提交作业需填写姓名和学号");
      return;
    }

    // overwrite check
    if (mode === "submit" && result?.成绩) {
      if (!confirm(`学号 ${studentId} 已有成绩 ${result.成绩}，确定覆盖吗？`)) {
        return;
      }
    }

    setLoading(true);
    setError("");
    try {
      const submitName = mode === "test" ? "测试" : name;
      const submitId = mode === "test" ? `test_${Date.now()}` : studentId;
      const data = await submitHomework(selectedQid, submitName, submitId, file, mode);
      if (data.result) {
        setResult({
          姓名: name,
          学号: studentId,
          成绩: data.grade,
          ...data.result,
        });
        setRefFilenames(data.ref_filenames || []);
        setStudentFilename(data.student_filename || "");
        setSubmitTs(Date.now());
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
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
                onChange={(e) => setFile(e.target.files?.[0] || null)}
                className="w-full"
              />
            </div>
            <button
              onClick={handleSubmit}
              disabled={loading}
              className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "批阅中..." : "提交批阅"}
            </button>
            {error && <p className="text-red-500 mt-2">{error}</p>}
          </div>
        )}

        {/* Grade Result */}
        {result && (
          <div className="bg-white rounded-lg shadow p-4 border-l-4 border-blue-500 space-y-4">
            <h2 className="text-lg font-semibold">批阅结果</h2>

            {/* Image Comparison */}
            {(refFilenames.length > 0 || studentFilename) && (
              <div className="border rounded p-3 bg-gray-50">
                <h3 className="text-sm font-medium text-gray-600 mb-2">图形对比</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {refFilenames.length > 0 && (
                    <div>
                      <p className="text-xs text-gray-500 mb-1">参考答案</p>
                      {refFilenames.map((fn,i) => (
                        <img key={i} src={getTeacherPreviewUrl(selectedQid!, fn, submitTs)} alt="参考图" className="w-full rounded border" />
                      ))}
                    </div>
                  )}
                  {studentFilename && (
                    <div>
                      <p className="text-xs text-gray-500 mb-1">学生作业</p>
                      <img src={getStudentPreviewUrl(selectedQid!, studentFilename, submitTs)} alt="学生工程图" className="w-full rounded border" />
                    </div>
                  )}
                </div>
              </div>
            )}

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
