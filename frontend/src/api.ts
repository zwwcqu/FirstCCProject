const BASE = "";

export async function api(path: string, options: RequestInit = {}): Promise<any> {
  const headers: Record<string, string> = {};
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string> || {}) },
    credentials: "include",
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  const ct = resp.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return resp.json();
  }
  return resp;
}

// ------- Student -------
export function listQuestions() {
  return api("/api/student/questions");
}

export function getQuestionDetail(qid: string) {
  return api(`/api/student/questions/${qid}`);
}

export function getStudentResult(qid: string, studentId: string) {
  return api(`/api/student/result/${qid}/${studentId}`);
}

// 身份校验
export function checkRoster(name: string, studentId: string) {
  return api("/api/student/check", {
    method: "POST",
    body: JSON.stringify({ name, student_id: studentId }),
  });
}

// 学生个人提交历史
export function getStudentSubmissions(name: string, studentId: string) {
  return api(`/api/student/submissions?name=${encodeURIComponent(name)}&student_id=${encodeURIComponent(studentId)}`);
}

// 异步提交状态轮询
export function getSubmitStatus(qid: string, name: string, studentId: string) {
  return api(`/api/student/status/${qid}?name=${encodeURIComponent(name)}&student_id=${encodeURIComponent(studentId)}`);
}

// 获取学生在该题的完整提交记录（文件、分析状态、成绩）
export function getSubmissionRecord(qid: string, name: string, studentId: string) {
  return api(`/api/student/submission-record/${qid}?name=${encodeURIComponent(name)}&student_id=${encodeURIComponent(studentId)}`);
}

// 获取分析结果（学生端，analyze 完成后的轮询目标）
export function getStudentAnalysisResult(qid: string, name: string, studentId: string) {
  return api(`/api/student/analysis/${qid}?name=${encodeURIComponent(name)}&student_id=${encodeURIComponent(studentId)}`);
}

// 新版：分步 upload → analyze → grade

export function uploadSubmission(qid: string, name: string, studentId: string, file: File, mode: "test" | "submit" = "submit") {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("student_id", studentId);
  fd.append("file", file);
  fd.append("mode", mode);
  return api(`/api/student/upload/${qid}`, { method: "POST", body: fd });
}

export function startAnalysis(qid: string, name: string, studentId: string, mode: "test" | "submit" = "submit") {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("student_id", studentId);
  fd.append("mode", mode);
  return api(`/api/student/analyze/${qid}/start`, { method: "POST", body: fd });
}

export function analyzeSubmission(qid: string, name: string, studentId: string, file: File, mode: "test" | "submit" = "submit") {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("student_id", studentId);
  fd.append("file", file);
  fd.append("mode", mode);
  return api(`/api/student/analyze/${qid}`, { method: "POST", body: fd });
}

export function gradeSubmission(qid: string, name: string, studentId: string, mode: "test" | "submit" = "submit") {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("student_id", studentId);
  fd.append("mode", mode);
  return api(`/api/student/grade/${qid}`, { method: "POST", body: fd });
}

// ------- Teacher -------
export async function teacherLogin(password: string) {
  const fd = new FormData();
  fd.append("password", password);
  return api("/api/teacher/login", { method: "POST", body: fd });
}

export function teacherLogout() {
  return api("/api/teacher/logout", { method: "POST" });
}

export function checkLogin() {
  return api("/api/teacher/check");
}

export function getTeacherQuestions() {
  return api("/api/teacher/questions");
}

export function createQuestion(data: FormData) {
  return api("/api/teacher/questions", { method: "POST", body: data });
}

export function updateQuestion(qid: string, data: FormData) {
  return api(`/api/teacher/questions/${qid}`, { method: "PUT", body: data });
}

export function deleteQuestion(qid: string) {
  return api(`/api/teacher/questions/${qid}`, { method: "DELETE" });
}

export function getSettings() {
  return api("/api/teacher/settings");
}

export function updateSettings(data: Record<string, string>) {
  return api("/api/teacher/settings", { method: "PUT", body: JSON.stringify(data) });
}

export function getGrades(qid: string) {
  return api(`/api/teacher/grades/${qid}`);
}

export function batchGrade(qid: string, studentIds: string[]) {
  return api(`/api/teacher/grades/${qid}/batch-grade`, {
    method: "POST",
    body: JSON.stringify({ student_ids: studentIds }),
  });
}

export function batchClearGrades(qid: string, studentIds: string[]) {
  return api(`/api/teacher/grades/${qid}/batch-clear`, {
    method: "POST",
    body: JSON.stringify({ student_ids: studentIds }),
  });
}

export function editGrade(qid: string, studentId: string, fields: Record<string, string>) {
  return api(`/api/teacher/grades/${qid}/${studentId}`, {
    method: "PUT",
    body: JSON.stringify(fields),
  });
}

export function supplementSubmission(qid: string, name: string, studentId: string, file: File) {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("student_id", studentId);
  fd.append("file", file);
  return api(`/api/teacher/grades/${qid}/supplement-submission`, { method: "POST", body: fd });
}

export function refreshGrades(qid: string) {
  return api(`/api/teacher/grades/${qid}/refresh`, { method: "POST" });
}

export function getTeacherStudentPreviewUrl(qid: string, studentId: string): string {
  return `${BASE}/api/teacher/student-preview/${qid}/${studentId}`;
}

// --- Roster (全局 StudentInfo) ---
export function getClasses() {
  return api("/api/teacher/roster/classes");
}

export function getClassStudents(className: string) {
  return api(`/api/teacher/roster/classes/${encodeURIComponent(className)}`);
}

export function createClass(className: string, file: File) {
  const fd = new FormData();
  fd.append("class_name", className);
  fd.append("file", file);
  return api("/api/teacher/roster/classes", { method: "POST", body: fd });
}

export function deleteClass(className: string) {
  return api(`/api/teacher/roster/classes/${encodeURIComponent(className)}`, { method: "DELETE" });
}

export function getScoringTemplates() {
  return api("/api/teacher/scoring-templates");
}

// 参考图分析
export function triggerAnalysis(qid: string) {
  return api(`/api/teacher/questions/${qid}/analyze`, { method: "POST" });
}

export function getAnalysisResult(qid: string) {
  return api(`/api/teacher/questions/${qid}/analysis`);
}

export function downloadRosterTemplate() {
  window.open(`${BASE}/api/teacher/roster/template`, "_blank");
}

export function getQuestionFileUrl(qid: string, filename: string): string {
  return `${BASE}/api/teacher/files/${qid}/${filename}`;
}

export function getStudentFileUrl(qid: string, filename: string): string {
  return `${BASE}/api/student/files/${qid}/${filename}`;
}

export function getTeacherPreviewUrl(qid: string, filename: string, ts?: number): string {
  const t = ts ? `?t=${ts}` : "";
  return `${BASE}/api/teacher/preview/${qid}/${filename}${t}`;
}

export function getStudentPreviewUrl(qid: string, filename: string, ts?: number): string {
  const t = ts ? `?t=${ts}` : "";
  return `${BASE}/api/student/preview/${qid}/${filename}${t}`;
}
