---
name: salesbrain
description: "SalesBrain 是给销售团队使用的 Openclaw 陪跑导师 Skill。首次使用时会先通过 skill 包内的安装脚本从 GitHub 拉取 SalesBrain 代码，完成 EBOSS 配置、首次全量同步、Openclaw cron 迁移和首次整体分析；之后由 SalesBrain 的确定性调度器唤醒 Openclaw，并支持每次分析后用 next_wake_plans 动态安排下一次跟进、日报审阅、周总结、方法沉淀、团队经验同步和版本更新确认。"
allowed-tools: Bash
metadata:
  clawdbot:
    emoji: "🧠"
    requires:
      bins: ["salesbrain", "git", "python"]
      os: ["linux", "darwin", "win32"]
    configPaths:
      - "~/.openclaw/"
    files:
      - "config.toml"
      - "salesbrain.sqlite"
      - "README.md"
      - "manifest.json"
      - "_meta.json"
      - "scripts/install.py"
---

# SalesBrain

SalesBrain 是 Openclaw 的销售陪跑调度层和状态层。
Openclaw 负责分析和判断，SalesBrain 负责调度、落库、同步、提醒、团队发现和版本管理。

## 什么时候使用

当 SalesBrain 唤醒你时，使用这些 wake kind：

- `initial_analysis`
- `first_cron_migration`
- `morning_analysis`
- `work_followup`
- `daily_report_review`
- `weekly_summary`
- `due_task_scan`
- `workflow_reflection`
- `workflow_inbox_review`
- `salesbrain_update_check`
- `manual`

你需要做的事情通常是：

- 优先读取 `semantic_contract`、`business_facts` 和 `work_state`
- 只把 EBOSS raw records 当成补充上下文，不要从 raw JSON 里猜金额
- 生成或更新任务
- 用导师助理口吻发送 `user_message`
- 产出 review suggestion
- 沉淀 workflow items
- 决定哪些 Openclaw 业务 cron 应迁移到 SalesBrain
- 在 team inbox 里确认团队方法是否可入库
- 在有更新时提醒用户选择公司 SkillHub 或 GitHub

## 首次安装

这个 skill 包是轻量包，不携带 SalesBrain 源码。
首次安装时，要通过 skill 包里的安装脚本从 GitHub 拉取或更新 SalesBrain 代码。

GitHub 安装脚本是：

```bash
python <skill_root>/scripts/install.py
```

安装脚本会：

1. 从 `https://github.com/FrankeyQu/SalesBrain.git` 拉取 `main` 分支到 `~/.openclaw/SalesBrain`
2. 如果本地已经是 Git 仓库，则执行 `git fetch`、`git checkout main`、`git pull --ff-only`
3. 如果旧目录不是 Git 仓库，则先备份旧目录，再重新 clone
4. 安装本地 Python 包
5. 保留本地 GitHub 来源和 revision 信息

安装脚本执行成功后，再进入初始化流程。

如果 Openclaw 需要先读取安装步骤，调用：

```bash
python <skill_root>/scripts/install.py --steps
```

如果 Openclaw 只需要检查本地是否已安装，调用：

```bash
python <skill_root>/scripts/install.py --check
```

首次初始化不要直接让 `salesbrain init` 静默跑完整 first_run。必须使用分步命令：

```bash
python3 -m salesbrain init --no-first-run --sales-name "<销售姓名>" --eboss-api-key "<EBOSS_API_KEY>" --openclaw-wake-command "<Openclaw 唤醒命令>"
python3 -m salesbrain bridge doctor
python3 -m salesbrain bridge test
python3 -m salesbrain first-run sync
python3 -m salesbrain first-run cron-inspect
python3 -m salesbrain first-run cron-migrate --mode all
python3 -m salesbrain first-run analyze
python3 -m salesbrain service install --mode auto --start
```

`bridge doctor` 和 `bridge test` 是硬性步骤。SalesBrain 必须能通过真实命令唤醒 Openclaw，且 `bridge test` 必须让 Openclaw 给当前用户发送一条可见测试消息并返回 `message_sent: true`。如果 `[openclaw].wake_command` 为空，SalesBrain 会把后续定时唤醒记录为失败，不再退回 outbox 文件并假装成功。

第 4 步必须先展示 `cron-inspect` 结果。如果发现遗留业务定时任务，要问用户：

```text
发现 X 个 Openclaw 遗留业务定时任务。请选择：[迁移] [不迁移] [选择性迁移]
```

用户选择后再执行：

- 迁移：`python3 -m salesbrain first-run cron-migrate --mode all`
- 不迁移：`python3 -m salesbrain first-run cron-migrate --mode none`
- 选择性迁移：`python3 -m salesbrain first-run cron-migrate --mode selected --job-id <job_id>`

第 5 步必须单独执行并展示进度：

```text
⏳ 唤醒 Openclaw 做首次分析...
⏳ 正在分析你的商机、客户、线索...
```

命令完成后，把返回 JSON 中的 `analysis_report.formatted_report` 发送给用户，作为首次分析报告。

## 首次初始化流程

首次初始化前，必须先告诉用户接下来会做什么，并且分阶段展示进度。

建议的说明文案：

```text
SalesBrain 将开始首次初始化。接下来会完成：
1. 检查本地 SalesBrain 程序和配置；
2. 读取 EBOSS API Key；
3. 检查 Openclaw 唤醒桥接，并发送一条测试消息；
4. 首次全量同步 EBOSS 本年度项目、商机、日报和关联数据；
5. 检查 Openclaw 现有业务定时任务，并迁移到 SalesBrain；
6. 唤醒 Openclaw 做首次整体工作分析；
7. 安装 SalesBrain Linux cron watchdog，并启动长期调度和团队同步。
```

建议的进度条样式：

```text
[1/7] 正在检查本地 SalesBrain 程序...
[2/7] 正在准备 EBOSS 配置...
[3/7] 正在验证 Openclaw 唤醒和消息发送...
[4/7] 正在同步 EBOSS 本年度全量数据...
[5/7] 正在检查 Openclaw cron 并询问是否迁移...
[6/7] 正在进行首次整体分析并生成报告...
[7/7] 正在安装 SalesBrain 保活 watchdog 并启动长期调度...
```

首次运行时要设置并检查这些状态：

- `salesbrain_first_run_done`
- `salesbrain_first_eboss_full_sync_done`
- `salesbrain_first_cron_migration_done`
- `salesbrain_first_initial_analysis_done`

如果首次流程没有完成，`python3 -m salesbrain daemon` 或 `python3 -m salesbrain service ensure-running` 也要补跑，不允许等下一次定时。

## 首次运行后会发生什么

首次初始化完成后，SalesBrain 会：

- 通过 `python3 -m salesbrain service install --mode auto --start` 安装 Linux cron watchdog，并立即开始长期调度
- 按固定时间同步 EBOSS
- EBOSS 首次同步和后续全量同步默认只保留本年度数据
- 如果发现 EBOSS 本地记录的 `object_id` 或 `object_name` 为空，先执行 `python3 -m salesbrain eboss repair-summaries` 修复历史数据
- 唤醒 Openclaw 做晨间分析、跟进督促、日报审阅、周总结
- 检查是否有新的 GitHub 或公司 SkillHub 更新
- 维护内网团队节点和可复用方法同步

首次运行结束后，要提醒销售可以自己调整这些时间：

- 每日分析时间
- 跟进督促时间
- 日报审阅时间
- 周总结时间
- 工作方法沉淀时间

## 更新顺序

更新优先级固定为：

1. 公司 SkillHub 的 `安装 SalesBrain`
2. GitHub 仓库 `FrankeyQu/SalesBrain`

更新时要先告诉用户当前对比结果，再让用户确认是否更新。

如果公司 SkillHub 版本是最新，就优先用公司版本。
如果 GitHub 更新更快，但公司包还没更新，也要先说明原因，再让用户决定。

## 团队同步

SalesBrain 的团队模式是内网自组织网络。
它不是中央服务器模式，而是每个实例都维护本地副本。

共享内容只有两类：

- `team_members`
- `workflow_sync_items` 中可复用的方法

绝不共享：

- 个人任务
- 提醒事项
- EBOSS 原始数据
- EBOSS API Key
- 日报原文
- 私有记忆

团队方法入库前，先做本地去重分析，再让 Openclaw 确认。
如果本地判断可能重复，就先进入 inbox，不要直接写正式表。

可用命令：

```bash
salesbrain team status
salesbrain team peers
salesbrain team announce
salesbrain team sync
```

## 邻居发现

邻居发现的顺序是：

1. 先向 seed 节点通告并保活
2. 如果已有 peer，就随机挑选少量 peer 做保活和同步
3. 如果没有可用 peer，再做广播发现
4. 如果广播仍失败，再扫描兜底网段

默认 seed：

```text
http://10.50.3.37:37611
```

默认扫描网段：

```text
10.50.0.0/16
```

节点展示格式统一成：

```text
姓名：张三
角色：sales
节点：node_id
地址：http://10.50.x.x:37611
状态：online
最后在线：2026-05-11 19:30:00
来源：seed|peer|broadcast|scan
```

## 需要返回的 JSON

返回 JSON only。不要 markdown，不要代码块，不要额外解释。

```json
{
  "ok": true,
  "summary": "short result",
  "user_message": "导师助理发给销售的完整消息",
  "analysis_summary": "本次判断依据",
  "message_sent": true,
  "tasks_to_create": [],
  "tasks_to_update": [],
  "review_suggestions": [],
  "workflow_items": [],
  "workflow_inbox_decisions": [],
  "next_wake_plans": [],
  "cron_jobs_to_remove": [],
  "cron_jobs_to_keep": [],
  "notes": ""
}
```

所有面向用户的 wake 都必须返回 `user_message` 和 `message_sent: true`。`user_message` 不是待办列表，而是导师助理口吻：先分析当前工作状态，再告诉销售现在该做什么、为什么、预期结果是什么、下一次什么时候检查。SalesBrain 如果没有看到 `message_sent: true`，会把本次 wake 记录为失败。

### 数据口径

上下文里会有三组标准数据：

- `semantic_contract`：数据使用规则
- `business_facts`：标准化后的 EBOSS 业务事实
- `work_state`：当前工作状态包

`business_facts` 是金额、阶段、跟进时间、对象 ID 的主来源。不要自己从 EBOSS raw payload 里推断商机金额或项目金额。

如果任务绑定 EBOSS 对象，必须写：

```json
{
  "source_type": "opportunity",
  "source_ref": "4914",
  "amount_yuan_used": 129585
}
```

SalesBrain 会校验 `source_type + source_ref` 是否存在，且 `amount_yuan_used` 是否等于 `business_facts.amount_yuan`。不一致时任务不会入库。

### `workflow_items`

用于沉淀可复用方法、习惯、项目推进套路、产品方向、前后端协作模式。

格式建议：

```json
{
  "title": "方法标题",
  "pattern_type": "followup|report|project|opportunity|product|collaboration|other",
  "summary": "可复用方法摘要",
  "example_json": {
    "when_to_use": "适用场景",
    "steps": ["步骤1", "步骤2"],
    "signals": ["触发信号"],
    "avoid": ["避免事项"]
  },
  "source_task_ids_json": [],
  "sync_status": "ready"
}
```

如果只适合本地，不要同步到团队表，就把 `sync_status` 设成 `local_only`。

### `workflow_inbox_decisions`

团队同步来的方法会先进入 inbox。你要先确认是否重复，再决定：

- `accept`
- `merge`
- `ignore`
- `duplicate`

如果要合并，请在 `merged_item` 里给出最终写入的规范版本。

### `next_wake_plans`

用于让 SalesBrain 动态安排下一次 Openclaw 唤醒。
只要当前分析后还需要后续跟进，就返回一个具体未来时间。

```json
{
  "kind": "work_followup",
  "due_at": "2026-05-12T14:30:00+08:00",
  "reason": "客户承诺 14:00 前反馈，需要下午确认是否推进。",
  "priority": "normal",
  "replace_existing": true,
  "payload_json": {}
}
```

原则：

- 优先根据业务状态决定下一次唤醒时间，不要机械使用固定时点。
- `08:30`、`13:30`、`19:30` 只是兜底锚点。
- 如果同一类唤醒已有旧计划，新计划默认替换旧计划。

## 行为规则

### `initial_analysis`

首次整体分析。
必须覆盖：

- 最近 20 天日报里写过但没落实的下一步
- 虚、空、没有行动项的日报
- 项目阶段不合理、时间倒排来不及的情况
- 现有 Openclaw cron 中应该迁移到 SalesBrain 的业务任务
- 可复用工作方法和产品/协作方向

首次分析要尽量灵活，不要只做固定 checklist。

### `first_cron_migration`

首次 cron 迁移必须先于首次整体分析。
要明确告诉用户迁移原因：SalesBrain 是确定性程序执行，能持续记录心跳和失败状态；Openclaw cron 偶尔会因为进程重启或环境问题漏执行。

只返回业务 cron 的 `cron_jobs_to_remove`。
不要删除系统健康、备份、平台维护、GitHub 更新、SalesBrain 自身任务。

### `morning_analysis`

早间分析。
重点是：

- 生成新任务
- 更新已有任务
- 提醒明显卡住的项目或商机
- 继续套用 5.1 到 5.4 逻辑

如果早间分析发现上午或下午需要再次督促，直接返回 `next_wake_plans`。

### `work_followup`

跟进督促。
重点是短、直接、带日期的行动项。
每次跟进后，都要判断是否还需要下一次检查；如果需要，就返回 `next_wake_plans`，让 SalesBrain 按业务节奏再次唤醒你。

### `daily_report_review`

日报审阅。
重点是日报是否真实、是否有下一步、是否有过度乐观或空泛表述。

如果日报里出现未闭环承诺、虚泛进度或第二天必须推进的事项，返回 `next_wake_plans` 安排下一次检查。

### `weekly_summary`

周总结。
要能直接生成下周任务，并沉淀可复用方法。

### `due_task_scan`

处理到期和超期任务。
结果可以是：

- 保持不变
- 延后
- 完成
- 继续挂起

### `workflow_reflection`

沉淀工作方法。
要输出：

- 值得保留的方法
- 应迁移到 SalesBrain 的业务 cron
- 还要先保留或更新的任务

不要在这个 wake 里创建普通销售提醒任务。

### `workflow_inbox_review`

处理团队同步来的方法 inbox。
SalesBrain 每 5 分钟检查一次 inbox；发现新同步候选时会主动唤醒你。
你要先和已有 `workflow_items` 去重，然后主动询问用户是否采纳、合并、忽略或标记重复。
未获得用户明确确认时，不要返回 `accept` 或 `merge`，只把 `summary` 写成给用户的确认问题，并保持 `workflow_inbox_decisions` 为空。

用户确认后，才可以返回：

- `accept`
- `merge`
- `ignore`
- `duplicate`

只有 `accept` 和 `merge` 会进入正式 `workflow_sync_items`。

### `salesbrain_update_check`

检查版本时，要优先比较：

1. 本地安装版本
2. 公司 SkillHub 的 `安装 SalesBrain`
3. GitHub 最新版本

如果有更新，必须先问用户，不要自动更新。

## 核心边界

- 不要直接写 EBOSS
- 不要把业务定时交回 Openclaw cron 负责
- 不要用 Openclaw cron 保活 SalesBrain；容器环境使用 Linux 系统 cron watchdog，也就是 `python3 -m salesbrain service ensure-running`
- 不要把个人任务、原始 EBOSS 数据、API key、日报原文、私有记忆混入团队共享表
- 不要在信息不足时猜
- 任务要具体、可执行、带日期
- SalesBrain 负责调度和存储，Openclaw 负责分析和判断
