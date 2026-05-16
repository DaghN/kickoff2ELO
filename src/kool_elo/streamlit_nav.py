"""Streamlit in-app navigation for multipage player profiles."""

from __future__ import annotations

import inspect
from typing import Any

import pandas as pd


def _get_member(obj: Any, name: str) -> Any:
    """Support Streamlit's dict-like event objects (attribute API may be absent on plain dicts)."""

    v = getattr(obj, name, None)
    if v is None and isinstance(obj, dict):
        return obj.get(name)
    return v


def _normalize_cell(entry: Any) -> tuple[int, str] | None:
    """Return ``(row_index, column_name)`` for one selection entry."""

    if entry is None:
        return None
    if isinstance(entry, dict):
        row = entry.get("row")
        if row is None:
            row = entry.get("row_index")
        col = entry.get("column")
        if col is None:
            col = entry.get("column_name")
        if row is None or col is None:
            return None
        try:
            return int(row), str(col)
        except (TypeError, ValueError):
            return None
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        try:
            return int(entry[0]), str(entry[1])
        except (TypeError, ValueError):
            return None
    return None


def _dataframe_selection_cells(event: Any) -> list[tuple[int, str]] | None:
    if event is None:
        return None
    sel = _get_member(event, "selection")
    if sel is None:
        return None
    cells = _get_member(sel, "cells")
    if not cells:
        return None
    out: list[tuple[int, str]] = []
    for entry in cells:
        pair = _normalize_cell(entry)
        if pair is not None:
            out.append(pair)
    return out or None


def navigate_if_player_name_cell_clicked(
    *,
    event: Any,
    df: pd.DataFrame,
    widget_key: str,
    universe: str,
    name_columns: dict[str, str],
) -> None:
    """If the selected dataframe cell is a player-name column, `switch_page` in the same tab.

    ``name_columns`` maps the visible name column -> ``player_id`` column in ``df``.
    """

    import streamlit as st

    cells = _dataframe_selection_cells(event)
    if not cells:
        # Hosted runtimes sometimes expose selection only via widget session_state.
        cells = _dataframe_selection_cells(st.session_state.get(widget_key))
    if not cells:
        return
    row_i, col_name = cells[0]
    id_col = name_columns.get(col_name)
    if id_col is None:
        return
    try:
        ri = int(row_i)
    except (TypeError, ValueError):
        return
    if ri < 0 or ri >= len(df):
        return
    row = df.iloc[ri]
    player_id = row[id_col]
    display_name = row[col_name]
    page = "pages/Amiga500_Player.py" if universe == "amiga500" else "pages/Online_Player.py"
    st.session_state.pop(widget_key, None)
    qp = {"player_id": str(player_id), "nm": str(display_name)}
    sig = inspect.signature(st.switch_page)
    if "query_params" in sig.parameters:
        st.switch_page(page, query_params=qp)
    else:
        st.query_params.clear()
        st.query_params["player_id"] = qp["player_id"]
        st.query_params["nm"] = qp["nm"]
        st.switch_page(page)
