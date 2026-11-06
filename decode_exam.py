#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极域电子教室 考试存档解码器
----------------------------
读取极域「考试结束」后生成的存档文件（OLE2 复合文档，内容 UTF-16LE 明文），
导出为 JSON / Markdown / HTML 三种格式。

依赖：同目录下的 cfb.py（纯标准库实现的 CFB 解析器，无需 olefile）

用法示例：
    python decode_exam.py "{525673A1-E980-43A4-B902-97A8E9CE568C}"
    python decode_exam.py ./存档目录 -o ./输出
    python decode_exam.py 存档文件 --no-html
"""
import argparse
import datetime
import html
import io
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfb import CFB

# ---------- 兼容 Windows GBK 控制台 ----------
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


# ============ 底层：遍历 OLE 目录树，取出所有流 ============
def collect_streams(c):
    out = []

    def walk(node_idx, path):
        stack = [node_idx]
        while stack:
            i = stack.pop()
            if i >= 0xFFFFFFFE or i >= len(c.entries):
                continue
            e = c.entries[i]
            if e['type'] == 0:
                continue
            curpath = path + [e['name']]
            if e['type'] == 2:
                if e['size'] < c.cutoff:
                    data = c.read_mini(e['start'], e['size'])
                else:
                    data = c.read_stream_big(e['start'], e['size'])
                out.append((curpath, e['size'], data))
            elif e['child'] < 0xFFFFFFFE:
                walk(e['child'], curpath)
            stack.append(e['left'])
            stack.append(e['right'])

    walk(c.root['child'], [])
    return out


def txt(d):
    """UTF-16LE 解码，去掉尾部填充的 \\x00"""
    b = d.rstrip(b'\x00')
    if len(b) % 2:
        b += b'\x00'
    try:
        return b.decode('utf-16-le')
    except Exception:
        return ''


def num(d):
    if d is None:
        return None
    if len(d) == 2:
        return struct.unpack('<H', d)[0]
    if len(d) == 4:
        return struct.unpack('<I', d)[0]
    return None


def sysdate(d):
    """16 字节 SYSTEMTIME：年 月 星期 日 时 分 秒 毫秒"""
    if not d or len(d) != 16:
        return ''
    y, mo, _dow, da, h, mi, s, ms = struct.unpack('<8H', d)
    if y == 0:
        return ''
    return '%04d-%02d-%02d %02d:%02d:%02d.%03d' % (y, mo, da, h, mi, s, ms)


def guid_str(g):
    if not g or len(g) != 16:
        return ''
    a, b, c = struct.unpack('<IHH', g[:8])
    return '{%08X-%04X-%04X-%s-%s}' % (a, b, c, g[8:10].hex().upper(), g[10:].hex().upper())


# ============ 解析成结构化数据 ============
def parse(path):
    c = CFB(path)
    streams = collect_streams(c)

    idx = {'/'.join(p): d for p, _sz, d in streams}

    def g(key, default=b''):
        return idx.get(key, default)

    meta = {
        'file': os.path.basename(path),
        'paper_name': txt(g('PaperInfo/Name')),
        'teacher': txt(g('PaperInfo/Teacher')),
        'total_score': num(g('PaperInfo/Score')),
        'time_limit_min': (num(g('PaperInfo/TimeInSec')) or 0) // 60,
        'remaining_min': (num(g('PaperInfo/RemainingInSec')) or 0) // 60,
        'status': num(g('PaperInfo/Status')),
        'student_count': num(g('PaperInfo/StudentCount')),
        'group_count': num(g('PaperInfo/GroupCount')),
        'time_last_modify': sysdate(g('PaperInfo/TimeLastMotify')),
        'time_begin': sysdate(g('PaperInfo/TimeTestBegin')),
        'time_end': sysdate(g('PaperInfo/TimeTestEnd')),
        'time_last_grade': sysdate(g('PaperInfo/TimeLastGrade')),
        'guid': guid_str(g('PaperInfo/Guid')),
    }

    # ---- 题目 ----
    questions = []
    for k in idx:
        m = re.match(r'^Groups/(Group_\d+)/Questions/(Question_\d+)/Name$', k)
        if not m:
            continue
        grp, qn = m.groups()
        base = 'Groups/%s/Questions/%s/' % (grp, qn)
        questions.append({
            'index': int(qn.split('_')[1]),
            'group': grp,
            'text': txt(g(base + 'Name')).replace('\r\n', '\n').strip('\n'),
            'type': num(g(base + 'Type')),
            'score': num(g(base + 'Score')),
            'choice_count': num(g(base + 'ChoiceCount')),
        })
    questions.sort(key=lambda x: x['index'])

    # ---- 学生 ----
    students = {}
    for k in idx:
        m = re.match(r'^Students/(Student_\d+)/(.+)$', k)
        if not m:
            continue
        sid, rest = m.groups()
        st = students.setdefault(sid, {'id': sid, 'answers': {}})

        if rest == 'Name':
            st['name'] = txt(g(k)).strip('\x00')
        elif rest == 'Mac':
            raw = g(k)
            st['mac'] = ':'.join('%02X' % x for x in raw) if raw else ''
        elif rest == 'State':
            st['state'] = num(g(k))
        elif rest == 'Count':
            st['answer_count'] = num(g(k))
        elif rest == 'ZoomScale':
            continue
        else:
            mm = re.match(r'Answer_(\d+)/(\w+)$', rest)
            if not mm:
                continue
            ai, field = int(mm.group(1)), mm.group(2)
            a = st['answers'].setdefault(ai, {})
            if field == 'AnswerData':
                a['text'] = txt(g(k)).replace('\r\n', '\n').rstrip('\n')
            elif field == 'AnswerScore':
                a['score'] = num(g(k))
            elif field == 'AnswerIsBlank':
                a['blank'] = num(g(k))
            elif field == 'AnswerGraded':
                a['graded'] = num(g(k))
            elif field == 'AnswerComment':
                cc = txt(g(k)).strip('\x00').strip()
                if cc:
                    a['comment'] = cc

    slist = sorted(students.values(), key=lambda s: int(s['id'].split('_')[1]))
    return {'meta': meta, 'questions': questions, 'students': slist}


# ============ 输出：Markdown ============
def to_markdown(d):
    meta, qs, sts = d['meta'], d['questions'], d['students']
    b = io.StringIO()
    b.write('# 极域电子教室 考试存档解码结果\n\n')
    b.write('原始文件：`%s`　试卷 GUID：`%s`\n\n' % (meta['file'], meta['guid']))
    b.write('> 该文件为 OLE2 复合文档（Compound File Binary），**未加密**，正文以 UTF-16LE 明文存储。\n\n')

    b.write('## 一、试卷信息\n\n| 字段 | 值 |\n|---|---|\n')
    for k, v in [('试卷名称', meta['paper_name']), ('教师', meta['teacher']),
                 ('总分', meta['total_score']), ('考试时长', '%d 分钟' % meta['time_limit_min']),
                 ('剩余时间', '%d 分钟' % meta['remaining_min']), ('状态码', meta['status']),
                 ('学生数', meta['student_count']), ('题组数', meta['group_count']),
                 ('开考时间', meta['time_begin'] or '未记录'), ('结束时间', meta['time_end']),
                 ('最后批改', meta['time_last_grade'] or '未批改')]:
        b.write('| %s | %s |\n' % (k, v))
    b.write('\n## 二、题目\n\n')
    for q in qs:
        b.write('**第 %d 题**（%d 分，类型 %d）\n\n> %s\n\n'
                % (q['index'] + 1, q['score'], q['type'], q['text'].replace('\n', '\n> ')))

    b.write('## 三、学生答卷（%d 人）\n\n' % len(sts))
    for st in sts:
        ans = st.get('answers', {})
        total = sum(len(ans.get(i, {}).get('text', '')) for i in range(len(qs)))
        b.write('---\n\n### %s　`%s`　MAC %s　（%d 字）\n\n'
                % (st.get('name', '?'), st['id'], st.get('mac', ''), total))
        for q in qs:
            a = ans.get(q['index'], {})
            t = a.get('text', '')
            if not t.strip():
                b.write('**第 %d 题**：未作答\n\n' % (q['index'] + 1))
                continue
            b.write('**第 %d 题**（%d 字）：\n\n```\n%s\n```\n\n'
                    % (q['index'] + 1, len(t.replace('\n', '')), t))
    return b.getvalue()


# ============ 输出：HTML ============
def to_html(d):
    meta, qs, sts = d['meta'], d['questions'], d['students']
    e = lambda x: html.escape(str(x if x is not None else ''))

    H = io.StringIO()
    H.write('''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>极域考试存档 · %s</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--line:#e3e6ea;--txt:#1a1d21;--dim:#6b7280;--acc:#2563eb;--acc2:#eff4ff;--warn:#b45309;--warnbg:#fef3c7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:24px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
.kv{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kv .k{font-size:12px;color:var(--dim)}
.kv .v{font-size:17px;font-weight:600;margin-top:2px}
.kv .v.sm{font-size:13px;font-weight:500;word-break:break-all}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:18px}
h2{font-size:15px;margin:0 0 12px;padding-left:9px;border-left:3px solid var(--acc);line-height:1.2}
.q{border:1px solid var(--line);border-radius:9px;padding:11px 14px;margin-bottom:9px;background:#fbfcfd}
.q b{color:var(--acc);font-size:13px}.q .t{margin-top:4px;white-space:pre-wrap}
.q .m{float:right;color:var(--dim);font-size:12px}
.stu{border:1px solid var(--line);border-radius:12px;margin-bottom:14px;overflow:hidden;background:var(--card)}
.hd{display:flex;align-items:center;gap:10px;padding:13px 16px;cursor:pointer;user-select:none;background:#fbfcfd}
.hd:hover{background:var(--acc2)}
.hd .nm{font-weight:600}.hd .meta{color:var(--dim);font-size:12px;font-family:ui-monospace,Consolas,monospace}
.hd .sum{margin-left:auto;font-size:12px;color:var(--dim)}
.bd{display:none;padding:0 16px 14px;border-top:1px solid var(--line)}
.stu.open .bd{display:block}
.ans{margin-top:14px}.ans .lb{font-size:13px;color:var(--dim);margin-bottom:5px}.ans .lb b{color:var(--txt)}
.ans .tx{white-space:pre-wrap;background:#fbfcfd;border:1px solid var(--line);border-left:3px solid #c7d2fe;border-radius:6px;padding:10px 13px;font-size:14px;line-height:1.75}
.empty{color:var(--warn);background:var(--warnbg);border-radius:6px;padding:6px 11px;font-size:13px;display:inline-block}
.bar{display:inline-block;height:6px;border-radius:3px;background:var(--acc);vertical-align:middle;opacity:.55}
.tips{font-size:13px;color:var(--dim);margin-bottom:10px}
</style></head><body><div class="wrap">''' % e(meta['paper_name']))

    H.write('<h1>极域电子教室 · %s</h1>' % e(meta['paper_name']))
    H.write('<div class="sub">源文件 <code>%s</code> · OLE2 复合文档 · UTF-16LE 明文（未加密）</div>' % e(meta['file']))

    H.write('<div class="grid">')
    items = [('教师', meta['teacher'], 0), ('总分', meta['total_score'], 0),
             ('时长', '%d 分钟' % meta['time_limit_min'], 0), ('剩余', '%d 分钟' % meta['remaining_min'], 0),
             ('学生数', meta['student_count'], 0), ('题目数', len(qs), 0),
             ('结束时间', meta['time_end'] or '未记录', 1), ('GUID', meta['guid'], 1)]
    for k, v, sm in items:
        H.write('<div class="kv"><div class="k">%s</div><div class="v%s">%s</div></div>'
                % (e(k), ' sm' if sm else '', e(v)))
    H.write('</div>')

    H.write('<div class="card"><h2>题目</h2>')
    for q in qs:
        H.write('<div class="q"><span class="m">%s 分 · 类型 %s</span><b>第 %d 题</b><div class="t">%s</div></div>'
                % (e(q['score']), e(q['type']), q['index'] + 1, e(q['text'])))
    H.write('</div>')

    rows = []
    for st in sts:
        lens = [len(st['answers'].get(i, {}).get('text', '')) for i in range(len(qs))]
        rows.append((st, sum(lens), sum(1 for x in lens if x == 0)))
    mx = max([r[1] for r in rows] + [1])

    H.write('<div class="card"><h2>作答字数总览</h2>'
            '<div class="tips">柱长 = 该生总作答字数；橙色姓名表示有整题空白。点击下方姓名展开全文。</div>')
    for st, tot, blank in rows:
        H.write('<div style="display:flex;align-items:center;gap:9px;font-size:13px;padding:3px 0">'
                '<span style="width:170px;color:%s">%s</span>'
                '<span class="bar" style="width:%dpx"></span>'
                '<span style="color:#6b7280">%d 字 · 空 %d 题</span></div>'
                % ('#b45309' if blank else 'var(--txt)', e(st.get('name', '')), int(tot / mx * 320), tot, blank))
    H.write('</div>')

    H.write('<h2 style="margin:26px 0 12px">学生答卷</h2>')
    for st, tot, blank in rows:
        ans = st['answers']
        H.write("<div class=\"stu\"><div class=\"hd\" onclick=\"this.parentNode.classList.toggle('open')\">"
                '<span class="nm">%s</span><span class="meta">%s · %s</span>'
                '<span class="sum">%d 字</span></div><div class="bd">'
                % (e(st.get('name', '')), e(st['id']), e(st.get('mac', '')), tot))
        for q in qs:
            t = ans.get(q['index'], {}).get('text', '').strip('\n')
            H.write('<div class="ans"><div class="lb"><b>第 %d 题</b> · %s</div>'
                    % (q['index'] + 1, e(q['text'].split('\n')[0].strip())))
            if t.strip():
                H.write('<div class="tx">%s</div>' % e(t))
            else:
                H.write('<span class="empty">未作答 / 空白</span>')
            H.write('</div>')
        H.write('</div></div>')

    H.write('</div></body></html>')
    return H.getvalue()


# ============ 主流程 ============
def looks_like_exam(path):
    try:
        with open(path, 'rb') as f:
            return f.read(8) == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    except Exception:
        return False


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', '_', s).strip() or 'exam'


def main():
    ap = argparse.ArgumentParser(
        description='解码极域电子教室考试存档（OLE2 复合文档）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n'
               '  python decode_exam.py "{525673A1-E980-43A4-B902-97A8E9CE568C}"\n'
               '  python decode_exam.py 存档目录 -o 输出目录\n'
               '  python decode_exam.py 存档 --no-html\n')
    ap.add_argument('path', help='存档文件，或存放多个存档的目录')
    ap.add_argument('-o', '--outdir', default='decoded', help='输出目录（默认 ./decoded）')
    ap.add_argument('--no-html', action='store_true', help='不生成 HTML')
    ap.add_argument('--no-md', action='store_true', help='不生成 Markdown')
    ap.add_argument('--no-json', action='store_true', help='不生成 JSON')
    ap.add_argument('-q', '--quiet', action='store_true', help='只在出错时输出')
    args = ap.parse_args()

    if not os.path.exists(args.path):
        sys.exit('找不到路径：%s' % args.path)

    if os.path.isdir(args.path):
        files = [os.path.join(args.path, f) for f in sorted(os.listdir(args.path))
                 if os.path.isfile(os.path.join(args.path, f)) and looks_like_exam(os.path.join(args.path, f))]
        if not files:
            sys.exit('目录中没有找到极域存档（OLE2 文件）')
    else:
        files = [args.path]

    os.makedirs(args.outdir, exist_ok=True)
    ok = 0
    for fp in files:
        try:
            data = parse(fp)
        except Exception as ex:
            print('[跳过] %s：%s' % (os.path.basename(fp), ex), file=sys.stderr)
            continue

        stem = safe_name(data['meta']['guid'] or os.path.basename(fp))
        base = os.path.join(args.outdir, stem)

        if not args.no_json:
            with open(base + '.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        if not args.no_md:
            with open(base + '.md', 'w', encoding='utf-8') as f:
                f.write(to_markdown(data))
        if not args.no_html:
            with open(base + '.html', 'w', encoding='utf-8') as f:
                f.write(to_html(data))

        ok += 1
        if not args.quiet:
            m = data['meta']
            n_ans = sum(1 for s in data['students'] for a in s['answers'].values() if a.get('text', '').strip())
            exts = [x for x, off in (('json', args.no_json), ('md', args.no_md), ('html', args.no_html)) if not off]
            print('✓ %-28s %s | %s | %s 人 / %d 题 | %d 处作答 | 结束 %s'
                  % (m['paper_name'] or '(未命名)', m['teacher'], os.path.basename(fp),
                     m['student_count'], len(data['questions']), n_ans, m['time_end'] or '-'))
            print('  → %s.{%s}' % (base, ','.join(exts)))

    if not args.quiet:
        print('\n完成：%d 个文件，输出目录 %s' % (ok, os.path.abspath(args.outdir)))


if __name__ == '__main__':
    main()
