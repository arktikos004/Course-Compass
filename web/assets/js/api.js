/**
 * 資料存取層（靜態版）
 *
 * 網站改為 Cloudflare Pages 全靜態部署後沒有 FastAPI 可以呼叫。這一層保持與
 * 原本相同的函式名稱與回傳結構，所以 find.js / dashboard.js / main.js 不必更動：
 *
 *   - 不隨查詢條件變動的東西（學期清單、篩選選項、統計、歷年中籤率）
 *     在建置期由 scripts/build_static.py 算好，這裡只負責抓 JSON 並快取。
 *   - 隨查詢條件變動的篩選／評分／排序改由 query.js 在瀏覽器執行，
 *     行為與原本的 Python 實作逐筆一致（見 tests/parity.mjs）。
 *   - 只有即時缺額仍需連線：缺額是會秒變的資料，靠每日快取回答只會給出過期答案。
 *     那一支走 Pages Function 代理學校站台。
 */

import { DATA_BASE, API_BASE } from './config.js';
import { queryCourses as runQuery } from './query.js';

// 同一份 JSON 在一次瀏覽中不會變，抓過就留著；用 Promise 快取避免同時發出重複請求
const cache = new Map();

function loadJson(path) {
    if (!cache.has(path)) {
        cache.set(path, fetch(`${DATA_BASE}/${path}`).then(response => {
            if (!response.ok) {
                cache.delete(path);   // 失敗不要留下壞掉的快取，下次可以重試
                throw new Error(`載入資料失敗（${response.status}）：${path}`);
            }
            return response.json();
        }));
    }
    return cache.get(path);
}

const loadMeta = () => loadJson('meta.json');
const loadSemester = (year, semester) => loadJson(`courses/${year}-${semester}.json`);

/** 找不到指定學期時退回資料中最新的那一個，避免整頁空白 */
async function resolveSemester(year, semester) {
    const meta = await loadMeta();
    const found = meta.semesters.find(s => String(s.year) === String(year)
        && String(s.semester) === String(semester));
    if (found) return [found.year, found.semester];
    const latest = meta.semesters[0];
    return latest ? [latest.year, latest.semester] : [year, semester];
}

export async function fetchSemesters() {
    const meta = await loadMeta();
    return { semesters: meta.semesters };
}

export async function fetchAllCourses(year, semester) {
    const [y, s] = await resolveSemester(year, semester);
    const data = await loadSemester(y, s);
    return { courses: data.courses, total: data.courses.length };
}

export async function fetchFilterOptions(year, semester) {
    const [y, s] = await resolveSemester(year, semester);
    const data = await loadSemester(y, s);
    return { ...data.filters, year: y, semester: s };
}

export async function fetchDepartments(year, semester) {
    const [y, s] = await resolveSemester(year, semester);
    const data = await loadSemester(y, s);
    const names = new Set();
    for (const course of data.courses) {
        const name = course['開課班別(代表)'];
        if (name && String(name).trim()) names.add(String(name));
    }
    return { departments: [...names].sort() };
}

export async function fetchDashboard(year, semester) {
    const [y, s] = await resolveSemester(year, semester);
    return await loadJson(`dashboard/${y}-${s}.json`);
}

/**
 * 課程查詢。原本是 POST /api/courses/query，現在在瀏覽器算。
 * 保持 async 是為了讓呼叫端不必改寫，也讓第一次查詢能等資料載入完成。
 */
export async function queryCourses(payload) {
    const [year, semester] = await resolveSemester(payload.year, payload.semester);
    const [meta, data] = await Promise.all([loadMeta(), loadSemester(year, semester)]);
    return runQuery({ ...payload, year, semester }, {
        courses: data.courses,
        acceptance: meta.acceptance,
        settled: meta.settled,
    });
}

/** 中籤預測模型的版本與驗證成績（建置期寫進 meta.json；沒有訓練過就是 null） */
export async function fetchPredictionModel() {
    const meta = await loadMeta();
    return meta.prediction_model || null;
}

/** 歷年課程查詢。整份歷年資料 gzip 後約 0.5 MB，只在使用者真的開這頁時才載入 */
export async function fetchHistory(query) {
    const data = await loadJson('history.json');
    const q = String(query || '').trim().toLowerCase();
    if (!q) return { courses: [], total: 0 };

    const matched = data.courses.filter(c =>
        String(c.課程名稱 || '').toLowerCase().includes(q)
        || String(c.教師姓名 || '').toLowerCase().includes(q));

    matched.sort((a, b) => (b.學年度 - a.學年度) || (b.學期 - a.學期));
    const page = matched.slice(0, 100);
    return { courses: page, total: page.length };
}

/**
 * 即時缺額：唯一還需要連線的功能，走 Pages Function 代理學校站台。
 * 快取好的資料回答不了「現在還有沒有位子」。
 */
export async function fetchVacancy(codes, year, semester) {
    const params = new URLSearchParams({ codes, year, semester });
    const response = await fetch(`${API_BASE}/vacancy?${params}`);
    if (!response.ok) {
        let detail = '';
        try {
            detail = (await response.json()).detail || '';
        } catch (e) { /* 回應不是 JSON 就用預設訊息 */ }
        throw new Error(detail || `查詢缺額失敗（${response.status}）`);
    }
    return await response.json();
}
