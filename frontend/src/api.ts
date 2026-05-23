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

export function submitHomework(qid: string, name: string, studentId: string, file: File, mode: "test" | "submit" = "submit") {
  const fd = new FormData();
  fd.append("name", name);
  fd.append("student_id", studentId);
  fd.append("file", file);
  fd.append("mode", mode);
  return api(`/api/student/submit/${qid}`, { method: "POST", body: fd });
}

export function getStudentResult(qid: string, studentId: string) {
  return api(`/api/student/result/${qid}/${studentId}`);
}

// ------- Teacher -------
export async function teacherLogin(password: string) {
  const fd = new FormData();
  fd.append("password", password);
  const result = await api("/api/teacher/login", { method: "POST", body: fd });
  // set session cookie
  document.cookie = `session=${result.token};path=/;max-age=14400`;
  return result;
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
