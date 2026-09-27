/**
 * 缺額監控（網頁版）
 *
 * 原本缺額監控只跑在 GitHub Actions 上並推播 LINE，網頁完全看不到。
 * 這裡接上 /api/vacancy，直接向學校站台查目前名額。
 *
 * 監控清單存在 localStorage：這是個人偏好而非共用資料，沒有後端帳號系統可以掛。
 * 查詢結果刻意不快取在前端 —— 缺額是會秒變的資料，顯示舊值比顯示「未查詢」更糟。
 */

import * as api from './api.js';
import * as ui from './ui.js';
import { state } from './state.js';

const STORAGE_KEY = 'vacancyWatchList';
const AUTO_REFRESH_MS = 60000;

let watchList = [];      // [{ code, name }]
let lastResults = [];
let lastCheckedAt = null;
let autoTimer = null;
let querying = false;

function load() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        watchList = raw ? JSON.parse(raw) : [];
    } catch (error) {
        console.error('讀取監控清單失敗', error);
        watchList = [];
    }
    if (!Array.isArray(watchList)) watchList = [];
}

function save() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(watchList));
    } catch (error) {
        console.error('儲存監控清單失敗', error);
    }
}

function addCode(code, name) {
    const trimmed = String(code || '').trim();
    if (!trimmed) return false;
    if (watchList.some(item => item.code === trimmed)) return false;
    watchList.push({ code: trimmed, name: name || '' });
    save();
    return true;
}

function removeCode(code) {
    watchList = watchList.filter(item => item.code !== String(code));
    save();
    renderWatchList();
    renderResults();
}

function renderWatchList() {
    const container = document.getElementById('vacancy-watch-list');
    const counter = document.getElementById('vacancy-count');
    if (!container) return;

    if (counter) {
        counter.textContent = `${watchList.length} / ${maxCodes()}`;
    }

    if (!watchList.length) {
        container.innerHTML = '<div class="text-muted small py-3">尚未加入任何課程。可從下方「從我的課表加入」，或直接輸入課程代碼。</div>';
        return;
    }

    container.innerHTML = watchList.map(item => `
        <span class="badge bg-primary bg-opacity-10 text-primary border border-primary border-opacity-25 py-2 px-3 me-2 mb-2 d-inline-flex align-items-center gap-2">
            <span class="font-monospace">${item.code}</span>
            ${item.name ? `<span class="fw-normal">${item.name}</span>` : ''}
            <button type="button" class="btn-close btn-close-sm" aria-label="移除"
                    style="font-size:0.6rem;" onclick="vacancyRemove('${item.code}')"></button>
        </span>`).join('');
}

let maxCodesFromApi = 12;
function maxCodes() {
    return maxCodesFromApi;
}

function statusRow(row) {
    if (row.error) {
        return `
            <div class="list-group-item px-3 py-3">
                <div class="d-flex justify-content-between align-items-center">
                    <span class="font-monospace">${row.課程代碼}</span>
                    <span class="badge bg-secondary"><i class="fas fa-circle-exclamation me-1"></i>${row.error}</span>
                </div>
            </div>`;
    }

    const vacant = row.has_vacancy;
    const percent = row.上限人數 > 0 ? Math.min(100, Math.round((row.登記人數 / row.上限人數) * 100)) : 0;

    return `
        <div class="list-group-item px-3 py-3 ${vacant ? 'border-start border-4 border-success' : ''}">
            <div class="d-flex flex-wrap justify-content-between align-items-start gap-2 mb-2">
                <div>
                    <span class="text-secondary small font-monospace me-2">${row.課程代碼}</span>
                    <span class="fw-bold">${row.課程名稱 || '（未提供名稱）'}</span>
                    <div class="small text-muted">${row.教師姓名 || '教師未定'}${row.序號 ? ` · 序號 ${row.序號}` : ''}</div>
                </div>
                <span class="badge ${vacant ? 'bg-success' : 'bg-danger'} py-2 px-3">
                    <i class="fas ${vacant ? 'fa-circle-check' : 'fa-circle-xmark'} me-1"></i>
                    ${vacant ? `有缺額 ${row.剩餘名額} 位` : '已額滿'}
                </span>
            </div>
            <div class="d-flex align-items-center gap-2">
                <div class="progress flex-grow-1" style="height: 6px;">
                    <div class="progress-bar ${vacant ? 'bg-success' : 'bg-danger'}" style="width: ${percent}%"></div>
                </div>
                <span class="small text-muted" style="font-variant-numeric: tabular-nums;">
                    ${row.登記人數} / ${row.上限人數}
                </span>
            </div>
        </div>`;
}

function renderResults() {
    const container = document.getElementById('vacancy-results');
    const stamp = document.getElementById('vacancy-timestamp');
    if (!container) return;

    if (stamp) {
        stamp.textContent = lastCheckedAt
            ? `最後查詢：${lastCheckedAt.toLocaleTimeString('zh-TW')}`
            : '尚未查詢';
    }

    if (!lastResults.length) {
        container.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="fas fa-bell fa-3x mb-3 opacity-25"></i>
                <p class="mb-0">加入要監控的課程後，按「立即查詢」取得目前名額</p>
            </div>`;
        return;
    }

    const vacant = lastResults.filter(r => r.has_vacancy).length;
    container.innerHTML = `
        ${vacant ? `<div class="alert alert-success py-2 mb-3"><i class="fas fa-circle-check me-2"></i>目前有 ${vacant} 門課出現缺額</div>` : ''}
        <div class="list-group list-group-flush">${lastResults.map(statusRow).join('')}</div>`;
}

async function checkNow() {
    if (querying) return;
    if (!watchList.length) {
        ui.showAlert('請先加入要監控的課程', 'warning');
        return;
    }

    querying = true;
    const button = document.getElementById('btn-check-vacancy');
    const original = button ? button.innerHTML : '';
    if (button) {
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>查詢中...';
    }

    try {
        const codes = watchList.map(item => item.code).join(',');
        const data = await api.fetchVacancy(codes, state.currentYear, state.currentSemester);
        lastResults = data.results || [];
        lastCheckedAt = new Date();
        maxCodesFromApi = data.max_codes || maxCodesFromApi;

        if (data.truncated) {
            ui.showAlert(`單次最多查詢 ${data.max_codes} 門課，超出的部分未查詢`, 'warning');
        }
        renderResults();
        renderWatchList();
    } catch (error) {
        console.error(error);
        ui.showAlert(error.message || '查詢缺額失敗，請稍後再試', 'danger');
    } finally {
        querying = false;
        if (button) {
            button.disabled = false;
            button.innerHTML = original || '<i class="fas fa-rotate me-2"></i>立即查詢';
        }
    }
}

function toggleAutoRefresh(enabled) {
    if (autoTimer) {
        clearInterval(autoTimer);
        autoTimer = null;
    }
    if (enabled) {
        autoTimer = setInterval(() => {
            // 只有停留在缺額監控分頁時才自動查，避免在背景一直打學校站台
            const section = document.getElementById('tab-vacancy');
            if (section && !section.classList.contains('d-none')) checkNow();
        }, AUTO_REFRESH_MS);
    }
}

/** 把目前課表裡的課一鍵加入監控 */
function importFromSchedule() {
    if (!state.selectedCourses.length) {
        ui.showAlert('課表目前沒有課程', 'warning');
        return;
    }
    const room = maxCodes() - watchList.length;
    if (room <= 0) {
        ui.showAlert(`監控清單已滿（上限 ${maxCodes()} 門）`, 'warning');
        return;
    }

    let added = 0;
    for (const course of state.selectedCourses) {
        if (added >= room) break;
        if (addCode(course.課程代碼, course.課程名稱)) added += 1;
    }

    renderWatchList();
    ui.showAlert(added ? `已加入 ${added} 門課程` : '課表中的課程都已在監控清單裡', added ? 'success' : 'info');
}

export function initVacancy() {
    load();
    renderWatchList();
    renderResults();

    document.getElementById('btn-check-vacancy')?.addEventListener('click', checkNow);
    document.getElementById('btn-import-schedule-vacancy')?.addEventListener('click', importFromSchedule);
    document.getElementById('vacancy-auto-refresh')?.addEventListener('change', event => {
        toggleAutoRefresh(event.target.checked);
        if (event.target.checked) ui.showAlert('已開啟自動更新（每分鐘一次）', 'info');
    });

    const addManual = () => {
        const input = document.getElementById('vacancy-code-input');
        if (!input) return;
        const codes = input.value.split(/[,\s]+/).filter(Boolean);
        if (!codes.length) {
            ui.showAlert('請輸入課程代碼', 'warning');
            return;
        }
        let added = 0;
        for (const code of codes) {
            if (watchList.length >= maxCodes()) {
                ui.showAlert(`監控清單已滿（上限 ${maxCodes()} 門）`, 'warning');
                break;
            }
            if (addCode(code)) added += 1;
        }
        input.value = '';
        renderWatchList();
        if (added) ui.showAlert(`已加入 ${added} 門課程`, 'success');
    };

    document.getElementById('btn-add-vacancy-code')?.addEventListener('click', addManual);
    document.getElementById('vacancy-code-input')?.addEventListener('keypress', event => {
        if (event.key === 'Enter') addManual();
    });

    window.vacancyRemove = removeCode;
}
