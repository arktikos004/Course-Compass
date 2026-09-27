import { WEEKDAYS, WEEKDAY_MAP, PERIOD_TIMES, PERIOD_ORDER } from './config.js';
import { state } from './state.js';
import { checkTimeConflict, isCourseSelected } from './utils.js';
import * as api from './api.js';
import { admissionBadge, admissionDetail } from './prediction.js';
import { bindSyllabusQA } from './assistant.js';

export function showAlert(message, type = 'info') {
    const iconMap = { info: 'info', success: 'success', warning: 'warning', danger: 'error', error: 'error' };
    const icon = iconMap[type] || type;
    Swal.fire({
        toast: true,
        position: 'top-end',
        icon: icon,
        title: message,
        showConfirmButton: false,
        timer: 3000,
        timerProgressBar: true,
    });
}

export function initializeScheduleTable() {
    const container = document.getElementById('schedule-table');
    let html = '<table class="schedule-table table-bordered"><thead><tr><th>節次</th>';
    for (let day of WEEKDAYS) html += `<th>週${day}</th>`;
    html += '</tr></thead><tbody>';
    
    for (let period of PERIOD_ORDER) {
        const timeStr = PERIOD_TIMES[period] || '';
        const label = period === 14 ? '中午' : `第${period}節`;
        
        html += `<tr><td class="time-cell">
                    <div class="fw-bold">${label}</div>
                    <div class="small text-muted" style="font-size: 0.75rem;">${timeStr}</div>
                 </td>`;
        for (let day of WEEKDAYS) {
            html += `<td class="schedule-cell-empty" data-day="${WEEKDAY_MAP[day]}" data-period="${period}"></td>`;
        }
        html += '</tr>';
    }
    html += '</tbody></table>';
    container.innerHTML = html;
}

export function updateScheduleDisplay() {
    document.querySelectorAll('td[data-day][data-period]').forEach(cell => {
        cell.innerHTML = '';
        cell.className = 'schedule-cell-empty'; 
        cell.style.display = '';              
        cell.removeAttribute('rowspan');
        cell.onclick = null;
    });
    
    state.selectedCourses.forEach(course => {
        const day = WEEKDAY_MAP[course.星期] || parseInt(course.星期);
        const startPeriod = parseInt(course.起始節次);
        const endPeriod = parseInt(course.結束節次);
        
        if (day && startPeriod && endPeriod) {
            const coveredPeriods = [];
            for (let p = startPeriod; p <= endPeriod; p++) {
                coveredPeriods.push(p);
            }
            
            const groups = [];
            let currentGroup = [];
            
            coveredPeriods.forEach(p => {
                const pIndex = PERIOD_ORDER.indexOf(p);
                if (pIndex === -1) return;
                
                if (currentGroup.length === 0) {
                    currentGroup.push(p);
                } else {
                    const lastP = currentGroup[currentGroup.length - 1];
                    const lastIndex = PERIOD_ORDER.indexOf(lastP);
                    if (pIndex === lastIndex + 1) {
                        currentGroup.push(p);
                    } else {
                        groups.push(currentGroup);
                        currentGroup = [p];
                    }
                }
            });
            if (currentGroup.length > 0) groups.push(currentGroup);

            groups.forEach(group => {
                const firstP = group[0];
                const span = group.length;
                const cell = document.querySelector(`td[data-day="${day}"][data-period="${firstP}"]`);
                
                if (cell) {
                    cell.rowSpan = span;
                    
                    let courseType = 'course-elective';
                    if (course.課程性質?.includes('必修')) courseType = 'course-required';
                    else if (course.課程性質?.match(/通識/)) courseType = 'course-general';
                    else if (course.課程性質?.match(/國文|英文/)) courseType = 'course-language';
                    
                    cell.className = `schedule-cell ${courseType}`;
                    cell.innerHTML = `
                        <div class="course-name">${course.課程名稱 || course.中文課程名稱 || ''}</div>
                        <div class="course-teacher">${course.教師姓名 || ''}</div>
                        <div class="course-info">${course.上課地點 || ''}</div>
                    `;
                    
                    cell.onclick = () => window.showCourseDetail(course);
                    
                    for (let i = 1; i < group.length; i++) {
                        const nextP = group[i];
                        const nextCell = document.querySelector(`td[data-day="${day}"][data-period="${nextP}"]`);
                        if (nextCell) nextCell.style.display = 'none';
                    }
                }
            });
        }
    });

    renderScheduleCards();
}

export function renderScheduleCards() {
    const container = document.getElementById('schedule-cards');
    if(!container) return;

    if (state.selectedCourses.length === 0) {
        container.innerHTML = `<div class="text-center text-muted py-4">尚未選擇任何課程</div>`;
        return;
    }

    const grouped = {};
    state.selectedCourses.forEach(course => {
        let day = course.星期 || '0';
        if (/^\d+$/.test(String(day))) {
            const map = { '1':'一','2':'二','3':'三','4':'四','5':'五','6':'六','7':'日' };
            day = map[String(day)] || String(day);
        }
        if (!grouped[day]) grouped[day] = [];
        grouped[day].push(course);
    });

    const days = Object.keys(grouped).sort((a,b)=> {
        const aIdx = WEEKDAYS.indexOf(a) === -1 ? 99 : WEEKDAYS.indexOf(a);
        const bIdx = WEEKDAYS.indexOf(b) === -1 ? 99 : WEEKDAYS.indexOf(b);
        return aIdx - bIdx;
    });

    const html = days.map(day => {
        const courses = grouped[day].sort((a,b)=>parseInt(a.起始節次) - parseInt(b.起始節次));
        const dayLabel = `週${day}`;
        const list = courses.map(course => `
            <div class="card course-card">
                <div class="card-body d-flex justify-content-between align-items-start">
                    <div>
                        <div class="course-time">${course.起始節次}-${course.結束節次}節</div>
                        <div class="fw-bold text-truncate">${course.課程名稱 || course.中文課程名稱 || ''}</div>
                        <div class="course-meta small text-muted">${course.教師姓名 || ''} • ${course.學分 || 0} 學分</div>
                        <div class="course-meta small text-muted">${course.上課地點 || ''}</div>
                    </div>
                    <div class="d-flex flex-column gap-2 ms-3">
                        <button class="btn btn-sm btn-outline-primary" onclick="showSelectedCourseDetail('${course.課程代碼}','${course.序號}')">詳情</button>
                        <button class="btn btn-sm btn-outline-danger" onclick="removeCourse('${course.課程代碼}', '${course.序號}')">移除</button>
                    </div>
                </div>
            </div>
        `).join('');

        return `
            <div class="day-group">
                <div class="day-header">${dayLabel}</div>
                ${list}
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

export function updateSelectedCoursesList() {
    const container = document.getElementById('selected-courses-list');
    
    const rawTotal = state.selectedCourses.reduce((sum, c) => sum + (parseFloat(c.學分) || 0), 0);
    const totalCredits = Number.isInteger(rawTotal) ? rawTotal : rawTotal.toFixed(1);
    
    const targetInput = document.getElementById('find-target-credits');
    const target = targetInput ? (parseFloat(targetInput.value) || 0) : 0;
    
    const isOver = target > 0 && rawTotal > target;

    const updateElement = (id) => {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = totalCredits;
            if (isOver) {
                el.classList.add('text-danger', 'fw-bold'); 
            } else {
                el.classList.remove('text-danger', 'fw-bold');
            }
        }
    };

    updateElement('total-credits');
    updateElement('current-credits');
    
    if (state.selectedCourses.length === 0) {
        container.innerHTML = '<div class="col-12 text-center text-muted py-4">尚未選擇任何課程</div>';
        return;
    }
    
    container.innerHTML = state.selectedCourses.map(course => `
        <div class="col-md-6 col-lg-4">
            <div class="card course-card h-100">
                <div class="card-body">
                    <h6 class="card-title">${course.課程名稱 || course.課程名稱}</h6>
                    <p class="card-text small text-muted mb-2">${course.教師姓名 || ''} • ${course.學分 || 0} 學分</p>
                    <p class="card-text small text-muted mb-2"><i class="fas fa-university me-1"></i>${course.學院 || ''} ${course.科系 || ''}</p>
                    <button class="btn btn-sm btn-outline-danger" onclick="removeCourse('${course.課程代碼}', '${course.序號}')">
                        <i class="fas fa-trash me-1"></i>移除
                    </button>
                </div>
            </div>
        </div>
    `).join('');
}

// 查詢結果的呈現方式：'card'（適合瀏覽）或 'list'（一次看得到比較多門課）
let resultView = localStorage.getItem('resultViewPref') === 'list' ? 'list' : 'card';

export function getResultView() {
    return resultView;
}

export function setResultView(view) {
    resultView = view === 'list' ? 'list' : 'card';
    localStorage.setItem('resultViewPref', resultView);
}

/** 歷年中籤率徽章；沒有歷年資料時明說「無資料」而不是留白 */
function acceptanceBadge(course) {
    if (course.historical_acceptance_rate == null) {
        return '<span class="badge bg-light text-muted border">無選上率資料</span>';
    }
    const rate = parseFloat(course.historical_acceptance_rate);
    const percent = (rate * 100).toFixed(0);
    const cls = rate < 0.5 ? 'bg-danger' : 'bg-success';
    const icon = rate < 0.5 ? '<i class="fas fa-fire me-1"></i>' : '';
    return `<span class="badge ${cls}" title="歷年平均選上率：${percent}%（排除登記=0）">${icon}選上率 ${percent}%</span>`;
}

/**
 * 推薦分數（0~100）與其組成。
 * 把依據攤在 title 裡，避免給一個無從檢驗的黑箱數字。
 */
function scoreParts(course) {
    const detail = course.score_detail;
    if (course.recommend_score == null || !detail) return { html: '', reasons: '' };

    const reasons = [
        `選上機率 ${Math.round(detail.chance * 100)}%（依${detail.chance_source}）`,
        detail.vacancy_seats > 0 ? `目前尚有 ${detail.vacancy_seats} 個名額` : '目前已額滿',
        detail.credit_fit === 0 ? '學分超出剩餘目標額度' : null,
        detail.has_conflict ? '與現有課表衝堂，已降權排序' : null,
    ].filter(Boolean).join('；');

    const tone = course.recommend_score >= 70 ? 'bg-success'
        : (course.recommend_score >= 45 ? 'bg-warning text-dark' : 'bg-secondary');

    return {
        reasons,
        html: `<span class="badge ${tone}" title="${reasons}" style="font-variant-numeric: tabular-nums;">
                   <i class="fas fa-star me-1"></i>${course.recommend_score}
               </span>`,
    };
}

function courseTime(course) {
    return (course.星期 && course.起始節次)
        ? `週${course.星期} ${course.起始節次}-${course.結束節次}節`
        : '時間未定';
}

/** 詳情／加入按鈕。extra 讓卡片檢視傳 flex-grow-1 把兩顆按鈕撐滿一列 */
function actionButtons(index, status, extra = '') {
    const detail = `<button class="btn btn-sm btn-outline-primary ${extra}" onclick='showCourseDetailModal(${index})'>詳情</button>`;
    if (status === 'selected') {
        return `${detail}<button class="btn btn-sm btn-success ${extra}" disabled>
            <i class="fas fa-check me-1"></i>已加入</button>`;
    }
    if (status === 'conflict') {
        return `${detail}<button class="btn btn-sm btn-secondary ${extra}" disabled>衝堂</button>`;
    }
    return `${detail}<button class="btn btn-sm btn-primary ${extra}"
        onclick='addRecommendedCourseByIndex(${index})'>加入</button>`;
}

function renderResultCards(courses) {
    // 條件列改成橫向後結果區是整個寬度，寬螢幕一排可以放到 4 張卡
    return courses.map((course, index) => {
        const status = courseStatus(course);
        const score = scoreParts(course);
        const border = status === 'conflict' ? 'border-danger'
            : (status === 'selected' ? 'border-success' : 'border-0 shadow-sm');
        const corner = status === 'selected'
            ? '<div class="position-absolute top-0 start-0 m-2"><span class="badge bg-success"><i class="fas fa-check me-1"></i>已加入</span></div>'
            : (status === 'conflict'
                ? '<div class="position-absolute top-0 start-0 m-2"><span class="badge bg-danger">衝堂</span></div>'
                : '');

        return `
            <div class="col-sm-6 col-lg-4 col-xxl-3">
                <div class="card course-card h-100 ${border}">
                    ${corner}

                    <div class="card-body d-flex flex-column p-3">
                        <div class="d-flex justify-content-between align-items-start mb-2 gap-1">
                            <span class="badge bg-primary bg-opacity-10 text-primary">${course.學分}學分</span>
                            <div class="d-flex gap-1 flex-wrap justify-content-end">
                                ${score.html}
                                ${acceptanceBadge(course)}
                            </div>
                        </div>

                        <h5 class="card-title fw-bold text-dark mb-1 text-truncate" title="${course.課程名稱}">
                            <span class="text-secondary fw-normal me-1" style="font-size: 0.9em;">${course.課程代碼}</span>
                            ${course.課程名稱 || course.中文課程名稱}
                        </h5>
                        <div class="small text-muted ${course.admission_pred ? 'mb-2' : 'mb-3'}">
                            ${course.教師姓名} <span class="mx-1">•</span> ${course.科系 || ''}
                        </div>
                        ${course.admission_pred ? `<div class="mb-3">${admissionBadge(course.admission_pred)}</div>` : ''}

                        <div class="mt-auto">
                            <div class="d-flex align-items-center mb-3 text-secondary small">
                                <i class="far fa-clock me-2"></i> ${courseTime(course)}
                                <small class="text-muted ms-auto"><i class="fas fa-user-friends me-1"></i>${course.選上人數}/${course.上限人數}</small>
                            </div>

                            <div class="d-flex gap-2">
                                ${actionButtons(index, status, 'flex-grow-1')}
                            </div>
                        </div>
                    </div>
                </div>
            </div>`;
    }).join('');
}

function renderResultList(courses) {
    const rows = courses.map((course, index) => {
        const status = courseStatus(course);
        const score = scoreParts(course);
        const seats = course.score_detail ? course.score_detail.vacancy_seats : null;

        // 用固定欄寬的 grid 而非 flex：徽章文字長度不一（「已額滿」比「尚有 18 名額」窄、
        // 「無選上率資料」又更寬），flex 會讓每一列的徽章落在不同的水平位置，看起來參差。
        // grid 讓同一欄的內容永遠對齊，缺值的格子留空即可。
        const seatBadge = seats == null ? ''
            : (seats > 0
                ? `<span class="badge bg-success bg-opacity-75">尚有 ${seats} 名額</span>`
                : '<span class="badge bg-secondary">已額滿</span>');

        return `
            <div class="course-row list-group-item is-${status}">
                <div class="course-row-grid">
                    <div class="cell-score" title="${score.reasons}">
                        ${score.html || '<span class="text-muted small">—</span>'}
                    </div>

                    <div class="cell-main">
                        <div class="d-flex align-items-center flex-wrap gap-2">
                            <span class="text-secondary small font-monospace">${course.課程代碼}</span>
                            <span class="fw-bold text-truncate">${course.課程名稱 || course.中文課程名稱}</span>
                            ${status === 'selected' ? '<span class="badge bg-success"><i class="fas fa-check me-1"></i>已加入</span>' : ''}
                            ${status === 'conflict' ? '<span class="badge bg-danger">衝堂</span>' : ''}
                            ${admissionBadge(course.admission_pred)}
                        </div>
                        <div class="small text-muted text-truncate">
                            ${course.教師姓名 || '未定'} <span class="mx-1">•</span>
                            ${courseTime(course)} <span class="mx-1">•</span> ${course.學分} 學分
                            ${course.上課地點 ? `<span class="mx-1">•</span> ${course.上課地點}` : ''}
                        </div>
                    </div>

                    <div class="cell-rate">${acceptanceBadge(course)}</div>
                    <div class="cell-seats">${seatBadge}</div>
                    <div class="cell-count">${course.選上人數}/${course.上限人數}</div>
                    <div class="cell-actions">${actionButtons(index, status)}</div>
                </div>
            </div>`;
    }).join('');

    return `<div class="col-12"><div class="list-group list-group-flush border rounded overflow-hidden">${rows}</div></div>`;
}

export function renderCourseResults(courses, containerId = 'find-results') {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!courses || courses.length === 0) {
        container.innerHTML = '<div class="col-12 text-center text-muted py-5">沒有找到符合條件的課程</div>';
        return;
    }

    container.innerHTML = resultView === 'list'
        ? renderResultList(courses)
        : renderResultCards(courses);
}

/**
 * 判斷一門課相對於目前課表的狀態。
 *
 * 「已加入」必須先於「衝堂」判斷：課程加入後會佔用自己的時段，
 * 此時 checkTimeConflict 會偵測到那些時段有課而回報衝堂——衝突對象其實是它自己。
 * 不先擋掉的話，剛加入的課會立刻變成「衝堂」且按鈕被停用。
 */
function courseStatus(course) {
    if (isCourseSelected(course)) return 'selected';
    return checkTimeConflict(course).hasConflict ? 'conflict' : 'available';
}


/** 詳情視窗裡的推薦分數區塊：把分數拆成各項來源，而不是只給一個數字 */
function detailScoreHtml(course) {
    const rate = course.historical_acceptance_rate;
    const detail = course.score_detail;
    if (rate == null && !detail) return '';

    const rows = [];
    if (rate != null) {
        rows.push(`<li>歷年中籤率 <strong>${Math.round(rate * 100)}%</strong>
                   <span class="text-muted">（上限人數 ÷ 登記人數，只採計已完成分發的學期）</span></li>`);
    }
    if (detail) {
        rows.push(`<li>選上機率推估 <strong>${Math.round(detail.chance * 100)}%</strong>
                   <span class="text-muted">（依${detail.chance_source}）</span></li>`);
        rows.push(detail.vacancy_seats > 0
            ? `<li>目前尚有 <strong>${detail.vacancy_seats}</strong> 個名額</li>`
            : '<li>目前登記人數已達或超過上限</li>');
        if (detail.credit_fit === 0) {
            rows.push('<li class="text-warning-emphasis">學分超出「目標學分 − 已選學分」的剩餘額度</li>');
        }
        if (detail.has_conflict) {
            rows.push('<li class="text-danger">與現有課表衝堂，排序時已降權</li>');
        }
    }

    const scoreBadge = course.recommend_score != null
        ? `<span class="badge bg-primary ms-2">推薦分數 ${course.recommend_score}</span>` : '';

    return `
        <div class="col-12">
            <div class="text-muted small mb-1">選課參考${scoreBadge}</div>
            <ul class="small mb-0 ps-3 border-bottom pb-2">${rows.join('')}</ul>
        </div>`;
}

/**
 * 非同步載入這門課的歷年開課紀錄。
 * 放在視窗開啟後才抓，避免每次渲染結果列表都預先打一堆 API。
 */
async function loadCourseHistory(course) {
    const container = document.getElementById('course-detail-history');
    if (!container) return;

    const name = String(course.課程名稱 || '').trim();
    if (!name) {
        container.textContent = '沒有可查詢的課程名稱';
        return;
    }

    try {
        const data = await api.fetchHistory(name);
        const teacher = String(course.教師姓名 || '').trim();
        // 同名課可能有多位教師開課，優先只看同一位；沒有再退回全部同名課
        let rows = (data.courses || []).filter(c => String(c.課程名稱 || '').trim() === name);
        const sameTeacher = rows.filter(c => String(c.教師姓名 || '').trim() === teacher);
        const usedTeacherFilter = teacher && sameTeacher.length > 0;
        if (usedTeacherFilter) rows = sameTeacher;

        if (!rows.length) {
            container.textContent = '查無歷年開課紀錄';
            return;
        }

        rows.sort((a, b) => (b.學年度 - a.學年度) || (b.學期 - a.學期));

        container.innerHTML = `
            ${usedTeacherFilter ? '' : '<div class="text-muted mb-1">找不到同一位教師的紀錄，以下為所有同名課程</div>'}
            <div class="table-responsive" style="max-height: 12rem; overflow-y: auto;">
                <table class="table table-sm mb-0" style="font-variant-numeric: tabular-nums;">
                    <thead><tr>
                        <th>學期</th><th>教師</th>
                        <th class="text-end">登記</th><th class="text-end">選上</th><th class="text-end">上限</th>
                    </tr></thead>
                    <tbody>
                        ${rows.slice(0, 12).map(r => `
                            <tr>
                                <td>${r.學年度}-${r.學期}</td>
                                <td>${r.教師姓名 || '—'}</td>
                                <td class="text-end">${r.登記人數}</td>
                                <td class="text-end">${r.選上人數}</td>
                                <td class="text-end">${r.上限人數}</td>
                            </tr>`).join('')}
                    </tbody>
                </table>
            </div>`;
    } catch (error) {
        console.error('載入歷年紀錄失敗', error);
        container.textContent = '載入歷年紀錄失敗';
    }
}

export function showCourseDetailModal(course) {
    const modalElement = document.getElementById('courseDetailModal');
    
    const modalDialog = modalElement.querySelector('.modal-dialog');
    if (modalDialog) {
        modalDialog.classList.remove('modal-lg');
        modalDialog.classList.remove('modal-xl');
    }

    const modal = new bootstrap.Modal(modalElement);
    document.getElementById('courseDetailTitle').textContent = course.課程名稱 || course.中文課程名稱;
    
    const body = document.getElementById('courseDetailBody');
    
    const syllabusUrl = course['教學大綱連結'];
    // 115-1 有 1383/1963 門有大綱，其餘是學校端未上傳。明說原因，
    // 避免使用者以為是本站抓不到而反覆點擊。
    const syllabusLinkHtml = (syllabusUrl && String(syllabusUrl).includes('http'))
        ? `<a href="${syllabusUrl}" target="_blank" rel="noopener" class="btn btn-sm btn-outline-primary w-100">
             <i class="fas fa-file-pdf me-1"></i>開啟教學大綱（${course['教學大綱狀態'] || '中文'}）
           </a>`
        : '<span class="text-muted small"><i class="fas fa-circle-info me-1"></i>學校尚未上傳這門課的教學大綱</span>';

    const enrolled = parseFloat(course.選上人數 || 0);
    const capacity = parseFloat(course.上限人數 || 0);
    let saturationPercent = 0;
    if (capacity > 0) {
        saturationPercent = Math.round((enrolled / capacity) * 100);
    }

    let satBadgeClass = 'bg-success';
    let satText = '名額充足';
    let satIcon = '<i class="fas fa-check-circle me-1"></i>';

    if (saturationPercent >= 100) {
        satBadgeClass = 'bg-danger';
        satText = '已額滿';
        satIcon = '<i class="fas fa-exclamation-circle me-1"></i>';
    } else if (saturationPercent >= 80) {
        satBadgeClass = 'bg-warning text-dark';
        satText = '即將額滿';
        satIcon = '<i class="fas fa-exclamation-triangle me-1"></i>';
    }

    const teacherDisplay = course.教師列表 || course.教師姓名 || '未定';

    body.innerHTML = `
        <div class="container-fluid p-0">
            <div class="row g-3">
                <div class="col-12">
                    <div class="text-muted small mb-1">課程代碼</div>
                    <div class="fw-bold text-dark border-bottom pb-2">${course.課程代碼 || '-'}</div>
                </div>

                <div class="col-12">
                    <div class="text-muted small mb-1">開課班別</div>
                    <div class="fw-bold text-dark border-bottom pb-2">${course['開課班別(代表)'] || course.開課班別 || '-'}</div>
                </div>

                <div class="col-12">
                    <div class="text-muted small mb-1">學分 / 性質</div>
                    <div class="border-bottom pb-2">
                        <span class="fs-5 fw-bold text-primary">${course.學分}</span> 學分
                        <span class="badge bg-secondary ms-2">${course.課程性質 || '選修'}</span>
                    </div>
                </div>

                <div class="col-12">
                    <div class="text-muted small mb-1">授課教師</div>
                    <div class="fw-bold border-bottom pb-2">${teacherDisplay}</div>
                </div>

                <div class="col-12">
                    <div class="text-muted small mb-1">上課時間</div>
                    <div class="border-bottom pb-2">
                        <i class="far fa-clock me-2 text-muted"></i>週${course.星期} 第 ${course.起始節次}-${course.結束節次} 節
                    </div>
                </div>

                <div class="col-12">
                    <div class="text-muted small mb-1">上課地點</div>
                    <div class="border-bottom pb-2">
                        <i class="fas fa-map-marker-alt me-2 text-muted"></i>${course.上課地點 || '未定'}
                    </div>
                </div>

                <div class="col-12">
                    <div class="text-muted small mb-1">選課狀況 (飽和度)</div>
                    <div class="border-bottom pb-2">
                        <div class="d-flex align-items-center justify-content-between mb-2">
                            <div>
                                <span class="fw-bold fs-5">${enrolled}</span>
                                <span class="text-muted mx-1">/</span>
                                <span class="text-muted">${capacity} 人</span>
                            </div>
                            <span class="badge ${satBadgeClass} p-2">
                                ${satIcon} ${satText}
                            </span>
                        </div>
                        <div class="progress" style="height: 6px;">
                            <div class="progress-bar ${satBadgeClass} saturation-bar" role="progressbar" style="width: ${Math.min(saturationPercent, 100)}%" aria-valuenow="${saturationPercent}" aria-valuemin="0" aria-valuemax="100"></div>
                        </div>
                        <div class="text-end mt-1">
                            <small class="text-muted">飽和度 ${saturationPercent}%</small>
                        </div>
                    </div>
                </div>
                <div class="col-12">
                    <div class="text-muted small mb-1">中籤預測</div>
                    <div class="border-bottom pb-2">${admissionDetail(course.admission_pred, course)}</div>
                </div>
                <div class="col-12">
                    <div class="text-muted small mb-1">課程大綱</div>
                    <div>${syllabusLinkHtml}</div>
                    ${(syllabusUrl && String(syllabusUrl).includes('http')) ? `
                    <div class="syllabus-qa mt-2 d-none">
                        <div class="input-group input-group-sm">
                            <input type="text" class="form-control" placeholder="問這門課的大綱，例如：期中考占多少？" aria-label="問這門課的大綱">
                            <button class="btn btn-outline-primary" type="button"><i class="fas fa-magnifying-glass me-1"></i>問大綱</button>
                        </div>
                        <div class="syllabus-qa-answer small mt-2"></div>
                    </div>` : ''}
                </div>

                ${detailScoreHtml(course)}

                <div class="col-12">
                    <div class="text-muted small mb-1">歷年開課紀錄</div>
                    <div id="course-detail-history" class="small text-muted">
                        <span class="spinner-border spinner-border-sm me-2"></span>載入中…
                    </div>
                </div>

                ${course.備註 ? `
                <div class="col-12 mt-2">
                    <div class="alert alert-light border border-secondary border-opacity-25 mb-0 small">
                        <i class="fas fa-info-circle me-1 text-primary"></i>
                        <strong>備註：</strong> ${course.備註}
                    </div>
                </div>` : ''}
            </div>
        </div>
    `;

    loadCourseHistory(course);
    bindSyllabusQA(body, course);
    
    const addBtn = document.getElementById('btn-add-to-schedule');
    if (addBtn) {
        const newBtn = addBtn.cloneNode(true);
        addBtn.parentNode.replaceChild(newBtn, addBtn);
        
        newBtn.onclick = () => {
            modal.hide();
            if (window.addRecommendedCourse) {
                window.addRecommendedCourse(course);
            }
        };
    }
    
    modal.show();
}

export function switchTab(tabId) {
    document.querySelectorAll('.page-section').forEach(s => s.classList.add('d-none'));
    const target = document.getElementById(tabId);
    if(target) target.classList.remove('d-none');
    
    document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
    const activeBtn = Array.from(document.querySelectorAll('.nav-btn')).find(btn => 
        btn.getAttribute('onclick')?.includes(tabId)
    );
    if(activeBtn) activeBtn.classList.add('active');
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

/**
 * 批次衝堂處理：把所有衝突的課程集中成一張列表讓使用者一次決定，
 * 取代逐門跳出視窗（一門跨多節的課甚至會連跳好幾次）。
 *
 * @param {Array<{course: Object, conflicts: Array<Object>}>} items
 * @returns {Promise<number[]|null>} 要改選的項目索引；null 代表全部略過
 */
export async function showBatchConflictResolutionModal(items) {
    const formatTime = (c) => `週${escapeHtml(c.星期)} ${escapeHtml(c.起始節次)}-${escapeHtml(c.結束節次)}節`;

    const rows = items.map((item, idx) => {
        const c = item.course;
        const clashes = item.conflicts.map(o => `
            <div class="small text-danger">
                <i class="fas fa-times-circle me-1"></i>會移除：${escapeHtml(o.課程名稱)}
                <span class="text-muted">${escapeHtml(o.教師姓名)}・${formatTime(o)}</span>
            </div>`).join('');

        return `
            <label class="list-group-item d-flex gap-2 align-items-start text-start" style="cursor:pointer;">
                <input class="form-check-input mt-1 flex-shrink-0 conflict-choice" type="checkbox" value="${idx}">
                <span class="flex-grow-1">
                    <span class="fw-semibold">${escapeHtml(c.課程名稱)}</span>
                    <span class="small text-muted ms-1">${escapeHtml(c.教師姓名)}・${formatTime(c)}</span>
                    ${clashes}
                </span>
            </label>`;
    }).join('');

    const result = await Swal.fire({
        title: `${items.length} 門課程衝堂`,
        html: `
            <p class="small text-muted text-start mb-2">
                勾選要<b>改選</b>的課程，未勾選者將保留課表上的原課程。
            </p>
            <div class="d-flex gap-2 mb-2">
                <button type="button" id="conflict-select-all" class="btn btn-sm btn-outline-primary">全部改選</button>
                <button type="button" id="conflict-select-none" class="btn btn-sm btn-outline-secondary">全部保留</button>
            </div>
            <div class="list-group text-start" style="max-height:45vh; overflow-y:auto;">${rows}</div>
        `,
        width: '42rem',
        showCancelButton: true,
        confirmButtonText: '套用',
        confirmButtonColor: '#0d6efd',
        cancelButtonText: '全部略過',
        cancelButtonColor: '#6c757d',
        reverseButtons: true,
        allowOutsideClick: false,
        didOpen: () => {
            const popup = Swal.getPopup();
            const boxes = () => popup.querySelectorAll('.conflict-choice');
            popup.querySelector('#conflict-select-all')
                .addEventListener('click', () => boxes().forEach(b => { b.checked = true; }));
            popup.querySelector('#conflict-select-none')
                .addEventListener('click', () => boxes().forEach(b => { b.checked = false; }));
        },
        preConfirm: () => Array.from(
            Swal.getPopup().querySelectorAll('.conflict-choice:checked')
        ).map(b => parseInt(b.value))
    });

    return result.isConfirmed ? (result.value || []) : null;
}
