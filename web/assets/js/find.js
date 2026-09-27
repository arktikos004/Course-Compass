/**
 * 找課 —— 搜尋與推薦合併後的單一查詢頁。
 *
 * 兩者原本是各自的分頁，但送出的是同一組條件（連 category 都是同一個欄位），
 * 後端也走同一個 apply_filters，等於同一件事維護了兩套介面。合併後差別只剩
 * 「排序選什麼」與「要不要把課表納入考量」，兩者都變成這頁上的選項。
 *
 * 結果寫進 state.recommendedCourses，讓既有的 showCourseDetailModal(index) /
 * addRecommendedCourseByIndex(index) 全域函式可以直接沿用索引取回課程。
 */

import * as api from './api.js';
import * as ui from './ui.js';
import * as utils from './utils.js';
import { state } from './state.js';

const PAGE_SIZE = 30;

let meta = { total: 0, has_more: false };
let lastParams = null;   // 「載入更多」沿用送出當下的條件，避免翻頁途中條件變動
let filterOptions = null;
let querying = false;

function el(id) {
    return document.getElementById(id);
}

/**
 * 程式改動 Select2 接管的選單後，要另外通知它更新顯示。
 *
 * Select2 只在收到 jQuery 的 change 事件時重繪，直接改 <select>.value 它不會知道，
 * 畫面會停在舊值——曾經出現過「全校性課程」與「通識類別」同時亮著，
 * 但底層其實只套用了其中一個的情況。
 *
 * 用 change.select2 這個命名空間：只觸發 Select2 內部的重繪，
 * 不會再跑一次我們自己的 change 處理器，因此不會多打一次查詢。
 */
function refreshSelectDisplay(element) {
    if (window.jQuery && element && element.classList.contains('select2-hidden-accessible')) {
        window.jQuery(element).trigger('change.select2');
    }
}

function checkedValues(selector) {
    return Array.from(document.querySelectorAll(selector))
        .filter(input => input.checked)
        .map(input => input.value);
}

function activeCategory() {
    return el('find-category')?.value || '';
}

/** 依 /api/filters 的回傳填入各下拉選單；選項全部由資料推導 */
export async function initFilters(preloaded = null) {
    filterOptions = preloaded;
    if (!filterOptions) {
        try {
            filterOptions = await api.fetchFilterOptions(state.currentYear, state.currentSemester);
        } catch (error) {
            console.error('載入篩選選項失敗', error);
            return;
        }
    }

    const fill = (id, items, placeholder) => {
        const select = el(id);
        if (!select) return;
        const previous = select.value;
        select.innerHTML = `<option value="">${placeholder}</option>` + (items || [])
            .map(item => `<option value="${item.name}">${item.name}（${item.course_count}）</option>`)
            .join('');
        if (previous) select.value = previous;
    };

    fill('find-category', filterOptions.categories, '全部課程');
    fill('find-level', filterOptions.levels, '全部學制');
    fill('find-division', filterOptions.divisions, '全部');
    fill('find-college', filterOptions.colleges, '全部學院');

    const groupSelect = el('find-general-group');
    if (groupSelect) {
        const previous = groupSelect.value;
        groupSelect.innerHTML = '<option value="">全部通識</option>' + (filterOptions.general || [])
            .map(g => `<option value="${g.group}">${g.group}（${g.course_count}）</option>`)
            .join('');
        if (previous) groupSelect.value = previous;
    }

    renderGeneralSubs();
}

/** 通識子類 chips：跨學院通識的文/理/工…、素養通識的兩個領域 */
function renderGeneralSubs() {
    const container = el('find-general-subs');
    const groupSelect = el('find-general-group');
    if (!container || !groupSelect) return;

    const group = (filterOptions?.general || []).find(g => g.group === groupSelect.value);
    const subs = group ? group.subs : [];

    if (!subs.length) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = `
        <div class="d-flex flex-wrap gap-1">
            ${subs.map((sub, i) => `
                <input type="checkbox" class="btn-check find-general-sub" id="fgsub-${i}" value="${sub.name}">
                <label class="btn btn-sm btn-outline-secondary" for="fgsub-${i}">${sub.name}（${sub.course_count}）</label>
            `).join('')}
        </div>`;
}

/**
 * 「全校性課程」與「通識類別」是同一個維度的粗細兩種切法，設成互斥。
 *
 * 不互斥會踩到一個查不出原因的空結果：校訂必修通識掛在各系班級下（電子一、公育一…），
 * 開課班別不是「核心通識」，所以「核心通識 + 校訂必修通識」交集必定是 0 門。
 * 「核心通識 + 跨學院通識」雖然合法，但跨學院通識本來就是核心通識的子集，
 * 單選通識類別結果完全相同，互斥不會少掉任何查得到的課。
 */
function syncCategoryAndGeneral(changed) {
    const category = el('find-category');
    const general = el('find-general-group');
    if (!category || !general) return;

    if (changed === 'general' && general.value) {
        category.value = '';
        refreshSelectDisplay(category);
    } else if (changed === 'category' && category.value) {
        general.value = '';
        refreshSelectDisplay(general);
        renderGeneralSubs();
    }
}

function collectParams() {
    const category = activeCategory();
    const rawGrade = el('find-grade')?.value || '';
    // 年級選單的 value 形如「大學部|1」，學制部分由 find-level 另外指定時以它為準
    const [gradeLevel, gradeNumber] = rawGrade.includes('|') ? rawGrade.split('|') : [null, rawGrade];

    const useSchedule = el('find-empty-slots')?.checked;
    const currentCourses = state.selectedCourses.map(c => ({ code: c.課程代碼, serial: c.序號 }));

    return {
        keyword: (el('find-keyword')?.value || '').trim() || null,
        year: state.currentYear,
        semester: state.currentSemester,
        category: category || null,
        level: el('find-level')?.value || gradeLevel || null,
        division: el('find-division')?.value || null,
        college: el('find-college')?.value || null,
        department: el('find-dept')?.value || null,
        grade: gradeNumber || null,
        general_group: el('find-general-group')?.value || null,
        general_subs: checkedValues('.find-general-sub'),
        preferred_days: checkedValues('.find-day'),
        min_credits: parseFloat(el('find-min-credits')?.value) || null,
        max_credits: parseFloat(el('find-max-credits')?.value) || null,
        english_only: el('find-english-only')?.checked || null,
        has_vacancy: el('find-has-vacancy')?.checked || null,
        // 跨學院通識不能選本院開的課；本院取自課表頁設定的學院
        exclude_college: (category === '核心通識' || el('find-general-group')?.value)
            ? (el('select-college-schedule')?.value || null)
            : null,
        empty_slots: useSchedule ? utils.getEmptySlots() : null,
        current_courses: currentCourses,
        target_credits: parseInt(el('find-target-credits')?.value) || null,
        exclude_conflicts: el('find-exclude-conflicts')?.checked || false,
        sort: el('find-sort')?.value || 'score',
    };
}

/**
 * 執行查詢。append=true 是「載入更多」，沿用上一次送出的條件只換 offset。
 */
export async function runQuery(append = false) {
    if (querying) return;

    const params = append && lastParams ? { ...lastParams } : collectParams();
    const offset = append ? state.recommendedCourses.length : 0;

    querying = true;
    const button = el('btn-find-run');
    const original = button ? button.innerHTML : '';
    if (button) {
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>查詢中...';
    }

    try {
        const data = await api.queryCourses({ ...params, limit: PAGE_SIZE, offset });
        const page = (data.courses || []).map(c => utils.normalizeCourse(c));

        state.recommendedCourses = append ? state.recommendedCourses.concat(page) : page;
        meta = { total: data.total || 0, has_more: !!data.has_more };
        lastParams = params;

        render();

        if (!append) {
            ui.showAlert(
                meta.total ? `找到 ${meta.total} 門課程` : '沒有符合條件的課程，試著放寬條件',
                meta.total ? 'success' : 'warning'
            );
        }
    } catch (error) {
        console.error(error);
        ui.showAlert(error.message || '查詢失敗，請稍後再試', 'danger');
    } finally {
        querying = false;
        if (button) {
            button.disabled = false;
            button.innerHTML = original || '<i class="fas fa-search me-2"></i>查詢';
        }
    }
}

/** 課表變動（加課／退課）後重畫，讓衝堂標記與按鈕狀態跟上 */
export function refresh() {
    if (state.recommendedCourses.length > 0) render();
}

function render() {
    ui.renderCourseResults(state.recommendedCourses, 'find-results');

    const summary = el('find-summary');
    if (summary) {
        summary.textContent = meta.total
            ? (meta.total > state.recommendedCourses.length
                ? `共 ${meta.total} 門，已顯示 ${state.recommendedCourses.length} 門`
                : `共 ${meta.total} 門`)
            : '';
    }

    // 分數欄位只有按推薦分數排序時才是排序依據，其他排序下仍顯示但不強調
    const hint = el('find-score-hint');
    if (hint) hint.classList.toggle('opacity-50', (el('find-sort')?.value || 'score') !== 'score');

    const more = el('find-load-more');
    if (more) {
        more.innerHTML = meta.has_more
            ? '<button class="btn btn-outline-primary" onclick="findLoadMore()"><i class="fas fa-angles-down me-2"></i>載入更多</button>'
            : (state.recommendedCourses.length ? '<span class="text-muted small">已顯示全部結果</span>' : '');
    }
}

function reset() {
    ['find-keyword', 'find-min-credits', 'find-max-credits'].forEach(id => {
        const input = el(id);
        if (input) input.value = '';
    });
    ['find-category', 'find-level', 'find-division', 'find-college', 'find-general-group',
     'find-dept', 'find-grade'].forEach(id => {
        const select = el(id);
        if (!select) return;
        select.value = '';
        refreshSelectDisplay(select);
    });
    document.querySelectorAll('.find-day, .find-general-sub').forEach(input => { input.checked = false; });
    ['find-has-vacancy', 'find-english-only', 'find-empty-slots', 'find-exclude-conflicts'].forEach(id => {
        const input = el(id);
        if (input) input.checked = false;
    });

    const sort = el('find-sort');
    if (sort) {
        sort.value = 'score';
        refreshSelectDisplay(sort);
    }

    renderGeneralSubs();

    state.recommendedCourses = [];
    meta = { total: 0, has_more: false };
    lastParams = null;

    const results = el('find-results');
    if (results) {
        results.innerHTML = `
            <div class="col-12 text-center py-5 text-muted">
                <i class="fas fa-compass fa-3x mb-3 opacity-25"></i>
                <p class="mb-0">輸入關鍵字或選擇條件後開始查詢</p>
            </div>`;
    }
    const summary = el('find-summary');
    if (summary) summary.textContent = '';
    const more = el('find-load-more');
    if (more) more.innerHTML = '';
}

export function bindEvents() {
    el('btn-find-run')?.addEventListener('click', () => runQuery(false));
    el('btn-find-reset')?.addEventListener('click', reset);
    el('find-keyword')?.addEventListener('keypress', event => {
        if (event.key === 'Enter') runQuery(false);
    });

    // 分類是主要決策，選了就直接查
    el('find-category')?.addEventListener('change', function () {
        syncCategoryAndGeneral('category');
        runQuery(false);
    });

    el('find-general-group')?.addEventListener('change', () => {
        syncCategoryAndGeneral('general');
        renderGeneralSubs();
        runQuery(false);
    });
    el('find-general-subs')?.addEventListener('change', event => {
        if (event.target.classList.contains('find-general-sub')) runQuery(false);
    });

    // 下拉條件改了就重查；勾選類的條件成組調整，交給「查詢」按鈕
    ['find-level', 'find-division', 'find-sort'].forEach(id => {
        el(id)?.addEventListener('change', () => runQuery(false));
    });

    const target = el('find-target-credits');
    if (target) {
        target.addEventListener('input', function () {
            const label = el('find-target-credits-value');
            if (label) label.textContent = this.value;
        });
        // 用 change 而非 input：拖曳過程中不要每動一格就打一次 API
        target.addEventListener('change', () => {
            if (state.recommendedCourses.length > 0) runQuery(false);
        });
    }

    // 呈現方式是純前端的事，直接用現有資料重畫
    const savedView = ui.getResultView();
    const savedRadio = el(`find-view-${savedView}`);
    if (savedRadio) savedRadio.checked = true;
    document.querySelectorAll('input[name="find-view"]').forEach(input => {
        input.addEventListener('change', event => {
            ui.setResultView(event.target.value);
            if (state.recommendedCourses.length > 0) render();
        });
    });

    window.findLoadMore = () => runQuery(true);
}
