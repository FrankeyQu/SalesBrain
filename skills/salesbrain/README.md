# SalesBrain 使用说明

SalesBrain 是给 Openclaw 用的销售陪跑系统。它不是 AI 模型，而是一个确定性调度、状态保存和团队同步层。

## 它会做什么

- 首次安装后，先检查本地 SalesBrain 程序和配置
- 读取 EBOSS API Key
- 验证 Openclaw 唤醒桥接，并发送一条可见测试消息
- 首次全量同步 EBOSS 本年度项目、商机、日报和相关明细
- 从 EBOSS 原始数据生成标准业务事实，统一金额、对象 ID、阶段和跟进时间口径
- 在首次整体分析前，先迁移 Openclaw 里的业务 cron
- 之后每天按固定时间唤醒 Openclaw 做分析、跟进、日报审阅、周总结和方法沉淀
- 发现 GitHub 或公司 SkillHub 有新版本时，提醒你选择更新
- 在内网里发现其他销售节点，并同步可复用的方法和成员状态

## 首次安装后会发生什么

首次初始化完成时，SalesBrain 会按这个顺序执行：

1. 检查本地程序与配置
2. 读取 EBOSS API Key
3. 验证 Openclaw 唤醒桥接和消息发送
4. 同步 EBOSS 本年度全量数据
5. 检查并迁移 Openclaw 的业务定时任务
6. 唤醒 Openclaw 做首次整体分析
7. 启动长期调度与团队同步

这几个步骤都会有进度提示。SalesBrain 不会静默跳过首次流程。

## 首次使用步骤

1. 在公司 SkillHub 上传或安装 `salesbrain.zip`。
2. 安装后对 Openclaw 说：`初始化 SalesBrain`。
3. Openclaw 会先执行 skill 包内的 GitHub 安装脚本：

```bash
python <skill_root>/scripts/install.py
```

4. 安装脚本会从 `https://github.com/FrankeyQu/SalesBrain.git` 拉取 `main` 分支到 `~/.openclaw/SalesBrain`，并执行本地 editable 安装。
5. Openclaw 会提示填写 EBOSS API Key，并创建本地配置。
6. Openclaw 会先执行 `python3 -m salesbrain bridge doctor` 和 `python3 -m salesbrain bridge test`，确认 SalesBrain 能唤醒 Openclaw 且 Openclaw 能向用户发出可见消息。
7. Openclaw 会分步执行首次 EBOSS 全量同步、cron 检查迁移和首次整体分析。
8. 首次完成后，Openclaw 执行 `python3 -m salesbrain service install --mode auto --start`，安装 Linux cron watchdog 并启动长期调度和团队同步。

首次安装要求能访问 GitHub。公司 SkillHub 只分发这个轻量 skill 包，不再在压缩包内携带 SalesBrain 源码。

EBOSS 同步默认只保留本年度数据。首次同步、手动全量同步和后续全量同步都会优先向 EBOSS 传入本年度时间范围，并在本地再次过滤；年初首次日报回填不会把上一年度日报写入本地库。

Openclaw 可以先读取安装步骤：

```bash
python <skill_root>/scripts/install.py --steps
```

也可以检查本地安装状态：

```bash
python <skill_root>/scripts/install.py --check
```

Openclaw 实际执行首次流程时，应使用这些分步命令：

```bash
python3 -m salesbrain init --no-first-run --sales-name "张三" --eboss-api-key "<EBOSS_API_KEY>" --openclaw-wake-command "<Openclaw 唤醒命令>"
python3 -m salesbrain bridge doctor
python3 -m salesbrain bridge test
python3 -m salesbrain first-run sync
python3 -m salesbrain first-run cron-inspect
python3 -m salesbrain first-run cron-migrate --mode all
python3 -m salesbrain first-run analyze
python3 -m salesbrain service install --mode auto --start
```

`cron-inspect` 之后如果发现 Openclaw 遗留业务定时任务，需要先让用户选择：

- 迁移：`python3 -m salesbrain first-run cron-migrate --mode all`
- 不迁移：`python3 -m salesbrain first-run cron-migrate --mode none`
- 选择性迁移：`python3 -m salesbrain first-run cron-migrate --mode selected --job-id <job_id>`

`first-run analyze` 会返回 `analysis_report.formatted_report`，Openclaw 应把它作为首次分析报告发送给销售。

如果 `[openclaw].wake_command` 为空，SalesBrain 会把定时唤醒记录为失败，不再写 outbox 文件并标记成功。`bridge test` 必须让 Openclaw 发送一条可见测试消息，并返回 `message_sent: true`。

## 为什么要迁移 Openclaw cron

SalesBrain 采用的是确定性程序调度。它会记录心跳、任务状态和失败情况，适合承接销售跟进、日报审阅、工作分析这类关键定时任务。

Openclaw 的原生 cron 可能受进程重启、运行环境或执行丢失影响而漏跑，所以业务定时任务应该迁移到 SalesBrain。

## 更新顺序

优先级是：

1. 公司 SkillHub 的 `安装 SalesBrain`
2. GitHub 仓库 `FrankeyQu/SalesBrain`

如果公司版本更新，就优先走公司更新；如果 GitHub 更快，就提示你两边对比后再决定。

默认每天 09:00 检查更新。任何更新都必须先经过用户确认，不会静默覆盖本地代码。

## 默认调度

- 02:00：同步 EBOSS
- 06:00：早间工作分析
- 08:30、13:30、19:30：跟进督促兜底锚点
- 22:00：日报审阅
- 23:30：工作方法沉淀
- 周五 17:30：周总结和下周计划
- 每 5 分钟：扫描到期任务
- 每 5 分钟：检查团队同步来的方法 inbox，发现新候选时唤醒 Openclaw 询问是否采纳
- 每天 09:00：检查更新

这些调度都由 SalesBrain 自己执行，不依赖 Openclaw cron。

在 Openclaw 托管 Docker 容器里，SalesBrain 使用 Linux 系统 cron 安装 watchdog：每分钟运行一次 `python3 -m salesbrain service ensure-running`，检查 daemon 进程、心跳和逾期任务；daemon 停止时自动拉起，并先补跑到期任务。这个 cron 是容器里的系统 cron，不是 Openclaw 业务 cron。

销售跟进的主节奏不是固定三次。Openclaw 每次分析后可以返回 `next_wake_plans`，SalesBrain 会把它保存成一次性 `planned_wake`，到点后再次唤醒 Openclaw。固定三次只是在没有明确动态计划时的兜底。

每次面向销售的唤醒都必须是导师分析，不是单纯待办推送。Openclaw 需要发送 `user_message`，内容要包括：当前工作状态判断、现在该做什么、为什么优先做、预期结果、下一次检查时间。SalesBrain 只有在收到 `message_sent: true` 后才认为本次唤醒成功。

## 数据口径和金额正确性

SalesBrain 会先把 EBOSS raw records 转成 `business_facts`：

- `object_type` / `object_id`：对象身份，避免靠名称匹配
- `amount_yuan` / `amount_display`：标准金额
- `amount_source`：金额来自哪个 EBOSS API 和字段
- `amount_confidence`：`high`、`medium`、`low`、`conflict`、`missing`
- `stage` / `last_follow_at`：阶段和最近跟进时间

Openclaw 分析时必须优先使用 `business_facts` 和 `work_state`。如果创建任务时引用了商机或项目金额，需要返回 `amount_yuan_used`。SalesBrain 会校验金额和对象 ID；金额不一致时，任务不会入库，并会在 wake run 里记录错误。

## 团队同步规则

团队只同步两类内容：

- 成员表
- 可复用的方法和工作套路

不会同步：

- 个人任务
- 提醒
- EBOSS 原始数据
- API Key
- 日报原文
- 私有记忆

团队方法会先进入本地 inbox，并做去重判断。SalesBrain 发现有新同步候选后，会主动唤醒 Openclaw 给销售发确认消息；只有销售明确确认后，Openclaw 才能返回 `accept` 或 `merge`，然后 SalesBrain 才会写入正式方法表。

默认团队参数：

- seed：`http://10.50.3.37:37611`
- 扫描网段：`10.50.0.0/16`
- team secret：`salesbrain-team-v1`
- 发现顺序：seed、已知 peer、广播、扫描兜底

## 常用命令

```bash
salesbrain init --sales-name "张三"
salesbrain daemon
salesbrain status
salesbrain eboss sync
salesbrain bridge doctor
salesbrain bridge test
salesbrain wake initial
salesbrain wake morning
salesbrain wake followup
salesbrain wake review
salesbrain wake weekly
salesbrain wake workflow
salesbrain team status
salesbrain team peers
salesbrain team announce
salesbrain team sync
```

## 排障建议

- 如果首次同步失败，先检查 EBOSS API Key、网络和 `salesbrain status` 里的 latest_sync。
- 如果 EBOSS 记录里 `object_id` 或 `object_name` 为空，执行 `python3 -m salesbrain eboss repair-summaries` 回填历史记录，再重新跑 `python3 -m salesbrain eboss sync --full`。
- 如果定时任务没执行，先看 `salesbrain status` 里的 first_run_state、scheduler_jobs 和 monitor_state。
- 如果定时任务显示执行但用户没收到消息，先执行 `python3 -m salesbrain bridge doctor` 和 `python3 -m salesbrain bridge test`。桥接未通过时，不要启动 daemon。
- 如果 daemon 停止，执行 `python3 -m salesbrain service status` 查看 pid、心跳和 watchdog 路径；执行 `python3 -m salesbrain service install --mode auto --start` 重新安装 Linux cron watchdog。
- 如果团队成员看不到，先执行 `salesbrain team status` 和 `salesbrain team sync`，确认 seed 或 peer 是否可达。
- 如果 Openclaw 原有 cron 没迁移，检查 Openclaw bridge 的 cron list/remove 命令是否配置正确。
- 如果更新提示异常，优先确认公司 SkillHub 中 `安装 SalesBrain` 是否已经发布最新包。

## 你会看到的进度提示

首次启动时，建议 Openclaw 明确告诉用户这些阶段：

- 准备环境
- 同步 EBOSS
- 迁移 cron
- 首次整体分析
- 启动长期调度

如果某一步失败，SalesBrain 会把错误写进本地状态，而不是直接吞掉。

## 版本记录

- `0.2.0`：改为轻量 skill 包，首次安装从 GitHub 拉取 SalesBrain 代码，支持团队发现、方法同步和首次 cron 迁移。
