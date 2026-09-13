import { getDocument, GlobalWorkerOptions } from '../lib/pdfjs/pdf.mjs?v=compat-21';

GlobalWorkerOptions.workerSrc = new URL('../lib/pdfjs/pdf.worker.mjs?v=compat-21', import.meta.url).href;

async function bounded(promise, label) {
    let timer;
    try {
        return await Promise.race([promise, new Promise((_, reject) => {
            timer = setTimeout(() => reject(new Error(label + '逾時，請重新整理後再試')), 20000);
        })]);
    } finally { clearTimeout(timer); }
}

export function parseSurveyLines(lines) {
    const compact = lines.map(line => line.replace(/\s+/g, ''));
    if (!compact.some(line => /地價區段勘查表/.test(line))) return null;
    const header = compact.slice(0, 14).join('\n');
    const segment = header.match(/區段編號[:：]?([A-Za-z0-9]+[-－][A-Za-z0-9-]+)/);
    const district = header.match(/(?:新北市|臺北市|台北市)([^()（）\n]{1,4}區)/);
    const caseNo = header.match(/(?:案號|案件編號)[:：]?([A-Za-z0-9][A-Za-z0-9-]+)/);
    const useLine = compact.find(line => /使用分區|使用地類別/.test(line)) || '';
    const landUse = useLine.match(/(?:使用分區[（(]使用地類別[）)]|使用分區|使用地類別)[:：]?((?:第[一二三四五六七八九十0-9]+種)?[^名稱○●\n]{1,15}?(?:住宅區|商業區|工業區|農業區|保護區|用地))/);
    const scopeIndex = compact.findIndex(line => line.includes('區段範圍'));
    let scope = '';
    if (scopeIndex >= 0) {
        const scopeLines = compact.slice(scopeIndex, scopeIndex + 3).join('');
        const start = scopeLines.search(/(?:沿[^：]{0,15}街|北側|東側|南側|西側)/);
        if (start >= 0) {
            scope = scopeLines.slice(start).split(/(?:都市計畫|名稱[:：]|使用分區|留地區段號)/)[0]
                .replace(/[（(]?公共設施.*$/, '').replace(/[）)]?\d*$/, '');
            const end = scope.match(/^.*?(?:區段[。.]|住宅區|商業區|工業區)/);
            if (end) scope = end[0];
        }
    }
    return { case_no: caseNo?.[1] || '', segment_code: segment?.[1].replace(/－/g, '-') || '',
        district: district?.[1] || '', land_use_type: landUse?.[1] || '', segment_scope: scope };
}

// getReader works in browsers that do not implement ReadableStream async iteration.
export async function readTextItems(stream) {
    const reader = stream.getReader();
    const items = [];
    try {
        while (true) {
            const { value, done } = await bounded(reader.read(), 'PDF 文字讀取');
            if (done) return { items };
            items.push(...value.items);
        }
    } finally {
        reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}

export function readScopeCell(items) {
    const label = items.find(item => item.str?.replace(/\s+/g, '') === '區段範圍');
    if (!label) return '';
    const labelY = label.transform[5];
    const starts = items.filter(item => /^(沿|北側|南側|東側|西側)/.test(item.str?.trim() || '')
        && item.transform[4] > label.transform[4] + label.width
        && Math.abs(item.transform[5] - labelY) < 12);
    if (starts.length !== 1) return '';
    const start = starts[0];
    const nextRow = items.filter(item => /都市計畫/.test(item.str || '') && item.transform[5] < labelY)
        .sort((a, b) => b.transform[5] - a.transform[5])[0];
    const bottom = nextRow ? nextRow.transform[5] + 2 : labelY - 12;
    const cell = items.filter(item => item.str?.trim() && item.transform[4] >= start.transform[4] - 6
        && item.transform[5] <= start.transform[5] + 2 && item.transform[5] > bottom);
    const rows = [];
    for (const item of cell.sort((a, b) => b.transform[5] - a.transform[5])) {
        let row = rows.find(row => Math.abs(row.y - item.transform[5]) < 2);
        if (!row) { row = { y: item.transform[5], items: [] }; rows.push(row); }
        row.items.push(item);
    }
    return rows.map(row => row.items.sort((a, b) => a.transform[4] - b.transform[4]).map(item => item.str).join('')).join('').replace(/\s+/g, '');
}

export async function extractFiles(files, onProgress = () => {}) {
    const records = [];
    const warnings = [];
    for (const file of files) {
        let task;
        const fileRecords = [];
        const caseReferences = [];
        try {
            task = getDocument({ data: new Uint8Array(await file.arrayBuffer()), isEvalSupported: false });
            const pdf = await bounded(task.promise, 'PDF 載入');
            let found = false;
            for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber++) {
                onProgress(file.name + '：正在讀取第 ' + pageNumber + ' / ' + pdf.numPages + ' 頁');
                const page = await bounded(pdf.getPage(pageNumber), 'PDF 頁面載入');
                const content = await readTextItems(page.streamTextContent());
                const rows = [];
                for (const item of content.items.filter(item => item.str?.trim()).sort((a, b) => b.transform[5] - a.transform[5])) {
                    let row = rows.find(row => Math.abs(row.y - item.transform[5]) < 2.5);
                    if (!row) { row = { y: item.transform[5], items: [] }; rows.push(row); }
                    row.items.push(item);
                }
                const lines = rows.sort((a, b) => b.y - a.y).map(row => row.items.sort((a, b) => a.transform[4] - b.transform[4]).map(item => item.str).join(' '));
                const header = lines.slice(0, 14).map(line => line.replace(/\s+/g, '')).join('\n');
                if (/表5[-－–]1/.test(header) && /影響地價區域因素分析明細表/.test(header)) {
                    const caseNumber = header.match(/案號[:：]([A-Za-z0-9]+(?:[-－][A-Za-z0-9]+)+)/);
                    const segmentRow = header.match(/地價區段號([^\n]*)(?:\n([^\n]*))?/);
                    if (caseNumber && segmentRow) caseReferences.push({
                        caseNo: caseNumber[1].replace(/－/g, '-'),
                        segments: (segmentRow[0].match(/[A-Za-z]\d+[-－]\d+/g) || []).map(code => code.replace(/－/g, '-'))
                    });
                }
                const record = parseSurveyLines(lines);
                if (record) record.segment_scope = readScopeCell(content.items) || record.segment_scope;
                if (record) { fileRecords.push({ ...record, source: file.name + ' · 第 ' + pageNumber + ' 頁' }); found = true; }
                page.cleanup();
            }
            if (!found) warnings.push(file.name + '：找不到可讀取的地價區段勘查表，掃描文件需 OCR 或手動填寫');
        } catch (error) {
            warnings.push(file.name + '：' + (error.message || '無法讀取，請確認 PDF 未損毀或加密'));
        } finally { if (task) task.destroy().catch(() => {}); }
        for (const record of fileRecords) {
            const matches = [...new Set(caseReferences.filter(ref => ref.segments.includes(record.segment_code)).map(ref => ref.caseNo))];
            if (record.case_no) matches.push(record.case_no);
            const candidates = [...new Set(matches)];
            if (candidates.length === 1) record.case_no = candidates[0];
            if (candidates.length > 1) {
                record.case_no = '';
                warnings.push(record.source + '：案號有衝突，請人工確認');
            }
            records.push(record);
        }
    }
    const data = {};
    const labels = { case_no: '案號', segment_code: '區段編號', district: '行政區', land_use_type: '用地類別', segment_scope: '區段範圍' };
    for (const key of Object.keys(labels)) {
        const values = [...new Set(records.map(record => record[key]).filter(Boolean))];
        data[key] = values.length === 1 ? values[0] : '';
        if (values.length > 1) warnings.push(labels[key] + '在多份書表中不同，請人工確認');
    }
    return { data, warnings, records };
}
