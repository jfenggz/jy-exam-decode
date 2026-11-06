# 极域电子教室 考试存档解码器

把极域电子教室（TopDomain / Mythware）在「考试结束」后自动生成的存档文件，还原成**可读的文本**。

极域会把整场考试的试卷、题目、学生信息、每人每题的作答原文，全部塞进一个以试卷 GUID 命名的文件里，例如：

```
{525673A1-E980-43A4-B902-97A8E9CE568C}
```

打开是乱码，看着像加密文件。**其实没有加密**——它是个 OLE2 复合文档，正文用 UTF-16LE 明文存储，只是套了一层二进制容器。本工具把那层壳拆掉。

---

## 目录

- [快速开始](#快速开始)
- [依赖](#依赖)
- [命令行用法](#命令行用法)
- [输出的三种格式](#输出的三种格式)
- [它到底做了什么](#它到底做了什么)
- [存档内部结构](#存档内部结构)
- [字段对照表](#字段对照表)
- [常见问题](#常见问题)
- [已知限制](#已知限制)

---

## 快速开始

```bash
python decode_exam.py "{525673A1-E980-43A4-B902-97A8E9CE568C}"
```

终端回显：

```
✓ exam-3-1   admin | {525673A1-...} | 25 人 / 5 题 | 118 处作答 | 结束 2026-11-06 10:23:53.243
  → decoded\{525673A1-E980-43A4-B902-97A8E9CE568C}.{json,md,html}
```

`decoded/` 目录下随即出现三个文件，其中 `.html` 双击即可在浏览器里翻阅。

> **路径一定要加引号**：文件名以 `{` 开头，不加引号会被 shell 当成花括号展开处理。

---

## 依赖

| 项 | 要求 |
|---|---|
| Python | 3.7 或更高（用到 `sys.stdout.reconfigure`） |
| 第三方库 | **无**。全部使用标准库 |
| 配套文件 | `cfb.py` 必须与 `decode_exam.py` 放在同一目录 |

`cfb.py` 是一个纯标准库实现的 CFB（Compound File Binary）解析器，约 100 行，负责 FAT / DIFAT / MiniFAT / 目录红黑树的遍历。之所以自己写、不用 `olefile`，是因为目标环境常常没有网络。如果你机器上已经装了 `olefile`，也可以不管它，脚本不会用到。

---

## 命令行用法

```
python decode_exam.py [-h] [-o OUTDIR] [--no-html] [--no-md] [--no-json] [-q] path
```

| 参数 | 说明 |
|---|---|
| `path` | 必填。存档文件路径，**或者**存放多个存档的目录 |
| `-o, --outdir` | 输出目录，默认 `./decoded` |
| `--no-html` | 不生成 HTML |
| `--no-md` | 不生成 Markdown |
| `--no-json` | 不生成 JSON |
| `-q, --quiet` | 只在出错时输出，适合放进批处理 |
| `-h, --help` | 查看帮助 |

### 单文件

```bash
python decode_exam.py "{525673A1-E980-43A4-B902-97A8E9CE568C}"
python decode_exam.py "{525673A1-E980-43A4-B902-97A8E9CE568C}" -o 输出
python decode_exam.py "{525673A1-E980-43A4-B902-97A8E9CE568C}" --no-html
```

### 批量

```bash
python decode_exam.py ./全部存档 -o ./输出
```

目录模式下脚本会先读每个文件的头 8 字节，只处理以 `D0 CF 11 E0 A1 B1 1A E1` 开头的 OLE2 文件，其余（`.py`、`.md`、`.json`、图片等）自动跳过。输出按**试卷 GUID** 命名，所以：

- 批量处理互不覆盖；
- 存档被改名成 `周一考试.dat` 也不影响输出名；
- 同一份存档重复解码会覆盖上一次结果，不会产生副本。

### 在 Python 里调用

```python
import decode_exam

data = decode_exam.parse(r"D:\存档\{525673A1-E980-43A4-B902-97A8E9CE568C}")
print(data['meta']['paper_name'])              # exam-3-1
print(data['questions'][0]['text'])            # 第一题的题干
print(data['students'][0]['answers'][0]['text'])  # 第一个学生第一题的答案

html = decode_exam.to_html(data)               # 也可以只取某种格式
```

---

## 输出的三种格式

### `{GUID}.json` — 结构化数据

给程序用的。顶层三个键：

```json
{
  "meta":      { "paper_name": "...", "teacher": "...", "students": [...] },
  "questions": [ { "index": 0, "text": "...", "score": 10, "type": 3 } ],
  "students":  [ { "id": "Student_0", "name": "王梦迪-网络工程",
                   "mac": "74:56:3C:40:B4:2B",
                   "answers": { "0": { "text": "...", "score": 0, "graded": 0 } } } ]
}
```

注意 `answers` 的键是**题目序号**（从 0 开始），不是「第几题」。JSON 里键会被序列化成字符串。

### `{GUID}.md` — 纯文本

给人和文档用的。开头是试卷信息表格，接着是题目列表，最后按学生分节，每道题的答案包在代码块里——这样答案中的换行、缩进、`<`、`*` 等符号不会被 Markdown 吃掉。

直接复制粘贴去交报告、发微信都不会串版。

### `{GUID}.html` — 可交互查看页

给浏览用的。单文件、无外部依赖、不联网：

- 顶部是试卷信息卡片和题目列表；
- 中间是「作答字数总览」条形图，一眼看出谁没写、谁写得多，有整题空白的姓名标成橙色；
- 下面每个学生一张可折叠卡片，点姓名展开五道题的全文。

配色跟随浅色主题，打印也正常。

---

## 它到底做了什么

整个流程四步，没有任何解密运算：

1. **识别容器** — 文件头 `D0CF11E0A1B11AE1` 即 OLE2 复合文档签名，和 `.doc`、`.xls` 是同一套容器格式。
2. **重建目录树** — 按 FAT 链读扇区，遍历目录项的红黑树，得到所有流（stream）的完整路径。小于 4096 字节的流存在 MiniFAT 里，单独处理。
3. **按路径取值** — 用 `PaperInfo/Name`、`Students/Student_7/Answer_0/AnswerData` 这样的路径直接取流，UTF-16LE 解码并去掉尾部 `\x00` 填充。
4. **拼装输出** — 组装成结构化数据，再渲染成三种格式。

时间字段是 16 字节的 Windows `SYSTEMTIME`（年/月/星期/日/时/分/秒/毫秒），不是常见的 FILETIME，需要单独解析。

---

## 存档内部结构

```
Root Entry
├── PaperInfo/                  试卷元数据
│   ├── Name                    试卷名
│   ├── Teacher                 教师账号
│   ├── Score                   总分
│   ├── TimeInSec               总时长（秒）
│   ├── RemainingInSec          剩余时长（秒）
│   ├── Status                  状态码
│   ├── StudentCount            学生数
│   ├── GroupCount              题组数
│   ├── Guid                    试卷 GUID（16 字节）
│   └── TimeLastMotify / TimeTestBegin / TimeTestEnd / TimeLastGrade
├── Groups/
│   └── Group_0/
│       ├── Name                题组说明
│       ├── QuestionCount       题目数
│       └── Questions/
│           └── Question_0..N/
│               ├── Name        题干
│               ├── Type        题型
│               ├── Score       分值
│               ├── ChoiceCount 选项数（主观题为 0）
│               └── Answer      参考答案
└── Students/
    └── Student_0..N/
        ├── Name                姓名（130 字节定长）
        ├── Mac                 6 字节裸 MAC
        ├── State               状态
        ├── Count               作答数
        └── Answer_0..N/
            ├── AnswerData      作答原文（2048 字节定长，UTF-16LE）
            ├── AnswerScore     得分
            ├── AnswerComment   评语
            ├── AnswerIsBlank   是否空白
            └── AnswerGraded    是否已批改
```

`AnswerData` 固定 2048 字节，即**单题最多 1024 个字符**，超出部分会被极域截断。`Name` 定长 130 字节、`AnswerComment` 定长 512 字节，读出来都要 `rstrip` 掉 `\x00`。

---

## 字段对照表

| 输出字段 | 原始流路径 | 类型 | 说明 |
|---|---|---|---|
| `paper_name` | `PaperInfo/Name` | UTF-16LE | 试卷名 |
| `teacher` | `PaperInfo/Teacher` | UTF-16LE | 教师账号 |
| `total_score` | `PaperInfo/Score` | uint16 | 总分 |
| `time_limit_min` | `PaperInfo/TimeInSec` | uint32 | 秒 → 分钟 |
| `remaining_min` | `PaperInfo/RemainingInSec` | uint32 | 秒 → 分钟 |
| `status` | `PaperInfo/Status` | uint16 | 状态码，3 通常表示已结束 |
| `student_count` | `PaperInfo/StudentCount` | uint16 | 名单人数 |
| `time_end` | `PaperInfo/TimeTestEnd` | SYSTEMTIME | 交卷/结束时间 |
| `guid` | `PaperInfo/Guid` | 16B | 小端序还原成标准 GUID |
| `questions[].text` | `.../Question_N/Name` | UTF-16LE | 题干 |
| `questions[].type` | `.../Question_N/Type` | uint16 | 3 = 主观题 |
| `students[].mac` | `Students/Student_N/Mac` | 6B | 原始 MAC，转成 `AA:BB:CC:DD:EE:FF` |
| `answers[].text` | `.../Answer_N/AnswerData` | UTF-16LE | 作答原文 |

---

## 常见问题

**Q：真的没有加密吗？我看到中文全是乱码。**

没有。你看到的乱码是 UTF-16LE 字节流被当成 GBK/ASCII 显示的结果——每个汉字占 2 字节，高位字节常常是 0，所以看起来像夹着 `\x00` 的乱码。用 `xxd` 看一眼就能发现规律：每两个字节一个字符。

**Q：为什么 `AnswerScore` 全是 0？**

两种情况：一是真的还没批改（`AnswerGraded` 也是 0）；二是极域只在教师端手动打分后才写入分数。本工具只读取文件里的值，不做任何计算。

**Q：`TimeTestBegin` 显示「未记录」？**

学生端没有上传开考时间时该字段全 0。这是极域自身的行为，不是解码失败。

**Q：`status` 的 3 是什么意思？**

极域没有公开文档，根据样本推断 3 表示「考试已结束、可导出」。脚本只原样输出数字，不做语义映射。

**Q：能不能改分数？**（在某些场景下有人会这么问）

本工具**只读**。解析器打开文件用 `rb` 模式，不写回。要改分数属于篡改考试记录，不在此工具的范围内。

**Q：换个版本的极域还能用吗？**

结构大体稳定，但字段名可能增减。判断方法：跑一次 `python decode_exam.py 存档 --no-html`，看终端那行统计——人数、题数、作答数三个数字和实际对上，就基本没问题；对不上说明结构变了，需要在 `parse()` 里补字段。

**Q：能处理 `.doc`、`.xls` 吗？**

不能，虽然容器格式相同。那些文件的流结构完全不同，本脚本只认极域那一套路径。

---

## 已知限制

- **只读，不写回**。无法修改分数、评语、学生名单。
- **单题上限 1024 字符**。这是存档格式的物理限制，超长部分已被极域丢弃，解不出来。
- **不还原图片和附件**。若考试含图片题，`AnswerData` 里可能只是占位符或空，图片本体不在该存档内。
- **`AnswerData` 按纯文本处理**。极域富文本编辑器产生的格式信息不会保留，只取可见字符。
- **依赖文件名同目录的 `cfb.py`**。把脚本拷到别处时要一起带走。
- **未做多题组验证**。样本只有 `GroupCount = 1`，多题组（`Group_1`、`Group_2`）的代码路径写得通但未实测。

---

## 文件清单

```
极域电子教室/
├── cfb.py           CFB 容器解析器（约 100 行，纯标准库）
├── decode_exam.py   解码主程序 + 三种格式渲染
├── README.md        本文档
├── {GUID}           原始存档（极域生成，勿改）
└── decoded/         输出目录
    ├── {GUID}.json
    ├── {GUID}.md
    └── {GUID}.html
```
