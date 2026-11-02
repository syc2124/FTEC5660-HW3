#!/usr/bin/env python3
"""FTEC5660 HW3 student starter: an agent that applies 3 of Fisher's 15 points using tools."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sqlite3
import threading
import zlib
from pathlib import Path
from typing import Any


MODEL_NAME = "deepseek-v4-flash"
INTERVIEW_LIMIT = 10
MAX_SQL_ROWS = 50
MAX_CONCURRENT_COMPANIES = 4
MODEL_CALL_TIMEOUT_SECONDS = 40
COMPANY_TIMEOUT_SECONDS = 75

# Philip Fisher, "Common Stocks and Uncommon Profits", Part One, Chapter 3.
# For every point, PASS means the company looks good on it. You choose 3
# points and decide yourself what PASS means for each of them.
FISHER_POINTS = {
    1: "Does the company have products or services with sufficient market potential to make possible a sizable increase in sales for at least several years?",
    2: "Does the management have a determination to continue to develop products or processes that will still further increase total sales potentials when the growth potentials of currently attractive product lines have largely been exploited?",
    3: "How effective are the company's research and development efforts in relation to its size?",
    4: "Does the company have an above-average sales organization?",
    5: "Does the company have a worthwhile profit margin?",
    6: "What is the company doing to maintain or improve profit margins?",
    7: "Does the company have outstanding labor and personnel relations?",
    8: "Does the company have outstanding executive relations?",
    9: "Does the company have depth to its management?",
    10: "How good are the company's cost analysis and accounting controls?",
    11: "Are there other aspects of the business, somewhat peculiar to the industry involved, which will give the investor important clues as to how outstanding the company may be in relation to its competition?",
    12: "Does the company have a short-range or long-range outlook in regard to profits?",
    13: "In the foreseeable future will the growth of the company require sufficient equity financing so that the larger number of shares then outstanding will largely cancel the existing stockholders' benefit from this anticipated growth?",
    14: "Does the management talk freely to investors about its affairs when things are going well but 'clam up' when troubles and disappointments occur?",
    15: "Does the company have a management of unquestionable integrity?",
}
VERDICTS = ("PASS", "FAIL")


def load_env_file(path: Path = Path(".env")) -> None:
    """Load the simple KEY=VALUE entries used by this homework."""
    if not path.is_file():
        return
    import os

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# Choose any 3 of the 15 points, e.g. CHOSEN_POINTS = [1, 7, 15]. Your agent
# gives a PASS or FAIL verdict on these 3 points only, with your own rule for each.
CHOSEN_POINTS: list[int] = [1, 5, 11]  ### YOUR CODE HERE


def build_agent() -> Any:
    """Create and return your model or agent once.

    Suggested imports:
        from langchain_deepseek import ChatDeepSeek
        from langchain.agents import create_agent

    Use the DeepSeek model named by ``MODEL_NAME``. The API key is loaded
    from .env. Each company has its own tools (see ``analyze_companies``), so
    you will probably create one tool-calling agent per company there.
    """
    ### YOUR CODE HERE
    from langchain_deepseek import ChatDeepSeek

    return ChatDeepSeek(
        model=MODEL_NAME,
        temperature=0,
        max_retries=1,
        timeout=30,
        max_tokens=180,
    )


def _tool_map(tools: list[Any]) -> dict[str, Any]:
    """Index the four company tools by their LangChain tool name."""
    return {tool.name: tool for tool in tools}


def _parse_table(output: str) -> list[dict[str, str]]:
    """Parse the pipe-separated output returned by the provided SQL tool."""
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2 or output.startswith("SQL error:"):
        return []
    columns = [column.strip() for column in lines[0].split("|")]
    rows = []
    for line in lines[1:]:
        if line.startswith("..."):
            break
        values = [value.strip() for value in line.split("|")]
        if len(values) == len(columns):
            rows.append(dict(zip(columns, values)))
    return rows


def _number(row: dict[str, str], key: str) -> float:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Missing numeric SQL field {key!r}") from exc


def _database_evidence(sql_query: Any) -> dict[str, Any]:
    """Compute Fisher points 1 and 5 entirely with SQL and Python arithmetic."""
    growth_query = """
        SELECT
            c.industry AS industry,
            im.revenue_growth_2y_pct AS industry_growth_pct,
            SUM(CASE WHEN substr(o.order_date, 1, 4) = '2023'
                     THEN o.amount ELSE 0 END) / 1000.0 AS revenue_2023_musd,
            SUM(CASE WHEN substr(o.order_date, 1, 4) = '2025'
                     THEN o.amount ELSE 0 END) / 1000.0 AS revenue_2025_musd
        FROM company AS c
        JOIN industry_medians AS im ON im.industry = c.industry
        JOIN orders AS o
          ON substr(o.order_date, 1, 4) IN ('2023', '2025')
        GROUP BY c.industry, im.revenue_growth_2y_pct
    """
    growth_rows = _parse_table(sql_query.invoke({"query": growth_query}))
    if not growth_rows:
        raise ValueError("No usable data for Fisher point 1")
    growth_row = growth_rows[0]
    revenue_2023 = _number(growth_row, "revenue_2023_musd")
    revenue_2025 = _number(growth_row, "revenue_2025_musd")
    if revenue_2023 <= 0:
        raise ValueError("Invalid 2023 revenue for Fisher point 1")
    company_growth = (revenue_2025 / revenue_2023 - 1.0) * 100.0
    industry_growth = _number(growth_row, "industry_growth_pct")

    margin_query = """
        WITH revenue AS (
            SELECT SUM(amount) AS revenue
            FROM orders
            WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01'
        ),
        costs AS (
            SELECT
                SUM(cost_of_sales) AS cost_of_sales,
                SUM(selling_admin) AS selling_admin,
                SUM(research_development) AS research_development
            FROM monthly_costs
            WHERE month >= '2025-01' AND month < '2026-01'
        )
        SELECT
            c.industry AS industry,
            im.operating_margin_pct AS industry_margin_pct,
            revenue.revenue / 1000.0 AS revenue_musd,
            (
                revenue.revenue
                - costs.cost_of_sales
                - costs.selling_admin
                - costs.research_development
            ) / revenue.revenue * 100.0 AS operating_margin_pct
        FROM company AS c
        JOIN industry_medians AS im ON im.industry = c.industry
        CROSS JOIN revenue
        CROSS JOIN costs
    """
    margin_rows = _parse_table(sql_query.invoke({"query": margin_query}))
    if not margin_rows:
        raise ValueError("No usable data for Fisher point 5")
    margin_row = margin_rows[0]
    operating_margin = _number(margin_row, "operating_margin_pct")
    industry_margin = _number(margin_row, "industry_margin_pct")

    return {
        "point_1": {
            "company_growth_pct": company_growth,
            "industry_growth_pct": industry_growth,
            "verdict": "PASS" if company_growth >= industry_growth else "FAIL",
        },
        "point_5": {
            "operating_margin_pct": operating_margin,
            "industry_margin_pct": industry_margin,
            "verdict": "PASS" if operating_margin >= industry_margin else "FAIL",
        },
    }


def _collect_competitive_edge(interview: Any) -> str:
    """Spend at most two interviews trying the roles that know competitive edge."""
    for role in ("competitor", "customer"):
        answer = interview.invoke({"role": role, "topic": "competitive_edge"})
        lowered = answer.lower()
        if not answer.startswith("Unknown") and "limit reached" not in lowered:
            return answer.strip()
    return "No usable competitive-edge interview was returned."


def _parse_verdict_json(text: str) -> dict[str, str]:
    """Read the small JSON object requested from the model, tolerating code fences."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("Model did not return a JSON object")
    payload = json.loads(match.group(0))
    verdict = str(payload.get("verdict", "")).strip().upper()
    reason = str(payload.get("reason", "")).strip()
    if verdict not in VERDICTS or not reason:
        raise ValueError("Model JSON lacked a valid verdict or reason")
    return {"verdict": verdict, "reason": reason}


async def _judge_competitive_edge(agent: Any, quote: str) -> dict[str, str]:
    """Use the required model only to interpret the qualitative interview evidence."""
    from langchain_core.messages import HumanMessage, SystemMessage

    system = (
        "You assess Fisher point 11 for a company. Decide whether the interview "
        "shows a durable, business-specific advantage over competitors. PASS only "
        "when the quote gives a concrete advantage such as technology, switching "
        "costs, cost leadership, distribution, service, or customer loyalty. FAIL "
        "when the quote indicates parity, weakness, price-only competition, lost "
        "business, or no meaningful advantage. Base the decision only on the quote. "
        'Return JSON only: {"verdict":"PASS" or "FAIL","reason":"one sentence"}'
    )
    human = f"Competitive-edge interview:\n{quote}"
    response = await agent.ainvoke([SystemMessage(content=system), HumanMessage(content=human)])
    return _parse_verdict_json(str(getattr(response, "content", response)))


def _fallback_competitive_verdict(quote: str) -> dict[str, str]:
    """Keep a valid verdict when an individual API call fails."""
    lowered = quote.lower()
    negative = (
        "no real advantage",
        "no advantage",
        "nothing special",
        "not hard to copy",
        "price fight",
        "interchangeable",
        "same as",
        "price list",
        "cheapest",
        "price only",
        "lost business",
        "weak",
        "commodity",
        "cannot comment",
        "no usable",
    )
    positive = (
        "patent",
        "proprietary",
        "technology",
        "switching cost",
        "installed base",
        "hard to copy",
        "go back to them",
        "know-how",
        "unique",
        "market-leading",
        "leading",
        "strong brand",
        "loyal",
        "better service",
        "lowest cost",
        "distribution",
    )
    if any(phrase in lowered for phrase in negative):
        return {"verdict": "FAIL", "reason": "The interview does not identify a durable competitive advantage."}
    if any(phrase in lowered for phrase in positive):
        return {"verdict": "PASS", "reason": "The interview identifies a concrete competitive advantage."}
    return {
        "verdict": "PASS",
        "reason": "The interview did not clearly contradict a competitive advantage.",
    }


def _evidence_summary(evidence: dict[str, Any], competitive: dict[str, str]) -> str:
    point_1 = evidence["point_1"]
    point_5 = evidence["point_5"]
    return (
        f"Point 1 is {point_1['verdict']} because two-year revenue growth of "
        f"{point_1['company_growth_pct']:.1f}% compares with an industry median of "
        f"{point_1['industry_growth_pct']:.1f}%. Point 5 is {point_5['verdict']} because "
        f"the FY2025 operating margin of {point_5['operating_margin_pct']:.1f}% compares "
        f"with an industry median of {point_5['industry_margin_pct']:.1f}%. Point 11 is "
        f"{competitive['verdict']}: {competitive['reason']}"
    )


async def _analyze_one(agent: Any, tools: list[Any]) -> dict[str, Any]:
    """Analyse one company while preserving a usable result on individual failures."""
    tool_map = _tool_map(tools)
    evidence: dict[str, Any] = {}
    quote = "No interview evidence available."
    try:
        evidence = await asyncio.to_thread(_database_evidence, tool_map["sql_query"])
        quote = await asyncio.to_thread(_collect_competitive_edge, tool_map["interview"])
    except Exception as exc:  # Preserve the company entry even when one evidence path fails.
        quote = f"Evidence collection issue: {exc}"

    if evidence:
        try:
            competitive = await asyncio.wait_for(
                _judge_competitive_edge(agent, quote),
                timeout=MODEL_CALL_TIMEOUT_SECONDS,
            )
        except Exception:
            competitive = _fallback_competitive_verdict(quote)
        return {
            "points": {
                1: evidence["point_1"]["verdict"],
                5: evidence["point_5"]["verdict"],
                11: competitive["verdict"],
            },
            "summary": _evidence_summary(evidence, competitive),
        }

    return {
        "points": {1: "FAIL", 5: "FAIL", 11: _fallback_competitive_verdict(quote)["verdict"]},
        "summary": f"Evidence collection failed, so conservative verdicts were returned. {quote}",
    }


async def analyze_companies(agent: Any, companies: dict[str, list[Any]]) -> dict[str, dict[str, Any]]:
    """Run your agent and return its verdicts on CHOSEN_POINTS for every company.

    ``companies`` maps each company folder name to its four LangChain tools:

        list_documents()             names and dates of the company's documents
        read_document(name)          the text of one document
        sql_query(query)             one read-only SQLite query on company.db
        interview(role, topic)       one interview answer (INTERVIEW_LIMIT per company)

    Give the tools to a tool-calling agent, e.g.
    ``create_agent(model, tools=companies[name], system_prompt=...)``, or call
    them yourself with ``tool.invoke({...})``. The README explains the
    documents, the database tables and the interview roles and topics.

    Return one dictionary per company folder name, e.g. with
    CHOSEN_POINTS = [1, 7, 15]:

        {
            "hollins_group": {
                "points": {1: "PASS", 7: "FAIL", 15: "PASS"},
                "summary": "Two or three sentences explaining the verdicts.",
            },
            ...
        }

    Each chosen point needs a verdict ("PASS" or "FAIL"; True/False also
    works). Other points are ignored.

    Catch errors per company (e.g. a failed API call) and still return an
    entry for it: an exception here means no results.csv, which scores zero.
    You may analyse companies in parallel (e.g. ``asyncio.gather``).
    """
    ### YOUR CODE HERE
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPANIES)

    async def bounded_analysis(name: str, tools: list[Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    _analyze_one(agent, tools),
                    timeout=COMPANY_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                return {
                    "points": {1: "FAIL", 5: "FAIL", 11: "PASS"},
                    "summary": f"{name} exceeded the per-company safety limit; conservative verdicts were used.",
                }

    names = list(companies)
    results = await asyncio.gather(
        *(bounded_analysis(name, companies[name]) for name in names),
        return_exceptions=True,
    )
    analyses: dict[str, dict[str, Any]] = {}
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            analyses[name] = {
                "points": {1: "FAIL", 5: "FAIL", 11: "PASS"},
                "summary": f"Per-company analysis failed: {result}",
            }
        else:
            analyses[name] = result
    return analyses


# Everything below is provided runner/scoring code. No edits are needed.
# Your code must read company data only through these tools: do not open
# company.db or interviews.dat directly.

INTERVIEWS_USED: dict[str, int] = {}


def company_folders(folder: Path) -> list[Path]:
    """Return one sub-folder per company directly inside *folder*, sorted by name."""
    return sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))


def make_tools(company_folder: Path) -> list[Any]:
    """Build the four tools for one company."""
    from langchain_core.tools import tool

    name = company_folder.name
    db_uri = f"{(company_folder / 'company.db').resolve().as_uri()}?mode=ro"
    answers = json.loads(zlib.decompress((company_folder / "interviews.dat").read_bytes()))
    documents = {p.name: p for p in sorted(company_folder.glob("*.md"))}
    lock = threading.Lock()
    INTERVIEWS_USED[name] = 0

    @tool
    def list_documents() -> str:
        """List this company's documents (annual letter, earnings calls, news) with their dates."""
        lines = []
        for doc_name, path in documents.items():
            text = path.read_text(encoding="utf-8").splitlines()
            dated = next((line for line in text if line.startswith("Date:")), "")
            lines.append(f"{doc_name}: {text[0].lstrip('# ')} ({dated or 'undated'})")
        return "\n".join(lines)

    @tool
    def read_document(name: str) -> str:
        """Return the full text of one document named by list_documents, e.g. 'annual_letter_2025.md'."""
        path = documents.get(name.strip())
        if path is None:
            return f"No document named {name!r}. Available: {', '.join(documents)}"
        return path.read_text(encoding="utf-8")

    @tool
    def sql_query(query: str) -> str:
        """Run one read-only SQLite query on this company's database and return at most 50 rows.

        Amounts are in USD thousands. Dates are 'YYYY-MM-DD', months 'YYYY-MM', quarters like '2025Q3'.
        Tables:
          company(name, industry)
          industry_medians(industry, revenue_growth_2y_pct, new_product_share_pct, new_product_sales_per_rd, operating_margin_pct)
          orders(order_id, order_date, product, customer, amount, note)
          products(product, launch_date)
          monthly_costs(month, cost_of_sales, selling_admin, research_development)
          quarterly_cash(quarter, operating_cash_flow, capex, cash_end)
          capex_plan(year, planned_capex)
          returns(month, units_shipped, units_returned)
          insider_trades(trade_date, person, title, side, shares, price)
        Use SUM, GROUP BY and WHERE to compute totals rather than reading raw rows.
        """
        try:
            con = sqlite3.connect(db_uri, uri=True)
            try:
                cursor = con.execute(query)
                columns = [c[0] for c in cursor.description or []]
                rows = cursor.fetchmany(MAX_SQL_ROWS + 1)
            finally:
                con.close()
        except sqlite3.Error as exc:
            return f"SQL error: {exc}"
        if not columns:
            return "Query returned no columns."
        fmt = lambda v: f"{v:.2f}" if isinstance(v, float) else str(v)  # noqa: E731
        out = [" | ".join(columns)] + [" | ".join(fmt(v) for v in row) for row in rows[:MAX_SQL_ROWS]]
        if len(rows) > MAX_SQL_ROWS:
            out.append(f"... more rows not shown (limit {MAX_SQL_ROWS}); aggregate or add LIMIT/OFFSET.")
        if not rows:
            out.append("(no rows)")
        return "\n".join(out)

    @tool
    def interview(role: str, topic: str) -> str:
        """Interview one person about the company (at most 10 interviews per company).

        role: customer, current_employee, former_employee, competitor or supplier.
        topic: sales_team, working_conditions, top_management, decision_making or competitive_edge.
        People who would not know about a topic say so; the README lists who knows what.
        """
        role, topic = role.strip().lower(), topic.strip().lower()
        if role not in answers:
            return f"Unknown role {role!r}. Roles: {', '.join(answers)}. (Not counted.)"
        if topic not in answers[role]:
            return f"Unknown topic {topic!r}. Topics: {', '.join(answers[role])}. (Not counted.)"
        with lock:
            if INTERVIEWS_USED[name] >= INTERVIEW_LIMIT:
                return f"Interview limit reached: {INTERVIEW_LIMIT} interviews per company."
            INTERVIEWS_USED[name] += 1
        return answers[role][topic]

    return [list_documents, read_document, sql_query, interview]


def normalize_verdict(value: Any) -> str | None:
    """Accept PASS/FAIL (any case) or a boolean."""
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    text = str(getattr(value, "content", value)).strip().upper()
    return text if text in VERDICTS else None


def check_chosen_points(chosen: Any) -> list[int]:
    """CHOSEN_POINTS must name 3 different points between 1 and 15."""
    try:
        points = sorted({int(p) for p in chosen})
    except (TypeError, ValueError):
        points = []
    if len(points) != 3 or len(chosen) != 3 or not all(p in FISHER_POINTS for p in points):
        raise SystemExit("Set CHOSEN_POINTS at the top of hw3.py to 3 different point numbers "
                         f"between 1 and 15, e.g. [1, 7, 15] (now: {chosen!r}).")
    return points


def normalize_points(points: Any, chosen: list[int]) -> dict[int, str | None]:
    """Map each chosen point number to PASS/FAIL, or None when missing/invalid."""
    points = points if isinstance(points, dict) else {}
    by_number: dict[int, Any] = {}
    for key, value in points.items():
        try:
            by_number[int(key)] = value
        except (TypeError, ValueError):
            continue
    return {p: normalize_verdict(by_number.get(p)) for p in chosen}


def read_ground_truth(folder: Path) -> dict[str, dict[str, Any]]:
    """Read the expected 15 point verdicts per company."""
    path = folder / "ground_truth.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def point_errors(points: dict[int, str | None], expected: dict[str, Any] | None) -> tuple[int, str]:
    """Count correct point verdicts and describe the wrong ones."""
    if expected is None:
        return 0, ""
    wrong = []
    for p in points:
        truth = expected["points"][str(p)]
        if points[p] != truth:
            wrong.append(f"{p}: expected {truth}, predicted {points[p] or 'missing'}")
    if all(v is None for v in points.values()):
        return 0, "all point verdicts missing"
    return len(points) - len(wrong), "; ".join(wrong)


def balanced_point_accuracy(
    analyses: dict[str, Any], truth: dict[str, dict[str, Any]], names: list[str], chosen: list[int]
) -> float:
    """Mean of the accuracy on true-PASS points and on true-FAIL points.

    Most verdicts are PASS, so answering PASS everywhere gets a high plain
    accuracy but only 50% balanced accuracy. Grading uses the balanced number.
    """
    hits = {"PASS": [0, 0], "FAIL": [0, 0]}
    for name in names:
        if name not in truth:
            continue
        entry = analyses.get(name)
        points = normalize_points(entry.get("points") if isinstance(entry, dict) else None, chosen)
        for p in chosen:
            expected = truth[name]["points"][str(p)]
            hits[expected][0] += points[p] == expected
            hits[expected][1] += 1
    rates = [h / n for h, n in hits.values() if n]
    return sum(rates) / len(rates) if rates else 0.0


def write_results(
    names: list[str], analyses: dict[str, Any], truth: dict[str, dict[str, Any]], chosen: list[int]
) -> tuple[Path, int]:
    """Write results.csv and return the number of correct point verdicts."""
    output = Path("results.csv")
    correct_points = 0
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["company", "points", "points_correct", "point_errors", "interviews_used", "summary"])
        for name in names:
            entry = analyses.get(name)
            entry = entry if isinstance(entry, dict) else {}
            points = normalize_points(entry.get("points"), chosen)
            expected = truth.get(name)
            n_points, errors = point_errors(points, expected)
            correct_points += n_points
            writer.writerow([
                name,
                " ".join(f"{p}:{points[p] or 'missing'}" for p in chosen),
                f"{n_points}/{len(chosen)}" if expected else "",
                errors,
                INTERVIEWS_USED.get(name, 0),
                str(entry.get("summary", "")).strip(),
            ])
    return output, correct_points


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run FTEC5660 HW3 on company data folders")
    parser.add_argument(
        "--data-folder",
        required=True,
        type=Path,
        help="folder containing one sub-folder per company",
    )
    return parser.parse_args()


async def run(folder: Path) -> int:
    folders = company_folders(folder)
    if not folders:
        raise SystemExit(f"no company folders found in {folder}")

    chosen = check_chosen_points(CHOSEN_POINTS)
    load_env_file()
    companies = {path.name: make_tools(path) for path in folders}
    agent = build_agent()
    analyses = await analyze_companies(agent, companies)
    if not isinstance(analyses, dict):
        raise TypeError("analyze_companies() must return a dictionary")

    truth = read_ground_truth(folder)
    output, correct_points = write_results(list(companies), analyses, truth, chosen)
    summary = f" Your points: {', '.join(map(str, chosen))}."
    if truth:
        summary += (
            f" Point accuracy: {correct_points}/{len(companies) * len(chosen)}"
            f" (balanced: {balanced_point_accuracy(analyses, truth, list(companies), chosen):.1%})."
        )
    print(f"Processed {len(companies)} compan{'y' if len(companies) == 1 else 'ies'}. Wrote {output}.{summary}")
    return 0


def main() -> int:
    args = parse_args()
    if not args.data_folder.is_dir():
        raise SystemExit(f"not a folder: {args.data_folder}")
    return asyncio.run(run(args.data_folder))


if __name__ == "__main__":
    raise SystemExit(main())
