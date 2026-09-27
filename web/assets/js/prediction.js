/**
 * 中籤預測的顯示元件（查詢結果、課程詳情、系統說明共用）
 * 預測在建置期由 scripts/build_static.py 寫進每門課的 admission_pred，靜態網站也看得到。
 */
import { fetchPredictionModel } from './api.js';

const pct = (v) => `${Math.round(v * 100)}%`;

function riskLevel(pFull) {
    if (pFull >= 0.5) return { cls: 'risk-high', label: '難搶', icon: 'fa-fire' };
    if (pFull >= 0.2) return { cls: 'risk-mid', label: '有風險', icon: 'fa-triangle-exclamation' };
    return { cls: 'risk-low', label: '好選', icon: 'fa-circle-check' };
}

/** 卡片上的小標章：以「爆滿機率」為主，滑過顯示中籤率區間 */
export function admissionBadge(pred) {
    if (!pred) return '';
    const r = riskLevel(pred.p_full);
    const tip = `爆滿機率 ${pct(pred.p_full)}；預估中籤率 ${pct(pred.est_admit)}（80% 區間 ${pct(pred.admit_lo)}–${pct(pred.admit_hi)}）`;
    return `<span class="badge admission-badge ${r.cls}" title="${tip}">
                <i class="fas ${r.icon} me-1"></i>${r.label} · 爆滿 ${pct(pred.p_full)}
            </span>`;
}

/** 課程詳情中的完整區塊 */
export function admissionDetail(pred, course) {
    if (!pred) {
        return `<div class="text-muted small">這門課沒有中籤預測（上限為 0，或屬於通常不經登記抽籤的課程類型）。</div>`;
    }
    const r = riskLevel(pred.p_full);
    const lo = Math.round(pred.admit_lo * 100);
    const hi = Math.round(pred.admit_hi * 100);
    const actual = (course && Number(course.登記人數) > 0 && Number(course.上限人數) > 0)
        ? Math.min(1, Number(course.上限人數) / Number(course.登記人數))
        : null;
    return `
        <div class="admission-detail ${r.cls}">
            <div class="d-flex justify-content-between align-items-baseline flex-wrap gap-2">
                <div>
                    <div class="small text-muted">爆滿機率（登記人數超過上限）</div>
                    <div class="fs-4 fw-bold">${pct(pred.p_full)} <span class="badge admission-badge ${r.cls} align-middle">${r.label}</span></div>
                </div>
                <div class="text-end">
                    <div class="small text-muted">預估中籤率</div>
                    <div class="fs-5 fw-bold">${pct(pred.est_admit)}</div>
                </div>
            </div>
            <div class="admit-range mt-2" aria-label="中籤率 80% 區間 ${lo}% 到 ${hi}%">
                <div class="admit-range-fill" style="left:${lo}%; width:${Math.max(hi - lo, 1)}%"></div>
            </div>
            <div class="d-flex justify-content-between small text-muted mt-1">
                <span>80% 區間 ${lo}%–${hi}%</span>
                ${actual !== null ? `<span>實際（上限÷登記）${pct(actual)}</span>` : ''}
            </div>
            <div class="small text-muted mt-2">
                <i class="fas fa-circle-info me-1"></i>樣本外預測：模型只用這學期<strong>以前</strong>的登記紀錄訓練。
                區間在新學期常偏窄（實測涵蓋率見「系統說明」），請當作參考而非保證。
            </div>
        </div>`;
}

/** 系統說明：從 API 讀驗證數字，避免說明文字與實際模型脫節 */
export async function renderModelInfo(containerId = 'model-info-body') {
    const el = document.getElementById(containerId);
    if (!el || el.dataset.loaded) return;
    try {
        const info = await fetchPredictionModel();
        if (!info) throw new Error('no model');
        const row = (list, subset, model) => (list || []).find(r => r.subset === subset && r.model === model);
        const fmt = (v) => (v === null || v === undefined) ? '—' : Number(v).toFixed(3);
        const coverage = (list, subset) => {
            const m = row(list, subset, 'M');
            return m && m.coverage80 != null ? `${Math.round(m.coverage80 * 100)}%` : '—';
        };
        const table = (title, list) => {
            const subsets = ['全部', '通識體育語文'];
            return `
                <div class="small fw-bold mt-2">${title}</div>
                <table class="table table-sm small mb-1">
                    <thead><tr><th></th><th>模型 PR-AUC</th><th>歷年平均 PR-AUC</th><th>模型 中籤率誤差</th><th>歷年平均 中籤率誤差</th></tr></thead>
                    <tbody>${subsets.map(s => {
                        const m = row(list, s, 'M'), b = row(list, s, 'B0 歷年平均');
                        if (!m || !b) return '';
                        return `<tr><td>${s}</td><td>${fmt(m.pr_auc)}</td><td>${fmt(b.pr_auc)}</td><td>${fmt(m.mae_admit)}</td><td>${fmt(b.mae_admit)}</td></tr>`;
                    }).join('')}</tbody>
                </table>`;
        };
        el.innerHTML = `
            <p class="mb-2">以 ${info.frozen_train_semesters?.[0]}～${info.frozen_train_semesters?.at(-1)} 的登記紀錄訓練（模型 <code>${info.model_version}</code>），
            預測每門課<strong>登記人數會不會超過上限</strong>，並估計中籤率（上限 ÷ 登記）。</p>
            ${table(`開發驗證（${(info.dev_test_semesters || []).join('、')}，逐學期只用過去資料）`, info.dev_summary)}
            ${info.holdout ? table(`保留學期 ${info.holdout.semester}（模型凍結後評估）`, info.holdout.results) : ''}
            ${info.holdout ? `<p class="mb-2">80% 區間在保留學期的實際涵蓋率：全部 ${coverage(info.holdout.results, '全部')}、通識 ${coverage(info.holdout.results, '通識體育語文')}（低於 80% 代表區間偏窄）。</p>` : ''}
            <p class="mb-0 text-muted">PR-AUC 越高越能辨認會爆滿的課；中籤率誤差越低越準。完整報告見 <code>docs/demand_model_report.md</code>。</p>`;
        el.dataset.loaded = '1';
    } catch (e) {
        el.innerHTML = '<span class="text-muted">這份資料包沒有中籤預測（執行 <code>python main.py train-demand</code> 後重新建置）。</span>';
    }
}
