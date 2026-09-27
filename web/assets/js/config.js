/**
 * 全局配置與常數
 */
// 靜態資料包的位置（由 scripts/build_static.py 產生）
export const DATA_BASE = 'data';

// 目前只剩即時缺額需要連線，走 Pages Function
export const API_BASE = '/api';

export const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];

export const WEEKDAY_MAP = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 7
};

export const PERIOD_TIMES = {
    1: '08:10-09:00', 2: '09:05-09:55', 3: '10:15-11:05', 4: '11:10-12:00',
    5: '13:10-14:00', 6: '14:05-14:55', 7: '15:15-16:05', 8: '16:10-17:00',
    9: '17:10-18:00', 10: '18:20-19:10', 11: '19:15-20:05', 12: '20:10-21:00',
    13: '21:05-21:55', 14: '12:05-12:55'
};

export const PERIOD_ORDER = [1, 2, 3, 4, 14, 5, 6, 7, 8, 9, 10, 11, 12, 13];
// 僅作為 /api/semesters 取得失敗時的後備值；正常情況下會被 API 回傳的最新學期覆蓋
export const DEFAULT_YEAR = 115;
export const DEFAULT_SEMESTER = 1;