"""選課助理的代理迴圈：LLM 決定呼叫哪些工具 → 執行 → 回填結果 → 最多 AGENT_MAX_STEPS 輪 →
最後一輪以 JSON schema 強制輸出 {reply, suggestions}。

防幻覺：LLM 建議的每一門課都要用「課程代碼+序號」回頭比對目標學期的資料，
不存在就剔除並記在 dropped，絕不把模型編出來的課送到前端。
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from config import AGENT_MAX_STEPS

from .llm import FINAL_INSTRUCTION
from .tools import DAY_ZH, PERIOD_TIMES, TOOL_SPECS, CourseContext, course_key, run_tool, slots_of, time_text

log = logging.getLogger(__name__)

FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"code": {"type": "string"}, "serial": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["code", "serial", "reason"],
            },
        },
    },
    "required": ["reply", "suggestions"],
}


def _period_table() -> str:
    return "、".join(f"第{p}節 {a}–{b}" for p, (a, b) in sorted(PERIOD_TIMES.items()) if p <= 13)


def system_prompt(ctx: CourseContext) -> str:
    if ctx.current_courses:
        cur = "\n".join(f"- {c.get('課程名稱')}（{course_key(c)[0]}）{time_text(c)}" for c in ctx.current_courses)
    else:
        cur = "（目前課表是空的）"
    busy_days = sorted({d for d, _ in ctx.occupied})
    return f"""你是國立彰化師範大學的選課助理「Course Compass」，用繁體中文回答。
目標學期：{ctx.year} 學年度第 {ctx.semester} 學期。
節次時間：{_period_table()}。
使用者目前的課表：
{cur}
（已有課的星期：{'、'.join('週' + DAY_ZH[d] for d in busy_days) or '無'}）

規則：
1. 一定要先用工具查資料，**只能推薦工具結果裡出現過的課**，課程代碼與序號要原樣照抄，不可自己編。
2. 使用者提到星期、學分、類別、時段、難不難搶，就轉成 find_courses 的參數；「好選／不要太難搶」用 max_p_full=0.3。
3. 課名看不出主題時（例如「跟資料分析有關」），用 semantic_search_syllabus 查教學大綱，再和 find_courses 的條件交叉比對。
4. 說明理由時引用工具給的事實：時間、爆滿機率 p_full、預估中籤率、大綱段落；中籤預測是模型估計，不要說成保證。
5. 找不到就老實說，並建議放寬哪個條件。"""


def _summarize(name: str, args: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    if "courses" in result:
        n = f"{result.get('count', len(result['courses']))} 門"
    elif "hits" in result:
        n = f"{len(result['hits'])} 筆" if result.get("available", True) else "索引未建立"
    elif "conflicts" in result:
        n = f"{len(result['conflicts'])} 組衝堂"
    elif "records" in result:
        n = f"{len(result['records'])} 筆紀錄"
    elif "error" in result:
        n = f"錯誤：{result['error']}"
    else:
        n = "完成"
    return {"tool": name, "args": args, "result": n}


def _parse_final(content: str) -> Dict[str, Any]:
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {"reply": content.strip() or "（模型沒有給出回覆）", "suggestions": []}


def run_agent(messages: List[Dict[str, str]], ctx: CourseContext, client, max_steps: int = AGENT_MAX_STEPS) -> Dict[str, Any]:
    t0 = time.time()
    convo: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt(ctx)}]
    convo += [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")]
    trace = []

    for _ in range(max_steps):
        out = client.chat(convo, tools=TOOL_SPECS)
        if not out["tool_calls"]:
            break
        convo.append({"role": "assistant", "content": out["content"],
                      "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in out["tool_calls"]]})
        for call in out["tool_calls"]:
            result = run_tool(ctx, call["name"], call["arguments"])
            trace.append(_summarize(call["name"], call["arguments"], result))
            convo.append({"role": "tool", "tool_name": call["name"], "content": json.dumps(result, ensure_ascii=False)})

    convo.append({"role": "user", "content": FINAL_INSTRUCTION})
    final = _parse_final(client.chat(convo, format=FINAL_SCHEMA)["content"])

    courses, dropped, seen = [], [], set()
    occupied = ctx.occupied
    for s in final.get("suggestions") or []:
        c = ctx.lookup(s.get("code"), s.get("serial"))
        if c is None:
            dropped.append({"code": s.get("code"), "serial": s.get("serial"), "reason": "目標學期沒有這門課（模型幻覺，已剔除）"})
            continue
        k = course_key(c)
        if k in seen:
            continue
        seen.add(k)
        courses.append({**c, "ai_reason": s.get("reason", ""), "conflicts_with_schedule": bool(slots_of(c) & occupied)})

    return {
        "reply": final.get("reply", ""),
        "courses": courses,
        "dropped": dropped,
        "tool_trace": trace,
        "provider": getattr(client, "name", "unknown"),
        "elapsed_sec": round(time.time() - t0, 2),
    }
