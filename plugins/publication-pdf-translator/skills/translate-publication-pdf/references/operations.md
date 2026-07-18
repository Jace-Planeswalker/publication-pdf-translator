# Operator tool and CLI reference

只在 MCP 工具不可用、诊断字段不明确或需要人工恢复时读取本文件。

## 固定目录

- 隔离运行时：`PUBTRANS_RUNTIME_HOME`，默认
  `~/.local/share/publication-pdf-translator`
- 项目控制清单：`<project>/control/project.json`
- 绑定源文件：`<project>/inputs/source.pdf`
- 绑定产品配置：`<project>/inputs/config.json`
- 可选证据：`<project>/inputs/evidence.json`
- 后台任务：`<project>/control/jobs/`
- 运行时状态：`<project>/state/`
- 受控成品：`<project>/output/`

源出版物、项目目录、凭据和成品不得进入未忽略的 Git 工作树。初始化会绑定输入
摘要；原位改变任一输入会得到 `BLOCKED`，不会静默混用状态。

## MCP 工具

| 工具 | 作用 | 是否写入 |
| --- | --- | --- |
| `pubtrans_bootstrap` | 安装/核验固定 `release/v0.3.0` 隔离运行时 | 是，幂等 |
| `pubtrans_init` | 创建输入绑定项目 | 是，幂等 |
| `pubtrans_doctor` | 体检项目、凭据存在性、provider、字体和磁盘 | 否 |
| `pubtrans_start` | 启动或续跑一个后台任务 | 是，幂等防重 |
| `pubtrans_poll` | 读取任务与产品状态 | 否 |
| `pubtrans_status` | 读取状态并重新核验已发布成品 | 否 |
| `pubtrans_collect` | 收集核验 PDF、报告与摘要清单 | 是，幂等 |

所有路径字段必须是绝对路径。`config` 与 `model` 在初始化时二选一。密钥不是
任何工具的参数。

## 等价 CLI

插件正常时优先使用 MCP。若 MCP 不可用且尚无运行时，先在 Git 之外创建隔离
环境并安装固定发布引用（Windows 将 `bin/python` 换成
`Scripts/python.exe`）：

```bash
python3 -m venv /absolute/runtime/0.3.0
/absolute/runtime/0.3.0/bin/python -m pip install \
  'publication-pdf-translator[babeldoc] @ git+https://github.com/Jace-Planeswalker/publication-pdf-translator.git@release/v0.3.0'
```

随后用该解释器执行等价 CLI：

```bash
/absolute/runtime/0.3.0/bin/python -m pubtrans init /absolute/source.pdf \
  --project /absolute/project \
  --config /absolute/config.json \
  --evidence /absolute/evidence.json

/absolute/runtime/0.3.0/bin/python -m pubtrans doctor /absolute/project
/absolute/runtime/0.3.0/bin/python -m pubtrans run /absolute/project
/absolute/runtime/0.3.0/bin/python -m pubtrans status /absolute/project
/absolute/runtime/0.3.0/bin/python -m pubtrans collect /absolute/project \
  --destination /absolute/delivery
```

`run` 与 `resume` 都只采用控制清单绑定的输入；进程中断后执行任一命令都会续跑
耐久状态。旧的 `translate` 命令只为兼容保留，不是插件标准入口。

## 状态解释

| 状态 | 含义 | 下一动作 |
| --- | --- | --- |
| `UNINITIALIZED` | 没有控制清单 | `init` |
| `INITIALIZED` / `NEW` | 已绑定输入，尚未形成翻译进度 | `doctor` 后 `start` |
| `IN_PROGRESS` | 有可续跑状态 | `start` 或继续 `poll` |
| `VERIFYING` | 已有候选发布，成品门禁未结束 | 继续运行/轮询 |
| `RELEASED` | 活跃报告与输出 PDF 字节一致 | 再 `status` 后 `collect` |
| `BLOCKED` | 完整性、质量或运行条件不成立 | 修复具体检查，不得强行发布 |
| `FAILED` | 最近任务退出非零 | 查看结构化 stderr 与状态后续跑 |
| `ORPHANED` | 记录为运行但进程已不存在 | 确认后对同一项目重新 `start` |

任务 stdout/stderr 仅保存在 `control/jobs/`，工具返回路径而不把长日志或可能的
敏感内容复制进对话。交付清单记录每个文件的 SHA-256 和大小；目标目录中若已有
不同字节，收集会拒绝覆盖。
