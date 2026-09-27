/**
 * 狀態管理
 */
import { DEFAULT_YEAR, DEFAULT_SEMESTER } from './config.js';

export const state = {
    selectedCourses: [],    // 已選課程列表
    currentSchedule: {},    // 當前課表 {day: {period: course}}

    allCoursesData: [],     // 所有課程數據
    currentYear: DEFAULT_YEAR,
    currentSemester: DEFAULT_SEMESTER,
    recommendedCourses: [], // 暫存推薦結果

    // 由 /api/semesters 取得。has_results=false 代表該學期仍在預選登記中，
    // 選上人數尚未公布，中籤率/飽和度統計必須排除。
    semesters: []
};

// 該學期是否已完成分發（資料可信）。查不到的學期一律當作已結算，
// 避免 API 失敗時整個歷年統計消失。
export function isSettledSemester(year, semester) {
    const found = state.semesters.find(
        s => String(s.year) === String(year) && String(s.semester) === String(semester)
    );
    return found ? found.has_results !== false : true;
}

// 目前選取的學期是否仍在預選登記中
export function isCurrentSemesterPending() {
    return !isSettledSemester(state.currentYear, state.currentSemester);
}

// 初始化課表結構
for (let day = 1; day <= 7; day++) {
    state.currentSchedule[day] = {};
}

export function saveToLocalStorage() {
    try {
        localStorage.setItem('selectedCourses', JSON.stringify(state.selectedCourses));
        localStorage.setItem('currentSchedule', JSON.stringify(state.currentSchedule));

    } catch (error) {
        console.error('保存失敗:', error);
    }
}

export function loadFromLocalStorage() {
    try {
        const savedCourses = localStorage.getItem('selectedCourses');
        const savedSchedule = localStorage.getItem('currentSchedule');
        
        if (savedCourses) state.selectedCourses = JSON.parse(savedCourses);
        if (savedSchedule) state.currentSchedule = JSON.parse(savedSchedule);
    } catch (error) {
        console.error('載入失敗:', error);
    }
}