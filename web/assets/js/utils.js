/**
 * 工具函數與邏輯運算
 */
import { WEEKDAY_MAP } from './config.js';
import { state } from './state.js';

// 獲取空堂時段
export function getEmptySlots() {
    const emptySlots = [];
    for (let day = 1; day <= 7; day++) {
        for (let period = 1; period <= 12; period++) {
            if (!state.currentSchedule[day] || !state.currentSchedule[day][period]) {
                emptySlots.push({ day, period });
            }
        }
    }
    return emptySlots;
}

// 檢查課程是否已選
export function isCourseSelected(course) {
    return state.selectedCourses.find(c => 
        String(c.課程代碼) === String(course.課程代碼) && 
        String(c.序號) === String(course.序號)
    );
}

// 檢查時間衝突
export function checkTimeConflict(course) {
    const day = WEEKDAY_MAP[course.星期] || parseInt(course.星期);
    if (!day) return { hasConflict: false };
    
    const startPeriod = parseInt(course.起始節次);
    const endPeriod = parseInt(course.結束節次);
    
    if (startPeriod && endPeriod) {
        for (let p = startPeriod; p <= endPeriod; p++) {
            if (state.currentSchedule[day] && state.currentSchedule[day][p]) {
                // 回傳衝突的具體課程物件
                return { 
                    hasConflict: true, 
                    day, 
                    conflictingCourse: state.currentSchedule[day][p] 
                };
            }
        }
    }
    return { hasConflict: false, day, startPeriod, endPeriod };
}

// 找出課程與課表衝突的「所有」既有課程
// checkTimeConflict 只回傳第一門，跨多節的課可能同時撞到好幾門，批次處理時需要完整清單
export function findConflictingCourses(course) {
    const day = WEEKDAY_MAP[course.星期] || parseInt(course.星期);
    const startPeriod = parseInt(course.起始節次);
    const endPeriod = parseInt(course.結束節次);

    if (!day || !startPeriod || !endPeriod) {
        return { day, startPeriod, endPeriod, conflicts: [] };
    }

    const seen = new Set();
    const conflicts = [];

    for (let p = startPeriod; p <= endPeriod; p++) {
        const occupied = state.currentSchedule[day] && state.currentSchedule[day][p];
        if (!occupied) continue;

        const key = `${occupied.課程代碼}_${occupied.序號}`;
        if (seen.has(key)) continue;
        seen.add(key);
        conflicts.push(occupied);
    }

    return { day, startPeriod, endPeriod, conflicts };
}

// 將傳入的課程物件規範化為前端期望的欄位
export function normalizeCourse(raw = {}) {
    // 優先欄位映射：中文課程名稱 -> 課程名稱 -> title
    const title = raw.中文課程名稱 || raw.課程名稱 || raw.英文課程名稱 || raw.title || '';
    const teacher = raw.教師姓名 || raw.教師 || raw.teacher || '';
    const credits = raw.學分 !== undefined ? (isNaN(Number(raw.學分)) ? 0 : Number(raw.學分)) : (raw.credits !== undefined ? Number(raw.credits) : 0);

    // 確保基本欄位為字串或合理預設
    // coerce numeric times into integer strings to avoid '5.0' issues
    const coerceIntString = (v) => {
        if (v === undefined || v === null || v === '') return '';
        const n = Number(v);
        if (!isNaN(n) && Number.isFinite(n) && Math.floor(n) === n) return String(n);
        if (!isNaN(n) && Number.isFinite(n)) return String(Math.trunc(n));
        return String(v);
    };

    const course = Object.assign({}, raw, {
        課程名稱: String(title),
        教師姓名: String(teacher),
        學分: credits,
        上課地點: raw.上課地點 || raw.教室 || raw.location || '',
        星期: raw.星期 !== undefined ? String(raw.星期) : (raw.day !== undefined ? String(raw.day) : ''),
        起始節次: coerceIntString(raw.起始節次 !== undefined ? raw.起始節次 : (raw.startPeriod !== undefined ? raw.startPeriod : '')),
        結束節次: coerceIntString(raw.結束節次 !== undefined ? raw.結束節次 : (raw.endPeriod !== undefined ? raw.endPeriod : '')),
        課程代碼: raw.課程代碼 !== undefined ? String(raw.課程代碼) : (raw.code !== undefined ? String(raw.code) : ''),
        序號: raw.序號 !== undefined ? String(raw.序號) : (raw.serial !== undefined ? String(raw.serial) : '')
    });

    return course;
}

// 添加課程到狀態
export function addCourseToState(course) {
    const c = normalizeCourse(course);
    if (isCourseSelected(c)) return false;
    
    const check = checkTimeConflict(c);
    if (check.hasConflict) return 'conflict'; // 回傳特殊字串表示衝突
    
    state.selectedCourses.push(c);
    
    if (check.startPeriod && check.endPeriod) {
        for (let p = check.startPeriod; p <= check.endPeriod; p++) {
            if (!state.currentSchedule[check.day]) state.currentSchedule[check.day] = {};
            state.currentSchedule[check.day][p] = c;
        }
    }
    return true;
}

// 從狀態移除課程
export function removeCourseFromState(courseCode, serial) {
    // 強制轉型為字串進行比對
    const sCode = String(courseCode);
    const sSerial = String(serial);

    const course = state.selectedCourses.find(c => 
        String(c.課程代碼) === sCode && 
        String(c.序號) === sSerial
    );
    
    if (!course) {
        console.warn(`找不到課程: ${sCode}, 序號: ${sSerial}`);
        return false;
    }

    // 從已選列表中移除
    state.selectedCourses = state.selectedCourses.filter(c => 
        !(String(c.課程代碼) === sCode && String(c.序號) === sSerial)
    );
    
    // 從課表視圖中移除 (清除佔用的時段)
    const day = WEEKDAY_MAP[course.星期] || parseInt(course.星期);
    if (day && state.currentSchedule[day]) {
        const startPeriod = parseInt(course.起始節次);
        const endPeriod = parseInt(course.結束節次);
        if (startPeriod && endPeriod) {
            for (let p = startPeriod; p <= endPeriod; p++) {
                if (state.currentSchedule[day][p] && 
                    String(state.currentSchedule[day][p].課程代碼) === sCode && 
                    String(state.currentSchedule[day][p].序號) === sSerial) {
                    delete state.currentSchedule[day][p];
                }
            }
        }
    }
    return true;
}