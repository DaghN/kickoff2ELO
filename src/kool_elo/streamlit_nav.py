"""Streamlit in-app navigation for multipage player profiles."""

from __future__ import annotations

from typing import Any

import pandas as pd


def _dataframe_selection_cells(event: Any) -> list[tuple[int, str]] | None:
    if event is None:
        return None
    sel = getattr(event, "selection", None)
    if sel is None:
        return None
    cells = getattr(sel, "cells", None)
    if cells is None and isinstance(sel, dict):
        cells = sel.get("cells")
    if not cells:
        return None
    return cells


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
    st.switch_page(
        page,
        query_params={"player_id": str(player_id), "nm": str(display_name)},
    )
