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
    cache: "no-store",
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

// 学生登录（含密码）
export function studentLogin(name: string, studentId: string, password: string) {
  return api("/api/student/login", {
    method: "POST",
    body: JSON.stringify({ name, student_id: studentId, password }),
  });
}

// 学生修改密码
export function studentChangePassword(name: string, studentId: string, className: string, oldPassword: string, newPassword: string) {
  return api("/api/student/change-password", {
    method: "POST",
    body: JSON.stringify({ name, student_id: studentId, class_name: className, old_password: oldPassword, new_password: newPassword }),
  });
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
  return api(`/api/student/analysis/${qid}?name=${encodeURIComponent(name)}&student_id=${encodeURIComponent(studentId)}&_t=${Date.now()}`);
}

// 学生提交流程：upload → analyze(optional preview) → grade(combined analyze+grade)

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

export function gradeSubmission(qid: string, name: string, studentId: string, mode: "test" | "submit" = "submit") {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("student_id", studentId);
  fd.append("mode", mode);
  return api(`/api/student/grade/${qid}`, { method: "POST", body: fd });
}

// ------- Teacher -------
export async function teacherLogin(password: string, username: string) {
  const fd = new FormData();
  fd.append("username", username);
  fd.append("password", password);
  return api("/api/teacher/login", { method: "POST", body: fd });
}

export function getTeacherProfile() {
  return api("/api/teacher/profile");
}

export function updateTeacherProfile(name: string, username: string) {
  return api("/api/teacher/profile", {
    method: "PUT",
    body: JSON.stringify({ name, username }),
  });
}

export function teacherChangePassword(oldPassword: string, newPassword: string) {
  return api("/api/teacher/profile/change-password", {
    method: "POST",
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
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

export function updateSettings(data: Record<string, unknown>) {
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

export function lookupRoster(name: string, studentId: string) {
  return api(`/api/teacher/roster/lookup?name=${encodeURIComponent(name)}&student_id=${encodeURIComponent(studentId)}`);
}

export function refreshGrades(qid: string) {
  return api(`/api/teacher/grades/${qid}/refresh`, { method: "POST" });
}

export function getTeacherStudentPreviewUrl(qid: string, studentId: string): string {
  return `${BASE}/api/teacher/student-preview/${qid}/${studentId}`;
}

export function getStudentAnalysis(qid: string, studentId: string, name: string) {
  return api(`/api/teacher/student-analysis/${qid}/${studentId}?name=${encodeURIComponent(name)}&_t=${Date.now()}`);
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

export function resetStudentPassword(className: string, studentId: string) {
  return api("/api/teacher/roster/reset-password", {
    method: "POST",
    body: JSON.stringify({ class_name: className, student_id: studentId }),
  });
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
  return api(`/api/teacher/questions/${qid}/analysis?_t=${Date.now()}`);
}

export function restartService() {
  return api("/api/teacher/settings/restart", { method: "POST" });
}

export function queryCurrentModel() {
  return api("/api/teacher/settings/query-model", { method: "POST" });
}

export function testVision(cfg: { api_base: string; api_key: string; model: string }) {
  return api("/api/teacher/settings/test-vision", {
    method: "POST",
    body: JSON.stringify(cfg),
  });
}

export function changePassword(currentPassword: string, newPassword: string) {
  return api("/api/teacher/settings/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export function getQueueStatus() {
  return api("/api/teacher/settings/queue-status");
}

export function clearQueue() {
  return api("/api/teacher/settings/queue-clear", { method: "POST" });
}

export function rejectSubmission(qid: string, studentId: string) {
  return api(`/api/teacher/grades/${qid}/reject/${studentId}`, { method: "POST" });
}

export function downloadRosterTemplate() {
  window.open(`${BASE}/api/teacher/roster/template`, "_blank");
}

export function getQuestionFileUrl(qid: string, filename: string, ts?: number): string {
  const t = ts ? `?t=${ts}` : "";
  return `${BASE}/api/teacher/files/${qid}/${filename}${t}`;
}

export function getHomeworkDownloadUrl(qid: string, className?: string): string {
  const params = className ? `?class_name=${encodeURIComponent(className)}` : "";
  return `${BASE}/api/teacher/questions/${qid}/download${params}`;
}

/** 通过 fetch（带 cookie）下载作业 ZIP，不离开 SPA */
export async function downloadHomeworkZip(qid: string, className?: string): Promise<void> {
  const url = getHomeworkDownloadUrl(qid, className);
  const resp = await fetch(url, { credentials: "include" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || `下载失败 (HTTP ${resp.status})`);
  }
  // 从 Content-Disposition 提取文件名
  const cd = resp.headers.get("content-disposition") || "";
  const match = cd.match(/filename\*?=UTF-8''([^;]+)/i) || cd.match(/filename="([^"]+)"/i);
  const filename = match ? decodeURIComponent(match[1]) : `${qid}_全部.zip`;

  const blob = await resp.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

export function getStudentFileUrl(qid: string, filename: string, ts?: number): string {
  const t = ts ? `?t=${ts}` : "";
  return `${BASE}/api/student/files/${qid}/${filename}${t}`;
}

export function getTeacherPreviewUrl(qid: string, filename: string, ts?: number): string {
  const t = ts ? `?t=${ts}` : "";
  return `${BASE}/api/teacher/preview/${qid}/${filename}${t}`;
}

export function getStudentPreviewUrl(qid: string, filename: string, ts?: number): string {
  const t = ts ? `?t=${ts}` : "";
  return `${BASE}/api/student/preview/${qid}/${filename}${t}`;
}

// ------- Templates -------
export function getTemplates() {
  return api("/api/teacher/templates");
}

export function updateTemplate(name: string, content: string) {
  return api(`/api/teacher/templates/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export function getQuestionTemplate(qid: string) {
  return api(`/api/teacher/questions/${qid}/template`);
}

export function updateQuestionTemplate(qid: string, content: string) {
  return api(`/api/teacher/questions/${qid}/template`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export function selectQuestionTemplate(qid: string, type: string) {
  return api(`/api/teacher/questions/${qid}/template`, {
    method: "POST",
    body: JSON.stringify({ type }),
  });
}
