import * as config from './config.js';
import { state, saveToLocalStorage, loadFromLocalStorage, isSettledSemester, isCurrentSemesterPending } from './state.js';
import * as api from './api.js';
import * as utils from './utils.js';
import * as ui from './ui.js';
import * as find from './find.js';
import * as dashboard from './dashboard.js';
import * as vacancy from './vacancy.js';
import { initAssistant } from './assistant.js';
import { renderModelInfo } from './prediction.js';

let historyGroupsCache = [];
let historyChartInstance = null;

// /api/filters 的回傳（學制、學院、通識分類…），推薦頁與搜尋頁共用
let filterOptions = null;

// 找課頁的所有下拉。全部交給 Select2：原生與 Select2 混用時，收合狀態可以靠 CSS
// 對齊，但一展開就完全不同（原生是作業系統清單，Select2 是 HTML 面板）。
const FIND_SELECTS = [
    'find-category', 'find-general-group', 'find-level', 'find-division',
    'find-college', 'find-dept', 'find-grade', 'find-sort',
];

/**
 * 把原生 select 換成 Select2。
 *
 * allowClear 只對「可不選」的篩選開啟：Select2 會把 value="" 的第一個選項當成
 * placeholder 並從展開清單裡移除，沒有清除鈕的話，選了之後就再也回不到「全部」。
 * 學年度、學期這類必填欄位則不能開，清空會讓後續查詢失去依據。
 */
function initSelect2(id, { allowClear = false } = {}) {
    if (typeof jQuery === 'undefined' || !jQuery.fn.select2) return;

    const $el = jQuery(`#${id}`);
    if ($el.length === 0) return;

    if ($el.hasClass("select2-hidden-accessible")) {
        $el.select2('destroy');
    }

    $el.select2({
        theme: 'bootstrap-5',
        width: '100%',
        placeholder: $el.find('option:first').text(),
        allowClear,
        // 選項不多時隱藏搜尋框——「日夜間部」只有三個選項，給搜尋框沒有意義
        minimumResultsForSearch: $el.find('option').length > 10 ? 0 : Infinity,
        language: {
            noResults: () => "沒有找到相關選項"
        }
    });

    // Select2 是用 jQuery 的事件系統送出 change，而 find.js 是以原生
    // addEventListener 監聽——兩者不相通，選了選項卻不會觸發查詢。
    // 這裡補送一個原生事件；旗標用來擋住它再被 jQuery 接回來造成無限迴圈。
    $el.on('change.nativeBridge', function () {
        if (this.__bridging) return;
        this.__bridging = true;
        this.dispatchEvent(new Event('change', { bubbles: true }));
        this.__bridging = false;
    });
}

/** 找課頁的下拉：排序一定要有值，其餘都可以清回「全部」 */
function initFindSelect(id) {
    initSelect2(id, { allowClear: id !== 'find-sort' });
}

document.addEventListener('DOMContentLoaded', initializeApp);

async function initializeApp() {
    ui.initializeScheduleTable();
    initSettings();
    await loadSemesters();
    await loadCoursesData();
    populateCollegeSelect();
    initSystemAndLevelSelects();
    await loadDepartments();
    await loadFilterOptions();
    // 系所與年級都是可不選的篩選條件，一開始就列出全部，不強迫先選學院。
    // 年級原本只在學院／系所變動時才生成，未選學院時整個選單只有「全部年級」一項。
    await loadDepartmentsByCollege('', 'find-dept', '全部系所');
    updateFindGradeList();
    find.bindEvents();
    dashboard.initDashboard();
    vacancy.initVacancy();
    setupEventListeners();
    loadFromLocalStorage();
    cleanupSelectedCourses();
    window.debugState = state;
    ui.updateScheduleDisplay();
    ui.updateSelectedCoursesList();
    exposeGlobalFunctions();
    initAssistant();   // 不 await：靜態網站上要等 /api/ai/status 逾時，不能卡住其他初始化
    document.getElementById('logicInfoModal')?.addEventListener('show.bs.modal', () => renderModelInfo());

    if (window.toggleScheduleView) {
        const pref = localStorage.getItem('scheduleViewPref');
        window.toggleScheduleView(pref === 'cards' ? 'cards' : 'table');
    }

    window.addEventListener('resize', debounce(detectAndApplyScheduleView, 200));
    detectAndApplyScheduleView();
    ['select-year', 'select-semester', 'select-grade-schedule', 'select-class-schedule',
     'select-department-schedule'].forEach(id => initSelect2(id));
    FIND_SELECTS.forEach(initFindSelect);
}

// 設定功能邏輯：主題、視圖、重置
function initSettings() {
    // 1. 初始化主題
    const savedTheme = localStorage.getItem('appTheme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    
    // 更新 Radio 按鈕狀態
    const themeRadio = document.getElementById(`theme-${savedTheme}`);
    if (themeRadio) themeRadio.checked = true;

    // 監聽主題切換
    document.querySelectorAll('input[name="theme-options"]').forEach(input => {
        input.addEventListener('change', (e) => {
            const newTheme = e.target.value;
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('appTheme', newTheme);
            
            document.querySelectorAll('select').forEach(el => {
                if (jQuery(el).hasClass("select2-hidden-accessible")) {
                    const instance = jQuery(el).data('select2');
                    if(instance) {
                         jQuery(el).trigger('change.select2'); 
                    }
                }
            });
            const label = newTheme === 'dark' ? '深色模式' : '亮色模式';
            ui.showAlert(`已切換為${label}`, 'success');
        });
    });

    // 2. 初始化視圖
    const savedView = localStorage.getItem('scheduleViewPref') || 'table';
    const viewRadio = document.getElementById(`view-${savedView === 'auto' ? 'table' : savedView}`);
    if (viewRadio) viewRadio.checked = true;

    // 監聽視圖切換
    document.querySelectorAll('input[name="view-options"]').forEach(input => {
        input.addEventListener('change', (e) => {
            const newView = e.target.value;
            if (window.toggleScheduleView) {
                window.toggleScheduleView(newView);
            }
        });
    });

    // 3. 重置功能
    const resetBtn = document.getElementById('btn-reset-app');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            Swal.fire({
                title: '確定要重置嗎？',
                text: "這將清除所有已選課程、設定與暫存資料，回到初始狀態。",
                icon: 'warning',
                showCancelButton: true,
                confirmButtonText: '確定重置',
                confirmButtonColor: '#dc3545',
                cancelButtonText: '取消'
            }).then((result) => {
                if (result.isConfirmed) {
                    localStorage.clear();
                    location.reload(); // 重新整理頁面
                }
            });
        });
    }
}

/**
 * 取一次 /api/filters，推薦頁與搜尋頁共用。
 * 選項來自資料本身（部別、課程性質…），新學期多出新類別時前端不必改。
 */
async function loadFilterOptions() {
    try {
        filterOptions = await api.fetchFilterOptions(state.currentYear, state.currentSemester);
    } catch (error) {
        console.error('載入篩選選項失敗', error);
        filterOptions = null;
        return;
    }

    await find.initFilters(filterOptions);
    // initFilters 會直接改寫 innerHTML，Select2 的掛載會失效，必須重新初始化
    FIND_SELECTS.forEach(initFindSelect);
}

/**
 * 換學期後，篩選選項（各類別的門數）與統計都要重算。
 * 儀表板只作廢快取、不立刻重打 API，等使用者真的切到那個分頁才載入。
 */
async function onSemesterChanged() {
    dashboard.invalidateDashboard();
    await loadFilterOptions();
    const dashboardTab = document.getElementById('tab-dashboard');
    if (dashboardTab && !dashboardTab.classList.contains('d-none')) {
        dashboard.loadDashboard(true);
    }
}


function cleanupSelectedCourses() {
    if (!state.selectedCourses || state.selectedCourses.length === 0) return;
    const unique = [];
    const seen = new Set();
    let hasDuplicate = false;
    state.selectedCourses.forEach(c => {
        const key = `${c.課程代碼}_${c.序號}`;
        if (!seen.has(key)) {
            seen.add(key);
            unique.push(c);
        } else {
            hasDuplicate = true;
        }
    });
    if (hasDuplicate) {
        console.log(`[System] Cleaned duplicate courses`);
        state.selectedCourses = unique;
        saveToLocalStorage();
    }
}

// 學年度/學期選單改由 API 動態產生，新學期上線後前端不必再改死值
async function loadSemesters() {
    try {
        const data = await api.fetchSemesters();
        state.semesters = data.semesters || [];
    } catch (error) {
        console.error('載入學期列表失敗，改用預設值', error);
        state.semesters = [];
    }

    if (state.semesters.length > 0) {
        const latest = state.semesters[0];
        state.currentYear = latest.year;
        state.currentSemester = latest.semester;
    }

    populateSemesterSelects();
}

function populateSemesterSelects() {
    const yearSelect = document.getElementById('select-year');
    const semesterSelect = document.getElementById('select-semester');
    if (!yearSelect || !semesterSelect || state.semesters.length === 0) return;

    const years = [...new Set(state.semesters.map(s => s.year))].sort((a, b) => b - a);
    yearSelect.innerHTML = years
        .map(y => `<option value="${y}">${y}學年度</option>`)
        .join('');
    yearSelect.value = state.currentYear;

    populateSemesterOptions();
}

// 只列出該學年度實際有資料的學期
function populateSemesterOptions() {
    const semesterSelect = document.getElementById('select-semester');
    if (!semesterSelect) return;

    const available = state.semesters
        .filter(s => String(s.year) === String(state.currentYear))
        .map(s => s.semester)
        .sort((a, b) => a - b);

    if (available.length === 0) return;

    semesterSelect.innerHTML = available
        .map(s => `<option value="${s}">第${s}學期</option>`)
        .join('');

    if (!available.includes(state.currentSemester)) {
        state.currentSemester = available[available.length - 1];
    }
    semesterSelect.value = state.currentSemester;
}

// 預選登記中的學期，選上人數尚未公布，需明確告知使用者
function updatePendingSemesterNotice() {
    const notice = document.getElementById('pending-semester-notice');
    if (!notice) return;

    if (isCurrentSemesterPending()) {
        notice.classList.remove('d-none');
        notice.querySelector('.pending-semester-text').textContent =
            `${state.currentYear} 學年度第 ${state.currentSemester} 學期尚在預選登記中，選上人數與中籤率皆未公布，目前僅顯示上限與登記人數。`;
    } else {
        notice.classList.add('d-none');
    }
}

async function loadCoursesData() {
    try {
        const data = await api.fetchAllCourses(state.currentYear, state.currentSemester);
        state.allCoursesData = (data.courses || []).map(c => utils.normalizeCourse ? utils.normalizeCourse(c) : c);
        console.log(`載入 ${state.allCoursesData.length} 門課程`);
        populateCollegeSelect();
        initSystemAndLevelSelects();
        updatePendingSemesterNotice();
    } catch (error) {
        console.error(error);
        ui.showAlert('載入課程數據失敗', 'danger');
    }
}

function getCourseSystem(course) {
    const sys = course['學制(日/夜)'] || course.學制;
    if (sys) return sys;
    
    const className = course['開課班別(代表)'] || course.班級 || '';
    return className.includes('夜') ? '夜間部' : '日間部';
}

function getCourseLevel(course) {
    const lvl = course['部別(大學/碩士/博士)'] || course.部別;
    if (lvl) return lvl;

    const className = course['開課班別(代表)'] || course.班級 || '';
    const grade = String(course.年級 || '');
    if (className.includes('博') || grade.includes('博')) return '博士班';
    if (className.includes('碩') || grade.includes('碩')) return '碩士班';
    return '大學部';
}

function populateCollegeSelect() {
    const select = document.getElementById('select-college-schedule');
    if (!select) return;
    const current = select.value;
    const colleges = new Set();
    state.allCoursesData.forEach(c => { if(c.學院) colleges.add(c.學院); });
    select.innerHTML = '<option value="">選擇學院</option>';
    Array.from(colleges).sort().forEach(c => select.add(new Option(c, c)));
    if (current && colleges.has(current)) select.value = current;
    initSelect2('select-college-schedule');
}

function initSystemAndLevelSelects() {
    const sysSelect = document.getElementById('select-system-schedule');
    const lvlSelect = document.getElementById('select-level-schedule');
    if (!sysSelect || !lvlSelect) return;
    const curSys = sysSelect.value;
    const curLvl = lvlSelect.value;
    sysSelect.innerHTML = '<option value="">全部學制</option>';
    lvlSelect.innerHTML = '<option value="">全部部別</option>';
    const systems = new Set();
    const levels = new Set();
    state.allCoursesData.forEach(c => {
        systems.add(getCourseSystem(c));
        levels.add(getCourseLevel(c));
    });
    Array.from(systems).sort().reverse().forEach(s => sysSelect.add(new Option(s, s)));
    const levelOrder = ['大學部', '碩士班', '博士班'];
    Array.from(levels).sort((a, b) => {
        const ia = levelOrder.indexOf(a), ib = levelOrder.indexOf(b);
        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    }).forEach(l => lvlSelect.add(new Option(l, l)));
    if (curSys && systems.has(curSys)) sysSelect.value = curSys;
    if (curLvl && levels.has(curLvl)) lvlSelect.value = curLvl;
    initSelect2('select-system-schedule');
    initSelect2('select-level-schedule');
}

async function loadDepartments() {
    try {
        const data = await api.fetchDepartments(state.currentYear, state.currentSemester);
        const departments = data.departments || [];
        const select = document.getElementById('find-dept');
        if (select) {
            select.innerHTML = '<option value="">選擇系所</option>';
            departments.forEach(dept => {
                select.add(new Option(dept, dept));
            });
        }
        initFindSelect('find-dept');
    } catch (error) {
        console.error('載入系所列表失敗:', error);
    }
}

/**
 * 填入某學院底下的系所清單。
 * placeholder 由呼叫端指定：找課頁的系所是「可不選」的篩選條件（全部系所），
 * 課表頁的科系則是導入班級的必填步驟（選擇科系），兩者語意不同。
 */
async function loadDepartmentsByCollege(college, targetSelectId, placeholder = '選擇科系') {
    try {
        if (state.allCoursesData.length === 0) await loadCoursesData();
        const departments = new Set();
        
        state.allCoursesData.forEach(course => {
            const cCollege = (course.學院 || '').trim();
            const target = (college || '').trim();
            
            if ((target === "" || cCollege === target) && course.科系) {
                departments.add(course.科系);
            }
        });

        const select = document.getElementById(targetSelectId);
        if (select) {
            select.innerHTML = `<option value="">${placeholder}</option>`;
            Array.from(departments).sort().forEach(dept => {
                select.add(new Option(dept, dept));
            });
            (targetSelectId === 'find-dept' ? initFindSelect : initSelect2)(targetSelectId);
        }
    } catch (error) {
        console.error('載入科系列表失敗:', error);
    }
}

function updateGradeList(college, department, system, level) {
    const select = document.getElementById('select-grade-schedule');
    select.innerHTML = '<option value="">選擇年級</option>';
    
    const grades = new Set();
    const zhToNum = { '一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7' };
    
    state.allCoursesData.forEach(course => {
        const cCollege = (course.學院 || '').trim();
        const cDept = (course.科系 || '').trim();
        const cSys = getCourseSystem(course); 
        const cLvl = getCourseLevel(course);

        // 寬鬆比對
        const matchCollege = !college || !cCollege || cCollege === college;
        const matchDept = !department || !cDept || cDept === department;
        const matchSys = !system || cSys === system;
        const matchLvl = !level || cLvl === level;

        if (matchCollege && matchDept && matchSys && matchLvl) {
            let g = String(course.年級 || '').trim();
            
            if (!g || g === '碩士' || g === '博士' || g === '0') {
                const className = course['開課班別(代表)'] || '';
                for (const [zh, num] of Object.entries(zhToNum)) {
                    if (className.includes(zh)) {
                        g = num;
                        break;
                    }
                }
                if (!g || g === '碩士' || g === '博士') {
                     g = '不分年級';
                }
            }
            grades.add(g);
        }
    });

    const numMap = { '1': '一', '2': '二', '3': '三', '4': '四', '5': '五', '6': '六', '7': '七' };

    Array.from(grades).sort((a, b) => {
        if (a === '不分年級') return -1;
        if (b === '不分年級') return 1;
        const numA = parseInt(a.match(/\d+/)?.[0] || 0);
        const numB = parseInt(b.match(/\d+/)?.[0] || 0);
        return numA - numB;
    }).forEach(g => {
        let label = g;
        if (g !== '不分年級') {
            const num = g.match(/\d+/)?.[0] || g;
            const zh = numMap[num] || num;

            if (/^\d+$/.test(g)) {
                if (level === '碩士班') label = `碩${zh}`;
                else if (level === '博士班') label = `博${zh}`;
                else label = `${zh}年級`;
            }
        }
        select.add(new Option(label, g));
    });
    
    initSelect2('select-grade-schedule');
}

function updateClassList(college, department, system, level, grade) {
    const select = document.getElementById('select-class-schedule');
    select.innerHTML = '<option value="">選擇班級</option>';
    const classMap = new Map();
    const zhToNum = { '一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7' };
    
    state.allCoursesData.forEach(course => {
        const cCollege = (course.學院 || '').trim();
        const cDept = (course.科系 || '').trim();
        const cSys = getCourseSystem(course);
        const cLvl = getCourseLevel(course);
        
        let cGrade = String(course.年級 || '').trim();
        if (!cGrade || cGrade === '碩士' || cGrade === '博士' || cGrade === '0') {
            const classNameRaw = course['開課班別(代表)'] || '';
            for (const [zh, num] of Object.entries(zhToNum)) {
                if (classNameRaw.includes(zh)) {
                    cGrade = num;
                    break;
                }
            }
            if (!cGrade || cGrade === '碩士' || cGrade === '博士') cGrade = '不分年級';
        }

        const matchCollege = !college || !cCollege || cCollege === college;
        const matchDept = !department || !cDept || cDept === department;
        const matchGrade = (grade === '不分年級') ? (cGrade === '不分年級') : (cGrade === String(grade));
        const matchSys = !system || cSys === system;
        const matchLvl = !level || cLvl === level;

        if (matchCollege && matchDept && matchGrade && matchSys && matchLvl) {
            let displayName = course['開課班別(代表)'];
            let realClass = course.班級;

            if (!displayName && realClass) displayName = realClass;
            if (!displayName) displayName = '不分班';

            if (!classMap.has(displayName)) {
                const safeClass = realClass || displayName;
                const compositeKey = `${cSys}|${cLvl}|${safeClass}`;
                
                classMap.set(displayName, { name: displayName, value: compositeKey });
            }
        }
    });
    
    Array.from(classMap.values())
        .sort((a, b) => a.name.localeCompare(b.name))
        .forEach(item => select.add(new Option(item.name, item.value)));
        
    initSelect2('select-class-schedule');
}

async function handleImportCourses() {
    const college = document.getElementById('select-college-schedule').value;
    const department = document.getElementById('select-department-schedule').value;
    const grade = document.getElementById('select-grade-schedule').value;
    const compositeClassValue = document.getElementById('select-class-schedule').value;

    if (!college || !department || !grade || !compositeClassValue) {
        ui.showAlert('請完整選擇所有條件', 'warning');
        return;
    }

    const [targetSystem, targetLevel, targetClassName] = compositeClassValue.split('|');
    const btn = document.getElementById('btn-import');
    btn.disabled = true;
    const originalText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>處理中...';

    try {
        const targetCourses = state.allCoursesData.filter(course => {
            // 1. 基本欄位比對
            const cCollege = (course.學院 || '').trim();
            const cDept = (course.科系 || '').trim();
            if (college && cCollege && cCollege !== college) return false;
            if (department && cDept && cDept !== department) return false;
            if (getCourseSystem(course) !== targetSystem) return false;
            if (getCourseLevel(course) !== targetLevel) return false;

            // 2. 年級比對
            let cGrade = String(course.年級 || '').trim();
            if (!cGrade || cGrade === '碩士' || cGrade === '博士' || cGrade === '0') {
                 const classNameRaw = course['開課班別(代表)'] || '';
                 for (const [zh, num] of Object.entries(zhToNum)) {
                     if (classNameRaw.includes(zh)) {
                         cGrade = num;
                         break;
                     }
                 }
                 if (!cGrade || cGrade === '碩士' || cGrade === '博士') cGrade = '不分年級';
            }
            if (cGrade !== String(grade)) return false;

            // 3. 班級比對
            const cClass = course.班級 || '';
            const cRepresent = course['開課班別(代表)'] || '';
            
            if (cClass === targetClassName) return true;
            if (cRepresent === targetClassName) return true;
            if (cRepresent.includes(targetClassName)) return true;

            return false;
        });

        if (targetCourses.length === 0) {
            ui.showAlert('查無符合條件的課程', 'warning');
            btn.disabled = false;
            btn.innerHTML = originalText;
            return;
        }

        const uniqueTargets = [];
        const seenTargets = new Set();
        targetCourses.forEach(c => {
            const key = `${c.課程代碼}_${c.序號}`;
            if (!seenTargets.has(key)) {
                seenTargets.add(key);
                uniqueTargets.push(c);
            }
        });

        await new Promise(r => setTimeout(r, 200));
        let addedCount = 0;
        let skippedCount = 0;
        let unresolvedCount = 0;

        // 第一輪：先把不衝堂的課程全部加入，並蒐集衝堂的課程
        const conflictItems = [];
        for (const rawCourse of uniqueTargets) {
            const course = utils.normalizeCourse(rawCourse);
            if (utils.isCourseSelected(course)) {
                skippedCount++;
                continue;
            }

            const { conflicts } = utils.findConflictingCourses(course);
            if (conflicts.length === 0) {
                utils.addCourseToState(course);
                addedCount++;
            } else {
                conflictItems.push({ course, conflicts });
            }
        }

        // 第二輪：所有衝堂集中成一張列表，一次決定，不再逐門跳視窗
        if (conflictItems.length > 0) {
            const chosen = await ui.showBatchConflictResolutionModal(conflictItems);

            if (chosen === null) {
                skippedCount += conflictItems.length;
            } else {
                const chosenSet = new Set(chosen);
                conflictItems.forEach((item, idx) => {
                    if (!chosenSet.has(idx)) {
                        skippedCount++;
                        return;
                    }
                    item.conflicts.forEach(o => utils.removeCourseFromState(o.課程代碼, o.序號));
                    // 兩門都被勾選的新課彼此衝堂時，後者仍會加不進去
                    if (utils.addCourseToState(item.course) === true) {
                        addedCount++;
                    } else {
                        unresolvedCount++;
                    }
                });
            }
        }

        ui.updateScheduleDisplay();
        ui.updateSelectedCoursesList();
        saveToLocalStorage();

        const notes = [];
        if (skippedCount > 0) notes.push(`略過 ${skippedCount} 門`);
        if (unresolvedCount > 0) notes.push(`${unresolvedCount} 門因與其他新課程衝堂而未加入`);

        if (addedCount > 0) {
            ui.showAlert(`成功導入 ${addedCount} 門課程${notes.length ? ` (${notes.join('、')})` : ''}`, 'success');
        } else if (unresolvedCount > 0) {
            ui.showAlert(`未導入任何課程：${unresolvedCount} 門新課程彼此衝堂`, 'warning');
        } else {
            ui.showAlert('未導入任何新課程 (皆已存在或選擇略過)', 'info');
        }
    } catch (error) {
        console.error(error);
        ui.showAlert('導入過程發生錯誤', 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

function handleClearSchedule() {
    Swal.fire({
        title: '確定要清空所有課程嗎？',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: '確定',
        cancelButtonText: '取消'
    }).then(result => {
        if (result.isConfirmed) {
            state.selectedCourses = [];
            state.currentSchedule = {};
            for (let day = 1; day <= 7; day++) state.currentSchedule[day] = {};
            ui.updateScheduleDisplay();
            ui.updateSelectedCoursesList();
            saveToLocalStorage();
            ui.showAlert('課表已清空', 'info');
        }
    });
}

async function handleSearchHistory() {
    const query = document.getElementById('search-history-input').value.trim();
    if (!query) {
        ui.showAlert('請輸入搜尋關鍵字', 'warning');
        return;
    }
    const btn = document.getElementById('btn-search-history');
    const input = document.getElementById('search-history-input');
    btn.disabled = true;
    input.disabled = true;
    const originalBtnContent = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>搜尋中...';
    
    try {
        const data = await api.fetchHistory(query);
        displayHistoryResults(data.courses || [], query);
    } catch (error) {
        console.error(error);
        ui.showAlert('搜尋失敗，請稍後再試', 'danger');
    } finally {
        btn.disabled = false;
        input.disabled = false;
        btn.innerHTML = originalBtnContent;
    }
}

function openHistoryModal(index) {
    const group = historyGroupsCache[index];
    if (!group) return;

    document.getElementById('historyDetailTitle').textContent = group.name;
    document.getElementById('historyDetailSubtitle').textContent = `主要教師：${group.teacher} | ${group.dept}`;

    const sortedForChart = [...group.data].sort((a, b) => {
        if (a.學年度 !== b.學年度) return a.學年度 - b.學年度;
        return a.學期 - b.學期;
    });

    renderHistoryChart(sortedForChart);

    const sortedForList = [...group.data].sort((a, b) => {
        if (b.學年度 !== a.學年度) return b.學年度 - a.學年度;
        return b.學期 - a.學期;
    });

    const tbody = document.getElementById('historyDetailTableBody');
    const tableElement = document.querySelector('#historyDetailModal table');
    if (tableElement) {
        tableElement.innerHTML = `
            <thead class="table-light">
                <tr class="text-nowrap">
                    <th class="ps-3">學年度</th>
                    <th>教師</th>
                    <th>開課班別</th>
                    <th>時間地點</th>
                    <th class="text-center">登記人數</th>
                    <th class="text-center">選上/上限</th>
                    <th class="text-end pe-3">大綱</th>
                </tr>
            </thead>
            <tbody id="historyDetailTableBody"></tbody>
        `;
    }
    const newTbody = document.getElementById('historyDetailTableBody');
    
    newTbody.innerHTML = sortedForList.map(c => {
        const enrolled = c.選上人數 || 0;
        const capacity = c.上限人數 || 0;
        const registered = c.登記人數 || 0;
        const rate = capacity > 0 ? Math.round((enrolled / capacity) * 100) : 0;
        
        const syllabusUrl = c.教學大綱連結 || c['教學大綱連結'];
        const syllabusBtn = (syllabusUrl && syllabusUrl.includes('http'))
            ? `<a href="${syllabusUrl}" target="_blank" class="btn btn-sm btn-outline-secondary">
                 <i class="fas fa-external-link-alt"></i>
               </a>`
            : `<span class="text-muted small">-</span>`;

        // 預選登記中的學期選上人數尚未公布，顯示 0/上限 會被誤讀為「沒人選上」
        const pending = !isSettledSemester(c.學年度, c.學期);
        const statusBadge = pending
            ? `<span class="badge bg-secondary bg-opacity-10 text-secondary border border-secondary border-opacity-25 rounded-pill"
                     title="此學期尚在預選登記中，選上人數未公布">預選中 / ${capacity}</span>`
            : rate >= 100
                ? `<span class="badge bg-danger rounded-pill">${enrolled}/${capacity}</span>`
                : `<span class="badge bg-success bg-opacity-10 text-success border border-success border-opacity-25 rounded-pill">${enrolled}/${capacity}</span>`;

        const registeredDisplay = registered > 0
            ? `${registered}`
            : `-`;

        const teachers = c.教師列表 || c.教師姓名 || '';

        // 處理多個時間地點
        let timeLocationHtml = '';
        
        // 檢查是否有時間地點數組或字符串
        if (Array.isArray(c.時間地點)) {
            // 如果是數組，顯示所有時間地點
            timeLocationHtml = c.時間地點.map((item, idx) => {
                const day = item.星期 || c.星期 || '?';
                const start = item.起始節次 || c.起始節次 || '';
                const end = item.結束節次 || c.結束節次 || '';
                const location = item.上課地點 || c.上課地點 || '';
                return `<div class="mb-1">週${day} ${start}-${end}節 ${location}</div>`;
            }).join('');
        } else if (c.時間地點 && typeof c.時間地點 === 'string' && c.時間地點.includes('(')) {
            // 如果是字符串格式，嘗試解析多個時間地點
            // 格式可能是: "(一) 1-2 教室A (二) 3-4 教室B"
            const timePattern = /\(([一二三四五六日])\)\s*(\d+)-(\d+)\s*([^\s(]+)/g;
            const matches = [...c.時間地點.matchAll(timePattern)];
            if (matches.length > 0) {
                timeLocationHtml = matches.map(match => {
                    const day = match[1];
                    const start = match[2];
                    const end = match[3];
                    const location = match[4] || '';
                    return `<div class="mb-1">週${day} ${start}-${end}節 ${location}</div>`;
                }).join('');
            } else {
                // 如果解析失敗，使用原始數據
                timeLocationHtml = `
                    <div class="mb-1">週${c.星期 || '?'} ${c.起始節次 || ''}-${c.結束節次 || ''}節</div>
                    <div>${c.上課地點 || ''}</div>
                `;
            }
        } else {
            // 單個時間地點
            timeLocationHtml = `
                <div class="mb-1">週${c.星期 || '?'} ${c.起始節次 || ''}-${c.結束節次 || ''}節</div>
                <div>${c.上課地點 || ''}</div>
            `;
        }

        return `
            <tr>
                <td class="ps-3"><span class="fw-bold">${c.學年度}</span>-${c.學期}</td>
                <td class="small text-muted" style="max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${teachers}">
                    ${teachers}
                </td>
                <td>${c['開課班別(代表)'] || c.開課班別 || '-'}</td>
                <td class="small text-muted">${timeLocationHtml}</td>
                <td class="text-center">${registeredDisplay}</td>
                <td class="text-center">${statusBadge}</td>
                <td class="text-end pe-3">${syllabusBtn}</td>
            </tr>
        `;
    }).join('');

    const modal = new bootstrap.Modal(document.getElementById('historyDetailModal'));
    modal.show();
}

function renderHistoryChart(data) {
    const ctx = document.getElementById('historyDetailChart').getContext('2d');
    if (historyChartInstance) {
        historyChartInstance.destroy();
    }
    // 預選登記中的學期選上人數為 0，直接畫成歸零柱狀看起來像資料錯誤，
    // 因此改以半透明呈現並在 tooltip 註明狀態
    const pendingFlags = data.map(d => !isSettledSemester(d.學年度, d.學期));
    const labels = data.map((d, i) => `${d.學年度}-${d.學期}${pendingFlags[i] ? ' (預選中)' : ''}`);
    const enrolledData = data.map((d, i) => pendingFlags[i] ? null : (d.選上人數 || 0));
    const capacityData = data.map(d => d.上限人數 || 0);
    const registeredData = data.map(d => d.登記人數 || 0);

    historyChartInstance = new Chart(ctx, {
        type: 'bar',  // 主類型設為bar，但個別dataset可以覆蓋
        data: {
            labels: labels,
            datasets: [
                {
                    type: 'line',  // 登記人數使用折線圖
                    label: '登記人數',
                    data: registeredData,
                    borderColor: '#BC9F77',
                    backgroundColor: 'rgba(188, 159, 119, 0.1)',
                    borderWidth: 2,
                    tension: 0.3,
                    fill: false,
                    pointBackgroundColor: '#fff',
                    pointBorderColor: '#BC9F77',
                    pointRadius: 4,
                    pointHoverRadius: 6,
                    order: 1,  // 確保在tooltip中顯示在最上面
                    yAxisID: 'y1'  // 使用右Y軸
                },
                {
                    type: 'bar',  // 選上人數使用直條圖
                    label: '選上人數',
                    data: enrolledData,
                    backgroundColor: 'rgba(13, 110, 253, 0.6)',
                    borderColor: '#0d6efd',
                    borderWidth: 1,
                    order: 2,
                    yAxisID: 'y'
                },
                {
                    type: 'bar',  // 上限人數使用直條圖
                    label: '上限人數',
                    data: capacityData,
                    backgroundColor: pendingFlags.map(
                        p => p ? 'rgba(220, 53, 69, 0.12)' : 'rgba(220, 53, 69, 0.3)'
                    ),
                    borderColor: '#dc3545',
                    borderWidth: 1,
                    borderDash: [5, 5],
                    order: 3,
                    yAxisID: 'y'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top' },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(255, 255, 255, 0.9)',
                    titleColor: '#000',
                    bodyColor: '#666',
                    borderColor: '#ddd',
                    borderWidth: 1,
                    filter: function(tooltipItem) {
                        // 確保tooltip按照指定順序顯示：登記人數、選上人數、上限人數
                        return true;
                    },
                    callbacks: {
                        afterBody: function(items) {
                            if (items.length && pendingFlags[items[0].dataIndex]) {
                                return '\n⏳ 此學期尚在預選登記中，選上人數未公布';
                            }
                            return '';
                        }
                    },
                    itemSort: function(a, b) {
                        // 自定義排序：確保登記人數顯示在選上人數上面
                        const order = ['登記人數', '選上人數', '上限人數'];
                        const indexA = order.indexOf(a.dataset.label);
                        const indexB = order.indexOf(b.dataset.label);
                        if (indexA === -1) return 1;
                        if (indexB === -1) return -1;
                        return indexA - indexB;
                    }
                }
            },
            scales: {
                y: { 
                    type: 'linear',
                    position: 'left',
                    beginAtZero: true, 
                    grid: { 
                        borderDash: [2, 2],
                        drawOnChartArea: true
                    },
                    stacked: false,  // 不堆疊，讓直條圖並排顯示
                    title: {
                        display: true,
                        text: '選上/上限人數'
                    }
                },
                y1: {
                    type: 'linear',
                    position: 'right',
                    beginAtZero: true,
                    grid: {
                        drawOnChartArea: false,  // 右Y軸不顯示網格線，避免與左Y軸重疊
                    },
                    title: {
                        display: true,
                        text: '登記人數'
                    }
                },
                x: { 
                    grid: { display: false },
                    stacked: false  // 不堆疊
                }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            }
        }
    });
}

function displayHistoryResults(courses, query) {
    const container = document.getElementById('history-results-container');
    const title = document.getElementById('history-search-title');
    const count = document.getElementById('history-result-count');

    // 1. 去重邏輯
    const uniqueCourses = [];
    const seen = new Set();
    (courses || []).forEach(c => {
        const year = String(c.學年度 || '').trim();
        const sem = String(c.學期 || '').trim();
        const code = String(c.課程代碼 || '').trim();
        const serial = String(c.序號 || '').trim();
        const key = `${year}_${sem}_${code}_${serial}`;
        if (!seen.has(key)) {
            seen.add(key);
            uniqueCourses.push(c);
        }
    });

    if (title) title.textContent = `搜尋結果："${query}"`;
    if (count) count.textContent = `共 ${uniqueCourses.length} 筆開課紀錄`;

    if (!uniqueCourses || uniqueCourses.length === 0) {
        if (container) container.innerHTML = '<div class="col-12 text-center text-muted py-5">沒有找到相關資料</div>';
        return;
    }

    // 2. 分群邏輯
    const groups = {};
    uniqueCourses.forEach(c => {
        const name = c.課程名稱 || c.中文課程名稱;
        const teacher = c.教師姓名;
        const key = `${name}_${teacher}`; 
        
        if (!groups[key]) {
            groups[key] = {
                name: name,
                teacher: teacher,
                dept: c.科系 || '',
                data: []
            };
        }
        groups[key].data.push(c);
    });

    historyGroupsCache = Object.values(groups);

    // 3. 排序邏輯
    historyGroupsCache.sort((a, b) => {
        const getStats = (group) => {
            let sRateSum = 0, sSatSum = 0, vCount = 0;
            group.data.forEach(item => {
                // 預選登記中的學期登記人數仍在累積，納入會讓中籤率偏樂觀
                if (!isSettledSemester(item.學年度, item.學期)) return;

                const reg = parseFloat(item.登記人數 || 0);
                const limit = parseFloat(item.上限人數 || 0);

                if (reg > 0 && limit > 0) {
                    let rate = limit / reg;
                    if (rate > 1) rate = 1;
                    
                    sRateSum += rate;
                    sSatSum += (reg / limit);
                    vCount++;
                }
            });
            return {
                rate: vCount > 0 ? (sRateSum / vCount) : 0, 
                sat: vCount > 0 ? (sSatSum / vCount) : -1
            };
        };

        const statsA = getStats(a);
        const statsB = getStats(b);

        // 先比選上率 (越低代表越難選，排前面)
        if (statsA.rate !== statsB.rate) {
            const rA = statsA.rate === 0 ? 999 : statsA.rate;
            const rB = statsB.rate === 0 ? 999 : statsB.rate;
            return rA - rB;
        }
        // 再比飽和度 (越高代表越熱門)
        return statsB.sat - statsA.sat;
    });

    // 4. 渲染邏輯
    if (container) {
        container.innerHTML = historyGroupsCache.map((group, index) => {
            let sumRate = 0;
            let sumSaturation = 0;
            let validCount = 0;

            group.data.forEach(item => {
                // 同上：排除仍在預選登記中、人數尚未結算的學期
                if (!isSettledSemester(item.學年度, item.學期)) return;

                const reg = parseFloat(item.登記人數 || 0);
                const limit = parseFloat(item.上限人數 || 0);

                if (reg > 0 && limit > 0) {
                    let rate = limit / reg;
                    if (rate > 1) rate = 1;
                    sumRate += rate;

                    sumSaturation += (reg / limit);
                    validCount++;
                }
            });

            const avgRate = validCount > 0 ? (sumRate / validCount) : 0;
            const avgRatePercent = (avgRate * 100).toFixed(0);
            
            const avgSat = validCount > 0 ? (sumSaturation / validCount) : 0;
            const avgSatPercent = (avgSat * 100).toFixed(0);
            
            let badgesHtml = '';
            if (validCount > 0) {
                const rateClass = avgRate < 0.3 ? 'bg-danger' : (avgRate < 0.6 ? 'bg-warning text-dark' : 'bg-success'); 
                const rateIcon = avgRate < 0.3 ? '<i class="fas fa-exclamation-triangle me-1"></i>' : '<i class="bi bi-dice-5 me-1"></i>';
                
                const satClass = avgSat >= 1.5 ? 'bg-danger' : (avgSat >= 1.0 ? 'bg-primary saturation-badge' : 'bg-info saturation-badge text-white');

                badgesHtml = `
                    <div class="d-flex flex-wrap gap-1 mt-2">
                        <span class="badge ${rateClass} p-2" 
                              title="歷年平均中籤率: ${avgRatePercent}%\n公式: 上限人數 / 登記人數 (取上限 100%)">
                            ${rateIcon}中籤率 ${avgRatePercent}%
                        </span>
                        <span class="badge ${satClass} p-2 saturation-badge" 
                              title="歷年平均飽和度: ${avgSatPercent}%\n公式: 登記人數 / 上限人數">
                            <i class="bi bi-people-fill me-1"></i>飽和度 ${avgSatPercent}%
                        </span>
                    </div>`;
            } else {
                badgesHtml = `<span class="badge bg-light text-muted border p-2 mt-2">無完整人數資料</span>`;
            }

            const latest = group.data.sort((a, b) => {
                const yearA = parseInt(a.學年度 || 0);
                const yearB = parseInt(b.學年度 || 0);
                if (yearB !== yearA) return yearB - yearA;
                return (b.學期 || 0) - (a.學期 || 0);
            })[0];

            return `
                <div class="col-md-6 col-lg-4 mb-4">
                    <div class="card h-100 shadow-sm border-0 hover-shadow transition-all" 
                         style="cursor: pointer;" 
                         onclick="openHistoryModal(${index})">
                        <div class="card-body d-flex flex-column">
                            <div class="d-flex justify-content-between align-items-start mb-2">
                                <span class="badge bg-primary bg-opacity-10 text-primary">${group.dept}</span>
                                <span class="badge rounded-pill bg-light text-dark border">${group.data.length} 次開課</span>
                            </div>
                            
                            <h5 class="card-title fw-bold text-dark mb-1 text-truncate" title="${group.name}">
                                ${group.name}
                            </h5>
                            
                            <p class="card-text text-muted small mb-3">
                                <i class="fas fa-chalkboard-teacher me-1"></i> ${group.teacher}
                            </p>
                            
                            <div class="mt-auto pt-3 border-top">
                                <div class="d-flex flex-column gap-2">
                                    ${badgesHtml}
                                    <div class="d-flex justify-content-end">
                                        <small class="text-muted">
                                            最近: ${latest.學年度 || '?'}-${latest.學期 || '?'} <i class="fas fa-chevron-right ms-1"></i>
                                        </small>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }
}

function exposeGlobalFunctions() {
    // 統計儀表板改成進入分頁時才載入，避免每次開啟網站都多打一支較重的 API
    window.switchTab = (tabId) => {
        ui.switchTab(tabId);
        if (tabId === 'tab-dashboard') dashboard.loadDashboard();
    };
    
    window.toggleSidebar = () => {
        const sidebar = document.querySelector('.sidebar');
        const backdrop = document.getElementById('sidebarBackdrop');
        const toggles = document.querySelectorAll('.sidebar-toggle');
        const isOpen = sidebar.classList.toggle('show');
        if (backdrop) {
            if (isOpen) {
                backdrop.classList.add('show');
                backdrop.setAttribute('aria-hidden', 'false');
            } else {
                backdrop.classList.remove('show');
                backdrop.setAttribute('aria-hidden', 'true');
            }
        }
        toggles.forEach(btn => btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false'));
    };

    window.toggleScheduleView = (mode) => {
        const section = document.getElementById('tab-schedule');
        if (!section) return;
        section.setAttribute('data-view', mode);

        if (mode === 'auto') localStorage.removeItem('scheduleViewPref');
        else localStorage.setItem('scheduleViewPref', mode);
        
        ui.updateScheduleDisplay();
        
        if (mode === 'table') ui.showAlert('已切換為表格檢視', 'success');
        else if (mode === 'cards') ui.showAlert('已切換為卡片檢視', 'success');
    };
    
    window.removeCourse = (code, serial) => {
        const sCode = String(code);
        const sSerial = String(serial);
        const course = state.selectedCourses.find(c => 
            String(c.課程代碼) === sCode && 
            String(c.序號) === sSerial
        );
        const courseName = course ? (course.課程名稱 || course.中文課程名稱) : '此課程';
        Swal.fire({
            title: `確定要移除「${courseName}」嗎？`,
            text: "移除後將無法復原，需重新加入",
            icon: 'warning',
            showCancelButton: true,
            confirmButtonText: '確定移除',
            confirmButtonColor: '#dc3545', 
            cancelButtonText: '取消',
            cancelButtonColor: '#6c757d',
            reverseButtons: true 
        }).then((result) => {
            if (result.isConfirmed) {
                if (utils.removeCourseFromState(code, serial)) {
                    ui.updateScheduleDisplay();
                    ui.updateSelectedCoursesList();
                    find.refresh();
                    saveToLocalStorage();
                    ui.showAlert('課程已移除', 'info');
                }
            }
        });
    };
    window.showCourseDetail = (course) => ui.showCourseDetailModal(course);
    window.showCourseDetailModal = (index) => { if (state.recommendedCourses[index]) ui.showCourseDetailModal(state.recommendedCourses[index]); };
    window.showSelectedCourseDetail = (code, serial) => {
        const sCode = String(code);
        const sSerial = String(serial);
        const course = state.selectedCourses.find(c => 
            String(c.課程代碼) === sCode && 
            String(c.序號) === sSerial
        );
        if (course) {
            ui.showCourseDetailModal(course);
        }
    };
    window.addRecommendedCourse = (course) => {
        const result = utils.addCourseToState(course);
        if (result === true) {
            ui.updateScheduleDisplay();
            ui.updateSelectedCoursesList();
            // 課表變了，查詢結果的衝堂標記與「加入」按鈕狀態要跟著重畫
            find.refresh();
            saveToLocalStorage();
            const rawTotal = state.selectedCourses.reduce((sum, c) => sum + (parseFloat(c.學分) || 0), 0);
            const target = parseFloat(document.getElementById('find-target-credits')?.value) || 0;
            if (rawTotal > target) {
                 ui.showAlert(`課程已加入，但總學分(${rawTotal})已超過目標(${target})`, 'warning');
            } else {
                 ui.showAlert('課程已加入', 'success');
            }
        } else if (result === 'conflict') {
            ui.showAlert('時間衝突：該時段已有課程', 'warning');
        } else {
            ui.showAlert('該課程已在您的課表中', 'info');
        }
    };
    window.addRecommendedCourseByIndex = (index) => { if (state.recommendedCourses[index]) window.addRecommendedCourse(state.recommendedCourses[index]); };
    window.openHistoryModal = openHistoryModal;
}

function debounce(fn, wait = 150) {
    let timer = null;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), wait);
    };
}

function detectAndApplyScheduleView() {
    const section = document.getElementById('tab-schedule');
    if (!section) return;
    const pref = localStorage.getItem('scheduleViewPref');
    if (pref === 'table' || pref === 'cards') {
        section.setAttribute('data-view', pref);
    } else {
        if (section.getAttribute('data-view') === 'auto') {
            section.setAttribute('data-view', window.innerWidth < 768 ? 'cards' : 'table');
        }
    }
    ui.updateScheduleDisplay();
}

function updateFindGradeList() {
    const college = document.getElementById('find-college').value;
    const dept = document.getElementById('find-dept').value;
    const select = document.getElementById('find-grade');
    
    // 預設選項
    let html = '<option value="">全部年級</option>';
    
    if (!state.allCoursesData || state.allCoursesData.length === 0) {
        select.innerHTML = html;
        initFindSelect('find-grade');
        return;
    }

    const groups = {
        '大學部': new Set(),
        '碩士班': new Set(),
        '博士班': new Set()
    };

    state.allCoursesData.forEach(course => {
        const cCollege = (course.學院 || '').trim();
        if (college && cCollege !== college) return;

        const cDept = (course.科系 || '').trim();
        if (dept && cDept !== dept) return;

        const level = getCourseLevel(course);
        const gradeRaw = String(course.年級 || '').trim();
        const gradeMatch = gradeRaw.match(/\d+/);
        
        if (level && gradeMatch) {
            const g = gradeMatch[0];
            if (groups[level]) {
                groups[level].add(g);
            }
        }
    });

    // 數字轉中文對照表
    const numMap = { '1': '一', '2': '二', '3': '三', '4': '四', '5': '五', '6': '六', '7': '七' };

    const buildGroup = (lvl) => {
        const grades = Array.from(groups[lvl]).sort((a, b) => parseInt(a) - parseInt(b));
        if (grades.length === 0) return '';
        
        const options = grades.map(g => {
            const zh = numMap[g] || g; // 轉成中文數字
            let label = `${zh}年級`;
            if (lvl === '碩士班') label = `碩${zh}`;
            if (lvl === '博士班') label = `博${zh}`;
            // value 維持阿拉伯數字以便後端搜尋 (例如 "大學部|1")
            return `<option value="${lvl}|${g}">${label}</option>`; 
        }).join('');
        
        return `<optgroup label="${lvl}">${options}</optgroup>`;
    };

    html += buildGroup('大學部');
    html += buildGroup('碩士班');
    html += buildGroup('博士班');

    select.innerHTML = html;
    initFindSelect('find-grade');
}

function setupEventListeners() {
    document.addEventListener('click', e => {
        const sidebar = document.querySelector('.sidebar');
        if (sidebar.classList.contains('show') && !sidebar.contains(e.target) && !e.target.closest('.sidebar-toggle')) {
            sidebar.classList.remove('show');
            const backdrop = document.getElementById('sidebarBackdrop');
            if (backdrop) backdrop.classList.remove('show');
        }
    });

    document.getElementById('btn-import').addEventListener('click', handleImportCourses);
    document.getElementById('btn-clear').addEventListener('click', handleClearSchedule);
    document.getElementById('btn-search-history').addEventListener('click', handleSearchHistory);
    document.getElementById('search-history-input').addEventListener('keypress', e => {
        if (e.key === 'Enter') handleSearchHistory();
    });

    document.getElementById('select-year').addEventListener('change', function() {
        state.currentYear = parseInt(this.value);
        // 各學年度開放的學期不一定相同（例如最新學年可能只有第一學期）
        populateSemesterOptions();
        initSelect2('select-semester');
        loadCoursesData();
        onSemesterChanged();
    });
    document.getElementById('select-semester').addEventListener('change', function() {
        state.currentSemester = parseInt(this.value);
        loadCoursesData();
        onSemesterChanged();
    });

    jQuery('#find-college').on('change', function() {
        loadDepartmentsByCollege(this.value, 'find-dept', '全部系所');
        updateFindGradeList();
    });

    jQuery('#find-dept').on('change', function() {
        updateFindGradeList();
    });

    const updateGradeHandler = () => {
        const college = document.getElementById('select-college-schedule').value;
        const department = document.getElementById('select-department-schedule').value; 
        const sys = document.getElementById('select-system-schedule').value;
        const lvl = document.getElementById('select-level-schedule').value;
        
        document.getElementById('select-grade-schedule').innerHTML = '<option value="">選擇年級</option>';
        document.getElementById('select-class-schedule').innerHTML = '<option value="">選擇班級</option>';

        if (college && department) {
            updateGradeList(college, department, sys, lvl);
        }
    };

    jQuery('#select-system-schedule').on('change', updateGradeHandler);
    jQuery('#select-level-schedule').on('change', updateGradeHandler);
    
    jQuery('#select-college-schedule').on('change', function() {
        loadDepartmentsByCollege(this.value, 'select-department-schedule');
        
        document.getElementById('select-grade-schedule').innerHTML = '<option value="">選擇年級</option>';
        document.getElementById('select-class-schedule').innerHTML = '<option value="">選擇班級</option>';
        
        initSelect2('select-grade-schedule');
        initSelect2('select-class-schedule');
    });

    jQuery('#select-department-schedule').on('change', updateGradeHandler);

    jQuery('#select-grade-schedule').on('change', function() {
        const college = document.getElementById('select-college-schedule').value;
        const dept = document.getElementById('select-department-schedule').value;
        const sys = document.getElementById('select-system-schedule').value;
        const lvl = document.getElementById('select-level-schedule').value;
        const grade = this.value;
        if (college && dept && grade) {
            updateClassList(college, dept, sys, lvl, grade);
        }
    });

}