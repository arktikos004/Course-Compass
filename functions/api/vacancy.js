/**
 * 即時缺額查詢（Cloudflare Pages Function）
 *
 * 網站其餘部分都是每日建置的靜態資料，只有這一支需要即時連線：缺額是會秒變的
 * 資料，用昨天的快取回答「現在還有沒有位子」沒有意義。
 *
 * 這是 src/api/vacancy.py + src/crawler/ncue_client.py 的移植。學校站台
 * （OB010）已從 ASP.NET WebForms 改版為 MVC，查詢有三個必要條件：
 *   - 先 GET 取得 __RequestVerificationToken，且 token 與 cookie 必須成對送回
 *   - 帶 X-Requested-With 標頭，MVC 端會檢查是否為 AJAX 請求
 *   - 表單含名為 CatchBot 的 honeypot 欄位，必須存在且留空
 * 少任何一項都會拿到 HTTP 500。
 */

const BASE_URL = 'https://webap0.ncue.edu.tw/DEANV2/Other/OB010';
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    + '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

// 單次最多查幾門：每門都是一次對外請求，過多會拖垮回應也對學校站台不禮貌
const MAX_CODES = 12;
const TIMEOUT_MS = 20000;

function json(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: {
            'Content-Type': 'application/json; charset=utf-8',
            // 缺額必須即時，任何一層快取都會給出過期答案
            'Cache-Control': 'no-store',
        },
    });
}

/** 從 Set-Cookie 收集 cookie；token 與 session 必須成對送回站台才會認 */
function collectCookies(response, jar) {
    const raw = response.headers.getSetCookie?.() ?? [];
    for (const line of raw) {
        const pair = line.split(';')[0];
        const index = pair.indexOf('=');
        if (index > 0) jar.set(pair.slice(0, index).trim(), pair.slice(index + 1).trim());
    }
}

const cookieHeader = jar => [...jar].map(([k, v]) => `${k}=${v}`).join('; ');

function decodeEntities(value) {
    return value
        .replace(/&nbsp;/g, ' ')
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'");
}

const stripTags = html => decodeEntities(html.replace(/<[^>]*>/g, '')).replace(/\s+/g, ' ').trim();

/**
 * 課程名稱儲存格是 <strong>中文</strong><br><small>English</small>，
 * 直接剝標籤會把中英文黏成「工程與生活 Engineering and Life」。
 * 這裡只取中文那一段；舊版版面沒有 <strong> 時退回整格文字。
 */
function courseName(cellHtml) {
    const zh = cellHtml.match(/<strong[^>]*>([\s\S]*?)<\/strong>/i);
    return zh ? stripTags(zh[1]) : stripTags(cellHtml);
}

/**
 * 解析結果表格。用正規表示式而非 HTMLRewriter：HTMLRewriter 是串流式的，
 * 要組出「一列的所有儲存格」得自己維護狀態，對這種小表格反而更容易出錯。
 */
function parseTable(html) {
    const table = html.match(/<table[^>]*class="[^"]*table[^"]*"[^>]*>([\s\S]*?)<\/table>/i);
    if (!table) return null;

    const rows = table[1].match(/<tr[\s\S]*?<\/tr>/gi) || [];
    if (!rows.length) return null;

    const headers = (rows[0].match(/<th[\s\S]*?<\/th>/gi) || []).map(stripTags);
    if (!headers.length) return null;

    const records = [];
    for (const row of rows.slice(1)) {
        const cells = row.match(/<td[\s\S]*?<\/td>/gi) || [];
        if (!cells.length) continue;
        const record = {};
        headers.forEach((name, i) => {
            const html = cells[i] ?? '';
            record[name] = name === '課程名稱' ? courseName(html) : stripTags(html);
        });
        records.push(record);
    }
    return { headers, records };
}

/** 依表頭名稱取值，避免硬編索引在站台改版時默默錯位 */
function pick(record, name) {
    // 站台改版後「全英語授課」被改名為「全英語」，人數欄位則維持原名
    return record[name] ?? '';
}

const toInt = value => {
    const digits = String(value ?? '').trim();
    return /^\d+$/.test(digits) ? Number(digits) : 0;
};

async function fetchWithTimeout(url, options) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
        return await fetch(url, { ...options, signal: controller.signal });
    } finally {
        clearTimeout(timer);
    }
}

async function queryCourse(code, year, semester) {
    const jar = new Map();

    const page = await fetchWithTimeout(BASE_URL, {
        headers: { 'User-Agent': USER_AGENT },
    });
    if (!page.ok) throw new Error(`取得查詢頁失敗（${page.status}）`);
    collectCookies(page, jar);

    const html = await page.text();
    const token = html.match(
        /name="__RequestVerificationToken"[^>]*value="([^"]+)"/i
    ) || html.match(/value="([^"]+)"[^>]*name="__RequestVerificationToken"/i);
    if (!token) throw new Error('找不到防偽 token，站台可能又改版了');

    const form = new URLSearchParams({
        __RequestVerificationToken: token[1],
        sel_cls_branch: '',
        sel_cls_id: '',
        sel_yms_year: String(year),
        sel_yms_smester: String(semester),
        scr_selcode: code,
        sub_name: '',
        emp_name: '',
        sel_scr_english: '',
        sel_sct_week: '',
        sel_SCR_IS_DIS_LEARN: '',
        CatchBot: '',                       // honeypot：必須存在且留空
    });

    const result = await fetchWithTimeout(BASE_URL, {
        method: 'POST',
        headers: {
            'User-Agent': USER_AGENT,
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Requested-With': 'XMLHttpRequest',   // MVC 端會檢查
            Referer: BASE_URL,
            Cookie: cookieHeader(jar),
        },
        body: form,
    });
    if (!result.ok) throw new Error(`查詢失敗（${result.status}）`);

    const parsed = parseTable(await result.text());
    if (!parsed) return { 課程代碼: code, error: '查無此課程（代碼有誤或該學期未開放）' };

    if (!parsed.headers.includes('上限人數') || !parsed.headers.includes('登記人數')) {
        return { 課程代碼: code, error: '站台格式改變，暫時無法解析' };
    }

    const rows = [];
    for (const record of parsed.records) {
        if (pick(record, '課程代碼') !== code) continue;
        const limit = toInt(pick(record, '上限人數'));
        const registered = toInt(pick(record, '登記人數'));
        rows.push({
            課程代碼: code,
            序號: pick(record, '序號'),
            課程名稱: pick(record, '課程名稱'),
            教師姓名: pick(record, '教師姓名'),
            上限人數: limit,
            登記人數: registered,
            剩餘名額: Math.max(0, limit - registered),
            has_vacancy: limit > registered,
        });
    }
    return rows.length ? rows : { 課程代碼: code, error: '查無此課程的開課資料' };
}

export async function onRequestGet({ request }) {
    const url = new URL(request.url);
    const codes = (url.searchParams.get('codes') || '')
        .split(',').map(c => c.trim()).filter(Boolean);
    const year = url.searchParams.get('year');
    const semester = url.searchParams.get('semester');

    if (!codes.length) return json({ detail: '請提供至少一個課程代碼' }, 400);
    if (!year || !semester) return json({ detail: '請指定 year 與 semester' }, 400);

    const unique = [...new Set(codes)].slice(0, MAX_CODES);
    const results = [];

    // 逐一送出而非併發：對學校站台友善，且每次查詢都要自己的 token/cookie 配對
    for (const code of unique) {
        try {
            const outcome = await queryCourse(code, year, semester);
            Array.isArray(outcome) ? results.push(...outcome) : results.push(outcome);
        } catch (error) {
            // 單一課程失敗不該讓整批一起垮掉
            results.push({ 課程代碼: code, error: '查詢失敗，請稍後再試' });
        }
    }

    return json({
        year: Number(year),
        semester: Number(semester),
        results,
        truncated: codes.length > MAX_CODES,
        max_codes: MAX_CODES,
    });
}
