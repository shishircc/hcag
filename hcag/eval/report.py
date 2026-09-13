"""HTML report generator (§7.8).

Renders a single self-contained ``.html`` file — inlined CSS, no external
assets — with a run summary, per-kind panels for each of the five question
types, a global score distribution, and a row-level table with expandable
transcripts. If ``cfg.report.baseline`` is set, a per-kind delta bar appears
at the top of the page.

When the rows carry personas (§6.7.2) the page also gets a per-persona
breakdown and a persona-by-kind grid. That is the view the roster was built
for: an overall pass rate can look healthy while one role's rows sit a point
lower, which is a phrasing and retrieval problem rather than a knowledge gap
and is invisible in every other panel on the page.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EvalConfig
from .csv_io import EvalRow, VALID_KINDS


KIND_ORDER = ["simple", "medium", "complex", "hard-1", "hard-2"]


def _stats_for(rows: list[EvalRow]) -> dict[str, Any]:
    scored = [r for r in rows if isinstance(r.score, int)]
    n = len(rows)
    n_scored = len(scored)
    hist = {i: 0 for i in range(4)}
    for r in scored:
        hist[int(r.score or 0)] += 1  # type: ignore[arg-type]
    total = sum(r.score or 0 for r in scored)
    passed = sum(1 for r in scored if (r.score or 0) >= 2)
    return {
        "count": n,
        "scored": n_scored,
        "unscored": n - n_scored,
        "mean_score": round(total / n_scored, 3) if n_scored else 0.0,
        "pass_rate": round(passed / n_scored, 3) if n_scored else 0.0,
        "histogram": hist,
    }


def _group_by_kind(rows: list[EvalRow]) -> dict[str, list[EvalRow]]:
    out: dict[str, list[EvalRow]] = {k: [] for k in KIND_ORDER}
    for r in rows:
        out.setdefault(r.kind, []).append(r)
    return out


#: Rows generated without a roster are grouped rather than dropped — an empty
#: `persona` means persona-free, which is a supported mode and not missing data.
UNATTRIBUTED = "(unattributed)"


def _group_by_persona(rows: list[EvalRow]) -> dict[str, list[EvalRow]]:
    out: dict[str, list[EvalRow]] = {}
    for r in rows:
        out.setdefault(r.persona or UNATTRIBUTED, []).append(r)
    return dict(sorted(out.items()))


def _persona_kind_grid(rows: list[EvalRow], personas: dict[str, list[EvalRow]]) -> str:
    """Mean score per (persona, kind) cell.

    It separates the two readings of a weak persona: low across every kind
    means that role's phrasing is not being retrieved against, low only on
    `complex` and `hard-1` means that role simply asks harder questions.
    """
    head = "".join(f"<th>{html.escape(k)}</th>" for k in KIND_ORDER)
    body = []
    for name, prows in personas.items():
        by_kind = _group_by_kind(prows)
        cells = []
        for kind in KIND_ORDER:
            cell = _stats_for(by_kind.get(kind, []))
            if cell["scored"] == 0:
                cells.append('<td class="muted">—</td>')
            else:
                cells.append(f'<td>{cell["mean_score"]} <span class="muted">({cell["scored"]})</span></td>')
        body.append(f"<tr><td>{html.escape(name)}</td>{''.join(cells)}</tr>")
    return (
        '<table class="rows"><thead><tr><th>Persona</th>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _bar(pct: float, width: int = 200) -> str:
    px = max(0, int(round(pct * width)))
    return (
        f'<div class="bar-wrap" style="width:{width}px">'
        f'<div class="bar-fill" style="width:{px}px"></div></div>'
    )


def _hist_bars(hist: dict[int, int], max_val: int) -> str:
    if max_val == 0:
        return "<span class='muted'>no scored rows</span>"
    out = []
    palette = ["#c53030", "#dd8f3a", "#4a8f4a", "#2f7d32"]  # 0,1,2,3
    for i in range(4):
        v = hist.get(i, 0)
        h = max(2, int(round((v / max_val) * 60)))
        out.append(
            f'<div class="hist-col"><div class="hist-bar" '
            f'style="height:{h}px;background:{palette[i]}"></div>'
            f'<div class="hist-label">{i}</div>'
            f'<div class="hist-count">{v}</div></div>'
        )
    return '<div class="hist-row">' + "".join(out) + "</div>"


def _panel(kind: str, rows: list[EvalRow]) -> str:
    stats = _stats_for(rows)
    max_h = max(stats["histogram"].values()) if stats["histogram"] else 0
    return f"""
    <div class="panel">
      <div class="panel-title">{html.escape(kind)}</div>
      <div class="panel-grid">
        <div class="metric"><div class="metric-value">{stats["count"]}</div><div class="metric-label">rows</div></div>
        <div class="metric"><div class="metric-value">{stats["mean_score"]}</div><div class="metric-label">mean score</div></div>
        <div class="metric"><div class="metric-value">{int(stats["pass_rate"] * 100)}%</div><div class="metric-label">pass rate (≥2)</div></div>
        <div class="metric"><div class="metric-value">{stats["unscored"]}</div><div class="metric-label">unscored</div></div>
      </div>
      {_hist_bars(stats["histogram"], max_h)}
    </div>
    """


def _baseline_delta(row: EvalRow, baseline_by_id: dict[str, EvalRow]) -> str:
    b = baseline_by_id.get(row.question_id)
    if not b or not isinstance(b.score, int) or not isinstance(row.score, int):
        return "—"
    d = row.score - b.score
    sign = "+" if d > 0 else ""
    color = "#2f7d32" if d > 0 else ("#c53030" if d < 0 else "#666")
    return f'<span style="color:{color};font-weight:600">{sign}{d}</span>'


#: What each transcript turn is called on the page. `evalrun` speaks in two
#: voices — the eval set's own question, and any clarification the clarifier
#: (§7.4.2) wrote on the asker's behalf — and a reader auditing a multi-turn row
#: needs to know which of the two pulled the next reply out of the agent.
_TURN_LABEL = {
    ("user", "user"): "User (from the eval set)",
    ("user", "clarifier"): "User (written by the clarifier)",
    ("bot", "bot"): "Agent",
    ("bot", "bot_final"): "Agent (final)",
    ("system", "error"): "evalrun (why it stopped)",
}


def _turns_cell(row: EvalRow, meta: dict) -> str:
    """The reply count, called out when it is more than one.

    A single reply is the ordinary case and needs no emphasis. Anything higher
    means the clarifier stepped in, which changes how the row should be read:
    the agent may have answered well and been asked again anyway.
    """
    replies = meta.get("bot_replies")
    if not isinstance(replies, int) or replies <= 0:
        replies = row.turns if isinstance(row.turns, int) else None
    if replies is None:
        return '<td class="muted">—</td>'
    if replies == 1:
        return f'<td class="turns-cell">{replies}</td>'
    return f'<td class="turns-cell multi" title="clarifier drove {replies - 1} extra turn(s)">{replies}</td>'


def _transcript_html(turns: list[dict], row: EvalRow) -> str:
    """Render the exchange turn by turn, labelled with who spoke.

    The JSON dump this replaces held the same information and was unreadable at
    the moment it matters most — when the question is why the agent was asked
    twice. Falls back to the CSV's `transcript` column, so a report rendered
    from a scored CSV alone still shows the exchange.
    """
    if not turns:
        if row.transcript.strip():
            return f"<pre>{html.escape(row.transcript)}</pre>"
        return '<div class="muted">(no transcript recorded)</div>'
    out = []
    for t in turns:
        label = _TURN_LABEL.get(
            (t.get("role", ""), t.get("source", "")), t.get("role", "turn").title()
        )
        cls = {"bot": "turn-bot", "system": "turn-error"}.get(t.get("role", ""), "turn-user")
        ms = t.get("elapsed_ms") or 0
        timing = f' <span class="muted">{int(ms)} ms</span>' if ms else ""
        out.append(
            f'<div class="turn {cls}"><div class="turn-label">{html.escape(label)}{timing}</div>'
            f'<pre>{html.escape(t.get("text", ""))}</pre></div>'
        )
    return '<div class="turns">' + "".join(out) + "</div>"


def _row_table(
    rows: list[EvalRow],
    baseline_by_id: dict[str, EvalRow],
    metas: dict[str, dict],
    inherited_ids: set[str],
) -> str:
    parts = ['<table class="rows"><thead><tr>',
             "<th>ID</th><th>Kind</th><th>Score</th><th>Turns</th>"]
    if baseline_by_id:
        parts.append("<th>Δ</th>")
    parts.append("<th>Question</th><th>Actual answer</th><th>Remark</th><th></th></tr></thead><tbody>")
    for i, r in enumerate(rows):
        meta = metas.get(r.question_id) or {}
        turns = meta.get("transcript") or []
        score_class = f"score-{r.score}" if isinstance(r.score, int) else "score-none"
        score_txt = str(r.score) if isinstance(r.score, int) else "—"
        score_label = f" (scored {r.score})" if isinstance(r.score, int) else ""
        # Inherited rows count toward every figure on the page, which is exactly
        # why they are labelled: a reader comparing two reports needs to see
        # that part of one was measured earlier (§7.3.3).
        inherited = r.question_id in inherited_ids
        scope = "inherited" if inherited else "executed"
        badge = ' <span class="tag">inherited</span>' if inherited else ""
        parts.append(
            f'<tr class="{score_class}" data-kind="{html.escape(r.kind)}" data-scope="{scope}">'
            f'<td class="mono">{html.escape(r.question_id)}{badge}</td>'
            f'<td>{html.escape(r.kind)}</td>'
            f'<td class="score-cell">{score_txt}</td>'
            f"{_turns_cell(r, meta)}"
        )
        if baseline_by_id:
            parts.append(f"<td>{_baseline_delta(r, baseline_by_id)}</td>")
        # The judge's remark is the reason a row scored what it did, so the
        # expanded view carries it in full — the column can only ever show the
        # first line of it. The question comes too, so an expanded row reads on
        # its own without the reader tracking back up to the clipped cell.
        remark_html = (
            f'<div class="detail-block"><b>Judge remark{score_label}:</b>'
            f'<pre>{html.escape(r.remark)}</pre></div>'
            if r.remark
            else '<div class="detail-block muted"><b>Judge remark:</b> (none recorded)</div>'
        )
        # colspan must cover every column including the toggle, or the expanded
        # block stops short of the table's width.
        span = 9 if baseline_by_id else 8
        parts.append(
            f'<td class="clip" title="{html.escape(r.question, quote=True)}">'
            f'{html.escape(r.question)}</td>'
            f'<td class="clip" title="{html.escape(r.actual_answer, quote=True)}">'
            f'{html.escape(r.actual_answer)}</td>'
            f'<td class="clip-remark" title="{html.escape(r.remark, quote=True)}">'
            f'{html.escape(r.remark)}</td>'
            f'<td><button class="toggle" onclick="toggleRow({i})">▸</button></td>'
            f"</tr>"
            f'<tr id="detail-{i}" class="detail" style="display:none">'
            f'<td colspan="{span}">'
            f'<div class="detail-block"><b>Question:</b><pre>{html.escape(r.question)}</pre></div>'
            f'<div class="detail-block"><b>Expected:</b><pre>{html.escape(r.expected_answer)}</pre></div>'
            f'<div class="detail-block"><b>Actual:</b><pre>{html.escape(r.actual_answer)}</pre></div>'
            f"{remark_html}"
            f'<div class="detail-block"><b>Transcript:</b>{_transcript_html(turns, r)}</div>'
            f"</td></tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _baseline_panel(rows: list[EvalRow], baseline_rows: list[EvalRow]) -> str:
    """Per-kind pass-rate deltas vs. a prior --out CSV."""
    curr_by_kind = _group_by_kind(rows)
    base_by_kind = _group_by_kind(baseline_rows)
    out = ['<div class="baseline"><h3>Baseline comparison</h3>']
    out.append('<table class="baseline-table"><thead><tr>'
               "<th>Kind</th><th>Curr rows</th><th>Curr mean</th><th>Base mean</th><th>Δ mean</th>"
               "<th>Curr pass%</th><th>Base pass%</th><th>Δ pass%</th></tr></thead><tbody>")
    for kind in KIND_ORDER:
        cs = _stats_for(curr_by_kind.get(kind, []))
        bs = _stats_for(base_by_kind.get(kind, []))
        dm = round(cs["mean_score"] - bs["mean_score"], 3)
        dp = int(round((cs["pass_rate"] - bs["pass_rate"]) * 100))
        dm_color = "#2f7d32" if dm > 0 else ("#c53030" if dm < 0 else "#666")
        dp_color = "#2f7d32" if dp > 0 else ("#c53030" if dp < 0 else "#666")
        sign_m = "+" if dm > 0 else ""
        sign_p = "+" if dp > 0 else ""
        out.append(
            f"<tr><td>{kind}</td><td>{cs['count']}</td>"
            f"<td>{cs['mean_score']}</td><td>{bs['mean_score']}</td>"
            f'<td style="color:{dm_color};font-weight:600">{sign_m}{dm}</td>'
            f"<td>{int(cs['pass_rate']*100)}%</td>"
            f"<td>{int(bs['pass_rate']*100)}%</td>"
            f'<td style="color:{dp_color};font-weight:600">{sign_p}{dp}%</td>'
            f"</tr>"
        )
    out.append("</tbody></table></div>")
    return "".join(out)


def _scope_note(scope: dict[str, Any] | None, inherited: int, total: int) -> str:
    """State, on the page, that a partial run's figures span two measurements.

    A headline pass rate computed across an inherited half and a re-run half has
    to say so where the reader is looking, not in a log file (§7.3.3).
    """
    if not inherited:
        return ""
    flag = (scope or {}).get("flag") or "a row selector"
    spec = (scope or {}).get("spec") or ""
    executed = (scope or {}).get("executed", total - inherited)
    empty = (scope or {}).get("empty_inherited") or 0
    spec_html = f" <code>{html.escape(str(spec))}</code>" if spec else ""
    empty_html = (
        f" {empty} inherited row(s) carry no score, so those figures have holes."
        if empty
        else ""
    )
    return (
        '<div class="note"><b>Partial run.</b> '
        f"{executed} row(s) were re-run, selected by <code>{html.escape(flag)}</code>"
        f"{spec_html}; {inherited} were inherited from the input file and are marked "
        "in the table below. Every figure on this page is computed across both, so it "
        "spans two runs made at different times and possibly by different judges."
        f"{empty_html}</div>"
    )


def _css() -> str:
    return """
    :root { --line: #e3e6e9; --ink: #24282c; --muted: #6b737a; --bg: #f6f7f8; --primary: #3554a5; }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, "Segoe UI", "Source Sans 3", Helvetica, sans-serif;
           color: var(--ink); background: #fff; margin: 0; padding: 32px; line-height: 1.5; }
    h1 { margin: 0 0 4px; font-size: 26px; }
    h2 { margin: 32px 0 12px; font-size: 18px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }
    h3 { margin: 24px 0 8px; font-size: 15px; }
    .sub { color: var(--muted); font-size: 13px; }
    .summary { display: flex; gap: 24px; flex-wrap: wrap; margin: 20px 0 4px; }
    .summary .metric { min-width: 100px; }
    .metric { padding: 10px 14px; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; }
    .metric-value { font-size: 22px; font-weight: 700; }
    .metric-label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
    .panels { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
    .panel { border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; background: #fff; }
    .panel-title { font-weight: 700; margin-bottom: 6px; text-transform: capitalize; }
    .panel-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-bottom: 10px; }
    .panel .metric { padding: 6px 8px; text-align: center; }
    .panel .metric-value { font-size: 16px; }
    .hist-row { display: flex; gap: 6px; align-items: flex-end; height: 80px; padding: 4px 0; border-top: 1px dashed var(--line); }
    .hist-col { display: flex; flex-direction: column; align-items: center; flex: 1; }
    .hist-bar { width: 100%; border-radius: 3px 3px 0 0; }
    .hist-label { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .hist-count { font-size: 11px; font-weight: 600; }
    .baseline-table { border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 12px; }
    .baseline-table th, .baseline-table td { border: 1px solid var(--line); padding: 6px 8px; text-align: left; }
    .baseline-table th { background: var(--bg); }
    .filters { margin: 12px 0; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    .chip { background: #fff; border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px;
            font-size: 12px; cursor: pointer; }
    .chip.on { background: var(--primary); color: #fff; border-color: transparent; }
    table.rows { border-collapse: collapse; width: 100%; font-size: 13px; }
    table.rows th, table.rows td { border-bottom: 1px solid var(--line); padding: 6px 8px; text-align: left; vertical-align: top; }
    table.rows th { background: var(--bg); position: sticky; top: 0; }
    .clip { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    /* The remark is the judge's reasoning — one ellipsised line is unreadable,
       so it wraps to three and the rest is in the expanded row (and title). */
    .clip-remark { max-width: 440px; white-space: normal; overflow: hidden;
                   display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3;
                   -webkit-box-orient: vertical; }
    .mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
    .score-cell { font-weight: 700; text-align: center; }
    tr.score-0 .score-cell { color: #c53030; }
    tr.score-1 .score-cell { color: #dd8f3a; }
    tr.score-2 .score-cell { color: #4a8f4a; }
    tr.score-3 .score-cell { color: #2f7d32; }
    tr.score-none .score-cell { color: var(--muted); }
    .detail-block { margin: 8px 0; }
    .detail-block pre { background: var(--bg); padding: 8px 12px; border-radius: 6px; overflow: auto;
                        white-space: pre-wrap; word-break: break-word; font-size: 12px; }
    .toggle { background: transparent; border: 0; cursor: pointer; font-size: 14px; color: var(--muted); }
    .muted { color: var(--muted); }
    .tag { font-family: -apple-system, sans-serif; font-size: 10px; text-transform: uppercase;
           letter-spacing: 0.06em; color: var(--muted); background: var(--bg);
           border: 1px solid var(--line); border-radius: 999px; padding: 1px 6px; white-space: nowrap; }
    .turns-cell { text-align: center; font-variant-numeric: tabular-nums; }
    .turns-cell.multi { font-weight: 700; color: #b4530a; }
    .turn { margin: 6px 0; border-left: 3px solid var(--line); padding-left: 10px; }
    .turn-user { border-left-color: #8a94a6; }
    .turn-bot { border-left-color: var(--primary); }
    .turn-error { border-left-color: #c53030; }
    .turn-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
                  color: var(--muted); margin-bottom: 2px; }
    .note { margin: 10px 0 0; padding: 10px 14px; font-size: 13px; background: #fdf6e6;
            border: 1px solid #e8d9a8; border-radius: 8px; }
    .bar-wrap { display: inline-block; background: var(--bg); border-radius: 3px; overflow: hidden;
                border: 1px solid var(--line); height: 8px; vertical-align: middle; }
    .bar-fill { background: var(--primary); height: 100%; }
    """


def _js() -> str:
    """Row filtering runs on two independent dimensions — kind, and whether the
    row was executed or inherited (§7.3.3) — so a chip group carries its own
    name and the two intersect."""
    return """
    function toggleRow(i) {
      var d = document.getElementById('detail-' + i);
      var open = d.style.display === 'none';
      d.style.display = open ? 'table-row' : 'none';
      d.dataset.open = open ? '1' : '0';
    }
    function activeValues(group) {
      var on = document.querySelectorAll('.chip.on[data-group="' + group + '"]');
      var vals = Array.prototype.map.call(on, function(c){ return c.dataset.value; });
      // No chips, or the `all` chip, means the dimension does not filter.
      return (vals.length === 0 || vals.indexOf('all') !== -1) ? null : vals;
    }
    function applyFilter() {
      var kinds = activeValues('kind');
      var scopes = activeValues('scope');
      document.querySelectorAll('tr[data-kind]').forEach(function(row){
        var show = (!kinds || kinds.indexOf(row.dataset.kind) !== -1)
                && (!scopes || scopes.indexOf(row.dataset.scope) !== -1);
        row.style.display = show ? '' : 'none';
        var next = row.nextElementSibling;
        if (next && next.classList.contains('detail')) {
          next.style.display = show && next.dataset.open === '1' ? 'table-row' : 'none';
        }
      });
    }
    document.addEventListener('click', function(e){
      var chip = e.target;
      if (!(chip && chip.classList && chip.classList.contains('chip'))) return;
      var group = chip.dataset.group;
      var sel = '.chip[data-group="' + group + '"]';
      if (chip.dataset.value === 'all') {
        document.querySelectorAll(sel + '.on').forEach(function(c){ c.classList.remove('on'); });
        chip.classList.add('on');
      } else {
        var allChip = document.querySelector(sel + '[data-value="all"]');
        if (allChip) allChip.classList.remove('on');
        chip.classList.toggle('on');
      }
      applyFilter();
    });
    """


def render_report(
    *,
    rows_with_meta: list[tuple[EvalRow, dict]],
    baseline_rows: list[EvalRow] | None,
    cfg: EvalConfig,
    path: Path,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render the run's HTML report.

    ``scope`` describes a partial run — which flag chose the executed rows and
    how many were inherited (§7.3.3). Every headline figure on the page is then
    computed across two runs made at different times, possibly by different
    judges, so the page says so instead of leaving it to a log the reader does
    not have.
    """
    rows = [r for r, _ in rows_with_meta]
    metas = {r.question_id: m for r, m in rows_with_meta}
    inherited_ids = {r.question_id for r, m in rows_with_meta if m.get("inherited")}

    overall = _stats_for(rows)
    per_kind = _group_by_kind(rows)
    max_h = max((_stats_for(v)["histogram"].get(i, 0) for v in per_kind.values() for i in range(4)), default=0)

    baseline_html = ""
    baseline_by_id: dict[str, EvalRow] = {}
    if baseline_rows:
        baseline_by_id = {r.question_id: r for r in baseline_rows}
        baseline_html = _baseline_panel(rows, baseline_rows)

    panels_html = "".join(_panel(k, per_kind.get(k, [])) for k in KIND_ORDER)

    # Shown only when the input carries personas: an empty section would
    # suggest the run lost something it never had.
    per_persona = _group_by_persona(rows)
    persona_html = ""
    if any(r.persona for r in rows):
        persona_panels = "".join(_panel(name, prows) for name, prows in per_persona.items())
        persona_html = (
            "<h2>Per-persona breakdown</h2>"
            f'<div class="panels">{persona_panels}</div>'
            "<h2>Persona × kind</h2>"
            + _persona_kind_grid(rows, per_persona)
        )
    chip_html = (
        '<div class="filters"><span class="muted">Kind:</span>'
        '<button class="chip on" data-group="kind" data-value="all">all</button>'
        + "".join(
            f'<button class="chip" data-group="kind" data-value="{k}">{k}</button>'
            for k in KIND_ORDER
        )
        + "</div>"
    )
    if inherited_ids:
        # The view the operator who re-ran a handful of rows actually wants.
        chip_html += (
            '<div class="filters"><span class="muted">Rows:</span>'
            '<button class="chip on" data-group="scope" data-value="all">all</button>'
            '<button class="chip" data-group="scope" data-value="executed">re-run</button>'
            '<button class="chip" data-group="scope" data-value="inherited">inherited</button>'
            "</div>"
        )
    table_html = _row_table(rows, baseline_by_id, metas, inherited_ids)

    scope_html = _scope_note(scope, len(inherited_ids), len(rows))
    inherited_metric = (
        f'  <div class="metric"><div class="metric-value">{len(inherited_ids)}</div>'
        '<div class="metric-label">inherited</div></div>\n'
        if inherited_ids
        else ""
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    html_doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(cfg.report.title)}</title>
<style>{_css()}</style>
</head>
<body>
<h1>{html.escape(cfg.report.title)}</h1>
<div class="sub">Generated {generated_at} · backend <code>{html.escape(cfg.backend.url)}</code>
· classifier <code>{html.escape(cfg.classifier.llm.model)}</code>
· judge <code>{html.escape(cfg.judge.llm.model)}</code>
· concurrency {cfg.run.concurrency}
· seed {cfg.run.seed if cfg.run.seed is not None else "-"}</div>

<div class="summary">
  <div class="metric"><div class="metric-value">{overall["count"]}</div><div class="metric-label">rows</div></div>
  <div class="metric"><div class="metric-value">{overall["scored"]}</div><div class="metric-label">scored</div></div>
  <div class="metric"><div class="metric-value">{overall["mean_score"]}</div><div class="metric-label">mean score</div></div>
  <div class="metric"><div class="metric-value">{int(overall["pass_rate"] * 100)}%</div><div class="metric-label">pass rate (≥2)</div></div>
  <div class="metric"><div class="metric-value">{overall["unscored"]}</div><div class="metric-label">unscored</div></div>
{inherited_metric}</div>

{scope_html}

{baseline_html}

<h2>Per-kind breakdown</h2>
<div class="panels">{panels_html}</div>

{persona_html}

<h2>Overall score distribution</h2>
{_hist_bars(overall["histogram"], max(overall["histogram"].values()) if overall["histogram"] else 0)}

<h2>Rows</h2>
{chip_html}
{table_html}

<script>{_js()}</script>
</body></html>
"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(html_doc)

    return {
        "mean_score": overall["mean_score"],
        "pass_rate": overall["pass_rate"],
        "scored": overall["scored"],
        "unscored": overall["unscored"],
    }
