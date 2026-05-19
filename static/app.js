const state = {
  data: null,
  loading: false,
  loadingCourse: false,
  loadingPlan: false,
  pageMode: "dashboard",
  dashboardView: "today",
  courseLoaded: false,
  planLoaded: false,
  showFullPlan: false,
  masteryChapterNumber: null,
  lessonStage: null,
  lessonStageLessonId: null,
  askPanelOpen: null,
  nextUpPanelOpen: null,
  completionScreen: null,
  quizDrafts: {},
  quizQuestionIndex: 0,
  quizQuestionLessonId: null,
  quizAdvanceTimer: null,
  inlineFlashcardIndex: 0,
  inlineFlashcardLessonId: null,
  inlineFlashcardMotion: "next",
};

const statsCard = document.getElementById("statsCard");
const dashboardShell = document.getElementById("dashboardShell");
const lessonScreen = document.getElementById("lessonScreen");
const curriculum = document.getElementById("curriculum");
const lessonCard = document.getElementById("lessonCard");
const resumeCard = document.getElementById("resumeCard");
const topProgressStrip = document.getElementById("topProgressStrip");
const todayAssignment = document.getElementById("todayAssignment");
const masteryMap = document.getElementById("masteryMap");
const examCenter = document.getElementById("examCenter");
const mistakeNotebook = document.getElementById("mistakeNotebook");
const questionForm = document.getElementById("questionForm");
const questionInput = document.getElementById("questionInput");
const teachBackButton = document.getElementById("teachBackButton");
const statusStrip = document.getElementById("statusStrip");
const connApi = document.getElementById("connApi");
const connBackend = document.getElementById("connBackend");
const connNeon = document.getElementById("connNeon");
const startOverButton = document.getElementById("startOverButton");
const weeklyPlan = document.getElementById("weeklyPlan");
const flashcardsPanel = document.getElementById("flashcardsPanel");
const flashcardReviewPanel = document.getElementById("flashcardReviewPanel");
const mistakeSummaryPanel = document.getElementById("mistakeSummaryPanel");
const upNextPanel = document.getElementById("upNextPanel");
const courseNavigator = document.getElementById("courseNavigator");
const actionGuide = document.getElementById("actionGuide");
const askDetails = document.getElementById("askDetails");
const nextUpDetails = document.getElementById("nextUpDetails");
const midtermForm = document.getElementById("midtermForm");
const weeklyGoalForm = document.getElementById("weeklyGoalForm");
const midtermEnabled = document.getElementById("midtermEnabled");
const midtermStart = document.getElementById("midtermStart");
const midtermEnd = document.getElementById("midtermEnd");
const weeklyGoalInput = document.getElementById("weeklyGoalInput");
const dashboardViewTabs = Array.from(document.querySelectorAll("[data-dashboard-view]"));
const actionButtons = Array.from(document.querySelectorAll("[data-action]"));
const actionButtonMap = new Map(actionButtons.map((button) => [button.dataset.action, button]));
const LOCAL_STATE_KEY = "tax_tutor_local_state_v1";
const CLIENT_ID_KEY = "tax_tutor_client_id_v1";
const SYNC_CODE_KEY = "tax_tutor_sync_code_v1";
const API_BASE = (window.TAX_TUTOR_API_BASE || "").replace(/\/+$/, "");
let statusPollTimer = null;
const syncForm = document.getElementById("syncForm");
const syncCodeInput = document.getElementById("syncCodeInput");
const clearSyncCodeButton = document.getElementById("clearSyncCodeButton");

if (syncCodeInput) {
  syncCodeInput.value = getSyncCode();
}

function getSyncCode() {
  try {
    return (window.localStorage.getItem(SYNC_CODE_KEY) || "").trim();
  } catch (error) {
    return "";
  }
}

function getOrCreateClientId() {
  try {
    const syncCode = getSyncCode();
    if (syncCode) return `sync:${syncCode}`;
    const existing = window.localStorage.getItem(CLIENT_ID_KEY);
    if (existing) return existing;
    const generated = `client_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
    window.localStorage.setItem(CLIENT_ID_KEY, generated);
    return generated;
  } catch (error) {
    return `client_ephemeral_${Date.now().toString(36)}`;
  }
}

function setSyncCode(value) {
  const normalized = String(value || "").trim();
  if (normalized) {
    window.localStorage.setItem(SYNC_CODE_KEY, normalized);
  } else {
    window.localStorage.removeItem(SYNC_CODE_KEY);
  }
}

function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-TaxTutor-Client", getOrCreateClientId());
  const requestUrl = `${API_BASE}${url}`;
  return fetch(requestUrl, { ...options, headers });
}

function loadLocalStateSnapshot() {
  try {
    const raw = window.localStorage.getItem(LOCAL_STATE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || !parsed.updated_at) return null;
    return parsed;
  } catch (error) {
    return null;
  }
}

function saveLocalStateSnapshot(appState) {
  if (!appState || typeof appState !== "object") return;
  try {
    const snapshot = {
      current_lesson_id: appState.current_lesson?.lesson_id || null,
      completed_lessons: Array.isArray(appState.completed_lessons) ? appState.completed_lessons : [],
      last_card: appState.last_card || null,
      flashcards: appState.flashcards || {},
      lesson_performance: appState.lesson_performance || {},
      mistake_notebook: appState.mistake_notebook?.items || [],
      weekly_goal_lessons: appState.weekly_goal_lessons || 2,
      midterm_mode: appState.midterm_mode || { enabled: false, start_chapter: 1, end_chapter: 25 },
      updated_at: appState.updated_at || new Date().toISOString(),
    };
    window.localStorage.setItem(LOCAL_STATE_KEY, JSON.stringify(snapshot));
  } catch (error) {
    // Ignore storage write failures so learning flow never blocks.
  }
}

async function hydrateStateFromLocal(bootstrapState) {
  const localState = loadLocalStateSnapshot();
  if (!localState) return bootstrapState;
  const localTs = Date.parse(localState.updated_at || "");
  const serverTs = Date.parse(bootstrapState?.updated_at || "");
  const localHasProgress = Boolean((localState.completed_lessons || []).length || localState.last_card);
  const serverHasProgress = Boolean((bootstrapState?.completed_lessons || []).length || bootstrapState?.has_saved_card);
  if (serverHasProgress) {
    if (Number.isFinite(serverTs) && Number.isFinite(localTs) && localTs <= serverTs) {
      return bootstrapState;
    }
  } else if (!localHasProgress) {
    return bootstrapState;
  }
  try {
    const response = await apiFetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "hydrate_state", state: localState }),
    });
    const data = await response.json();
    if (!response.ok || !data?.state) return bootstrapState;
    return data.state;
  } catch (error) {
    return bootstrapState;
  }
}

function setPageMode(mode) {
  state.pageMode = mode;
  if (state.data) {
    render(state.data);
  } else {
    renderLayoutMode();
  }
}

function setDashboardView(view) {
  state.dashboardView = view;
  if (view === "course") {
    loadCourseData();
  } else if (view === "plan") {
    loadPlanData();
  }
  if (state.data) {
    render(state.data);
  } else {
    renderLayoutMode();
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setStatus(message, kind = "info") {
  if (!message) {
    statusStrip.classList.add("hidden");
    statusStrip.textContent = "";
    statusStrip.dataset.kind = "";
    return;
  }
  statusStrip.classList.remove("hidden");
  statusStrip.dataset.kind = kind;
  statusStrip.textContent = message;
}

function setConnChip(node, label, stateText) {
  if (!node) return;
  node.textContent = `${label}: ${stateText}`;
  node.dataset.state = String(stateText || "").toLowerCase().replace(/\s+/g, "-");
}

async function pollConnectionStatus() {
  try {
    const response = await apiFetch("/api/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    setConnChip(connApi, "API", "connected");
    setConnChip(connBackend, "Render", "connected");
    if (!payload.neon_configured) {
      setConnChip(connNeon, "Neon", "not configured");
      return;
    }
    if (payload.neon_connected && payload.neon_in_use) {
      setConnChip(connNeon, "Neon", "connected");
      return;
    }
    if (payload.neon_connected && !payload.neon_in_use) {
      setConnChip(connNeon, "Neon", "reachable not in use");
      return;
    }
    setConnChip(connNeon, "Neon", "disconnected");
  } catch (error) {
    setConnChip(connApi, "API", "disconnected");
    setConnChip(connBackend, "Render", "disconnected");
    setConnChip(connNeon, "Neon", "unknown");
  }
}

function startStatusPolling() {
  if (statusPollTimer) return;
  pollConnectionStatus();
  statusPollTimer = window.setInterval(pollConnectionStatus, 30000);
}

function currentCardHasQuiz(appState = state.data) {
  return Boolean(appState?.last_card?.quiz_questions?.length);
}

function currentQuizIsGraded(appState = state.data) {
  return Boolean(appState?.last_card?.quiz_feedback?.question_feedback?.length);
}

function clearCompletionScreen() {
  state.completionScreen = null;
}

function scrollQuizIntoView() {
  const quizForm = document.getElementById("quizForm");
  if (!quizForm) return false;
  quizForm.scrollIntoView({ behavior: "smooth", block: "start" });
  return true;
}

function scrollToLessonCard() {
  lessonCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

function scrollDashboardSection(target, statusMessage = "") {
  if (!target) return;
  state.pageMode = "dashboard";
  const parentView = target.closest("[data-dashboard-panel]")?.dataset.dashboardPanel;
  if (parentView) {
    state.dashboardView = parentView;
  }
  clearCompletionScreen();
  render(state.data);
  requestAnimationFrame(() => {
    const scrollTarget =
      target.closest(".study-card, .question-card, .resume-card, .stats-card, .hero, .brand-card") || target;
    scrollTarget.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  if (statusMessage) {
    setStatus(statusMessage, "info");
  }
}

function toTitleCase(value) {
  return String(value || "")
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function masteryStatusMeta(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "mastered") return { label: "Strong", className: "mastered" };
  if (normalized === "learned") return { label: "Learned", className: "learned" };
  if (normalized === "current") return { label: "Current", className: "current" };
  if (normalized === "needs_review") return { label: "Needs review", className: "needs-review" };
  if (normalized === "started") return { label: "Started", className: "started" };
  return { label: "Not started", className: "not-started" };
}

function getSelectedMasteryChapter(appState) {
  const chapterNumbers = (appState?.chapters || []).map((chapter) => Number(chapter.chapter_number));
  const fallback =
    appState?.chapter_mastery?.chapter_number || appState?.current_lesson?.chapter_number || chapterNumbers[0] || 1;
  if (chapterNumbers.includes(Number(state.masteryChapterNumber))) {
    return Number(state.masteryChapterNumber);
  }
  state.masteryChapterNumber = fallback;
  return fallback;
}

function setMasteryChapter(chapterNumber) {
  state.masteryChapterNumber = Number(chapterNumber);
  if (state.data) {
    renderMasteryMap(state.data);
  }
}

function defaultLessonStage(card) {
  if (!card) return null;
  if (card.quiz_feedback?.question_feedback?.length) return "quiz";
  if (card.card_type === "quiz" || card.card_type === "exam") return "quiz";
  if (card.card_type === "example") return "example";
  return "learn";
}

function ensureLessonStage(card) {
  const validStages = new Set(["learn", "example", "quiz", "review", "evidence"]);
  if (!card) {
    state.lessonStage = null;
    state.lessonStageLessonId = null;
    return null;
  }
  if (state.lessonStageLessonId !== card.lesson_id || !validStages.has(state.lessonStage)) {
    state.lessonStage = defaultLessonStage(card);
    state.lessonStageLessonId = card.lesson_id;
  }
  return state.lessonStage;
}

function setLessonStage(stage) {
  if (!state.data?.last_card) return;
  if (state.quizAdvanceTimer) {
    clearTimeout(state.quizAdvanceTimer);
    state.quizAdvanceTimer = null;
  }
  state.lessonStage = stage;
  state.lessonStageLessonId = state.data.last_card.lesson_id;
  renderCard(state.data.last_card);
}

function ensureQuizState(card) {
  if (!card?.quiz_questions?.length) {
    state.quizQuestionIndex = 0;
    state.quizQuestionLessonId = null;
    return { draftAnswers: [], questionIndex: 0 };
  }

  if (state.quizQuestionLessonId !== card.lesson_id) {
    state.quizQuestionIndex = 0;
    state.quizQuestionLessonId = card.lesson_id;
  }

  const existingDraft = state.quizDrafts[card.lesson_id];
  const gradedAnswers = card.quiz_feedback?.question_feedback?.map((item) => item.selected_option || "") || [];
  const baseAnswers =
    existingDraft && existingDraft.length === card.quiz_questions.length
      ? existingDraft
      : card.quiz_questions.map((_, index) => gradedAnswers[index] || "");

  state.quizDrafts[card.lesson_id] = baseAnswers;
  state.quizQuestionIndex = Math.max(0, Math.min(state.quizQuestionIndex, card.quiz_questions.length - 1));
  return { draftAnswers: baseAnswers, questionIndex: state.quizQuestionIndex };
}

function setQuizQuestionIndex(card, index) {
  if (!card?.quiz_questions?.length) return;
  if (state.quizAdvanceTimer) {
    clearTimeout(state.quizAdvanceTimer);
    state.quizAdvanceTimer = null;
  }
  state.quizQuestionLessonId = card.lesson_id;
  state.quizQuestionIndex = Math.max(0, Math.min(index, card.quiz_questions.length - 1));
  renderCard(card);
}

function setQuizDraftAnswer(card, questionIndex, answer) {
  if (!card?.lesson_id) return;
  const draftAnswers = state.quizDrafts[card.lesson_id] || card.quiz_questions.map(() => "");
  draftAnswers[questionIndex] = answer;
  state.quizDrafts[card.lesson_id] = draftAnswers;
}

function ensureInlineFlashcardState(card) {
  const cards = card?.flashcards || [];
  if (!cards.length) {
    state.inlineFlashcardIndex = 0;
    state.inlineFlashcardLessonId = null;
    return 0;
  }
  if (state.inlineFlashcardLessonId !== card.lesson_id) {
    state.inlineFlashcardLessonId = card.lesson_id;
    state.inlineFlashcardIndex = 0;
  }
  state.inlineFlashcardIndex = Math.max(0, Math.min(state.inlineFlashcardIndex, cards.length - 1));
  return state.inlineFlashcardIndex;
}

function setInlineFlashcardIndex(card, index) {
  const cards = card?.flashcards || [];
  if (!cards.length) return;
  state.inlineFlashcardMotion = index > state.inlineFlashcardIndex ? "next" : "prev";
  state.inlineFlashcardLessonId = card.lesson_id;
  state.inlineFlashcardIndex = Math.max(0, Math.min(index, cards.length - 1));
  renderCard(card);
}

function buildCompletionScreen(previousCard, nextState) {
  const feedback = previousCard?.quiz_feedback || null;
  const current = nextState?.current_lesson || null;
  const correctCount =
    feedback?.correct_count ||
    feedback?.question_feedback?.filter((item) => String(item.verdict).toLowerCase() === "correct").length ||
    0;
  const totalQuestions = feedback?.total_questions || feedback?.question_feedback?.length || 0;
  const chapterPercent = current?.chapter_lesson_total
    ? Math.round((current.position_in_chapter / current.chapter_lesson_total) * 100)
    : 0;

  return {
    completedTitle: previousCard?.title || "Lesson complete",
    chapterNumber: previousCard?.chapter_number || current?.chapter_number || 1,
    chapterTitle: previousCard?.chapter_title || current?.chapter_title || "",
    scoreLabel: totalQuestions ? `${correctCount}/${totalQuestions}` : "Done",
    summary: feedback?.overall_summary || "Nice work. You finished this lesson and your progress is saved.",
    nextLessonTitle: current?.title || "You are caught up for now.",
    nextLessonMeta: current
      ? `Chapter ${current.chapter_number} · Lesson ${current.position_in_chapter}/${current.chapter_lesson_total}`
      : "You have reached the end of the available lesson path.",
    nextLessonKind: current ? lessonLabel(current) : "Done",
    chapterPercent,
  };
}

function lessonLabel(lesson) {
  if (lesson.lesson_kind === "overview") return "Overview";
  if (lesson.lesson_kind === "section") return "Section";
  if (lesson.lesson_kind === "concept") return "Concept";
  if (lesson.lesson_kind === "example") return "Example";
  if (lesson.lesson_kind === "practice") return "Practice";
  if (lesson.lesson_kind === "review") return "Review";
  return "Lesson";
}

function renderStats(appState) {
  const completed = appState.completed_lesson_count;
  const total = appState.lesson_count;
  const current = appState.current_lesson;
  const percent = total ? Math.round((completed / total) * 100) : 0;

  statsCard.innerHTML = `
    <div class="stats-grid stats-grid-compact">
      <div class="stat-pill">
        <span class="stat-label">Progress</span>
        <strong>${completed} / ${total}</strong>
      </div>
      <div class="stat-pill">
        <span class="stat-label">Coverage</span>
        <strong>${percent}%</strong>
      </div>
    </div>
    <div class="stats-grid">
      <div class="stat-pill">
        <span class="stat-label">Flashcards Due</span>
        <strong>${appState.flashcards_due_count}</strong>
      </div>
      <div class="stat-pill">
        <span class="stat-label">Flashcards Total</span>
        <strong>${appState.flashcard_total_count}</strong>
      </div>
    </div>
    <div class="current-focus">
      <span class="stat-label">Saved Place</span>
      <strong>${current ? escapeHtml(current.title) : "Ready to begin"}</strong>
      <p>${current ? `Chapter ${escapeHtml(current.chapter_number)} · Lesson ${escapeHtml(current.position_in_chapter)}/${escapeHtml(current.chapter_lesson_total)}` : "Chapter 1 will be first."}</p>
    </div>
  `;
}

function renderTopProgress(appState) {
  const current = appState.current_lesson;
  if (!current) {
    topProgressStrip.classList.add("hidden");
    topProgressStrip.innerHTML = "";
    return;
  }

  const coursePercent = appState.lesson_count ? Math.round((appState.completed_lesson_count / appState.lesson_count) * 100) : 0;
  const chapterPercent = current.chapter_lesson_total
    ? Math.round((current.position_in_chapter / current.chapter_lesson_total) * 100)
    : 0;
  const stageLabel = state.completionScreen
    ? "Lesson Complete"
    : state.data?.last_card
      ? toTitleCase(ensureLessonStage(state.data.last_card))
      : "Ready";

  topProgressStrip.classList.remove("hidden");
  topProgressStrip.innerHTML = `
    <div class="top-progress-shell">
      <div class="top-progress-copy">
        <p class="eyebrow">Your place in the course</p>
        <strong>Chapter ${escapeHtml(current.chapter_number)} · Lesson ${escapeHtml(current.position_in_chapter)}/${escapeHtml(current.chapter_lesson_total)}</strong>
        <p>${escapeHtml(current.title)} · Current step: ${escapeHtml(stageLabel)}</p>
      </div>
      <div class="top-progress-meters">
        <div class="top-progress-meter">
          <div class="top-progress-meter-copy">
            <span>Course progress</span>
            <strong>${escapeHtml(appState.completed_lesson_count)}/${escapeHtml(appState.lesson_count)}</strong>
          </div>
          <div class="mini-progress-bar">
            <span style="width:${coursePercent}%"></span>
          </div>
        </div>
        <div class="top-progress-meter">
          <div class="top-progress-meter-copy">
            <span>This chapter</span>
            <strong>${escapeHtml(current.position_in_chapter)}/${escapeHtml(current.chapter_lesson_total)}</strong>
          </div>
          <div class="mini-progress-bar mini-progress-bar-green">
            <span style="width:${chapterPercent}%"></span>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderResumeCard(appState) {
  const current = appState.current_lesson;
  if (!current) {
    resumeCard.classList.add("hidden");
    resumeCard.innerHTML = "";
    return;
  }

  const completed = appState.completed_lesson_count || 0;
  const hasSavedLesson = Boolean(appState.has_saved_card || appState.last_card);
  const resumeLabel = completed ? "Continue where you left off" : "Your first lesson is ready";
  const resumeCopy = hasSavedLesson
    ? "Open your current lesson right where you stopped, with your place saved."
    : "Start the next lesson and the tutor will guide you step by step.";

  resumeCard.classList.remove("hidden");
  resumeCard.innerHTML = `
    <div class="resume-shell">
      <div class="resume-copy">
        <p class="eyebrow">Quick resume</p>
        <h3>${escapeHtml(resumeLabel)}</h3>
        <p>${escapeHtml(resumeCopy)}</p>
        <div class="resume-meta">
          <span class="lesson-badge lesson-badge-accent">Chapter ${escapeHtml(current.chapter_number)}</span>
          <span class="lesson-badge">${escapeHtml(lessonLabel(current))}</span>
          <span class="lesson-badge">Lesson ${escapeHtml(current.position_in_chapter)}/${escapeHtml(current.chapter_lesson_total)}</span>
        </div>
        <strong class="resume-title">${escapeHtml(current.title)}</strong>
      </div>
      <div class="resume-actions">
        <button class="primary-button" data-resume-action="${hasSavedLesson ? "jump" : "next"}" type="button">
          <span class="action-button-title">${hasSavedLesson ? "Resume Lesson" : "Start Lesson"}</span>
          <span class="action-button-help">${hasSavedLesson ? "Jump back into your current lesson" : "Open the next lesson in sequence"}</span>
        </button>
      </div>
    </div>
  `;
}

function renderWeeklyPlan(appState) {
  midtermEnabled.checked = Boolean(appState.midterm_mode.enabled);
  midtermStart.value = appState.midterm_mode.start_chapter;
  midtermEnd.value = appState.midterm_mode.end_chapter;
  weeklyGoalInput.value = appState.weekly_goal_lessons;

  const scopeCopy = appState.midterm_mode.enabled
    ? `Midterm mode is on for Chapters ${appState.midterm_mode.start_chapter}-${appState.midterm_mode.end_chapter}.`
    : "Midterm mode is off. The full course is in scope.";

  const hasHiddenWeeks = (appState.weekly_plan_total_count || appState.weekly_plan.length) > appState.weekly_plan.length;
  const showPlanToggle = hasHiddenWeeks || state.showFullPlan;
  const weeksToRender =
    state.showFullPlan || !hasHiddenWeeks
      ? appState.weekly_plan
      : appState.weekly_plan;

  weeklyPlan.innerHTML = `
    <p class="plan-note">${escapeHtml(scopeCopy)}</p>
    ${
      showPlanToggle
        ? `
          <div class="plan-note">
            Showing ${weeksToRender.length} of ${appState.weekly_plan_total_count} weeks.
            <button class="ghost-button compact-button" data-plan-toggle type="button">
              ${state.showFullPlan ? "Show Less" : "Show Full Plan"}
            </button>
          </div>
        `
        : ""
    }
    <div class="weekly-plan-list">
      ${weeksToRender
        .map(
          (week) => `
            <div class="week-chip">
              <div class="week-chip-top">
                <strong>Week ${week.week_number}</strong>
                <span>${week.completed_lesson_count}/${week.lesson_count}</span>
              </div>
              <p>Chapters ${escapeHtml(week.chapter_span)}</p>
              <ul class="mini-list">
                ${week.focus_titles.map((title) => `<li>${escapeHtml(title)}</li>`).join("")}
              </ul>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function renderTodayAssignment(appState) {
  const assignment = appState.today_assignment;
  const current = appState.current_lesson;
  const nextLesson = (appState.up_next || []).find((lesson) => !lesson.current) || null;
  if (!assignment?.tasks?.length) {
    todayAssignment.innerHTML = `
      <div class="empty-cardlet">
        <p>No assignment is queued yet.</p>
        <p>Start the next lesson and the app will build today's path for you.</p>
      </div>
    `;
    return;
  }

  const [primaryTask, ...supportTasks] = assignment.tasks;

  todayAssignment.innerHTML = `
    <div class="today-assignment-shell">
      <div class="today-route-strip">
        <div class="today-route-card">
          <span class="stat-label">Current</span>
          <strong>${current ? escapeHtml(current.title) : "Ready to begin"}</strong>
          <p>${current ? `Chapter ${escapeHtml(current.chapter_number)} · Lesson ${escapeHtml(current.position_in_chapter)}/${escapeHtml(current.chapter_lesson_total)}` : "Open the first lesson to start."}</p>
        </div>
        <div class="today-route-card">
          <span class="stat-label">Next</span>
          <strong>${nextLesson ? escapeHtml(nextLesson.title) : "Nothing else queued yet"}</strong>
          <p>${nextLesson ? `Chapter ${escapeHtml(nextLesson.chapter_number)} · Lesson ${escapeHtml(nextLesson.position_in_chapter)}/${escapeHtml(nextLesson.chapter_lesson_total)}` : "Finish today’s lesson and the next step will appear here."}</p>
        </div>
      </div>
      <p class="plan-note">${escapeHtml(assignment.headline)}</p>
      <div class="today-time-row">
        <span class="mini-pill">${escapeHtml(assignment.estimated_minutes)} min plan</span>
      </div>
      <article class="today-task-card today-primary-task">
        <div class="today-primary-copy">
          <span class="today-task-index">1</span>
          <div>
            <span class="today-focus-label">Start here</span>
            <strong>${escapeHtml(primaryTask.title)}</strong>
            <p>${escapeHtml(primaryTask.detail)}</p>
          </div>
        </div>
        <button class="primary-button compact-button" data-assignment-task='${escapeHtml(JSON.stringify(primaryTask))}' type="button">
          ${escapeHtml(primaryTask.cta_label)}
        </button>
      </article>
      ${
        supportTasks.length
          ? `
            <div class="today-support-head">
              <strong>Then do these</strong>
              <p>Quick wins for today after the main lesson step.</p>
            </div>
            <div class="today-support-grid">
              ${supportTasks
                .map(
                  (task, index) => `
                    <div class="today-task-card today-task-card-secondary">
                      <div class="today-task-copy">
                        <span class="today-task-index">${index + 2}</span>
                        <div>
                          <strong>${escapeHtml(task.title)}</strong>
                          <p>${escapeHtml(task.detail)}</p>
                        </div>
                      </div>
                      <button class="ghost-button compact-button" data-assignment-task='${escapeHtml(JSON.stringify(task))}' type="button">
                        ${escapeHtml(task.cta_label)}
                      </button>
                    </div>
                  `
                )
                .join("")}
            </div>
          `
          : ""
      }
    </div>
  `;
}

function renderMasteryMap(appState) {
  const mastery = appState.chapter_mastery;
  const chapters = appState.chapters || [];
  if (!chapters.length) {
    masteryMap.innerHTML = `
      <div class="empty-cardlet">
        <p>Chapter mastery will appear after you open a lesson.</p>
      </div>
    `;
    return;
  }

  const selectedChapterNumber = getSelectedMasteryChapter(appState);
  const selectedChapter = chapters.find((chapter) => Number(chapter.chapter_number) === selectedChapterNumber) || chapters[0];
  const selectedUsesLiveMastery = Number(mastery?.chapter_number) === Number(selectedChapter.chapter_number);
  const masteryTiles = selectedUsesLiveMastery
    ? mastery.tiles
    : selectedChapter.lessons.map((lesson) => {
        let status = "not_started";
        if (lesson.current) status = "current";
        else if (lesson.completed) status = "learned";
        const statusMeta = masteryStatusMeta(status);
        return {
          lesson_id: lesson.lesson_id,
          title: lesson.title,
          status,
          status_label: statusMeta.label,
          position_in_chapter: lesson.position_in_chapter,
          chapter_lesson_total: lesson.chapter_lesson_total,
          score_label: lesson.completed ? "Completed" : "No quiz yet",
        };
      });

  const currentTile = masteryTiles.find((tile) => tile.status === "current") || masteryTiles[0] || null;
  const nextTiles = masteryTiles
    .filter((tile) => tile.lesson_id !== currentTile?.lesson_id && !["learned", "mastered"].includes(String(tile.status)))
    .slice(0, 3);
  const needsReviewTiles = masteryTiles.filter((tile) => tile.status === "needs_review").slice(0, 3);
  const recentlyCompletedTiles = [...masteryTiles]
    .filter((tile) => ["learned", "mastered"].includes(String(tile.status)))
    .slice(-3)
    .reverse();
  const completedCount = selectedChapter.lessons.filter((lesson) => lesson.completed).length;
  const objectiveMastery = (appState.learning_objective_mastery || []).slice(0, 3);
  const chapterPercent = selectedUsesLiveMastery
    ? mastery.mastery_percent
    : selectedChapter.lesson_count
      ? Math.round((completedCount / selectedChapter.lesson_count) * 100)
      : 0;

  const renderMiniTile = (tile) => {
    const meta = masteryStatusMeta(tile.status);
    return `
      <button class="mastery-mini-card mastery-mini-${escapeHtml(meta.className)}" data-lesson-id="${escapeHtml(tile.lesson_id)}" type="button">
        <span class="lesson-tag">${escapeHtml(meta.label)}</span>
        <strong>${escapeHtml(tile.title)}</strong>
        <p>Lesson ${escapeHtml(tile.position_in_chapter)}/${escapeHtml(tile.chapter_lesson_total)} · ${escapeHtml(tile.score_label)}</p>
      </button>
    `;
  };

  masteryMap.innerHTML = `
    <div class="mastery-shell">
      <div class="mastery-header">
        <div>
          <strong>Chapter ${escapeHtml(selectedChapter.chapter_number)}</strong>
          <p>${escapeHtml(selectedChapter.chapter_title)}</p>
        </div>
        <span class="chapter-progress">${escapeHtml(chapterPercent)}%</span>
      </div>
      <div class="chapter-progress-bar">
        <span style="width:${chapterPercent}%"></span>
      </div>
      <div class="mastery-objective-strip">
        ${
          objectiveMastery.length
            ? objectiveMastery
                .map(
                  (objective) => `
                    <div class="mastery-objective-chip status-${escapeHtml(objective.status)}">
                      <strong>LO ${escapeHtml(objective.objective_code)}</strong>
                      <span>${escapeHtml(objective.mastery_score)}%</span>
                    </div>
                  `
                )
                .join("")
            : `<div class="empty-cardlet"><p>Objective mastery appears after objective-tagged lessons.</p></div>`
        }
      </div>
      <div class="mastery-chapter-chooser">
        ${chapters
          .map(
            (chapter) => `
              <button
                class="mastery-chapter-chip ${Number(chapter.chapter_number) === Number(selectedChapter.chapter_number) ? "is-active" : ""}"
                data-mastery-chapter="${escapeHtml(chapter.chapter_number)}"
                type="button"
              >
                Ch ${escapeHtml(chapter.chapter_number)}
              </button>
            `
          )
          .join("")}
      </div>
      <div class="mastery-focus-grid">
        ${
          currentTile
            ? `
              <section class="mastery-focus-block">
                <div class="mastery-focus-head">
                  <strong>Current focus</strong>
                  <span class="mini-pill">Start here</span>
                </div>
                ${renderMiniTile(currentTile)}
              </section>
            `
            : ""
        }
        <section class="mastery-focus-block">
          <div class="mastery-focus-head">
            <strong>Up next</strong>
            <span class="mini-pill">${escapeHtml(nextTiles.length || 0)} waiting</span>
          </div>
          <div class="mastery-mini-list">
            ${
              nextTiles.length
                ? nextTiles.map((tile) => renderMiniTile(tile)).join("")
                : `<div class="empty-cardlet"><p>No upcoming lessons are queued in this chapter right now.</p></div>`
            }
          </div>
        </section>
        <section class="mastery-focus-block">
          <div class="mastery-focus-head">
            <strong>Needs review</strong>
            <span class="mini-pill">${escapeHtml(needsReviewTiles.length || 0)}</span>
          </div>
          <div class="mastery-mini-list">
            ${
              needsReviewTiles.length
                ? needsReviewTiles.map((tile) => renderMiniTile(tile)).join("")
                : `<div class="empty-cardlet"><p>No weak spots are flagged in this chapter yet.</p></div>`
            }
          </div>
        </section>
        <section class="mastery-focus-block">
          <div class="mastery-focus-head">
            <strong>Recently completed</strong>
            <span class="mini-pill">${escapeHtml(recentlyCompletedTiles.length || 0)}</span>
          </div>
          <div class="mastery-mini-list">
            ${
              recentlyCompletedTiles.length
                ? recentlyCompletedTiles.map((tile) => renderMiniTile(tile)).join("")
                : `<div class="empty-cardlet"><p>You have not completed anything in this chapter yet.</p></div>`
            }
          </div>
        </section>
      </div>
      <details class="mastery-full-map">
        <summary>
          <strong>See full chapter map</strong>
          <span>${escapeHtml(selectedChapter.lesson_count)} lessons</span>
        </summary>
        <div class="mastery-grid">
          ${masteryTiles
            .map((tile) => {
              const meta = masteryStatusMeta(tile.status);
              return `
                <button class="mastery-tile mastery-${escapeHtml(meta.className)}" data-lesson-id="${escapeHtml(tile.lesson_id)}" type="button">
                  <span class="lesson-tag">${escapeHtml(meta.label)}</span>
                  <strong>${escapeHtml(tile.title)}</strong>
                  <p>Lesson ${escapeHtml(tile.position_in_chapter)}/${escapeHtml(tile.chapter_lesson_total)} · ${escapeHtml(tile.score_label)}</p>
                </button>
              `;
            })
            .join("")}
        </div>
      </details>
    </div>
  `;

  masteryMap.querySelectorAll("[data-mastery-chapter]").forEach((button) => {
    button.addEventListener("click", () => setMasteryChapter(Number(button.dataset.masteryChapter)));
  });
  masteryMap.querySelectorAll("[data-lesson-id]").forEach((button) => {
    button.addEventListener("click", () => runAction("open_lesson", { lesson_id: button.dataset.lessonId }));
  });
}

function renderExamCenter(appState) {
  const center = appState.exam_center;
  if (!center?.modes?.length) {
    examCenter.innerHTML = `
      <div class="empty-cardlet">
        <p>Exam drills will appear after the course loads.</p>
      </div>
    `;
    return;
  }

  examCenter.innerHTML = `
    <div class="exam-center-shell">
      <div class="exam-mode-card exam-mode-card-accent">
        <strong>Weekly Review</strong>
        <p>Build a mixed review from the chapters in your current study plan.</p>
        <button class="secondary-button compact-button" data-inline-dashboard-action="review_session" type="button">
          Start Weekly Review
        </button>
      </div>
      ${center.modes
        .map(
          (mode) => `
            <div class="exam-mode-card ${mode.disabled ? "is-disabled" : ""}">
              <strong>${escapeHtml(mode.title)}</strong>
              <p>${escapeHtml(mode.detail)}</p>
              <button class="secondary-button compact-button" data-exam-mode="${escapeHtml(mode.exam_mode)}" type="button" ${
                mode.disabled ? "disabled" : ""
              }>
                ${escapeHtml(mode.cta_label)}
              </button>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function renderMistakeNotebook(appState) {
  const notebook = appState.mistake_notebook;
  if (!notebook?.items?.length) {
    mistakeNotebook.innerHTML = `
      <div class="empty-cardlet">
        <p>Your mistake notebook is empty right now.</p>
        <p>When you miss quiz questions, the app will save them here with the fix.</p>
      </div>
    `;
    return;
  }

  const taxonomyCounts = notebook.taxonomy_counts || {};
  const taxonomySummary = Object.entries(taxonomyCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([label, count]) => `<span class="mini-pill">${escapeHtml(toTitleCase(label))}: ${escapeHtml(count)}</span>`)
    .join("");

  mistakeNotebook.innerHTML = `
    <div class="mistake-shell">
      <p class="plan-note">${escapeHtml(notebook.unresolved_count)} questions still need review.</p>
      ${taxonomySummary ? `<div class="mistake-taxonomy-row">${taxonomySummary}</div>` : ""}
      <div class="mistake-list">
        ${notebook.items
          .map(
            (item) => `
              <details class="source-chunk">
                <summary>
                  <strong>Chapter ${escapeHtml(item.chapter_number)} · ${escapeHtml(item.lesson_title)}</strong>
                  <span>${escapeHtml(item.selected_option)} -> ${escapeHtml(item.correct_option)}</span>
                </summary>
                <p><strong>Type:</strong> ${escapeHtml(toTitleCase(item.taxonomy || "uncategorized"))}</p>
                <p class="mistake-prompt">${escapeHtml(item.prompt)}</p>
                <p><strong>Why your choice was off:</strong> ${escapeHtml(item.why_selected_wrong)}</p>
                <p><strong>Why the right choice works:</strong> ${escapeHtml(item.why_correct_right)}</p>
                <button
                  class="ghost-button compact-button"
                  data-reopen-lesson-id="${escapeHtml(item.reopen_lesson_id || "")}"
                  type="button"
                  ${item.reopen_lesson_id ? "" : "disabled"}
                >
                  Reopen related lesson
                </button>
              </details>
            `
          )
          .join("")}
      </div>
    </div>
  `;

  mistakeNotebook.querySelectorAll("[data-reopen-lesson-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const lessonId = button.dataset.reopenLessonId;
      if (!lessonId) return;
      runAction("open_lesson", { lesson_id: lessonId });
    });
  });
}

function renderUpNext(appState) {
  const items = appState.up_next || [];
  if (!items.length) {
    upNextPanel.innerHTML = `
      <div class="empty-cardlet">
        <p>No upcoming lessons are available right now.</p>
      </div>
    `;
    return;
  }

  upNextPanel.innerHTML = `
    <div class="weekly-plan-list next-up-list">
      ${items
        .map(
          (lesson, index) => `
            <button class="week-chip next-step-chip ${lesson.current ? "is-current" : ""}" data-lesson-id="${escapeHtml(lesson.lesson_id)}" type="button">
              <div class="week-chip-top">
                <strong>${index === 0 ? "Current lesson" : `Up next ${index}`}</strong>
                <span class="mini-pill">${escapeHtml(lessonLabel(lesson))}</span>
              </div>
              <p>Chapter ${escapeHtml(lesson.chapter_number)} · Lesson ${escapeHtml(lesson.position_in_chapter)}/${escapeHtml(lesson.chapter_lesson_total)}</p>
              <strong>${escapeHtml(lesson.title)}</strong>
            </button>
          `
        )
        .join("")}
    </div>
  `;

  upNextPanel.querySelectorAll("[data-lesson-id]").forEach((button) => {
    button.addEventListener("click", () => runAction("open_lesson", { lesson_id: button.dataset.lessonId }));
  });
}

function renderCourseNavigator(appState) {
  const items = (appState.up_next || []).slice(0, 4);
  const current = appState.current_lesson;
  if (!items.length && !current) {
    courseNavigator.innerHTML = `
      <div class="empty-cardlet">
        <p>Your next lessons will appear here after you start.</p>
      </div>
    `;
    return;
  }

  courseNavigator.innerHTML = `
    <div class="course-nav-shell">
      ${current
        ? `
          <div class="course-nav-current">
            <span class="stat-label">Current Lesson</span>
            <strong>${escapeHtml(current.title)}</strong>
            <p>Chapter ${escapeHtml(current.chapter_number)} · Lesson ${escapeHtml(current.position_in_chapter)}/${escapeHtml(current.chapter_lesson_total)}</p>
          </div>
        `
        : ""}
      <div class="course-nav-list">
        ${items
          .map(
            (lesson, index) => `
              <button class="course-nav-item ${lesson.current ? "is-current" : ""}" data-course-lesson-id="${escapeHtml(lesson.lesson_id)}" type="button">
                <div>
                  <strong>${lesson.current ? "Current" : `Next ${index}`}</strong>
                  <p>Chapter ${escapeHtml(lesson.chapter_number)} · Lesson ${escapeHtml(lesson.position_in_chapter)}/${escapeHtml(lesson.chapter_lesson_total)}</p>
                </div>
                <span class="course-nav-title">${escapeHtml(lesson.title)}</span>
              </button>
            `
          )
          .join("")}
      </div>
    </div>
  `;

  courseNavigator.querySelectorAll("[data-course-lesson-id]").forEach((button) => {
    button.addEventListener("click", () => runAction("open_lesson", { lesson_id: button.dataset.courseLessonId }));
  });
}

function renderCurriculum(appState) {
  if (!state.courseLoaded) {
    if (state.dashboardView === "course") {
      loadCourseData();
    }
    curriculum.innerHTML = `
      <div class="empty-cardlet">
        <p>Loading full course map…</p>
      </div>
    `;
    return;
  }
  curriculum.innerHTML = appState.chapters
    .map((chapter) => {
      const progress = chapter.lesson_count
        ? Math.round((chapter.completed_lesson_count / chapter.lesson_count) * 100)
        : 0;
      return `
        <details class="chapter-group ${chapter.in_scope ? "" : "is-muted"}" ${
          chapter.lessons.some((lesson) => lesson.current) ? "open" : ""
        }>
          <summary>
            <div>
              <span class="chapter-number">Chapter ${chapter.chapter_number}</span>
              <strong>${escapeHtml(chapter.chapter_title)}</strong>
              <p>PDF pages ${chapter.start_pdf_page}-${chapter.end_pdf_page}</p>
            </div>
            <span class="chapter-progress">${chapter.completed_lesson_count}/${chapter.lesson_count}</span>
          </summary>
          <div class="chapter-progress-bar">
            <span style="width:${progress}%"></span>
          </div>
          <div class="lesson-list">
            ${chapter.lessons
              .map(
                (lesson) => `
                  <button class="lesson-pill ${lesson.completed ? "is-complete" : ""} ${
                  lesson.current ? "is-current" : ""
                } ${lesson.in_scope ? "" : "is-out-of-scope"}" data-lesson-id="${escapeHtml(lesson.lesson_id)}" type="button">
                    <span class="lesson-tag">${escapeHtml(lessonLabel(lesson))}</span>
                    <span class="lesson-text">${escapeHtml(lesson.title)}</span>
                    <span class="lesson-meta">Lesson ${lesson.position_in_chapter}/${lesson.chapter_lesson_total}</span>
                  </button>
                `
              )
              .join("")}
          </div>
        </details>
      `;
    })
    .join("");

  curriculum.querySelectorAll("[data-lesson-id]").forEach((button) => {
    button.addEventListener("click", () => runAction("open_lesson", { lesson_id: button.dataset.lessonId }));
  });
}

function renderFlashcardsSummary(appState) {
  const flashcard = appState.next_due_flashcard;
  if (!flashcard) {
    flashcardsPanel.innerHTML = `
      <div class="empty-cardlet">
        <p>No flashcards are due right now.</p>
        <p>Your next lesson will add more cards when you finish it.</p>
      </div>
    `;
    return;
  }

  flashcardsPanel.innerHTML = `
    <div class="flashcard-summary-card">
      <span class="mini-pill">${escapeHtml(appState.flashcards_due_count)} due</span>
      <h3>${escapeHtml(flashcard.lesson_title)}</h3>
      <p class="flashcard-chapter">Chapter ${escapeHtml(flashcard.chapter_number)}: ${escapeHtml(flashcard.chapter_title)}</p>
      <div class="flashcard-summary-preview">${escapeHtml(flashcard.front)}</div>
      <button class="primary-button compact-button" data-dashboard-jump-view="practice" data-dashboard-focus="flashcards" type="button">
        Start flashcard review
      </button>
    </div>
  `;
}

function renderFlashcardReview(appState) {
  const flashcard = appState.next_due_flashcard;
  if (!flashcard) {
    flashcardReviewPanel.innerHTML = `
      <div class="empty-cardlet">
        <p>No flashcards are due right now.</p>
        <p>When more cards are due, this is where you will review them.</p>
      </div>
    `;
    return;
  }

  flashcardReviewPanel.innerHTML = `
    <div class="flashcard-shell">
      <div class="flashcard-review-head">
        <div>
          <p class="eyebrow">Due Now</p>
          <h3>${escapeHtml(flashcard.lesson_title)}</h3>
          <p class="flashcard-chapter">Chapter ${escapeHtml(flashcard.chapter_number)}: ${escapeHtml(flashcard.chapter_title)}</p>
        </div>
        <span class="mini-pill">${escapeHtml(appState.flashcards_due_count)} due</span>
      </div>
      <div class="flashcard-front">${escapeHtml(flashcard.front)}</div>
      <details class="flashcard-answer">
        <summary>Show answer</summary>
        <p>${escapeHtml(flashcard.back)}</p>
      </details>
      <div class="flashcard-actions">
        <button class="ghost-button" data-flashcard-rating="again" data-card-id="${escapeHtml(flashcard.card_id)}" type="button">Again</button>
        <button class="ghost-button" data-flashcard-rating="hard" data-card-id="${escapeHtml(flashcard.card_id)}" type="button">Hard</button>
        <button class="secondary-button" data-flashcard-rating="good" data-card-id="${escapeHtml(flashcard.card_id)}" type="button">Good</button>
        <button class="primary-button" data-flashcard-rating="easy" data-card-id="${escapeHtml(flashcard.card_id)}" type="button">Easy</button>
      </div>
    </div>
  `;
}

function renderMistakeSummary(appState) {
  const notebook = appState.mistake_notebook;
  const firstItem = notebook?.items?.[0];
  if (!notebook?.items?.length) {
    mistakeSummaryPanel.innerHTML = `
      <div class="empty-cardlet">
        <p>You have no active mistakes right now.</p>
        <p>When you miss questions, the full notebook will live in Practice.</p>
      </div>
    `;
    return;
  }

  mistakeSummaryPanel.innerHTML = `
    <div class="mistake-summary-card">
      <span class="mini-pill">${escapeHtml(notebook.unresolved_count)} unresolved</span>
      <h3>${escapeHtml(firstItem.lesson_title)}</h3>
      <p>${escapeHtml(firstItem.prompt)}</p>
      <button class="ghost-button compact-button" data-dashboard-jump-view="practice" data-dashboard-focus="mistakes" type="button">
        Open mistake notebook
      </button>
    </div>
  `;
}

function renderActionButtons(appState) {
  const hasQuiz = currentCardHasQuiz(appState);
  const quizGraded = currentQuizIsGraded(appState);
  const hasCard = Boolean(appState?.last_card);
  const inCompletionMode = Boolean(state.completionScreen);
  const supplementalCard =
    Boolean(appState?.last_card && appState?.current_lesson) &&
    appState.last_card.lesson_id !== appState.current_lesson.lesson_id;
  const buttonConfig = {
    next_lesson: {
      visible: (!hasCard && !inCompletionMode) || supplementalCard,
      className: supplementalCard ? "primary-button" : "primary-button",
      order: 1,
      title: supplementalCard ? "Return To Current Lesson" : "Start The Next Lesson",
      help: supplementalCard ? "Go back to the lesson you are studying" : "Open the next unfinished lesson in order",
    },
    complete_and_continue: {
      visible: hasCard && !inCompletionMode && !supplementalCard,
      className: quizGraded ? "primary-button" : "ghost-button",
      order: quizGraded ? 1 : 3,
      title: "Mark Complete + Continue",
      help: quizGraded ? "Save this lesson and move to the next one" : "Move on now even if you want to return later",
    },
    review_session: {
      visible: true,
      className: inCompletionMode ? "secondary-button" : hasCard ? "ghost-button" : "secondary-button",
      order: hasCard ? 3 : 2,
      title: "Review This Week",
      help: "Mixed review for the chapters in your plan",
    },
    quiz_me: {
      visible: hasCard && !inCompletionMode && !supplementalCard,
      className: !quizGraded ? "secondary-button" : "ghost-button",
      order: 2,
      title: !quizGraded ? "Take The 3-Question Quiz" : "Retake Quiz",
      help: !quizGraded ? "Move into the quiz step for this lesson" : "Build a fresh check for this lesson",
    },
    explain_simpler: {
      visible: false,
      className: "ghost-button",
      title: "Explain This More Simply",
      help: "Same lesson, easier wording",
    },
    another_example: {
      visible: false,
      className: "ghost-button",
      title: "Show Another Example",
      help: "Same lesson, different worked example",
    },
  };

  actionButtonMap.forEach((button, action) => {
    const config = buttonConfig[action];
    if (!config) return;
    button.classList.remove("primary-button", "secondary-button", "ghost-button", "hidden");
    if (!config.visible) {
      button.classList.add("hidden");
      return;
    }
    button.classList.add(config.className);
    button.style.order = String(config.order || 0);
    button.innerHTML = `
      <span class="action-button-title">${escapeHtml(config.title)}</span>
      <span class="action-button-help">${escapeHtml(config.help)}</span>
    `;
    button.title = `${config.title} — ${config.help}`;
  });
}

function renderActionGuide(appState) {
  if (!actionGuide) return;
  const quizGraded = currentQuizIsGraded(appState);
  const current = appState?.current_lesson;
  const guideItems = state.completionScreen
    ? ["Lesson complete. Start the saved next lesson when you are ready."]
    : current
      ? [
          `Today: Chapter ${current.chapter_number}, lesson ${current.position_in_chapter} of ${current.chapter_lesson_total}.`,
          quizGraded ? "Quiz done. Finish the lesson or review misses." : "Best flow: lesson, example, quiz.",
        ]
      : ["Open the next lesson and the dashboard will keep the path simple."];

  if (!guideItems.length) {
    actionGuide.innerHTML = "";
    actionGuide.classList.add("hidden");
    return;
  }

  actionGuide.classList.remove("hidden");
  actionGuide.innerHTML = guideItems
    .map((item) => `<span class="action-guide-chip">${escapeHtml(item)}</span>`)
    .join("");
}

function renderDashboardView() {
  document.querySelectorAll("[data-dashboard-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.dashboardPanel !== state.dashboardView);
    panel.classList.toggle("is-active", panel.dataset.dashboardPanel === state.dashboardView);
  });
  dashboardViewTabs.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.dashboardView === state.dashboardView);
  });
}

function renderLayoutMode(appState) {
  const hasActiveLesson = Boolean(appState?.last_card);
  document.body.classList.toggle("has-active-lesson", hasActiveLesson);
  document.body.classList.toggle("page-mode-dashboard", state.pageMode === "dashboard");
  document.body.classList.toggle("page-mode-study", state.pageMode === "study");
  if (dashboardShell) {
    dashboardShell.classList.toggle("hidden", state.pageMode === "study");
  }
  if (lessonScreen) {
    lessonScreen.classList.toggle("hidden", state.pageMode !== "study");
  }
  renderDashboardView();
  if (askDetails) {
    askDetails.open = state.pageMode === "dashboard" ? (state.askPanelOpen ?? false) : false;
  }
  if (nextUpDetails) {
    nextUpDetails.open = state.pageMode === "dashboard" ? (state.nextUpPanelOpen ?? false) : false;
  }
}

function lessonQuickActions(card, activeStage) {
  const quizDone = Boolean(card?.quiz_feedback?.question_feedback?.length);
  let helperAction = "complete_and_continue";
  let helperTitle = "Mark Done + Next";
  let helperHelp = "Move on now and come back later if needed";

  if (quizDone) {
    helperAction = "quiz_me";
    helperTitle = "Retake Quiz";
    helperHelp = "Build a fresh 3-question check";
  }

  return `
    <div class="lesson-quick-actions">
      <button class="primary-button compact-action-button" data-inline-action="${
        quizDone ? "complete_and_continue" : "quiz_me"
      }" type="button">
        <span class="action-button-title">${
          quizDone ? "Mark Done + Next" : "Take The Quiz"
        }</span>
        <span class="action-button-help">${
          quizDone
            ? "Save this lesson and move forward"
            : "Finish this lesson with the 3-question check"
        }</span>
      </button>
      <button class="ghost-button compact-action-button" data-inline-action="${helperAction}" type="button">
        <span class="action-button-title">${escapeHtml(helperTitle)}</span>
        <span class="action-button-help">${escapeHtml(helperHelp)}</span>
      </button>
    </div>
  `;
}

function renderStudyFlow(card, activeStage) {
  const quizDone = Boolean(card?.quiz_feedback?.question_feedback?.length);
  const steps = [
    { id: "learn", label: "Learn", detail: "Read the key ideas" },
    { id: "example", label: "Example", detail: "Watch one worked example" },
    { id: "quiz", label: "Quiz", detail: quizDone ? "Checked and graded" : "3 questions waiting", complete: quizDone },
    { id: "move_on", label: "Move On", detail: "Mark done when ready" },
  ];

  return `
    <div class="study-flow">
      ${steps
        .map(
          (step, index) => `
            <div class="study-flow-step ${step.complete ? "is-complete" : ""} ${
              step.id === activeStage || (step.id === "move_on" && state.completionScreen) ? "is-active" : ""
            }">
              <span class="study-flow-index">${index + 1}</span>
              <div>
                <strong>${escapeHtml(step.label)}</strong>
                <p>${escapeHtml(step.detail)}</p>
              </div>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function renderLessonTabs(card) {
  const activeStage = ensureLessonStage(card);
  const stages = [
    { id: "learn", label: "Lesson", hint: "Core + cards" },
    { id: "example", label: "Example", hint: "Worked steps" },
    { id: "quiz", label: "Quiz", hint: "3 questions" },
    { id: "review", label: "Review", hint: "Recap" },
    { id: "evidence", label: "Evidence", hint: "Book support" },
  ];
  return `
    <nav class="lesson-tabs" aria-label="Lesson sections">
      ${stages
        .map(
          (stage) => `
            <button
              class="lesson-tab ${activeStage === stage.id ? "is-active" : ""}"
              data-lesson-stage="${stage.id}"
              type="button"
            >
              <span class="lesson-tab-title">${escapeHtml(stage.label)}</span>
              <span class="lesson-tab-hint">${escapeHtml(stage.hint)}</span>
            </button>
          `
        )
        .join("")}
    </nav>
  `;
}

function renderStageCoachPanel(card, activeStage, isQuizGraded) {
  const stageConfigs = {
    learn: {
      eyebrow: "Step 1",
      title: "Read the lesson, then move straight into the example.",
      detail: "The left side is your core explanation. Keep the flashcards open while you read so the key rules stick sooner.",
      primary: { type: "stage", value: "example", className: "primary-button", title: "Open Worked Example", help: "See the rule in action next" },
      supports: [
        { type: "action", value: "quiz_me", className: "secondary-button", title: "Take The 3-Question Quiz", help: "Skip ahead if you already get it" },
        { type: "action", value: "complete_and_continue", className: "ghost-button", title: "Mark Complete + Continue", help: "Move on and come back later if needed" },
        { type: "action", value: "explain_simpler", className: "ghost-button", title: "Need It Simpler?", help: "Rewrite this lesson in easier words" },
      ],
      checklist: [
        "Read the explanation on the left first.",
        "Look at the flashcards while the lesson is still fresh.",
        "When the rule makes sense, open the worked example.",
      ],
    },
    example: {
      eyebrow: "Step 2",
      title: "Use the example to connect the rule to a real situation.",
      detail: "The example is where abstract tax language starts to feel concrete. If this one still feels thin, generate another one before you quiz.",
      primary: { type: "action", value: "quiz_me", className: "primary-button", title: "Take The 3-Question Quiz", help: "Check whether the rule really clicked" },
      supports: [
        { type: "action", value: "another_example", className: "secondary-button", title: "Show Another Example", help: "Swap in a fresh worked example" },
        { type: "action", value: "complete_and_continue", className: "ghost-button", title: "Mark Complete + Continue", help: "Move on if you already feel solid" },
      ],
      checklist: [
        "Follow each example step in order.",
        "Match the example back to the lesson bullets on the left.",
        "If you can explain why each step happens, you're ready to quiz.",
      ],
    },
    quiz: isQuizGraded
      ? {
          eyebrow: "Step 3",
          title: "Your quiz is graded. Use the feedback to lock in the rule.",
          detail: "Focus on the wrong or partial answers first. The goal now is not just the score, but understanding why the textbook-supported answer wins.",
          primary: { type: "action", value: "complete_and_continue", className: "primary-button", title: "Mark Complete + Continue", help: "Save this lesson and open the next one" },
          supports: [
            { type: "stage", value: "review", className: "secondary-button", title: "Open Review Recap", help: "See the summary before moving on" },
            { type: "action", value: "quiz_me", className: "ghost-button", title: "Retake Quiz", help: "Generate a fresh 3-question check" },
          ],
          checklist: [
            "Read why your choice was wrong before moving on.",
            "Notice the exact wording that made the right answer right.",
            "If needed, jump back to Lesson or Evidence before continuing.",
          ],
        }
      : {
          eyebrow: "Step 3",
          title: "Finish the quiz one question at a time, then grade it.",
          detail: "Stay in this panel until all 3 questions are answered. If a question feels fuzzy, jump back to the lesson without losing your place.",
          primary: null,
          supports: [
            { type: "stage", value: "learn", className: "secondary-button", title: "Back To Lesson", help: "Re-read the explanation on the left" },
            { type: "action", value: "complete_and_continue", className: "ghost-button", title: "Mark Complete + Continue", help: "Skip ahead for now if you need to" },
          ],
          checklist: [
            "Answer all 3 questions before grading.",
            "Use the hint line if a question feels vague.",
            "The feedback will show exactly why an answer was right or wrong.",
          ],
        },
    review: {
      eyebrow: "Step 4",
      title: "Use the recap to decide whether this lesson is done.",
      detail: "The review panel is your last checkpoint before you move on. If something still feels slippery, reopen the lesson or evidence instead of guessing.",
      primary: { type: "action", value: "complete_and_continue", className: "primary-button", title: "Mark Complete + Continue", help: "Save the win and move to the next concept" },
      supports: [
        { type: "stage", value: "evidence", className: "secondary-button", title: "Check Book Evidence", help: "See exactly where the book supported this lesson" },
        { type: "stage", value: "learn", className: "ghost-button", title: "Back To Lesson", help: "Re-read the main explanation" },
      ],
      checklist: [
        "Say the rule out loud in your own words.",
        "Use the memory trick only after the real meaning makes sense.",
        "If you still hesitate, reopen the evidence or retake the quiz.",
      ],
    },
    evidence: {
      eyebrow: "Support",
      title: "Use the textbook proof when you want the exact grounding.",
      detail: "This is the safety net. Use it to verify wording, numbers, or structure from the book, then return to the main lesson flow.",
      primary: { type: "stage", value: "review", className: "primary-button", title: "Back To Review", help: "Return to the recap and decide whether to move on" },
      supports: [
        { type: "stage", value: "learn", className: "secondary-button", title: "Back To Lesson", help: "Return to the teaching explanation" },
        { type: "action", value: "quiz_me", className: "ghost-button", title: isQuizGraded ? "Retake Quiz" : "Open Quiz", help: isQuizGraded ? "Try a fresh check after reviewing the source" : "Move into the quiz when you're ready" },
      ],
      checklist: [
        "Use the grounding chunks when you want quick book support.",
        "Open Book Evidence when you need the extracted source text.",
        "Return to the study flow once the confusion is resolved.",
      ],
    },
  };

  const config = stageConfigs[activeStage] || stageConfigs.learn;
  const renderCoachButton = (button) => {
    const actionAttr =
      button.type === "stage"
        ? `data-lesson-stage-jump="${escapeHtml(button.value)}"`
        : `data-inline-action="${escapeHtml(button.value)}"`;
    return `
      <button class="${escapeHtml(button.className)}" ${actionAttr} type="button">
        <span class="action-button-title">${escapeHtml(button.title)}</span>
        <span class="action-button-help">${escapeHtml(button.help)}</span>
      </button>
    `;
  };

  return `
    <section class="section-block lesson-focus-panel">
      <div class="panel-header">
        <div>
          <h3>Do This Now</h3>
          <p>${escapeHtml(config.eyebrow)} in the lesson flow</p>
        </div>
        <span class="mini-pill">${escapeHtml(toTitleCase(activeStage))}</span>
      </div>
      <div class="lesson-focus-callout lesson-focus-${escapeHtml(activeStage)}">
        <strong>${escapeHtml(config.title)}</strong>
        <p>${escapeHtml(config.detail)}</p>
      </div>
      ${
        config.primary
          ? `<div class="lesson-focus-primary">${renderCoachButton(config.primary)}</div>`
          : ""
      }
      ${
        config.supports?.length
          ? `
            <div class="lesson-focus-actions">
              ${config.supports.map((button) => renderCoachButton(button)).join("")}
            </div>
          `
          : ""
      }
      <ul class="lesson-focus-checklist">
        ${config.checklist.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
  `;
}

function renderChapterCheckpointRail(appState, card) {
  const chapter = appState?.chapters?.find((item) => Number(item.chapter_number) === Number(card.chapter_number));
  if (!chapter?.lessons?.length) return "";

  const currentPosition = Number(card.coverage?.position_in_chapter || 1);
  const chapterProgress = chapter.lesson_count
    ? Math.round((chapter.completed_lesson_count / chapter.lesson_count) * 100)
    : 0;
  const currentIndex = Math.max(0, currentPosition - 1);
  const focusStart = Math.max(0, currentIndex - 2);
  const focusEnd = Math.min(chapter.lessons.length, currentIndex + 4);
  const focusLessons = chapter.lessons.slice(focusStart, focusEnd);
  const hiddenCount = Math.max(0, chapter.lessons.length - focusLessons.length);

  const renderCheckpointItems = (lessons) =>
    lessons
      .map((lesson) => {
        const position = Number(lesson.position_in_chapter || 0);
        const statusText = lesson.current ? "Now" : lesson.completed ? "Done" : position === currentPosition + 1 ? "Next" : "Open";
        const itemClasses = [
          "checkpoint-item",
          lesson.current ? "is-current" : "",
          lesson.completed ? "is-complete" : "",
          !lesson.in_scope ? "is-muted" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return `
          <button class="${itemClasses}" data-checkpoint-lesson-id="${escapeHtml(lesson.lesson_id)}" type="button">
            <span class="checkpoint-status">${escapeHtml(statusText)}</span>
            <div class="checkpoint-copy">
              <strong>Lesson ${escapeHtml(position)}/${escapeHtml(lesson.chapter_lesson_total)}</strong>
              <p>${escapeHtml(lesson.title)}</p>
            </div>
            <span class="checkpoint-kind">${escapeHtml(lessonLabel(lesson))}</span>
          </button>
        `;
      })
      .join("");

  return `
    <section class="section-block checkpoint-panel">
      <div class="panel-header">
        <div>
          <h3>Chapter Checkpoints</h3>
          <p>Use this rail to jump around Chapter ${escapeHtml(chapter.chapter_number)} without losing your place.</p>
        </div>
        <span class="mini-pill">${escapeHtml(chapterProgress)}%</span>
      </div>
      <div class="chapter-progress-bar checkpoint-progress-bar">
        <span style="width:${chapterProgress}%"></span>
      </div>
      <div class="checkpoint-summary">
        <strong>Nearby lessons</strong>
        <p>Showing the lessons around where you are right now.</p>
      </div>
      <div class="checkpoint-list" role="list">
        ${renderCheckpointItems(focusLessons)}
      </div>
      ${
        hiddenCount
          ? `
            <details class="checkpoint-more">
              <summary>
                <strong>Show full chapter rail</strong>
                <span>${escapeHtml(hiddenCount)} more lessons</span>
              </summary>
              <div class="checkpoint-list checkpoint-list-full" role="list">
                ${renderCheckpointItems(chapter.lessons)}
              </div>
            </details>
          `
          : ""
      }
    </section>
  `;
}

function renderLearnPanel(card) {
  return `
    <section class="section-block stage-panel">
      <div class="panel-header">
        <div>
          <h3>What You Need To Learn</h3>
          <p>Read this once before you move to the example.</p>
        </div>
      </div>
      <div class="lesson-intro stage-intro">${escapeHtml(card.intro)}</div>
      <ul class="bullet-list">
        ${card.teaching_points.map((point) => `<li>${escapeHtml(point)}</li>`).join("")}
      </ul>
      ${renderInlineLessonFlashcards(card)}
    </section>
  `;
}

function renderInlineLessonFlashcards(card) {
  if (!card.flashcards?.length) return "";
  const currentIndex = ensureInlineFlashcardState(card);
  const flashcard = card.flashcards[currentIndex];
  return `
    <section class="section-block accent inline-flashcard-panel">
      <div class="panel-header">
        <div>
          <h3>Flashcards From This Lesson</h3>
          <p>Keep one card open while you study so the key idea sticks without crowding the page.</p>
        </div>
        <span class="mini-pill">${escapeHtml(currentIndex + 1)}/${escapeHtml(card.flashcards.length)}</span>
      </div>
      <article class="inline-flashcard-card inline-flashcard-card-single" data-motion="${escapeHtml(state.inlineFlashcardMotion || "next")}">
        <div class="inline-flashcard-face">
          <span class="inline-flashcard-label">Card ${escapeHtml(currentIndex + 1)}</span>
          <strong>${escapeHtml(flashcard.front)}</strong>
        </div>
        <div class="inline-flashcard-divider" aria-hidden="true"></div>
        <div class="inline-flashcard-face inline-flashcard-back">
          <span class="inline-flashcard-label">Answer</span>
          <p>${escapeHtml(flashcard.back)}</p>
        </div>
      </article>
      <div class="inline-flashcard-controls">
        <button class="ghost-button compact-button" data-inline-flashcard-nav="prev" type="button" ${currentIndex === 0 ? "disabled" : ""}>
          Previous card
        </button>
        <button class="ghost-button compact-button" data-inline-flashcard-nav="next" type="button" ${currentIndex === card.flashcards.length - 1 ? "disabled" : ""}>
          Next card
        </button>
      </div>
    </section>
  `;
}

function renderExamplePanel(card) {
  return `
    <section class="section-block stage-panel">
      <div class="panel-header">
        <div>
          <h3>Worked Example</h3>
          <p>Use this to see the rule in motion.</p>
        </div>
      </div>
      <ol class="number-list">
        ${card.worked_example.map((point) => `<li>${escapeHtml(point)}</li>`).join("")}
      </ol>
      <div class="review-callout">
        <strong>Memory Trick</strong>
        <p>${escapeHtml(card.memory_trick)}</p>
      </div>
    </section>
  `;
}

function renderQuizSummary(feedback) {
  if (!feedback) return "";
  const totalQuestions = feedback.total_questions || feedback.question_feedback.length || 0;
  const correctCount =
    feedback.correct_count ||
    feedback.question_feedback.filter((item) => String(item.verdict).toLowerCase() === "correct").length;
  const summaryTone =
    correctCount === totalQuestions
      ? "perfect"
      : correctCount >= Math.max(1, totalQuestions - 1)
        ? "strong"
        : correctCount > 0
          ? "partial"
          : "retry";

  return `
    <section class="section-block quiz-feedback quiz-feedback-${summaryTone}">
      <div class="quiz-feedback-hero">
        <div class="quiz-score-orb quiz-score-${summaryTone}">
          <strong>${escapeHtml(correctCount)}/${escapeHtml(totalQuestions)}</strong>
          <span>${correctCount === totalQuestions ? "RIGHT" : "SCORE"}</span>
        </div>
        <div class="quiz-feedback-copy">
          <h3>Your Quiz Feedback</h3>
          <p>${escapeHtml(feedback.overall_summary)}</p>
        </div>
      </div>
      <p class="feedback-next-step">${escapeHtml(feedback.next_step)}</p>
    </section>
  `;
}

function renderQuizQuestionPanel(card, question, questionIndex, selectedAnswer, feedbackItem, isQuizGraded) {
  return `
    <fieldset class="quiz-question-block ${feedbackItem ? "is-graded" : ""}">
      <div class="quiz-question-shell">
        <span class="quiz-label">Question ${questionIndex + 1}</span>
        <strong class="quiz-question">${escapeHtml(question.prompt)}</strong>
        <span class="quiz-hint">Hint: ${escapeHtml(question.hint)}</span>
      </div>
      <div class="quiz-options quiz-options-single">
        ${question.options
          .map((option) => {
            const isSelected = selectedAnswer === option.label;
            const isCorrect = feedbackItem && feedbackItem.correct_option === option.label;
            const optionClasses = [
              "quiz-option",
              isSelected ? "is-selected" : "",
              isCorrect ? "is-correct" : "",
              feedbackItem && isSelected && !isCorrect ? "is-incorrect" : "",
            ]
              .filter(Boolean)
              .join(" ");
            const stateLabel = isCorrect
              ? `<span class="quiz-option-state option-state-correct">Correct answer</span>`
              : feedbackItem && isSelected
                ? `<span class="quiz-option-state option-state-selected">Your choice</span>`
                : "";
            return `
              <label class="${optionClasses}">
                <input type="radio" name="answer-${questionIndex}" value="${escapeHtml(option.label)}" ${
                  isSelected ? "checked" : ""
                } ${isQuizGraded ? "disabled" : ""}>
                <span class="quiz-option-badge">${escapeHtml(option.label)}</span>
                <span class="quiz-option-text">${escapeHtml(option.text)}</span>
                ${stateLabel}
              </label>
            `;
          })
          .join("")}
      </div>
    </fieldset>
  `;
}

function renderCurrentQuestionFeedback(feedbackItem) {
  if (!feedbackItem) return "";
  const verdictClass = escapeHtml(String(feedbackItem.verdict || "").toLowerCase().replaceAll(" ", "-"));
  const mark =
    verdictClass === "correct" ? "✓" : verdictClass === "partly-correct" ? "△" : "✕";
  const selectedExplanation =
    verdictClass === "correct"
      ? "You matched the rule the textbook was testing."
      : feedbackItem.why_selected_wrong || "This choice does not fit the rule the lesson is testing.";
  const correctExplanation =
    feedbackItem.why_correct_right || feedbackItem.ideal_answer || "Use the lesson explanation and source support to restate the rule.";

  return `
    <div class="feedback-chip verdict-${verdictClass}">
      <div class="feedback-chip-top">
        <div class="feedback-mark verdict-${verdictClass}">${mark}</div>
        <div>
          <strong>Question ${feedbackItem.question_number}: ${escapeHtml(feedbackItem.verdict)}</strong>
          <p class="feedback-answer-line">You chose ${escapeHtml(feedbackItem.selected_option || "?")} · Correct answer ${escapeHtml(feedbackItem.correct_option || "?")}</p>
        </div>
      </div>
      <div class="feedback-answer-grid">
        <section class="feedback-answer-card ${verdictClass === "correct" ? "is-correct" : "is-selected"}">
          <span class="feedback-answer-label">${verdictClass === "correct" ? "Your answer was right" : "Why your choice missed"}</span>
          <strong>${escapeHtml(feedbackItem.selected_option || "?")}</strong>
          <p>${escapeHtml(selectedExplanation)}</p>
        </section>
        <section class="feedback-answer-card is-correct">
          <span class="feedback-answer-label">Why the correct answer wins</span>
          <strong>${escapeHtml(feedbackItem.correct_option || "?")}</strong>
          <p>${escapeHtml(correctExplanation)}</p>
        </section>
      </div>
      <div class="feedback-fix-block">
        <strong>What to remember next time</strong>
        <p>${escapeHtml(feedbackItem.explanation)}</p>
        <p><strong>Study answer:</strong> ${escapeHtml(feedbackItem.ideal_answer)}</p>
      </div>
    </div>
  `;
}

function renderQuizPanel(card, isQuizGraded) {
  const { draftAnswers, questionIndex } = ensureQuizState(card);
  const totalQuestions = card.quiz_questions.length;
  const answeredCount = draftAnswers.filter(Boolean).length;
  const question = card.quiz_questions[questionIndex];
  const feedbackByQuestion = new Map(
    (card.quiz_feedback?.question_feedback || []).map((item) => [item.question_number, item])
  );
  const feedbackItem = feedbackByQuestion.get(questionIndex + 1) || null;
  const progressPercent = Math.round(((questionIndex + 1) / totalQuestions) * 100);

  return `
    <section class="section-block stage-panel quiz-stage-panel ${card.card_type === "exam" ? "is-exam" : "is-lesson-quiz"}">
      <div class="panel-header">
        <div>
          <h3>${card.card_type === "exam" ? "Exam Questions" : `${totalQuestions}-Question Check`}</h3>
          <p>${isQuizGraded ? "Your results are ready. Review one question at a time." : card.card_type === "exam" ? "Work through the exam one question at a time." : "Answer one question at a time, then submit when you reach the end."}</p>
        </div>
        ${
          card.exam_meta?.timed_minutes
            ? `<span class="mini-pill">${escapeHtml(card.exam_meta.timed_minutes)} min drill</span>`
            : ""
        }
      </div>
      ${isQuizGraded ? renderQuizSummary(card.quiz_feedback) : ""}
      <div class="quiz-flow-card">
        <div class="quiz-command-bar">
          <span class="quiz-progress-pill">Question ${escapeHtml(questionIndex + 1)} of ${escapeHtml(totalQuestions)}</span>
          <strong>${card.card_type === "exam" ? "Exam mode" : "Lesson quiz"}</strong>
          <p>${isQuizGraded ? "Use the jump chips to review each question." : "Pick one answer. The quiz moves to the next question automatically."}</p>
        </div>
        <div class="quiz-flow-header">
          <div>
            <strong>${isQuizGraded ? "Review your answers" : `${answeredCount} of ${totalQuestions} answered`}</strong>
            <p class="quiz-flow-subcopy">${isQuizGraded ? "Open each question to see exactly why it was right or wrong." : "Stay in the same flow and keep moving until the grade screen."}</p>
          </div>
          <div class="quiz-flow-progress">
            <div class="mini-progress-bar">
              <span style="width:${progressPercent}%"></span>
            </div>
          </div>
        </div>

        <div class="quiz-jump-list">
          ${card.quiz_questions
            .map((_, index) => {
              const jumpFeedback = feedbackByQuestion.get(index + 1);
              const jumpClass = [
                "quiz-jump-chip",
                index === questionIndex ? "is-active" : "",
                draftAnswers[index] ? "is-answered" : "",
                jumpFeedback ? `is-${String(jumpFeedback.verdict || "").toLowerCase().replaceAll(" ", "-")}` : "",
              ]
                .filter(Boolean)
                .join(" ");
              return `
                <button class="${jumpClass}" data-quiz-jump="${index}" type="button">
                  Q${index + 1}
                </button>
              `;
            })
            .join("")}
        </div>

        <form id="quizForm" class="quiz-form ${isQuizGraded ? "is-graded" : ""}">
          ${renderQuizQuestionPanel(card, question, questionIndex, draftAnswers[questionIndex], feedbackItem, isQuizGraded)}
          <div class="quiz-nav-actions">
            <button class="ghost-button compact-panel-button" data-quiz-nav="prev" type="button" ${
              questionIndex === 0 ? "disabled" : ""
            }>
              <span class="action-button-title">Previous</span>
              <span class="action-button-help">Go back one question</span>
            </button>
            ${
              questionIndex < totalQuestions - 1
                ? `
                  <button class="secondary-button compact-panel-button" data-quiz-nav="next" type="button">
                    <span class="action-button-title">Next Question</span>
                    <span class="action-button-help">Move to question ${questionIndex + 2}</span>
                  </button>
                `
                : `
                  <button class="primary-button compact-panel-button" type="submit" ${isQuizGraded ? "disabled" : ""}>
                    <span class="action-button-title">${isQuizGraded ? "Quiz Graded" : "Grade My Answers"}</span>
                    <span class="action-button-help">${
                      isQuizGraded
                        ? "Use Retake Quiz if you want a fresh set"
                        : answeredCount === totalQuestions
                          ? "Submit all 3 answers for grading"
                          : `Answer the remaining ${totalQuestions - answeredCount}`
                    }</span>
                  </button>
                `
            }
          </div>
        </form>
      </div>
      ${renderCurrentQuestionFeedback(feedbackItem)}
    </section>
  `;
}

function renderReviewPanel(card) {
  const cumulativeQuiz = Array.isArray(card.cumulative_mini_quiz) ? card.cumulative_mini_quiz : [];
  return `
    <section class="section-block stage-panel">
      <div class="panel-header">
        <div>
          <h3>Review + Retain</h3>
          <p>Use this as your quick recap before you move on.</p>
        </div>
      </div>
      <div class="section-grid">
        <section class="section-block accent nested-panel">
          <h3>Memory Trick</h3>
          <p>${escapeHtml(card.memory_trick)}</p>
        </section>
        <section class="section-block accent nested-panel">
          <h3>What To Do Next</h3>
          <p>${escapeHtml(card.next_step)}</p>
        </section>
      </div>
      <div class="review-inline-note">
        <strong>Flashcards are already open in the Lesson tab.</strong>
        <p>Use them while you read the explanation, then come back here when you want a quick recap.</p>
      </div>
      ${
        cumulativeQuiz.length
          ? `
            <section class="section-block accent nested-panel">
              <h3>2-Question Cumulative Check</h3>
              <p>Quickly revisit prior lessons before you move on.</p>
              <ol class="number-list">
                ${cumulativeQuiz
                  .map(
                    (item) => `
                      <li>
                        <strong>${escapeHtml(item.prompt)}</strong>
                        <p><strong>Best answer:</strong> ${escapeHtml(item.correct_option)} · ${escapeHtml(item.study_answer)}</p>
                      </li>
                    `
                  )
                  .join("")}
              </ol>
            </section>
          `
          : ""
      }
    </section>
  `;
}

function renderEvidencePanel(card) {
  return `
    <section class="section-block stage-panel">
      <div class="panel-header">
        <div>
          <h3>Where This Came From</h3>
          <p>Use this when you want to see the textbook support for the lesson.</p>
        </div>
      </div>
      <details class="lesson-collapsible">
        <summary>
          <h3>Grounding Chunks</h3>
          <span class="collapsible-state" aria-hidden="true"></span>
        </summary>
        <div class="citation-list compact-stack">
          ${card.citations
            .map(
              (citation) => `
                <div class="citation-chip">
                  <strong>Chunk ${escapeHtml(citation.chunk_id)}</strong>
                  <span>Chapter ${escapeHtml(citation.chapter_number)}, PDF pp. ${escapeHtml(citation.pages)}</span>
                  <p>${escapeHtml(citation.why_this_chunk)}</p>
                </div>
              `
            )
            .join("")}
        </div>
      </details>
      <details class="lesson-collapsible">
        <summary>
          <h3>Book Evidence</h3>
          <span class="collapsible-state" aria-hidden="true"></span>
        </summary>
        <div class="source-stack compact-stack">
          ${card.source_chunks
            .map(
              (chunk) => `
                <details class="source-chunk">
                  <summary>
                    <strong>${escapeHtml(chunk.chunk_id)}</strong>
                    <span>Chapter ${escapeHtml(chunk.chapter_number)}, PDF pp. ${escapeHtml(chunk.pages)}</span>
                  </summary>
                  <p class="source-headings">${escapeHtml((chunk.headings || []).join(" • "))}</p>
                  <pre>${escapeHtml(chunk.preview)}</pre>
                </details>
              `
            )
            .join("")}
        </div>
      </details>
    </section>
  `;
}

function renderCompletionScreen() {
  const completion = state.completionScreen;
  if (!completion) return "";
  return `
    <article class="completion-screen">
      <div class="completion-celebration" aria-hidden="true">
        <span></span>
        <span></span>
        <span></span>
      </div>
      <p class="eyebrow">Lesson complete</p>
      <div class="completion-header">
        <div class="completion-orb">${escapeHtml(completion.scoreLabel)}</div>
        <div>
          <h2>${escapeHtml(completion.completedTitle)}</h2>
          <p>${escapeHtml(completion.summary)}</p>
        </div>
      </div>
      <div class="completion-grid">
        <div class="completion-panel">
          <span class="stat-label">Finished</span>
          <strong>Chapter ${escapeHtml(completion.chapterNumber)}</strong>
          <p>${escapeHtml(completion.chapterTitle)}</p>
        </div>
        <div class="completion-panel">
          <span class="stat-label">Next up</span>
          <strong>${escapeHtml(completion.nextLessonTitle)}</strong>
          <p>${escapeHtml(completion.nextLessonMeta)}</p>
        </div>
      </div>
      <div class="completion-progress">
        <div class="top-progress-meter-copy">
          <span>Chapter progress after this lesson</span>
          <strong>${escapeHtml(completion.chapterPercent)}%</strong>
        </div>
        <div class="mini-progress-bar mini-progress-bar-green">
          <span style="width:${completion.chapterPercent}%"></span>
        </div>
      </div>
      <div class="completion-actions">
        <button class="primary-button" data-completion-action="resume-next" type="button">
          <span class="action-button-title">Start Next Lesson</span>
          <span class="action-button-help">Open the next lesson that is already saved</span>
        </button>
        <button class="ghost-button" data-completion-action="course" type="button">
          <span class="action-button-title">See The Course Map</span>
          <span class="action-button-help">Jump back to your chapter roadmap</span>
        </button>
      </div>
    </article>
  `;
}

function renderStudyActionRow(card, activeStage, isQuizGraded) {
  let primaryAction = null;
  let secondaryAction = null;

  if (activeStage === "learn") {
    primaryAction = {
      mode: "stage",
      value: "example",
      title: "Next: Open Worked Example",
      help: "See the rule in motion before you quiz.",
    };
    secondaryAction = {
      mode: "action",
      value: "explain_simpler",
      title: "Need simpler wording?",
    };
  } else if (activeStage === "example") {
    primaryAction = {
      mode: "action",
      value: "quiz_me",
      title: "Next: Take The Quiz",
      help: "Check the lesson with 3 questions.",
    };
    secondaryAction = {
      mode: "action",
      value: "another_example",
      title: "Show one more example",
    };
  } else if (activeStage === "quiz" && isQuizGraded) {
    primaryAction = {
      mode: "action",
      value: "complete_and_continue",
      title: "Next: Mark Done + Continue",
      help: "Save this lesson and move forward.",
    };
    secondaryAction = {
      mode: "action",
      value: "quiz_me",
      title: "Retake this quiz",
    };
  } else if (activeStage === "review") {
    primaryAction = {
      mode: "action",
      value: "complete_and_continue",
      title: "Next: Finish This Lesson",
      help: "Save it and open the next lesson.",
    };
  } else if (activeStage === "evidence") {
    primaryAction = {
      mode: "stage",
      value: "learn",
      title: "Back To Lesson",
      help: "Return to the teaching view.",
    };
  }

  if (!primaryAction) return "";

  const primaryAttr =
    primaryAction.mode === "stage"
      ? `data-lesson-stage-jump="${escapeHtml(primaryAction.value)}"`
      : `data-inline-action="${escapeHtml(primaryAction.value)}"`;
  const secondaryMarkup = secondaryAction
    ? `
        <button
          class="study-secondary-link"
          ${
            secondaryAction.mode === "stage"
              ? `data-lesson-stage-jump="${escapeHtml(secondaryAction.value)}"`
              : `data-inline-action="${escapeHtml(secondaryAction.value)}"`
          }
          type="button"
        >
          ${escapeHtml(secondaryAction.title)}
        </button>
      `
    : "";

  return `
    <section class="study-next-panel">
      <div class="study-next-copy">
        <span class="eyebrow">Next step</span>
        <strong>${escapeHtml(primaryAction.title)}</strong>
        <p>${escapeHtml(primaryAction.help || "")}</p>
      </div>
      <div class="study-next-actions">
        <button class="primary-button compact-action-button study-next-button" ${primaryAttr} type="button">
          <span class="action-button-title">${escapeHtml(primaryAction.title)}</span>
          <span class="action-button-help">${escapeHtml(primaryAction.help || "")}</span>
        </button>
        ${secondaryMarkup}
      </div>
    </section>
  `;
}

function renderLessonWorkspace(appState, card, activeStage, isQuizGraded) {
  let sideStagePanel = "";
  if (activeStage === "example") sideStagePanel = renderExamplePanel(card);
  if (activeStage === "quiz") sideStagePanel = renderQuizPanel(card, isQuizGraded);
  if (activeStage === "review") sideStagePanel = renderReviewPanel(card);
  if (activeStage === "evidence") sideStagePanel = renderEvidencePanel(card);

  return `
    <div class="lesson-workspace">
      <div class="lesson-main-column">
        ${renderLearnPanel(card)}
      </div>
      <aside class="lesson-side-column">
        ${renderLessonTabs(card)}
        ${renderStageCoachPanel(card, activeStage, isQuizGraded)}
        ${sideStagePanel}
        ${renderChapterCheckpointRail(appState, card)}
      </aside>
    </div>
  `;
}

function renderCard(card) {
  if (!card) {
    lessonCard.innerHTML = `
      <div class="empty-state">
        <h3>Ready to start</h3>
        <p>Press <strong>Teach Me The Next Thing</strong> and the app will guide the first lesson, make flashcards, and generate a 3-question quiz you can submit.</p>
      </div>
    `;
    return;
  }

  if (state.completionScreen) {
    lessonCard.innerHTML = renderCompletionScreen();
    return;
  }

  const isQuizGraded = Boolean(card.quiz_feedback?.question_feedback?.length);
  const position = Number(card.coverage?.position_in_chapter || 1);
  const total = Number(card.coverage?.chapter_lesson_total || 1);
  const progressPercent = total ? Math.max(4, Math.round((position / total) * 100)) : 0;
  const sourcePages = card.citations?.[0]?.pages || "Textbook grounded";
  const lessonKindLabel = toTitleCase(card.lesson_kind);
  const integrityBadge = state.data?.current_lesson?.completion_quality?.label || "In progress";
  const providerMode = card.provider_mode || "fallback";
  const generationBackend = card.generation_backend || "unknown";
  const modelBadge =
    generationBackend === "opencode"
      ? "Model: OpenCode"
      : generationBackend === "moonshot"
        ? "Model: Moonshot"
        : generationBackend === "codex"
          ? "Model: Codex"
          : "Model: Local";
  const modeBadge = providerMode === "provider" ? "Mode: provider" : "Mode: fallback";
  ensureLessonStage(card);
  const activeStage = state.lessonStage;
  let stagePanel = renderLearnPanel(card);
  if (activeStage === "example") stagePanel = renderExamplePanel(card);
  if (activeStage === "quiz") stagePanel = renderQuizPanel(card, isQuizGraded);
  if (activeStage === "review") stagePanel = renderReviewPanel(card);
  if (activeStage === "evidence") stagePanel = renderEvidencePanel(card);

  lessonCard.innerHTML = `
    <article class="study-surface card-type-${escapeHtml(card.card_type)}">
      <div class="study-header">
        <button class="ghost-button compact-button study-home-button" data-page-mode="dashboard" type="button">Home</button>
        <div class="study-header-copy">
          <p class="eyebrow">Study Mode</p>
          <strong>Chapter ${escapeHtml(card.chapter_number)} · Lesson ${escapeHtml(position)}/${escapeHtml(total)}</strong>
          <p class="study-meta-line">${escapeHtml(lessonKindLabel)} · PDF pp. ${escapeHtml(sourcePages)}</p>
          <p class="study-meta-line">${escapeHtml(card.exam_day_why || "")}</p>
        </div>
        <div>
          <span class="mini-pill">${escapeHtml(toTitleCase(activeStage))}</span>
          <span class="mini-pill">${escapeHtml(integrityBadge)}</span>
          <span class="mini-pill">${escapeHtml(modelBadge)}</span>
          <span class="mini-pill">${escapeHtml(modeBadge)}</span>
        </div>
      </div>

      <div class="study-title-block">
        <h2>${escapeHtml(card.title)}</h2>
        <p class="lesson-subtitle">${escapeHtml(card.subtitle)}</p>
      </div>

      ${renderLessonTabs(card)}

      ${renderStudyActionRow(card, activeStage, isQuizGraded)}

      ${stagePanel}
    </article>
  `;

  lessonCard.querySelectorAll("[data-lesson-stage]").forEach((button) => {
    button.addEventListener("click", () => setLessonStage(button.dataset.lessonStage));
  });
  lessonCard.querySelectorAll("[data-lesson-stage-jump]").forEach((button) => {
    button.addEventListener("click", () => setLessonStage(button.dataset.lessonStageJump));
  });
  lessonCard.querySelectorAll("[data-inline-flashcard-nav]").forEach((button) => {
    button.addEventListener("click", () => {
      const direction = button.dataset.inlineFlashcardNav === "next" ? 1 : -1;
      setInlineFlashcardIndex(card, state.inlineFlashcardIndex + direction);
    });
  });

  const quizForm = document.getElementById("quizForm");
  if (quizForm) {
    quizForm.addEventListener("change", (event) => {
      const answerInput = event.target.closest("input[type='radio']");
      if (!answerInput) return;
      const questionIndex = Number(String(answerInput.name || "").replace("answer-", ""));
      if (!Number.isNaN(questionIndex)) {
        setQuizDraftAnswer(card, questionIndex, answerInput.value);
        if (!isQuizGraded && questionIndex === state.quizQuestionIndex && questionIndex < card.quiz_questions.length - 1) {
          if (state.quizAdvanceTimer) {
            clearTimeout(state.quizAdvanceTimer);
          }
          state.quizAdvanceTimer = setTimeout(() => {
            state.quizAdvanceTimer = null;
            setQuizQuestionIndex(card, questionIndex + 1);
          }, 220);
        }
        if (!isQuizGraded && questionIndex === card.quiz_questions.length - 1) {
          renderCard(card);
          setStatus("Last question ready. Grade it when you're ready.", "info");
        }
      }
    });

    lessonCard.querySelectorAll("[data-quiz-nav]").forEach((button) => {
      button.addEventListener("click", () => {
        const offset = button.dataset.quizNav === "next" ? 1 : -1;
        setQuizQuestionIndex(card, state.quizQuestionIndex + offset);
      });
    });

    lessonCard.querySelectorAll("[data-quiz-jump]").forEach((button) => {
      button.addEventListener("click", () => {
        setQuizQuestionIndex(card, Number(button.dataset.quizJump));
      });
    });

    quizForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (isQuizGraded) {
        setStatus("This quiz is already graded. Use Quiz Me if you want a fresh check.", "success");
        return;
      }
      const answers = (state.quizDrafts[card.lesson_id] || []).slice(0, card.quiz_questions.length);
      if (answers.some((answer) => !answer)) {
        setStatus("Please answer all 3 quiz questions before submitting.", "error");
        return;
      }
      runAction("submit_quiz", { answers });
    });
  }

  const quizFeedback = lessonCard.querySelector(".quiz-feedback");
  if (quizFeedback) {
    requestAnimationFrame(() => {
      quizFeedback.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }
}

function render(appState) {
  state.data = appState;
  saveLocalStateSnapshot(appState);
  renderLayoutMode(appState);
  renderTopProgress(appState);
  renderResumeCard(appState);
  renderStats(appState);
  renderWeeklyPlan(appState);
  renderTodayAssignment(appState);
  renderUpNext(appState);
  renderCourseNavigator(appState);
  renderMasteryMap(appState);
  renderActionButtons(appState);
  renderActionGuide(appState);
  renderCurriculum(appState);
  renderExamCenter(appState);
  renderFlashcardsSummary(appState);
  renderFlashcardReview(appState);
  renderMistakeSummary(appState);
  renderMistakeNotebook(appState);
  renderCard(appState.last_card);
}

async function fetchBootstrap() {
  const response = await apiFetch("/api/bootstrap");
  const bootstrapState = await response.json();
  const data = await hydrateStateFromLocal(bootstrapState);
  state.courseLoaded = false;
  state.planLoaded = false;
  state.showFullPlan = false;
  render(data);
}

async function loadCourseData() {
  if (!state.data || state.courseLoaded || state.loadingCourse) return;
  state.loadingCourse = true;
  try {
    const response = await apiFetch("/api/course");
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Could not load course map.");
    }
    state.data = {
      ...state.data,
      chapters: data.chapters || [],
      chapter_count: data.chapter_count || state.data.chapter_count,
      lesson_count: data.lesson_count || state.data.lesson_count,
      updated_at: data.updated_at || state.data.updated_at,
    };
    state.courseLoaded = true;
    render(state.data);
  } catch (error) {
    setStatus(error.message || "Could not load the course map.", "error");
  } finally {
    state.loadingCourse = false;
  }
}

async function loadPlanData() {
  if (!state.data || state.planLoaded || state.loadingPlan) return;
  state.loadingPlan = true;
  try {
    const response = await apiFetch("/api/plan");
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Could not load plan details.");
    }
    state.data = {
      ...state.data,
      weekly_goal_lessons: data.weekly_goal_lessons,
      midterm_mode: data.midterm_mode,
      weekly_plan: data.weekly_plan_preview || data.weekly_plan || [],
      weekly_plan_total_count: data.weekly_plan_total_count || (data.weekly_plan || []).length,
      weekly_plan_current_index: data.weekly_plan_current_index || 0,
      weekly_plan_is_preview: true,
      _weekly_plan_preview_cache: data.weekly_plan_preview || data.weekly_plan || [],
      _weekly_plan_full_cache: data.weekly_plan || [],
    };
    state.planLoaded = true;
    state.showFullPlan = false;
    render(state.data);
  } catch (error) {
    setStatus(error.message || "Could not load full plan.", "error");
  } finally {
    state.loadingPlan = false;
  }
}

async function runAction(action, payload = {}) {
  if (state.loading) return;
  if (state.quizAdvanceTimer) {
    clearTimeout(state.quizAdvanceTimer);
    state.quizAdvanceTimer = null;
  }
  state.loading = true;
  setStatus(actionStatusText(action));
  const previousCard = state.data?.last_card ? JSON.parse(JSON.stringify(state.data.last_card)) : null;

  try {
    const response = await apiFetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...payload }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Something went wrong.");
    }
    if (
      [
        "next_lesson",
        "open_lesson",
        "complete_and_continue",
        "review_session",
        "build_exam",
        "build_diagnostic",
        "build_workpaper",
        "quiz_me",
        "explain_simpler",
        "another_example",
        "submit_quiz",
        "ask_question",
        "teach_back",
      ].includes(action)
    ) {
      state.pageMode = "study";
    } else if (["set_midterm_mode", "set_weekly_goal", "rate_flashcard", "start_over"].includes(action)) {
      state.pageMode = "dashboard";
    }
    if (action === "submit_quiz") {
      state.lessonStage = "quiz";
      state.lessonStageLessonId = data.state?.last_card?.lesson_id || data.state?.current_lesson?.lesson_id || null;
      const feedback = data.state?.last_card?.quiz_feedback?.question_feedback || [];
      const lessonId = state.lessonStageLessonId;
      if (lessonId) {
        state.quizDrafts[lessonId] = feedback.map((item) => item.selected_option || "");
      }
      const firstMiss = feedback.findIndex((item) => String(item.verdict).toLowerCase() !== "correct");
      state.quizQuestionIndex = firstMiss >= 0 ? firstMiss : 0;
      state.quizQuestionLessonId = lessonId;
      clearCompletionScreen();
    } else if (action === "another_example") {
      state.lessonStage = "example";
      state.lessonStageLessonId = data.state?.last_card?.lesson_id || data.state?.current_lesson?.lesson_id || null;
      clearCompletionScreen();
    } else if (action === "explain_simpler") {
      state.lessonStage = "learn";
      state.lessonStageLessonId = data.state?.last_card?.lesson_id || data.state?.current_lesson?.lesson_id || null;
      clearCompletionScreen();
    } else if (action === "ask_question") {
      state.lessonStage = "learn";
      state.lessonStageLessonId = data.state?.last_card?.lesson_id || data.state?.current_lesson?.lesson_id || null;
      clearCompletionScreen();
    } else if (action === "teach_back") {
      state.lessonStage = "learn";
      state.lessonStageLessonId = data.state?.last_card?.lesson_id || data.state?.current_lesson?.lesson_id || null;
      clearCompletionScreen();
    } else if (action === "quiz_me") {
      const lessonId = data.state?.last_card?.lesson_id || data.state?.current_lesson?.lesson_id || null;
      state.lessonStage = "quiz";
      state.lessonStageLessonId = lessonId;
      state.quizQuestionIndex = 0;
      state.quizQuestionLessonId = lessonId;
      if (lessonId) {
        state.quizDrafts[lessonId] = (data.state?.last_card?.quiz_questions || []).map(() => "");
      }
      clearCompletionScreen();
    } else if (action === "review_session") {
      state.lessonStage = "learn";
      state.lessonStageLessonId = data.state?.last_card?.lesson_id || null;
      clearCompletionScreen();
    } else if (["build_exam", "build_diagnostic", "build_workpaper"].includes(action)) {
      state.lessonStage = "quiz";
      state.lessonStageLessonId = data.state?.last_card?.lesson_id || null;
      state.quizQuestionIndex = 0;
      state.quizQuestionLessonId = state.lessonStageLessonId;
      if (state.lessonStageLessonId) {
        state.quizDrafts[state.lessonStageLessonId] = (data.state?.last_card?.quiz_questions || []).map(() => "");
      }
      clearCompletionScreen();
    } else if (action === "complete_and_continue") {
      state.lessonStage = null;
      state.lessonStageLessonId = null;
      state.quizQuestionIndex = 0;
      state.quizQuestionLessonId = null;
      if (previousCard?.lesson_id) {
        delete state.quizDrafts[previousCard.lesson_id];
      }
      state.completionScreen = buildCompletionScreen(previousCard, data.state);
    } else if (["next_lesson", "open_lesson", "complete_and_continue", "start_over"].includes(action)) {
      state.lessonStage = null;
      state.lessonStageLessonId = null;
      state.quizQuestionIndex = 0;
      state.quizQuestionLessonId = null;
      clearCompletionScreen();
    }
    state.planLoaded = false;
    state.showFullPlan = false;
    if (action !== "open_lesson") {
      state.courseLoaded = false;
    }
    render(data.state);
    const successStatus = successStatusForAction(action, data.state);
    if (successStatus) {
      setStatus(successStatus.message, successStatus.kind);
    } else {
      setStatus("");
    }
  } catch (error) {
    setStatus(error.message || "Something went wrong.", "error");
  } finally {
    state.loading = false;
  }
}

function actionStatusText(action) {
  const labels = {
    next_lesson: "Building the next lesson from the textbook and warming what comes after it…",
    open_lesson: "Opening that lesson and preloading the nearby study tools…",
    complete_and_continue: "Saving your progress and loading the next lesson…",
    review_session: "Building your weekly review session…",
    build_exam: "Building a focused exam drill from the textbook…",
    build_diagnostic: "Building your chapter diagnostic pretest…",
    build_workpaper: "Building a tax workpaper drill…",
    quiz_me: "Preparing a sharper 3-question check…",
    explain_simpler: "Rewriting this lesson in simpler language…",
    another_example: "Building a fresh worked example…",
    submit_quiz: "Checking your answers…",
    ask_question: "Looking through the book for the best answer…",
    teach_back: "Grading your teach-back against the lesson concepts…",
    rate_flashcard: "Updating your flashcard schedule…",
    set_midterm_mode: "Updating your chapter range…",
    set_weekly_goal: "Rebuilding your weekly plan…",
    start_over: "Resetting your study progress…",
  };
  return labels[action] || "Working on it…";
}

function successStatusForAction(action, appState) {
  if (action === "submit_quiz" && appState?.last_card?.quiz_feedback) {
    const feedback = appState.last_card.quiz_feedback;
    const total = feedback.total_questions || feedback.question_feedback.length || 0;
    const correct =
      feedback.correct_count ||
      feedback.question_feedback.filter((item) => String(item.verdict).toLowerCase() === "correct").length;
    return {
      message: `Quiz graded: ${correct}/${total} right. Lesson progress saved.`,
      kind: correct === total ? "celebrate" : "info",
    };
  }
  if (action === "complete_and_continue") {
    return {
      message: appState?.current_lesson
        ? "Lesson complete. Your progress is saved and the next lesson is ready."
        : "Lesson complete. Your progress is saved and the course is fully finished.",
      kind: "celebrate",
    };
  }
  if (action === "rate_flashcard") {
    return {
      message: "Flashcard progress saved.",
      kind: "success",
    };
  }
  if (["build_exam", "build_diagnostic", "build_workpaper"].includes(action)) {
    return {
      message: "Exam drill ready.",
      kind: "success",
    };
  }
  return null;
}

function handleActionButton(action) {
  if (action === "complete_and_continue") {
    const recallText = window.prompt("Before moving on, write one sentence that states the core rule from this lesson:");
    if (recallText == null) return;
    if (recallText.trim().split(/\s+/).filter(Boolean).length < 4) {
      setStatus("Please write one clear sentence (at least 4 words) before continuing.", "error");
      return;
    }
    runAction(action, { recall_text: recallText.trim() });
    return;
  }
  if (action === "quiz_me" && currentCardHasQuiz() && !currentQuizIsGraded()) {
    state.pageMode = "study";
    clearCompletionScreen();
    state.lessonStage = "quiz";
    state.lessonStageLessonId = state.data?.last_card?.lesson_id || null;
    state.quizQuestionLessonId = state.lessonStageLessonId;
    renderCard(state.data.last_card);
    const didScroll = scrollQuizIntoView();
    setStatus(didScroll ? "Quiz step ready." : "Quiz ready.", "info");
    return;
  }
  runAction(action);
}

actionButtons.forEach((button) => {
  button.addEventListener("click", () => handleActionButton(button.dataset.action));
});

if (askDetails) {
  askDetails.addEventListener("toggle", () => {
    state.askPanelOpen = askDetails.open;
  });
}

if (nextUpDetails) {
  nextUpDetails.addEventListener("toggle", () => {
    state.nextUpPanelOpen = nextUpDetails.open;
  });
}

if (syncForm && syncCodeInput) {
  syncForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const previousCode = getSyncCode();
    const nextCode = syncCodeInput.value.trim();
    const snapshot = loadLocalStateSnapshot();
    setSyncCode(nextCode);
    try {
      if (snapshot) {
        const response = await apiFetch("/api/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "hydrate_state", state: snapshot }),
        });
        const data = await response.json();
        if (!response.ok || !data?.state) {
          throw new Error(data?.error || "Unable to save sync state.");
        }
      }
      if (nextCode) {
        setStatus("Sync code saved. Use the same code on your other devices.", "success");
      } else if (previousCode) {
        setStatus("This device is back on local-only progress.", "success");
      } else {
        setStatus("Local-only progress is still enabled.", "info");
      }
      window.location.reload();
    } catch (error) {
      if (previousCode && !nextCode) {
        setSyncCode(previousCode);
      } else if (previousCode && nextCode) {
        setSyncCode(previousCode);
      }
      setStatus(error?.message || "Could not save sync code.", "error");
    }
  });
}

if (clearSyncCodeButton) {
  clearSyncCodeButton.addEventListener("click", async () => {
    if (!getSyncCode()) {
      setStatus("This device is already using local-only progress.", "info");
      return;
    }
    const snapshot = loadLocalStateSnapshot();
    const previousCode = getSyncCode();
    setSyncCode("");
    try {
      if (snapshot) {
        const response = await apiFetch("/api/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "hydrate_state", state: snapshot }),
        });
        const data = await response.json();
        if (!response.ok || !data?.state) {
          throw new Error(data?.error || "Unable to keep local progress.");
        }
      }
      setStatus("This device now uses its own local progress id.", "success");
      window.location.reload();
    } catch (error) {
      setSyncCode(previousCode);
      setStatus(error?.message || "Could not switch back to local-only mode.", "error");
    }
  });
}

document.addEventListener("click", (event) => {
  const dashboardViewButton = event.target.closest("[data-dashboard-view]");
  if (dashboardViewButton) {
    setDashboardView(dashboardViewButton.dataset.dashboardView);
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }

  const modeButton = event.target.closest("[data-page-mode]");
  if (modeButton) {
    if (modeButton.dataset.pageMode === "dashboard") {
      state.dashboardView = "today";
    }
    setPageMode(modeButton.dataset.pageMode);
    if (modeButton.dataset.pageMode === "dashboard") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    } else {
      scrollToLessonCard();
    }
    return;
  }

  const assignmentButton = event.target.closest("[data-assignment-task]");
  if (assignmentButton) {
    const task = JSON.parse(assignmentButton.dataset.assignmentTask || "{}");
    if (task.ui_action === "flashcards" || task.ui_action === "sidebar-review") {
      scrollDashboardSection(flashcardReviewPanel, "Flashcard review is ready.", "practice");
      return;
    }
    if (task.ui_action === "mistake_notebook" || task.ui_action === "mistake-notebook") {
      scrollDashboardSection(mistakeNotebook, "Mistake notebook is ready.", "practice");
      return;
    }
    if (task.ui_action === "course_map") {
      scrollDashboardSection(courseNavigator, "Course map is ready.", "course");
      return;
    }
    if (task.action) {
      runAction(task.action, task.payload || {});
      return;
    }
  }

  const examButton = event.target.closest("[data-exam-mode]");
  if (examButton) {
    runAction("build_exam", { exam_mode: examButton.dataset.examMode });
    return;
  }

  const dashboardActionButton = event.target.closest("[data-inline-dashboard-action]");
  if (dashboardActionButton) {
    runAction(dashboardActionButton.dataset.inlineDashboardAction);
    return;
  }

  const dashboardJumpButton = event.target.closest("[data-dashboard-jump-view]");
  if (dashboardJumpButton) {
    const view = dashboardJumpButton.dataset.dashboardJumpView;
    const focus = dashboardJumpButton.dataset.dashboardFocus;
    const targetMap = {
      flashcards: flashcardReviewPanel,
      mistakes: mistakeNotebook,
      course: curriculum,
    };
    const target = targetMap[focus] || null;
    if (target) {
      scrollDashboardSection(target, "", view);
    } else {
      setDashboardView(view);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
    return;
  }

  const resumeButton = event.target.closest("[data-resume-action]");
  if (resumeButton) {
    const action = resumeButton.dataset.resumeAction;
    if (action === "jump") {
      state.pageMode = "study";
      clearCompletionScreen();
      if (state.data?.last_card) {
        state.lessonStage = null;
        state.lessonStageLessonId = null;
        render(state.data);
        scrollToLessonCard();
        setStatus("Resumed right where you left off.", "success");
      } else if (state.data?.current_lesson?.lesson_id) {
        runAction("open_lesson", { lesson_id: state.data.current_lesson.lesson_id });
        setTimeout(() => scrollToLessonCard(), 260);
      } else {
        render(state.data);
        scrollToLessonCard();
      }
      return;
    }
    if (action === "next") {
      state.pageMode = "study";
      handleActionButton("next_lesson");
      return;
    }
  }

  const completionButton = event.target.closest("[data-completion-action]");
  if (completionButton) {
    const action = completionButton.dataset.completionAction;
    if (action === "resume-next") {
      state.pageMode = "study";
      clearCompletionScreen();
      state.lessonStage = "learn";
      state.lessonStageLessonId = state.data?.last_card?.lesson_id || null;
      render(state.data);
      scrollToLessonCard();
      return;
    }
    if (action === "course") {
      state.pageMode = "dashboard";
      state.dashboardView = "today";
      clearCompletionScreen();
      render(state.data);
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
  }

  const inlineActionButton = event.target.closest("[data-inline-action]");
  if (inlineActionButton) {
    handleActionButton(inlineActionButton.dataset.inlineAction);
    return;
  }
  const planToggle = event.target.closest("[data-plan-toggle]");
  if (planToggle) {
    if (state.showFullPlan) {
      state.data.weekly_plan = state.data._weekly_plan_preview_cache || state.data.weekly_plan;
      state.data.weekly_plan_is_preview = true;
      state.showFullPlan = false;
    } else {
      state.data.weekly_plan = state.data._weekly_plan_full_cache || state.data.weekly_plan;
      state.data.weekly_plan_is_preview = false;
      state.showFullPlan = true;
    }
    render(state.data);
    return;
  }
  const flashcardButton = event.target.closest("[data-flashcard-rating]");
  if (!flashcardButton) return;
  runAction("rate_flashcard", {
    card_id: flashcardButton.dataset.cardId,
    rating: flashcardButton.dataset.flashcardRating,
  });
});

questionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) {
    setStatus("Please type a question first.", "error");
    return;
  }
  runAction("ask_question", { question });
});

if (teachBackButton) {
  teachBackButton.addEventListener("click", () => {
    const responseText = questionInput.value.trim();
    if (!responseText) {
      setStatus("Please type your teach-back explanation first.", "error");
      return;
    }
    runAction("teach_back", { response_text: responseText });
  });
}

midtermForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runAction("set_midterm_mode", {
    enabled: midtermEnabled.checked,
    start_chapter: Number(midtermStart.value || 1),
    end_chapter: Number(midtermEnd.value || 25),
  });
});

weeklyGoalForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runAction("set_weekly_goal", { weekly_goal_lessons: Number(weeklyGoalInput.value || 2) });
});

startOverButton.addEventListener("click", () => runAction("start_over"));

fetchBootstrap().catch((error) => {
  setStatus(error.message || "Could not load the app.", "error");
});
startStatusPolling();
