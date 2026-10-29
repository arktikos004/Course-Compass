/**
 * 選課助理與大綱問答的轉發（Cloudflare Pages Function）
 *
 * 網站是全靜態的，需要語言模型的端點跑在自架主機上（python main.py api，經 Cloudflare
 * Tunnel 對外，部署方式見 docs/DEPLOY_WINDOWS.md）。這支把 /api/ai/* 原樣轉過去：
 *   - 前端維持同網域呼叫 /api/ai/…，不必處理跨網域，也不會在頁面上露出主機網址
 *   - 帶上共用密鑰 X-Backend-Secret，主機只接受帶正確密鑰的請求
 *   - 帶上使用者 IP（X-Client-IP），主機依此限流（規則見 src/api/ai_gate.py）
 *
 * 需要在 Pages 專案設定兩個 Secret：AI_BACKEND_URL、AI_BACKEND_SECRET。
 * 沒設定或主機連不上時，status 回 available=false 的 JSON，前端改顯示說明，不會壞掉。
 */

const ALLOWED = new Set(['status', 'chat', 'syllabus-search', 'syllabus-qa']);
// 對話歷史＋「我的課表」通常只有幾 KB；主機端另有逐欄長度限制
const MAX_BODY = 64 * 1024;
// 助理最多 5 輪工具呼叫，雲端模型慢的時候要數十秒；Cloudflare 等來源回應的上限約 100 秒
const TIMEOUT_MS = 90000;
const STATUS_TIMEOUT_MS = 6000;

function json(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
    });
}

export async function onRequest({ request, env, params }) {
    const path = [].concat(params.path || []).join('/');
    if (!ALLOWED.has(path)) return json({ detail: 'not found' }, 404);
    const isStatus = path === 'status';

    if (!env.AI_BACKEND_URL || !env.AI_BACKEND_SECRET) {
        return isStatus
            ? json({ available: false, reason: 'not_configured' })
            : json({ detail: '選課助理尚未開放' }, 503);
    }
    if (request.method !== 'GET' && request.method !== 'POST') {
        return json({ detail: 'method not allowed' }, 405);
    }

    let body;
    if (request.method === 'POST') {
        body = await request.arrayBuffer();
        if (body.byteLength > MAX_BODY) return json({ detail: '內容太長了，請縮短問題或清除對話後再試' }, 413);
    }

    const target = new URL(`/api/ai/${path}${new URL(request.url).search}`, env.AI_BACKEND_URL);
    const headers = new Headers({
        'X-Backend-Secret': env.AI_BACKEND_SECRET,
        'X-Client-IP': request.headers.get('CF-Connecting-IP') || '',
    });
    const type = request.headers.get('Content-Type');
    if (type) headers.set('Content-Type', type);

    try {
        const res = await fetch(target, {
            method: request.method,
            headers,
            body,
            signal: AbortSignal.timeout(isStatus ? STATUS_TIMEOUT_MS : TIMEOUT_MS),
        });
        // 密鑰不符（403）或主機回了非 JSON：對前端來說都等於主機不能用
        if (isStatus && !res.ok) return json({ available: false, reason: 'backend_error' });
        return new Response(res.body, {
            status: res.status,
            headers: {
                'Content-Type': res.headers.get('Content-Type') || 'application/json; charset=utf-8',
                'Cache-Control': 'no-store',
            },
        });
    } catch {
        return isStatus
            ? json({ available: false, reason: 'backend_down' })
            : json({ detail: '選課助理的主機暫時連不上，請稍後再試' }, 502);
    }
}
