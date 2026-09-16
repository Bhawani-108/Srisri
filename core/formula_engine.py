import os
import re
import json
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "column_config.json")

# In-memory evaluation cache state
_CACHED_CONFIG_MTIME = -1
_CACHED_DEFINITIONS = []
_CACHED_ORDERED_COLUMNS = []
_CACHED_VISIBLE_COLUMNS = []
_CACHED_EXECUTION_PLAN = []
_CACHED_DISPLAY_ORDER = []
_CACHED_RENAME_MAP = {}

ENV = {
    "IF": lambda cond, t, f: np.where(cond, t, f),
    "MAX": np.maximum,
    "MIN": np.minimum,
    "ABS": np.abs,
    "np": np
}

def _sanitize_col_name(name: str) -> str:
    return re.sub(r'[\s\.\-\%/]', '_', str(name))

def _get_formula_dependencies(expr: str, all_sanitized_targets: set) -> set:
    tokens = set(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', expr))
    return tokens.intersection(all_sanitized_targets)

def _rebuild_cache():
    global _CACHED_CONFIG_MTIME, _CACHED_DEFINITIONS, _CACHED_ORDERED_COLUMNS
    global _CACHED_VISIBLE_COLUMNS, _CACHED_EXECUTION_PLAN, _CACHED_DISPLAY_ORDER, _CACHED_RENAME_MAP

    if not os.path.exists(CONFIG_PATH):
        _CACHED_DEFINITIONS = []
        _CACHED_ORDERED_COLUMNS = []
        _CACHED_VISIBLE_COLUMNS = []
        _CACHED_EXECUTION_PLAN = []
        _CACHED_DISPLAY_ORDER = []
        _CACHED_RENAME_MAP = {}
        return

    try:
        current_mtime = os.stat(CONFIG_PATH).st_mtime_ns
    except OSError:
        current_mtime = -1

    if current_mtime == _CACHED_CONFIG_MTIME:
        return

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        defs = data.get("columns", [])
    except Exception as e:
        print(f"Error reading {CONFIG_PATH}: {e}")
        return

    _CACHED_CONFIG_MTIME = current_mtime
    _CACHED_DEFINITIONS = defs
    _CACHED_ORDERED_COLUMNS = [c.get("name") for c in defs if isinstance(c, dict) and "name" in c]
    _CACHED_VISIBLE_COLUMNS = [c.get("name") for c in defs if isinstance(c, dict) and c.get("visible", True)]

    # Pre-compile topological execution plan
    all_col_identifiers = {_sanitize_col_name(it.get("name")) for it in defs}
    formula_items = [it for it in defs if it.get("type") == "formula" and it.get("formula")]

    # Build dependency graph
    graph = {}
    item_map = {}
    for item in formula_items:
        s_name = _sanitize_col_name(item.get("name"))
        item_map[s_name] = item
        expr = str(item.get("formula", ""))
        deps = _get_formula_dependencies(expr, all_col_identifiers) - {s_name}
        graph[s_name] = deps

    # Topological sort
    sorted_names = []
    visited = set()
    visiting = set()

    def visit(node):
        if node in visiting: return
        if node not in visited:
            visiting.add(node)
            for neighbor in graph.get(node, []):
                if neighbor in item_map:
                    visit(neighbor)
            visiting.remove(node)
            visited.add(node)
            sorted_names.append(node)

    for node in graph:
        if node not in visited:
            visit(node)

    # Pre-compile expressions to native Python bytecode
    plan = []
    for s_name in sorted_names:
        item = item_map[s_name]
        raw_expr = str(item.get("formula", ""))

        for raw_col in defs:
            r_name = raw_col.get("name", "")
            c_target = _sanitize_col_name(r_name)
            if r_name != c_target and r_name in raw_expr:
                raw_expr = raw_expr.replace(r_name, c_target)

        is_if = False
        compiled_parts = None
        if raw_expr.startswith("IF(") and raw_expr.endswith(")"):
            inner = raw_expr[3:-1]
            parts = [p.strip() for p in inner.split(",")]
            if len(parts) == 3:
                is_if = True
                compiled_parts = [
                    compile(parts[0], '', 'eval'),
                    compile(parts[1], '', 'eval'),
                    compile(parts[2], '', 'eval')
                ]

        if not is_if:
            compiled_code = compile(raw_expr, '', 'eval')
        else:
            compiled_code = None

        plan.append({
            "target": s_name,
            "is_if": is_if,
            "code": compiled_code,
            "parts": compiled_parts
        })

    _CACHED_EXECUTION_PLAN = plan
    _CACHED_RENAME_MAP = {_sanitize_col_name(item.get("name")): item.get("name") for item in defs}
    _CACHED_DISPLAY_ORDER = [item.get("name") for item in defs]

def load_column_definitions():
    _rebuild_cache()
    return _CACHED_DEFINITIONS

def save_column_definitions(columns):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"columns": columns}, f, indent=2)
        _rebuild_cache()
    except Exception as e:
        print(f"Error saving {CONFIG_PATH}: {e}")

def get_configured_column_order():
    _rebuild_cache()
    return _CACHED_ORDERED_COLUMNS

def get_visible_columns():
    _rebuild_cache()
    return _CACHED_VISIBLE_COLUMNS

def set_column_visibility(visible_names):
    visible_set = set(visible_names)
    definitions = load_column_definitions()
    for col in definitions:
        col["visible"] = col.get("name") in visible_set
    save_column_definitions(definitions)

def evaluate_formulas(df: pd.DataFrame) -> pd.DataFrame:
    """Ultra-fast, zero-IO, precompiled formula evaluator."""
    _rebuild_cache()
    if df is None or df.empty:
        return pd.DataFrame(columns=_CACHED_DISPLAY_ORDER)

    output_df = df.copy()

    # Fast column renaming via direct dictionary map
    col_mapping = {col: _sanitize_col_name(col) for col in output_df.columns}
    working_df = output_df.rename(columns=col_mapping)

    # Local namespace containing references to series and custom functions
    eval_scope = dict(ENV)
    for col in working_df.columns:
        eval_scope[col] = pd.to_numeric(working_df[col], errors="coerce").fillna(0.0)

    # Execute bytecode in dependency order
    for step in _CACHED_EXECUTION_PLAN:
        target = step["target"]
        try:
            if step["is_if"]:
                p0, p1, p2 = step["parts"]
                cond = eval(p0, eval_scope)
                val_t = eval(p1, eval_scope)
                val_f = eval(p2, eval_scope)
                res = np.where(cond, val_t, val_f)
            else:
                res = eval(step["code"], eval_scope)

            res = pd.to_numeric(pd.Series(res, index=output_df.index), errors="coerce").replace([np.inf, -np.inf], 0.0).fillna(0.0)
            eval_scope[target] = res

            real_col_name = _CACHED_RENAME_MAP.get(target, target)
            output_df[real_col_name] = res
        except Exception:
            real_col_name = _CACHED_RENAME_MAP.get(target, target)
            output_df[real_col_name] = 0.0

    # Retain strictly integer typing for Index
    if "Index" in output_df.columns:
        output_df["Index"] = pd.to_numeric(output_df["Index"], errors="coerce").fillna(0).astype(int)

    # Ensure all configured columns exist
    for col in _CACHED_DISPLAY_ORDER:
        if col not in output_df.columns:
            output_df[col] = np.nan

    # Fast column projection without full iteration
    available = [c for c in _CACHED_DISPLAY_ORDER if c in output_df.columns]
    return output_df[available]