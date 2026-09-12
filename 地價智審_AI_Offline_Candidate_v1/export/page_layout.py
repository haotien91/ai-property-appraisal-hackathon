"""Page identity shared by rendering and artifact delivery; no valuation logic."""

def page_layout(surveys, table51, table4):
    base = table51.base_segment_code
    comparisons = sorted(table51.comparisons, key=lambda item: item.comparison_index)
    codes = [item.comparable_segment_code for item in comparisons]
    indices = [item.comparison_index for item in comparisons]
    other = sorted(table4.comparisons, key=lambda item: item.comparison_index)
    if (table4.base_segment_code != base or len(codes) != len(set(codes))
            or len(indices) != len(set(indices)) or base in codes
            or [(x.comparison_index, x.comparable_segment_code) for x in other]
               != list(zip(indices, codes)) or set(surveys) != set(codes + [base])):
        raise ValueError('Survey and comparison segment identities must match')
    order = codes + [base]
    pages = [{'kind': 'survey', 'segment_code': code, 'page_start': i + 1, 'page_end': i + 1}
             for i, code in enumerate(order)]
    batches = max(1, (len(codes) + 2) // 3)
    for kind in ('regional_factors', 'comparison'):
        start = len(pages) + 1 if kind == 'regional_factors' else len(order) + batches + 1
        pages.append({'kind': kind, 'page_start': start, 'page_end': start + batches - 1})
    return order, batches, pages


def comparison_page(analysis, batch):
    """Only reassign visual column slots on a copy, preserving stored identities."""
    items = sorted(analysis.comparisons, key=lambda item: item.comparison_index)[batch * 3:(batch + 1) * 3]
    return analysis.model_copy(update={'comparisons': [item.model_copy(update={'comparison_index': i + 1})
                                                      for i, item in enumerate(items)]})
