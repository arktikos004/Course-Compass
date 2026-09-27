/**
 * 選課助理分頁＋課程詳情中的「問這門課的大綱」
 *
 * 這兩個功能需要本機的 Python API 與 Ollama（python main.py api），正式網站是全靜態部署、
 * 沒有這些端點。載入時先打 /api/ai/status 判斷：Cloudflare Pages 找不到路徑時會回首頁
 * （200 + HTML），所以必須確認拿到的是 JSON 且 available=true，不能只看狀態碼。
 * LLM 產生的文字一律跳脫後再放進 DOM。
 */
import { API_BASE } from './config.js';
import { state } from './state.js';
import { admissionBadge } from './prediction.js';

const MAX_TURNS = 6;           // 送給後端的歷史訊息上限，避免 context 過長
const history = [];            // [{role, content}]
const courseStore = new Map(); // 卡片 id → 課程物件，給「加入」按鈕用
let courseSeq = 0;

let statusPromise = null;

/** AI 後端狀態（快取）；不可用時回傳 null */
export function aiStatus() {
    if (!statusPromise) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4000);
        statusPromise = fetch(`${API_BASE}/ai/status`, { signal: controller.signal })
            .then(async (res) => {
                const type = res.headers.get('content-type') || '';
                if (!res.ok || !type.includes('application/json')) return null;
                const data = await res.json();
                return data && data.available === true ? data : null;
            })
            .catch(() => null)
            .finally(() => clearTimeout(timer));
    }
    return statusPromise;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

function logEl() { return document.getElementById('assistant-log'); }

function scrollToEnd() {
    const el = logEl();
    if (el) el.scrollTop = el.scrollHeight;
}

function appendBubble(role, html) {
    const el = logEl();
    el.querySelector('.assistant-empty')?.remove();
    const div = document.createElement('div');
    div.className = `assistant-msg assistant-${role}`;
    div.innerHTML = html;
    el.appendChild(div);
    scrollToEnd();
    return div;
}

function currentCoursesPayload() {
    return state.selectedCourses.map(c => ({
        課程代碼: c.課程代碼, 序號: c.序號, 課程名稱: c.課程名稱,
        星期: c.星期, 起始節次: c.起始節次, 結束節次: c.結束節次,
    }));
}

function courseCard(course) {
    const id = `ac-${++courseSeq}`;
    courseStore.set(id, course);
    const time = (course.星期 && course.起始節次) ? `週${esc(course.星期)} ${esc(course.起始節次)}-${esc(course.結束節次)}節` : '時間未定';
    const conflict = course.conflicts_with_schedule
        ? '<span class="badge bg-danger-subtle text-danger-emphasis border border-danger-subtle ms-1">與課表衝堂</span>' : '';
    return `
        <div class="col-md-6">
            <div class="card h-100 border shadow-sm assistant-course">
                <div class="card-body p-3 d-flex flex-column">
                    <div class="d-flex justify-content-between align-items-start gap-2 mb-1">
                        <div class="fw-bold text-truncate" title="${esc(course.課程名稱)}">
                            <span class="text-secondary fw-normal me-1">${esc(course.課程代碼)}</span>${esc(course.課程名稱)}
                        </div>
                        <span class="badge bg-primary bg-opacity-10 text-primary flex-shrink-0">${esc(course.學分)}學分</span>
                    </div>
                    <div class="small text-muted mb-2">${esc(course.教師列表 || course.教師姓名)} · ${time} · ${esc(course['開課班別(代表)'] || '')}${conflict}</div>
                    <div class="mb-2">${admissionBadge(course.admission_pred)}</div>
                    <div class="small assistant-reason mb-3"><i class="far fa-comment-dots me-1 text-primary"></i>${esc(course.ai_reason)}</div>
                    <div class="mt-auto d-flex gap-2">
                        <button type="button" class="btn btn-sm btn-outline-primary flex-grow-1" data-action="detail" data-id="${id}">詳情</button>
                        <button type="button" class="btn btn-sm btn-primary flex-grow-1" data-action="add" data-id="${id}" ${course.conflicts_with_schedule ? 'disabled' : ''}>加入課表</button>
                    </div>
                </div>
            </div>
        </div>`;
}

function traceHtml(trace, dropped) {
    if (!trace?.length && !dropped?.length) return '';
    const items = (trace || []).map(t => `<li><code>${esc(t.tool)}</code> ${esc(JSON.stringify(t.args))} → ${esc(t.result)}</li>`).join('');
    const drop = dropped?.length
        ? `<div class="text-danger mt-1"><i class="fas fa-shield-halved me-1"></i>已剔除 ${dropped.length} 門不存在的課（模型幻覺）：${dropped.map(d => esc(d.code)).join('、')}</div>` : '';
    return `<details class="small text-muted mt-2"><summary>助理做了哪些查詢（${trace?.length || 0}）</summary><ol class="mb-0 mt-1 ps-3">${items}</ol>${drop}</details>`;
}

async function send(text) {
    text = text.trim();
    if (!text) return;
    history.push({ role: 'user', content: text });
    appendBubble('user', `<div class="assistant-bubble">${esc(text)}</div>`);
    const pending = appendBubble('bot', `<div class="assistant-bubble text-muted"><span class="spinner-border spinner-border-sm me-2"></span>查詢中…</div>`);
    const btn = document.getElementById('assistant-send');
    btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/ai/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                messages: history.slice(-MAX_TURNS),
                current_courses: currentCoursesPayload(),
                year: state.currentYear,
                semester: state.currentSemester,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.status);
        history.push({ role: 'assistant', content: data.reply });
        const cards = (data.courses || []).map(courseCard).join('');
        pending.innerHTML = `
            <div class="assistant-bubble">${esc(data.reply)}</div>
            ${cards ? `<div class="row g-2 mt-1">${cards}</div>` : ''}
            ${traceHtml(data.tool_trace, data.dropped)}`;
        const prov = document.getElementById('assistant-provider');
        if (prov) prov.textContent = `模型：${data.provider} · ${data.semester} · ${data.elapsed_sec}s`;
    } catch (e) {
        history.pop();
        pending.innerHTML = `<div class="assistant-bubble text-danger">助理暫時無法回應：${esc(e.message)}</div>`;
    } finally {
        btn.disabled = false;
        scrollToEnd();
    }
}

function renderUnavailable() {
    const form = document.getElementById('assistant-form');
    form?.classList.add('d-none');
    document.querySelectorAll('.assistant-example').forEach(b => { b.disabled = true; });
    logEl().innerHTML = `
        <div class="assistant-empty small">
            <div class="fw-bold mb-1"><i class="fas fa-laptop-code me-1"></i>選課助理是本機版功能</div>
            <div class="text-muted">它需要在自己的電腦上執行語言模型（Ollama），正式網站是純靜態頁面，無法提供。
            在專案資料夾執行：</div>
            <pre class="assistant-cmd mt-2 mb-0">pip install -r requirements.txt -r requirements-ai.txt
ollama pull qwen2.5:7b &amp;&amp; ollama pull bge-m3
python scripts/build_static.py &amp;&amp; python main.py api</pre>
            <div class="text-muted mt-2">再打開 http://localhost:8000。沒有 Ollama 時會自動改用離線規則模式。
            中籤預測不需要本機版，查詢結果上就看得到。</div>
        </div>`;
}

export async function initAssistant() {
    const form = document.getElementById('assistant-form');
    if (!form) return;
    const status = await aiStatus();
    if (!status) {
        renderUnavailable();
        return;
    }
    const prov = document.getElementById('assistant-provider');
    if (prov) prov.textContent = status.llm_ready ? `模型：${status.provider}` : `目前為離線規則模式（${status.provider}）`;
    logEl().innerHTML = `<div class="assistant-empty text-muted small">
        <i class="far fa-lightbulb me-1"></i>會參考「我的課表」避開衝堂。推薦結果可以直接加入課表。</div>`;
    const input = document.getElementById('assistant-input');
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const text = input.value;
        input.value = '';
        send(text);
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {  // 中文輸入法選字時不要送出
            e.preventDefault();
            form.requestSubmit();
        }
    });
    document.querySelectorAll('.assistant-example').forEach(b => b.addEventListener('click', () => send(b.textContent)));
    document.getElementById('assistant-reset')?.addEventListener('click', () => {
        history.length = 0;
        courseStore.clear();
        logEl().innerHTML = '<div class="assistant-empty text-muted small">對話已清除。</div>';
    });
    logEl().addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-action]');
        if (!btn) return;
        const course = courseStore.get(btn.dataset.id);
        if (!course) return;
        if (btn.dataset.action === 'add') window.addRecommendedCourse?.(course);
        if (btn.dataset.action === 'detail') window.showCourseDetail?.(course);
    });
}

/** 課程詳情彈窗中的大綱問答；ui.showCourseDetailModal 產生 HTML 後呼叫 */
export async function bindSyllabusQA(container, course) {
    const box = container.querySelector('.syllabus-qa');
    if (!box) return;
    const status = await aiStatus();
    if (!status || !status.syllabus_index) return;   // 靜態網站或沒建索引：維持隱藏
    box.classList.remove('d-none');
    const input = box.querySelector('input');
    const out = box.querySelector('.syllabus-qa-answer');
    const ask = async () => {
        const q = input.value.trim();
        if (!q) return;
        out.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>查詢大綱中…';
        try {
            const res = await fetch(`${API_BASE}/ai/syllabus-qa`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: String(course.課程代碼), serial: String(course.序號), question: q,
                                       year: Number(course.學年度) || state.currentYear, semester: Number(course.學期) || state.currentSemester }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || res.status);
            const cites = (data.citations || []).map(c =>
                `<blockquote class="syllabus-cite mb-1"><span class="badge bg-secondary me-1">[${c.n}] ${esc(c.section)}</span>${esc(c.text)}</blockquote>`).join('');
            out.innerHTML = `<div class="mb-2">${esc(data.answer)}</div>${cites}`;
        } catch (e) {
            out.innerHTML = `<span class="text-muted">${esc(e.message)}</span>`;
        }
    };
    box.querySelector('button').addEventListener('click', ask);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); ask(); } });
}
