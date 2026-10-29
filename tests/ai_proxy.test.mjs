/**
 * functions/api/ai/[[path]].js 的行為測試：把 fetch 換成假的主機，不連網。
 *
 * 用法：node tests/ai_proxy.test.mjs
 */

import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const { onRequest } = await import(
    'file:///' + join(ROOT, 'functions', 'api', 'ai', '[[path]].js').replace(/\\/g, '/')
);

const ENV = { AI_BACKEND_URL: 'https://ai-api.example.com', AI_BACKEND_SECRET: 's3cret' };
const realFetch = globalThis.fetch;
let sent = null;

function call(path, { method = 'GET', body, env = ENV, search = '', backend } = {}) {
    sent = null;
    globalThis.fetch = async (url, init) => {
        sent = { url: String(url), init };
        if (!backend) throw new Error('connection refused');
        return backend;
    };
    const request = new Request(`https://course.example.com/api/ai/${path}${search}`, {
        method, body,
        headers: { 'CF-Connecting-IP': '203.0.113.9', 'Content-Type': 'application/json' },
    });
    return onRequest({ request, env, params: { path: path.split('/') } });
}

const backendJson = (obj, status = 200) =>
    new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json' } });

const cases = {
    async 'not configured: status says unavailable, chat 503'() {
        const status = await (await call('status', { env: {} })).json();
        assert.deepEqual(status, { available: false, reason: 'not_configured' });
        assert.equal((await call('chat', { method: 'POST', body: '{}', env: {} })).status, 503);
        assert.equal(sent, null);
    },
    async 'unknown path is rejected without touching the backend'() {
        assert.equal((await call('admin')).status, 404);
        assert.equal(sent, null);
    },
    async 'forwards secret, client IP, query string and body'() {
        const res = await call('syllabus-search', { search: '?q=%E8%B3%87%E6%96%99&k=5', backend: backendJson({ hits: [] }) });
        assert.equal(res.status, 200);
        assert.equal(sent.url, 'https://ai-api.example.com/api/ai/syllabus-search?q=%E8%B3%87%E6%96%99&k=5');
        assert.equal(sent.init.headers.get('X-Backend-Secret'), 's3cret');
        assert.equal(sent.init.headers.get('X-Client-IP'), '203.0.113.9');
        assert.equal(res.headers.get('Cache-Control'), 'no-store');

        await call('chat', { method: 'POST', body: '{"messages":[]}', backend: backendJson({ reply: 'ok' }) });
        assert.equal(new TextDecoder().decode(sent.init.body), '{"messages":[]}');
    },
    async 'backend errors pass through (e.g. rate limit 429)'() {
        const res = await call('chat', { method: 'POST', body: '{}', backend: backendJson({ detail: '問得太快了' }, 429) });
        assert.equal(res.status, 429);
        assert.equal((await res.json()).detail, '問得太快了');
    },
    async 'backend down: status unavailable, chat 502'() {
        assert.deepEqual(await (await call('status')).json(), { available: false, reason: 'backend_down' });
        assert.equal((await call('chat', { method: 'POST', body: '{}' })).status, 502);
    },
    async 'wrong secret on backend makes status unavailable'() {
        const res = await call('status', { backend: backendJson({ detail: 'forbidden' }, 403) });
        assert.deepEqual(await res.json(), { available: false, reason: 'backend_error' });
    },
    async 'oversized body is rejected before forwarding'() {
        const res = await call('chat', { method: 'POST', body: 'x'.repeat(70 * 1024), backend: backendJson({}) });
        assert.equal(res.status, 413);
        assert.equal(sent, null);
    },
};

let failed = 0;
for (const [name, fn] of Object.entries(cases)) {
    try {
        await fn();
        console.log(`  ✓ ${name}`);
    } catch (e) {
        failed++;
        console.log(`  ✗ ${name}\n    ${e.message}`);
    }
}
globalThis.fetch = realFetch;
console.log(failed ? `AI 轉發測試：${failed} 項失敗` : `AI 轉發測試：${Object.keys(cases).length} 項全部通過`);
process.exit(failed ? 1 : 0);
