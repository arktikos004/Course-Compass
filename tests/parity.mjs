/**
 * 移植一致性驗證：JS 查詢引擎必須逐筆重現 Python 版的結果。
 *
 * 網站改為全靜態部署後，篩選／評分／排序從 FastAPI 搬到了瀏覽器。這類移植最大的
 * 風險是行為悄悄飄移——邊界條件、排序穩定性、四捨五入規則稍有不同，使用者看到的
 * 結果就不一樣，而且不會有任何錯誤訊息。
 *
 * 這支腳本拿 scripts/build_fixtures.py 用 Python 實作產生的樣本，
 * 餵給 web/assets/js/query.js，比對每一筆的課程順序、分數與評分細節。
 *
 * 用法：
 *     python scripts/build_static.py        # 先產生資料包
 *     python scripts/build_fixtures.py      # 再產生對照樣本
 *     node tests/parity.mjs
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const { queryCourses } = await import(
    'file:///' + join(ROOT, 'web', 'assets', 'js', 'query.js').replace(/\\/g, '/')
);

function readJson(...parts) {
    return JSON.parse(readFileSync(join(ROOT, ...parts), 'utf-8'));
}

const cases = readJson('tests', 'fixtures', 'query_cases.json');
const meta = readJson('web', 'data', 'meta.json');

// 樣本都以 115-1 為基準
const semesterData = readJson('web', 'data', 'courses', '115-1.json');
const dataset = {
    courses: semesterData.courses,
    acceptance: meta.acceptance,
    settled: meta.settled,
};

let passed = 0;
const failures = [];

for (const testCase of cases) {
    const actual = queryCourses(testCase.params, dataset);
    const expected = testCase.expected;
    const problems = [];

    if (actual.total !== expected.total) {
        problems.push(`total ${actual.total} ≠ ${expected.total}`);
    }
    if (actual.has_more !== expected.has_more) {
        problems.push(`has_more ${actual.has_more} ≠ ${expected.has_more}`);
    }
    if (actual.courses.length !== expected.courses.length) {
        problems.push(`回傳筆數 ${actual.courses.length} ≠ ${expected.courses.length}`);
    }

    const limit = Math.min(actual.courses.length, expected.courses.length);
    for (let i = 0; i < limit && problems.length < 4; i += 1) {
        const got = actual.courses[i];
        const want = expected.courses[i];
        const gotKey = `${got.課程代碼}-${got.序號}`;
        const wantKey = `${want.code}-${want.serial}`;

        if (gotKey !== wantKey) {
            problems.push(`第 ${i} 筆課程不同：${gotKey} ≠ ${wantKey}`);
            continue;
        }
        const checks = [
            ['score', got.recommend_score, want.score],
            ['chance', got.score_detail.chance, want.chance],
            ['chance_source', got.score_detail.chance_source, want.chance_source],
            ['vacancy_seats', got.score_detail.vacancy_seats, want.vacancy_seats],
            ['credit_fit', got.score_detail.credit_fit, want.credit_fit],
            ['conflict', got.has_conflict, want.conflict],
            ['rate', got.historical_acceptance_rate, want.rate],
        ];
        for (const [field, a, b] of checks) {
            const same = typeof a === 'number' && typeof b === 'number'
                ? Math.abs(a - b) < 1e-6
                : a === b;
            if (!same) problems.push(`第 ${i} 筆 ${gotKey} 的 ${field}：${a} ≠ ${b}`);
        }
    }

    if (problems.length) {
        failures.push({ name: testCase.name, problems });
    } else {
        passed += 1;
    }
}

console.log(`對照樣本 ${cases.length} 組：通過 ${passed}，失敗 ${failures.length}`);
for (const failure of failures) {
    console.log(`\n  ✗ ${failure.name}`);
    for (const p of failure.problems.slice(0, 4)) console.log(`      ${p}`);
}

process.exit(failures.length ? 1 : 0);
