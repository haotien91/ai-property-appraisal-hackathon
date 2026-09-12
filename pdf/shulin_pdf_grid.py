# -*- coding: utf-8 -*-
"""
shulin_pdf_grid.py — OFFICIAL-SIX-PAGE-PDF-E1 Task 3 support module.

Reconstructs the real cell grid (column/row boundary coordinates, in PDF
point space) of a shulin_table3/table51/table4_blank_v1.pdf template
page, by reading the ACTUAL vector rectangles Excel itself drew for
every bordered cell (via PyMuPDF's page.get_drawings()) -- never a
hand-guessed Excel-column-width-to-pixel formula (font-dependent and
easy to get subtly wrong). This is what lets scripts/build_shulin_pdf_
mappings.py turn a field's real XLSX cell/merged-range reference (e.g.
"G9:I9") into a concrete PDF rectangle: look up openpyxl's own 1-indexed
column/row numbers for that range (openpyxl.utils.range_boundaries),
then index into the boundary arrays this module extracts.

Excel's own PDF export draws each bordered cell's border as EITHER a
small filled/stroked rectangle ('re' path item -- common for "thin"-style
borders) OR a plain line segment ('l' path item -- common for "hair"-
style borders between densely-packed narrow columns, e.g. 表3's many
small cells) -- both are read here; relying on only one kind under-
detects real grid lines (caught while building shulin_table3_mapping.
json: 're'-only detection found just 19 of 表3's 22 column boundaries,
adding 'l' items recovered the rest). Most true grid lines appear MANY
times (once per adjacent cell) at near-identical (but not bit-identical,
due to floating-point rendering) coordinates -- `_cluster()` collapses
those into one boundary per tolerance window.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import fitz


def _cluster(values: List[float], tol: float = 1.5) -> List[float]:
    """Collapses near-duplicate coordinates (within `tol` points of each
    other) into one boundary, keeping their average. `values` need not
    be sorted."""
    if not values:
        return []
    ordered = sorted(values)
    clusters: List[List[float]] = [[ordered[0]]]
    for v in ordered[1:]:
        if v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def extract_grid_boundaries(page: "fitz.Page", tol: float = 1.5) -> Tuple[List[float], List[float]]:
    """Returns (x_boundaries, y_boundaries): sorted lists of column/row
    grid-line positions in PDF point space, derived from every 'rectangle'
    path Excel's PDF export drew on this page (cell borders). x_boundaries
    index 0 is the left edge of column A, index 1 is the boundary between
    column A and B, etc. -- i.e. column N (1-indexed, A=1) spans
    [x_boundaries[N-1], x_boundaries[N]]. Same convention for rows."""
    drawings = page.get_drawings()
    xs: List[float] = []
    ys: List[float] = []
    for d in drawings:
        for item in d.get("items") or []:
            kind = item[0]
            if kind == "re":
                rect = item[1]
                xs.append(rect.x0)
                xs.append(rect.x1)
                ys.append(rect.y0)
                ys.append(rect.y1)
            elif kind == "l":
                p0, p1 = item[1], item[2]
                xs.append(p0.x)
                xs.append(p1.x)
                ys.append(p0.y)
                ys.append(p1.y)
    return _cluster(xs, tol), _cluster(ys, tol)


def _best_leading_offset(detected: List[float], relative_sizes: List[float], deficit: int) -> int:
    """Least-squares search (used only when `force_leading` is not given):
    tries every way of splitting the boundary deficit between "missing at
    the very start" and "missing at the very end" (assuming, for THIS
    search only, that any missing boundaries are NOT scattered in the
    middle), and returns whichever leading count fits the detected
    spacing best. This is a reasonable default for sheets whose only gaps
    are at the ends; `_fill_gaps()` below independently corrects any
    remaining internal gaps regardless of what this picks, so a
    imperfect-but-close leading guess here is not fatal -- but 表3's row
    axis (rows 1-2 have NO cell borders at all, so several nearby leading
    values fit almost equally well by pure least-squares error) was found
    by ground-truth verification (page.search_for() against known row
    labels) to sometimes need the caller to override this via
    reconcile_boundaries()'s `force_leading` instead of trusting this
    search."""
    n = len(relative_sizes)
    best = None
    for leading in range(0, deficit + 1):
        trailing = deficit - leading
        window = relative_sizes[leading: n - trailing] if trailing else relative_sizes[leading:]
        if len(window) + 1 != len(detected) or not window:
            continue
        total = sum(window)
        span = detected[-1] - detected[0]
        cumulative = [0.0]
        for w in window:
            cumulative.append(cumulative[-1] + w)
        expected = [detected[0] + (c / total) * span for c in cumulative]
        error = sum((e - d) ** 2 for e, d in zip(expected, detected))
        if best is None or error < best[0]:
            best = (error, leading)
    return best[1] if best else 0


def reconcile_boundaries(detected: List[float], relative_sizes: List[float],
                          force_leading: "int | None" = None) -> List[float]:
    """Some sheets have a handful of column/row boundaries with NO
    rendered border anywhere on the page -- extract_grid_boundaries() then
    returns fewer than N+1 boundaries for N columns/rows, and naive
    positional indexing (rect_for_span()) would silently misalign every
    boundary. Two distinct patterns were found (both caught only by
    rendering REAL values and comparing their position against
    page.search_for()'d ground-truth label positions -- a plain grid-line
    overlay check draws a plausible-looking box regardless, since it never
    checks which SEMANTIC row/column a box lands on):

      (a) LEADING boundaries missing entirely (表3's title rows 1-2 have
          no cell borders anywhere, so detection only starts picking up
          real grid lines from row 3 onward) -- `force_leading` (verified
          independently, e.g. via page.search_for()) pins this exactly;
          otherwise `_best_leading_offset()` guesses it.
      (b) a handful of INTERNAL boundaries missing, scattered anywhere
          in the middle (表3's row axis has 3 separate such gaps, in
          addition to (a) above, once (a) is corrected for) -- handled by
          `_fill_gaps()`: it walks the detected sequence assigning each
          consecutive pair to however many actual rows/columns its gap
          size best matches (using a scale factor estimated from the
          median of "normal" 1-row/1-column gaps), inserting proportional
          interpolated boundaries for any gap spanning more than one.

    `relative_sizes` is the N columns'/rows' own declared openpyxl
    widths/heights (proportions only matter, not absolute units)."""
    n = len(relative_sizes)
    need = n + 1
    if len(detected) >= need:
        return detected[:need]
    deficit = need - len(detected)
    leading = force_leading if force_leading is not None else _best_leading_offset(detected, relative_sizes, deficit)
    return _fill_gaps(detected, relative_sizes, leading)


def _fill_gaps(detected: List[float], relative_sizes: List[float], leading: int) -> List[float]:
    n = len(relative_sizes)

    # Robust scale estimate (points per relative-size-unit): the median
    # ratio of each consecutive detected gap to the ONE row/column it
    # would span if no boundaries were missing between that specific
    # pair -- multi-row gaps (ratio far from the pack) naturally sort
    # away from the median and don't skew it.
    ratios = []
    idx = leading
    for i in range(len(detected) - 1):
        if idx >= n:
            break
        rs = relative_sizes[idx]
        if rs > 0:
            ratios.append((detected[i + 1] - detected[i]) / rs)
        idx += 1
    ratios.sort()
    scale = ratios[len(ratios) // 2] if ratios else 1.0

    result: List[Optional[float]] = [None] * (n + 1)
    result[leading] = detected[0]
    cur_pos = leading
    for i in range(len(detected) - 1):
        diff = detected[i + 1] - detected[i]
        best_k, best_err, cum = 1, None, 0.0
        # Every remaining detected point (after this one) needs at least
        # 1 position of its own -- capping max_k this way guarantees
        # cur_pos can never overshoot n before all of `detected` is placed,
        # even if the scale estimate makes a later gap look deceptively
        # large/small.
        remaining_detected_after = (len(detected) - 1) - (i + 1)
        max_k = max(1, (n - cur_pos) - remaining_detected_after)
        for k in range(1, max_k + 1):
            cum += relative_sizes[cur_pos + k - 1]
            err = abs(cum * scale - diff)
            if best_err is None or err < best_err:
                best_err, best_k = err, k
            elif err > best_err * 3:
                break  # error grows monotonically past the best k -- no need to keep scanning
        sub_total = sum(relative_sizes[cur_pos: cur_pos + best_k])
        sub_cum = 0.0
        for j in range(1, best_k):
            sub_cum += relative_sizes[cur_pos + j - 1]
            frac = (sub_cum / sub_total) if sub_total else (j / best_k)
            result[cur_pos + j] = detected[i] + frac * diff
        cur_pos += best_k
        result[cur_pos] = detected[i + 1]

    # Trailing rows/columns past the last detected boundary.
    cum = 0.0
    for j in range(1, (n - cur_pos) + 1):
        cum += relative_sizes[cur_pos + j - 1]
        result[cur_pos + j] = detected[-1] + cum * scale

    # Leading rows/columns before the first detected boundary.
    cum = 0.0
    for j in range(1, leading + 1):
        cum += relative_sizes[leading - j]
        result[leading - j] = detected[0] - cum * scale

    assert all(v is not None for v in result), "reconcile_boundaries: internal gap-fill left a hole"
    return result  # type: ignore[return-value]


def rect_for_span(x_boundaries: List[float], y_boundaries: List[float],
                   min_col: int, min_row: int, max_col: int, max_row: int) -> List[float]:
    """min_col/min_row/max_col/max_row are 1-indexed openpyxl column/row
    numbers (e.g. from openpyxl.utils.range_boundaries("G9:I9") ->
    (7, 9, 9, 9)). Returns [x0, y0, x1, y1] in PDF point space by
    indexing directly into the boundary arrays -- column N's left edge
    is x_boundaries[N-1], its right edge is x_boundaries[N]."""
    x0 = x_boundaries[min_col - 1]
    x1 = x_boundaries[max_col]
    y0 = y_boundaries[min_row - 1]
    y1 = y_boundaries[max_row]
    return [x0, y0, x1, y1]
