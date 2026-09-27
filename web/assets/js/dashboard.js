/**
 * 統計儀表板
 *
 * 資料來自 /api/dashboard。所有牽涉登記/選上的數字都只採計已結算學期，
 * 預選登記中的學期在趨勢圖上會斷線而不是畫出一個假的低點。
 *
 * 配色說明：三條人數趨勢線用的是通過 CVD 驗證的類別色順序（藍/橘/青），
 * 其餘都是單一數量級圖表，用同一個藍色階。亮色模式下青色對背景的對比為 2.82:1，
 * 低於 3:1，因此趨勢區一律同時提供數據表，不讓顏色單獨承載資訊。
 */

import * as api from './api.js';
import * as ui from './ui.js';
import { state } from './state.js';

// 類別色（藍/橘/青）：兩種模式都通過 all-pairs CVD 與一般視覺分離度檢查
const SERIES_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a'];
const SERIES_DARK = ['#3987e5', '#d95926', '#199e70'];

// 單一藍色階，用於熱區與各種數量級長條圖。
// 亮色模式由淺到深（近零貼近白底），深色模式反過來（近零貼近深底）。
const SEQ_LIGHT = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7',
                   '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b'];
const SEQ_DARK = ['#0d366b', '#104281', '#184f95', '#1c5cab', '#256abf', '#2a78d6',
                  '#3987e5', '#5598e7', '#6da7ec', '#86b6ef', '#9ec5f4'];

const charts = {};
let latestData = null;
let loading = false;

function isDark() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
}

function theme() {
    const dark = isDark();
    return {
        series: dark ? SERIES_DARK : SERIES_LIGHT,
        seq: dark ? SEQ_DARK : SEQ_LIGHT,
        ink: dark ? '#f1f5f9' : '#1e293b',
        muted: '#898781',
        grid: dark ? 'rgba(255,255,255,0.10)' : '#e1e0d9',
        surface: dark ? '#1e293b' : '#FFFFFF',
    };
}

function seqColor(ratio) {
    const steps = theme().seq;
    if (!(ratio > 0)) return steps[0];
    const index = Math.min(steps.length - 1, Math.round(ratio * (steps.length - 1)));
    return steps[index];
}

function destroyChart(key) {
    if (charts[key]) {
        charts[key].destroy();
        delete charts[key];
    }
}

function baseOptions(t) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            legend: {
                display: false,
                labels: { color: t.ink, usePointStyle: true, boxWidth: 8 },
            },
            tooltip: {
                backgroundColor: t.surface,
                titleColor: t.ink,
                bodyColor: t.ink,
                borderColor: t.grid,
                borderWidth: 1,
                padding: 10,
            },
        },
        scales: {
            x: { ticks: { color: t.muted }, grid: { display: false } },
            y: { ticks: { color: t.muted }, grid: { color: t.grid }, beginAtZero: true },
        },
    };
}

function statTile(label, value, hint) {
    return `
        <div class="col-6 col-lg-3">
            <div class="card border-0 shadow-sm h-100">
                <div class="card-body py-3">
                    <div class="text-muted small mb-1">${label}</div>
                    <div class="fs-4 fw-bold">${value}</div>
                    ${hint ? `<div class="text-muted small mt-1">${hint}</div>` : ''}
                </div>
            </div>
        </div>`;
}

function renderOverview(data) {
    const container = document.getElementById('dashboard-overview');
    if (!container) return;
    const o = data.overview || {};
    const pending = o.has_results === false
        ? '<span class="badge bg-warning text-dark ms-2">預選登記中</span>'
        : '';

    const title = document.getElementById('dashboard-title');
    if (title) title.innerHTML = `${o.year}-${o.semester} 學期總覽${pending}`;

    container.innerHTML = [
        statTile('開課總數', (o.total_courses || 0).toLocaleString(), `${(o.total_credits || 0).toLocaleString()} 學分`),
        statTile('授課教師', (o.total_teachers || 0).toLocaleString(), `全英語授課 ${o.english_courses || 0} 門`),
        statTile('招生上限', (o.capacity || 0).toLocaleString(),
            o.has_results ? `已選上 ${(o.enrolled || 0).toLocaleString()} 人次` : '尚未分發'),
        statTile('目前有缺額', (o.vacancy_courses || 0).toLocaleString(),
            o.saturation != null ? `整體飽和度 ${(o.saturation * 100).toFixed(0)}%` : ''),
    ].join('');
}

function renderTrend(data) {
    const canvas = document.getElementById('chart-trend');
    const t = theme();
    const rows = data.trend || [];
    if (!canvas || !rows.length) return;

    destroyChart('trend');
    const labels = rows.map(r => r.label);
    // 預選中的學期人數不完整，補 null 讓線斷開，而不是畫出一個誤導的低點
    const pick = (key) => rows.map(r => (r.has_results ? r[key] : null));

    charts.trend = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: '上限人數', data: rows.map(r => r.capacity), borderColor: t.series[0], backgroundColor: t.series[0] },
                { label: '登記人數', data: pick('registered'), borderColor: t.series[1], backgroundColor: t.series[1] },
                { label: '選上人數', data: pick('enrolled'), borderColor: t.series[2], backgroundColor: t.series[2] },
            ].map(d => ({ ...d, borderWidth: 2, pointRadius: 4, tension: 0.25, spanGaps: false })),
        },
        options: {
            ...baseOptions(t),
            plugins: {
                ...baseOptions(t).plugins,
                legend: { display: true, position: 'bottom', labels: { color: t.ink, usePointStyle: true, boxWidth: 8 } },
            },
        },
    });

    // 顏色對比不足時的替代管道：同一份數字以表格呈現
    const table = document.getElementById('dashboard-trend-table');
    if (table) {
        table.innerHTML = `
            <table class="table table-sm table-hover align-middle mb-0" style="font-variant-numeric: tabular-nums;">
                <thead><tr>
                    <th>學期</th><th class="text-end">開課數</th><th class="text-end">上限</th>
                    <th class="text-end">登記</th><th class="text-end">選上</th><th class="text-end">飽和度</th>
                </tr></thead>
                <tbody>
                    ${rows.slice().reverse().map(r => `
                        <tr>
                            <td class="fw-bold">${r.label}${r.has_results ? '' : ' <span class="badge bg-warning text-dark">預選中</span>'}</td>
                            <td class="text-end">${r.course_count.toLocaleString()}</td>
                            <td class="text-end">${r.capacity.toLocaleString()}</td>
                            <td class="text-end">${r.has_results ? r.registered.toLocaleString() : '—'}</td>
                            <td class="text-end">${r.has_results ? r.enrolled.toLocaleString() : '—'}</td>
                            <td class="text-end">${r.saturation != null ? (r.saturation * 100).toFixed(0) + '%' : '—'}</td>
                        </tr>`).join('')}
                </tbody>
            </table>`;
    }
}

function renderBar(canvasId, chartKey, items, labelKey, valueKey, horizontal = true) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !items || !items.length) return;
    const t = theme();
    destroyChart(chartKey);

    const values = items.map(i => i[valueKey]);
    const max = Math.max(...values, 1);

    charts[chartKey] = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: items.map(i => i[labelKey]),
            datasets: [{
                label: '開課數',
                data: values,
                backgroundColor: values.map(v => seqColor(v / max)),
                borderRadius: 4,
                borderSkipped: false,
            }],
        },
        options: {
            ...baseOptions(t),
            indexAxis: horizontal ? 'y' : 'x',
            plugins: { ...baseOptions(t).plugins, legend: { display: false } },
        },
    });
}

function renderHeatmap(data) {
    const container = document.getElementById('dashboard-heatmap');
    const heat = data.heatmap || {};
    if (!container || !heat.matrix || !heat.matrix.length) return;

    const weekdays = ['一', '二', '三', '四', '五', '六'];
    const flat = heat.matrix.flat();
    const max = Math.max(...flat, 1);
    const t = theme();

    const rows = heat.matrix.map((row, index) => `
        <tr>
            <th class="text-muted small fw-normal text-end pe-2" style="width:3.75rem;white-space:nowrap;">第${index + 1}節</th>
            ${row.map((count, dayIndex) => {
                const ratio = count / max;
                // 底色深的格子改用淺色文字，避免數字被吃掉
                const light = isDark() ? ratio > 0.5 : ratio < 0.55;
                return `<td class="text-center small" title="週${weekdays[dayIndex]} 第${index + 1}節：${count} 門課"
                            style="background:${seqColor(ratio)};color:${light ? '#0b0b0b' : '#ffffff'};
                                   border:2px solid ${t.surface};border-radius:4px;min-width:2.6rem;">${count || ''}</td>`;
            }).join('')}
        </tr>`).join('');

    container.innerHTML = `
        <div class="table-responsive">
            <table class="table table-borderless mb-2" style="font-variant-numeric: tabular-nums;">
                <thead><tr><th></th>${weekdays.map(d => `<th class="text-center small text-muted fw-normal">週${d}</th>`).join('')}</tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
        <div class="d-flex align-items-center gap-2 small text-muted">
            <span>少</span>
            ${[0, 0.25, 0.5, 0.75, 1].map(r => `<span style="display:inline-block;width:1.6rem;height:0.7rem;border-radius:2px;background:${seqColor(r)};"></span>`).join('')}
            <span>多（最多 ${max} 門）</span>
        </div>`;
}

function rankingList(items, containerId, emptyText) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!items || !items.length) {
        container.innerHTML = `<div class="text-muted small py-3">${emptyText}</div>`;
        return;
    }
    container.innerHTML = `
        <div class="list-group list-group-flush">
            ${items.map((item, index) => {
                const percent = Math.round(item.saturation * 100);
                return `
                <div class="list-group-item px-0 py-2 bg-transparent">
                    <div class="d-flex justify-content-between align-items-start gap-2">
                        <div class="flex-grow-1">
                            <span class="badge bg-secondary bg-opacity-25 text-secondary me-1">${index + 1}</span>
                            <span class="fw-bold">${item.課程名稱}</span>
                            <div class="small text-muted ms-4">
                                ${item.教師姓名 || '未定'} · ${item.學年度}-${item.學期} · 登記 ${item.登記人數}／上限 ${item.上限人數}
                            </div>
                        </div>
                        <span class="badge ${percent >= 100 ? 'bg-danger' : 'bg-success'}" style="font-variant-numeric: tabular-nums;">
                            ${percent}%
                        </span>
                    </div>
                </div>`;
            }).join('')}
        </div>`;
}

/** 各通識領域競爭程度：橫向長條，越長越難搶 */
function renderGeneralCompetition(data) {
    const items = data.general_competition || [];
    const canvas = document.getElementById('chart-general-competition');
    if (!canvas || !items.length) return;

    const t = theme();
    destroyChart('generalCompetition');
    const values = items.map(i => i.avg_saturation);
    const max = Math.max(...values, 1);

    charts.generalCompetition = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: items.map(i => i.name),
            datasets: [{
                label: '平均飽和度',
                data: values,
                backgroundColor: values.map(v => seqColor(v / max)),
                borderRadius: 4,
                borderSkipped: false,
            }],
        },
        options: {
            ...baseOptions(t),
            indexAxis: 'y',
            plugins: {
                ...baseOptions(t).plugins,
                legend: { display: false },
                tooltip: {
                    ...baseOptions(t).plugins.tooltip,
                    callbacks: {
                        label: ctx => {
                            const item = items[ctx.dataIndex];
                            return [
                                `平均飽和度 ${(item.avg_saturation * 100).toFixed(0)}%`,
                                `最高 ${(item.max_saturation * 100).toFixed(0)}%`,
                                `${item.course_count} 門課`,
                            ];
                        },
                    },
                },
            },
            scales: {
                ...baseOptions(t).scales,
                x: {
                    ticks: { color: t.muted, callback: v => `${(v * 100).toFixed(0)}%` },
                    grid: { color: t.grid },
                    beginAtZero: true,
                },
            },
        },
    });
}

/** 時段 × 平均飽和度熱區。數字越高越難搶，因此沿用「越深越擠」的色階方向 */
function renderPeriodAcceptance(data) {
    const container = document.getElementById('dashboard-period-acceptance');
    const pa = data.period_acceptance || {};
    if (!container || !pa.matrix || !pa.matrix.length) return;

    const weekdays = ['一', '二', '三', '四', '五', '六'];
    const flat = pa.matrix.flat().filter(v => v != null);
    if (!flat.length) { container.innerHTML = ''; return; }

    const max = Math.max(...flat);
    const min = Math.min(...flat);
    const span = max - min || 1;
    const t = theme();

    const rows = pa.matrix.map((row, index) => `
        <tr>
            <th class="text-muted small fw-normal text-end pe-2" style="width:3.75rem;white-space:nowrap;">第${index + 1}節</th>
            ${row.map((value, dayIndex) => {
                if (value == null) {
                    return `<td class="text-center small text-muted"
                                style="border:2px solid ${t.surface};min-width:2.8rem;">－</td>`;
                }
                const ratio = (value - min) / span;
                const light = isDark() ? ratio > 0.5 : ratio < 0.55;
                const count = (pa.counts && pa.counts[index]) ? pa.counts[index][dayIndex] : 0;
                return `<td class="text-center small"
                            title="週${weekdays[dayIndex]} 第${index + 1}節：平均飽和度 ${(value * 100).toFixed(0)}%（${count} 門課）"
                            style="background:${seqColor(ratio)};color:${light ? '#0b0b0b' : '#ffffff'};
                                   border:2px solid ${t.surface};border-radius:4px;min-width:2.8rem;">${(value * 100).toFixed(0)}</td>`;
            }).join('')}
        </tr>`).join('');

    container.innerHTML = `
        <div class="table-responsive">
            <table class="table table-borderless mb-2" style="font-variant-numeric: tabular-nums;">
                <thead><tr><th></th>${weekdays.map(d => `<th class="text-center small text-muted fw-normal">週${d}</th>`).join('')}</tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
        <div class="d-flex align-items-center gap-2 small text-muted">
            <span>好選（${(min * 100).toFixed(0)}%）</span>
            ${[0, 0.25, 0.5, 0.75, 1].map(r => `<span style="display:inline-block;width:1.6rem;height:0.7rem;border-radius:2px;background:${seqColor(r)};"></span>`).join('')}
            <span>難搶（${(max * 100).toFixed(0)}%）</span>
        </div>`;
}

function renderTeachers(data) {
    const container = document.getElementById('dashboard-teachers');
    const teachers = data.teachers || {};
    if (!container) return;

    const list = (items, valueFn, empty) => {
        if (!items || !items.length) return `<div class="text-muted small py-2">${empty}</div>`;
        return `<div class="list-group list-group-flush">${items.map((item, i) => `
            <div class="list-group-item px-0 py-2 bg-transparent d-flex justify-content-between align-items-center">
                <span>
                    <span class="badge bg-secondary bg-opacity-25 text-secondary me-2">${i + 1}</span>
                    ${item.name}
                </span>
                <span class="small text-muted" style="font-variant-numeric: tabular-nums;">${valueFn(item)}</span>
            </div>`).join('')}</div>`;
    };

    container.innerHTML = `
        <div class="row g-3">
            <div class="col-sm-6">
                <div class="small fw-bold text-muted mb-1">本學期開課最多</div>
                ${list(teachers.most_courses, i => `${i.course_count} 門`, '沒有資料')}
            </div>
            <div class="col-sm-6">
                <div class="small fw-bold text-muted mb-1">平均登記人數最多</div>
                ${list(teachers.most_popular, i => `${i.avg_registered} 人`, '沒有資料')}
            </div>
        </div>`;
}

function renderCollegeSaturation(data) {
    const container = document.getElementById('dashboard-college-saturation');
    const items = data.college_saturation || [];
    if (!container) return;
    if (!items.length) {
        container.innerHTML = '<div class="text-muted small py-2">沒有足夠資料</div>';
        return;
    }

    const max = Math.max(...items.map(i => i.avg_saturation), 0.01);
    container.innerHTML = items.map(item => {
        const percent = item.avg_saturation * 100;
        return `
            <div class="mb-3">
                <div class="d-flex justify-content-between align-items-baseline mb-1">
                    <span class="small fw-bold">${item.name}</span>
                    <span class="small text-muted" style="font-variant-numeric: tabular-nums;">
                        飽和度 ${percent.toFixed(0)}%
                        <span class="ms-2">全英語 ${(item.english_ratio * 100).toFixed(1)}%</span>
                    </span>
                </div>
                <div class="progress" style="height: 8px;">
                    <div class="progress-bar" role="progressbar"
                         style="width: ${(item.avg_saturation / max) * 100}%; background: ${seqColor(item.avg_saturation / max)};"></div>
                </div>
            </div>`;
    }).join('');
}

function renderAll(data) {
    latestData = data;
    renderOverview(data);
    renderTrend(data);
    renderBar('chart-colleges', 'colleges', data.colleges || [], 'name', 'course_count', true);
    renderBar('chart-levels', 'levels', data.levels || [], 'name', 'course_count', false);
    renderBar('chart-credits', 'credits',
        (data.credits || []).map(c => ({ name: `${c.credits} 學分`, course_count: c.course_count })),
        'name', 'course_count', false);
    renderHeatmap(data);
    renderGeneralCompetition(data);
    renderPeriodAcceptance(data);
    rankingList(data.hottest, 'dashboard-hottest', '沒有足夠資料');
    rankingList(data.easiest, 'dashboard-easiest', '沒有足夠資料');
    renderTeachers(data);
    renderCollegeSaturation(data);
}

export async function loadDashboard(force = false) {
    if (loading) return;
    if (latestData && !force) return;

    loading = true;
    const container = document.getElementById('dashboard-overview');
    if (container) {
        container.innerHTML = '<div class="col-12 text-center py-4 text-muted"><span class="spinner-border spinner-border-sm me-2"></span>載入統計資料...</div>';
    }

    try {
        const data = await api.fetchDashboard(state.currentYear, state.currentSemester);
        renderAll(data);
    } catch (error) {
        console.error(error);
        ui.showAlert(error.message || '載入統計資料失敗', 'danger');
        if (container) container.innerHTML = '<div class="col-12 text-muted py-4">載入失敗</div>';
    } finally {
        loading = false;
    }
}

export function initDashboard() {
    // 主題切換後圖表顏色要跟著換；Chart.js 不會自己重讀 CSS 變數，只能重畫
    const observer = new MutationObserver(mutations => {
        if (mutations.some(m => m.attributeName === 'data-theme') && latestData) {
            renderAll(latestData);
        }
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    document.getElementById('btn-refresh-dashboard')?.addEventListener('click', () => loadDashboard(true));
}

// 學期切換後統計要重算
export function invalidateDashboard() {
    latestData = null;
}
