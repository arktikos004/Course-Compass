# 選課助理評估

> 目標學期 115-1；查詢與條件見 `data/eval/agent_queries.json`（條件為人工寫定，與解析器無關）。
> mock 的「理解」就是 `ai/parse.py` 的規則，所以 mock 的分數反映的是規則解析器的涵蓋度，不代表 LLM。

| provider | 查詢數 | 推薦課數 | 條件滿足率 | 全部符合的查詢比例 | 幻覺率 | 有解卻空答 | 中位秒數 |
|---|---|---|---|---|---|---|---|
| mock | 30 | 109 | 86.2% | 80.8% | 0.0% | 0.0% | 0.0 |

## 逐題結果

| # | provider | 查詢 | 推薦 | 符合 | 幻覺 | 有解 | 工具 |
|---|---|---|---|---|---|---|---|
| 1 | mock | 週五不要有課、2 學分通識、跟資料分析有關、不要太難搶 | 1 | 1 | 0 | ✓ | find_courses → semantic_search_syllabus → find_courses |
| 2 | mock | 我想找三門英文課，只要週二或週四，不要早八 | 3 | 3 | 0 | ✓ | find_courses |
| 3 | mock | 星期一和三不想上課，找體育課 | 5 | 5 | 0 | ✓ | find_courses |
| 4 | mock | 推薦下午的通識，關於心理學的課 | 5 | 5 | 0 | ✓ | find_courses → semantic_search_syllabus → find_courses |
| 5 | mock | 有沒有好選的教育學程，禮拜四五空下來 | 5 | 5 | 0 | ✓ | find_courses |
| 6 | mock | 禮拜五想放假，幫我找通識 | 5 | 5 | 0 | ✓ | find_courses |
| 7 | mock | 我只有週三下午有空，有什麼通識 | 5 | 0 | 0 | ✓ | find_courses |
| 8 | mock | 想修 3 學分的英文，晚上的也可以 | 0 | 0 | 0 | ✗ | find_courses |
| 9 | mock | 找不會爆滿的國文課 | 5 | 3 | 0 | ✓ | find_courses |
| 10 | mock | 早上的體育課，週一以外都可以 | 5 | 5 | 0 | ✓ | find_courses |
| 11 | mock | 通識課，2 學分，星期二 | 5 | 1 | 0 | ✓ | find_courses |
| 12 | mock | 推薦兩門好選的通識 | 2 | 2 | 0 | ✓ | find_courses |
| 13 | mock | 跟人工智慧有關的通識 | 5 | 5 | 0 | ✓ | find_courses → semantic_search_syllabus → find_courses |
| 14 | mock | 跟法律相關的通識，週一週二不要 | 1 | 1 | 0 | ✓ | find_courses → semantic_search_syllabus |
| 15 | mock | 想上跟藝術有關的課 | 5 | 5 | 0 | ✓ | find_courses → semantic_search_syllabus |
| 16 | mock | 不要早八的英文課 | 5 | 5 | 0 | ✓ | find_courses |
| 17 | mock | 大三體育有哪些？星期四不行 | 5 | 3 | 0 | ✓ | find_courses |
| 18 | mock | 教育學程晚上的課 | 5 | 5 | 0 | ✓ | find_courses |
| 19 | mock | 找穩上的通識，週三週五不要 | 1 | 1 | 0 | ✓ | find_courses |
| 20 | mock | 我想學程式設計 | 5 | 5 | 0 | ✓ | find_courses → semantic_search_syllabus |
| 21 | mock | 有沒有週一上午的通識 | 5 | 3 | 0 | ✓ | find_courses |
| 22 | mock | 2學分的國文，不要週三 | 5 | 5 | 0 | ✓ | find_courses |
| 23 | mock | 我想要週二跟週四的體育課 | 5 | 5 | 0 | ✓ | find_courses |
| 24 | mock | 下午的英文課，不要太搶 | 0 | 0 | 0 | ✗ | find_courses |
| 25 | mock | 跟環境永續有關的通識 | 5 | 5 | 0 | ✓ | find_courses → semantic_search_syllabus → find_courses |
| 26 | mock | 找一門跟心理健康有關、好選的通識 | 1 | 1 | 0 | ✓ | find_courses → semantic_search_syllabus → find_courses |
| 27 | mock | 週四不想上課的教育學程 | 5 | 5 | 0 | ✓ | find_courses |
| 28 | mock | 英文課只要週一 | 0 | 0 | 0 | ✗ | find_courses |
| 29 | mock | 給我三門晚上的通識 | 0 | 0 | 0 | ✗ | find_courses |
| 30 | mock | 體育課，不要週五也不要早八 | 5 | 5 | 0 | ✓ | find_courses |
