# 虚拟股板块 · 设计方案

> 版本：0.1.0（设计阶段）
> 状态：架构设计，尚未编码

---

## 一、模块总览

### 1.1 定位

虚拟股是 QQ 群机器人的一个**独立功能板块**，不嵌入任何现有文件。自身维护独立的版本号（见 `VERSION_HISTORY.md` → 虚拟股版本），通过一个入口函数注册到 `command_handler.py` 和 `reverse_bot.py`。

### 1.2 设计原则

- **数据流单向**：消息 → engine（定价）→ market（交易）→ account（结算）→ data（持久化）
- **风险优先**：爆仓检查、熔断、限仓、体力值等风控逻辑在所有交易路径之前执行
- **无状态服务**：每个模块对外暴露纯函数接口，状态统一由 `data.py` 管理
- **与主 bot 松耦合**：仅通过命令注册和消息 hook 两个触点连接

### 1.3 文件结构

```
scripts/virtual_stock/
├── __init__.py       # 包入口，导出 register_commands() 和 register_message_hook()
├── engine.py         # 核心引擎：8 支股票的定价算法、消息监听与指标统计
├── market.py         # AMM 做市商：买卖执行、点差、手续费计算
├── account.py        # 账户系统：金币、持仓、保证金、杠杆
├── risk.py           # 风控：爆仓检查、熔断判定、交易体力值、单一股票限仓
├── events.py         # 事件系统：拆股、分红、破产恢复、每日收盘、富豪榜
├── commands.py       # 指令处理：所有 # 开头的虚拟股指令
├── data.py           # 持久化层：账户/持仓/股票/配置的 JSON 读写
├── scheduler.py      # 后台定时任务：10 分钟股价刷新、爆仓遍历、每日收盘、体力恢复
└── DESIGN.md         # 本文件
```

---

## 二、模块职责与接口

### 2.1 engine.py — 核心引擎

**职责**：
- 监听每条群消息，为 8 支股票分别计算原始驱动数据（发言占比、图片数、关键词匹配等）
- 每 10 分钟汇总一次滚动窗口内的统计数据，调用各股票的定价公式，输出公允价格 $P$
- 维护各股票的历史价格序列（用于熔断判定、分红判定、K 线数据）

**8 支股票的定价算法**：

| 代码 | 名称 | 定价公式 | 输入数据 |
|------|------|----------|----------|
| 600001 | 群主控股 | $P = f(P_{owner})$，$5\% \le P_{owner} \le 15\%$ 时上涨；$P_{owner}=0$ 阴跌；$P_{owner}>30\%$ 大跌 | 群主发言字数占比 |
| 300001 | 水群地产 | $P$ 与图片数 + ≤3 字短消息数正相关 | 图片计数、短消息计数 |
| 300002 | 搬运物流 | $P$ 与合并转发消息数正相关 | XML 合并转发计数 |
| 300003 | 思考者重工 | 子股 A（人文思潮）：>50字 + 人文关键词；子股 B（科技前沿）：>50字 + 技术关键词 | 长文本分类 |
| 000001 | 消息密度 | $P$ 与 TPM（每分钟消息数）正相关 | 消息频率 |
| 100001 | 战雷航空 | $P$ 与战雷/WT 等关键词词频正相关 | 关键词匹配 |
| 100002 | 二游娱乐 | $P$ 与二游关键词词频正相关 | 关键词匹配 |
| 900001 | 智械危机 | $P$ 与机器人指令调用次数正相关 | 指令计数 |

**对外接口**：

```python
# 消息处理（每条群消息调用一次）
def on_message(group_id: str, user_id: str, raw_message: str) -> None

# 定时刷新（scheduler 每 10 分钟调用一次）
def refresh_prices() -> dict[str, float]  # {股票代码: 新价格}

# 获取当前价格
def get_price(stock_code: str) -> float
def get_all_prices() -> dict[str, float]

# 获取历史价格（用于熔断判定）
def get_price_history(stock_code: str, hours: int = 1) -> list[float]

# 获取股票信息
def get_stock_info(stock_code: str) -> dict  # 名称、当前价格、指标描述等
def get_all_stocks() -> list[dict]
```

**内部数据结构（滚动窗口）**：

```python
# 每个群独立维护，窗口大小 10 分钟
{
    group_id: {
        "owner_words": 0,           # 群主发言字数（累计）
        "total_words": 0,           # 全群总发言字数（累计）
        "image_count": 0,           # 图片+表情包数量
        "short_msg_count": 0,       # ≤3 字消息数
        "forward_count": 0,         # 合并转发消息数
        "long_text_humanities": 0,  # 人文类长文本数
        "long_text_tech": 0,        # 科技类长文本数
        "msg_timestamps": [],       # 消息时间戳列表（计算TPM）
        "keyword_war_thunder": 0,   # 战雷关键词命中数
        "keyword_gacha": 0,         # 二游关键词命中数
        "bot_command_count": 0,     # 机器人指令调用次数
    }
}
```

**定价公式具体设计**：

每支股票的价格 $P$ 由两部分组成：
$$P = P_{base} \times (1 + \Delta)$$

其中 $P_{base}$ 为上轮价格，$\Delta$ 为基于指标的变动率，由各股票的驱动函数 $g(indices)$ 计算得到。$g$ 的取值范围控制在 $[-0.15, +0.15]$ 之间，即单次刷新价格波动不超过 ±15%。

- **群主控股 (600001)**：$P_{owner} = \frac{\text{owner\_words}}{\text{total\_words}}$
  - $5\% \le P_{owner} \le 15\%$：$\Delta = +0.02$（稳步上涨）
  - $P_{owner} < 1\%$：$\Delta = -0.03$（群龙无首阴跌）
  - $P_{owner} > 30\%$：$\Delta = -0.08$（极权忧虑大跌）
  - 中间区间按线性插值

- **水群地产 (300001)**：$\text{index} = \frac{\text{image\_count} + \text{short\_msg\_count}}{\text{total\_msgs}}$
  - $\Delta = (\text{index} - 0.3) \times 0.2$（指数越高、涨越多）

- **搬运物流 (300002)**：$\text{index} = \text{forward\_count}$
  - 每 1 条转发 $\to \Delta = +0.03$，封顶 $+0.15$

- **思考者重工 (300003)**：两支子股竞争资金
  - 子股 A：$\Delta_A = \text{long\_text\_humanities} \times 0.04$，封顶 $+0.15$
  - 子股 B：$\Delta_B = \text{long\_text\_tech} \times 0.04$，封顶 $+0.15$

- **消息密度 (000001)**：$TPM = \frac{\text{msg\_count}}{\text{minute\_span}}$
  - $TPM > 50$：$\Delta = +0.10$
  - $TPM < 1$：$\Delta = -0.10$
  - 中间线性插值

- **战雷航空 (100001)** / **二游娱乐 (100002)**：
  - $\Delta = \frac{\text{keyword\_count}}{\max(1, \text{total\_msgs})} \times 0.3$
  - 即关键词密度越高，涨幅越大

- **智械危机 (900001)**：
  - $\Delta = \text{command\_count} \times 0.02$，封顶 $+0.15$

---

### 2.2 market.py — AMM 做市商

**职责**：
- 提供买卖双轨价格（Ask / Bid）
- 执行交易（买入、卖出、做空、平空）
- 计算并扣除手续费
- 提供点差（Spread）作为通缩回收

**点差双轨制**：

```
Ask（买入价）= P × (1 + 0.005)   # 群友向机器人买入，价格微抬
Bid（卖出价）= P × (1 - 0.005)   # 群友向机器人卖出，价格微压
点差 = 1%，由机器人回收销毁（通缩）
```

**交易手续费**：

| 交易类型 | 费率 | 说明 |
|----------|------|------|
| 买入做多 | 1.0% | 从交易额扣除 |
| 卖出平多 | 1.5% | 1.0% 手续费 + 0.5% 印花税 |
| 融券做空 | 2.0% | 建仓一次性服务费 |
| 买回平空 | 1.0% | 正常平仓费 |
| 杠杆利息 | 0.2%/日 | 每日凌晨扣除 |

**对外接口**：

```python
# 获取交易价格
def get_ask_price(stock_code: str) -> float    # 买入价
def get_bid_price(stock_code: str) -> float    # 卖出价

# 交易操作（返回交易结果）
def buy_long(user_id: str, group_id: str, stock_code: str, quantity: int, leverage: int = 1) -> TradeResult
def sell_long(user_id: str, group_id: str, stock_code: str, quantity: int) -> TradeResult
def sell_short(user_id: str, group_id: str, stock_code: str, quantity: int) -> TradeResult
def cover_short(user_id: str, group_id: str, stock_code: str, quantity: int) -> TradeResult

# 计算手续费
def calculate_fee(amount: float, trade_type: str) -> float
```

**TradeResult 结构**：

```python
@dataclass
class TradeResult:
    success: bool
    message: str              # 成功/失败描述
    stock_code: str
    trade_type: str           # buy_long / sell_long / sell_short / cover_short
    quantity: int
    price: float              # 成交价
    total_amount: float       # 成交额
    fee: float                # 手续费
    leverage: int             # 杠杆倍数
    new_position: dict        # 更新后的持仓
    new_balance: float        # 更新后的可用余额
```

---

### 2.3 account.py — 账户系统

**职责**：
- 管理群友的账户（金币余额、冻结保证金、持仓列表、负债）
- 计算账户总资产、保证金率
- 处理金币的增减（交易结算、分红、破产恢复）
- 维护杠杆账户的负债信息

**账户数据模型**：

```python
@dataclass
class Account:
    user_id: str
    group_id: str
    balance: float                     # 可用金币
    frozen_balance: float              # 冻结保证金
    positions: dict[str, Position]     # {股票代码: Position}
    liabilities: dict[str, Liability]  # {股票代码: Liability}（做空+杠杆欠款）
    stamina: int                       # 交易体力值（上限10）
    stamina_updated_at: str            # 体力最后更新时间
    total_trade_count: int             # 累计交易次数

@dataclass  
class Position:
    stock_code: str
    quantity: int                      # 持股数量（不含杠杆部分）
    avg_cost: float                    # 平均成本价
    leveraged_quantity: int            # 杠杆买入的数量
    leverage_multiplier: int           # 杠杆倍数
    debt: float                        # 欠庄家金币数

@dataclass
class Liability:
    stock_code: str
    short_quantity: int                # 融券卖出的股数
    short_price: float                 # 做空开仓价
    frozen_margin: float               # 冻结的保证金
```

**对外接口**：

```python
# 账户 CRUD
def get_account(user_id: str, group_id: str) -> Account
def create_account(user_id: str, group_id: str, initial_balance: float = 1000.0) -> Account

# 余额操作
def add_balance(user_id: str, group_id: str, amount: float) -> float
def deduct_balance(user_id: str, group_id: str, amount: float) -> float  # 可能返回负数=失败
def freeze_balance(user_id: str, group_id: str, amount: float) -> None
def unfreeze_balance(user_id: str, group_id: str, amount: float) -> None

# 持仓操作
def add_position(account: Account, position: Position) -> None
def remove_position(account: Account, stock_code: str) -> None
def update_position(account: Account, stock_code: str, **kwargs) -> None

# 查询
def get_total_assets(account: Account) -> float       # 现金 + 持仓市值 - 负债
def get_margin_ratio(account: Account) -> float       # 保证金率
def get_position_value(account: Account, stock_code: str) -> float
def get_all_positions_value(account: Account) -> float

# 破产判定
def is_bankrupt(account: Account) -> bool              # 总资产 < 50
def apply_bankruptcy_recovery(account: Account) -> None  # 重置为 200 金币
```

---

### 2.4 risk.py — 风控系统

**职责**：
- 杠杆账户爆仓检查与强制平仓
- 熔断判定（单股 1h ±30% 或大盘 -15%）
- 交易体力值管理
- 单一股票限仓检查（≤15% 总发行量）
- 所有风控逻辑在交易执行前生效

**爆仓机制**：

$$MR = \frac{\text{当前持仓市值} - \text{欠款金额}}{\text{当前持仓市值}}$$

- 当 $MR \le 10\%$ 时，触发强制平仓
- 平仓后优先偿还庄家欠款+利息，残余退还用户
- 全群广播强平公告

**熔断规则**：

- 单支股票 1 小时内涨跌幅超过 ±30% → 该股停牌 1 小时
- 大盘（所有股票均价）跌幅超过 15% → 全盘停牌 1 小时
- 停牌期间拒绝所有该股/全盘的买卖指令

**交易体力值**：

- 上限 10 点
- 每次 `#买入` / `#卖出` / `#做空` / `#平空` 消耗 1 点
- 每 30 分钟恢复 1 点
- 体力不足时拒绝交易

**单一股票限仓**：

- 单一用户持有某股票数量 ≤ 该股票总流通量的 15%
- 在买入/做空前检查

**对外接口**：

```python
# 爆仓检查（scheduler 每次价格刷新后调用）
def check_liquidation(group_id: str) -> list[LiquidationEvent]

# 熔断检查
def check_circuit_breaker(stock_code: str = None) -> CircuitBreakerStatus
def is_trading_halted(stock_code: str) -> bool
def get_halt_until(stock_code: str) -> datetime | None

# 体力值
def check_stamina(user_id: str, group_id: str) -> StaminaResult
def consume_stamina(user_id: str, group_id: str) -> None
def recover_stamina() -> None  # 全局体力恢复

# 限仓
def check_position_limit(user_id: str, group_id: str, stock_code: str, quantity: int) -> bool

# 盈余回收
def collect_trading_fees(fee_amount: float) -> None  # 手续费进入庄家池（分红来源）
```

---

### 2.5 events.py — 事件系统

**职责**：
- 自动拆股（股价 ≥ 1000 → 1:10）
- 每周分红（周日 22:00，触发指标达历史峰值时分红）
- 破产恢复处理（每日限 1 次）
- 每日收盘统计与富豪榜生成（23:30 收盘，0:05 发送）

**拆股逻辑**：

```
触发：单股 P ≥ 1000 金币
操作：
  - 股价 → P / 10
  - 所有持股用户股数 → × 10
  - 总资产价值不变
  - 全群公告拆股信息
```

**分红逻辑**：

```
触发：每周日 22:00
条件：某股票本周的绑定指标达到历史峰值
操作：
  - 从「生态发展基金」（手续费累积池）拨出分红
  - 按持股比例派发给各持股用户
  - 全群公告分红详情
```

**破产恢复**：

```
触发：用户发送 #申请破产恢复
判定：总资产 < 50 金币
操作：
  - 清除所有 < 50 金币价值的残余持仓
  - 现金重置为 200 金币
  - 当日禁止使用杠杆
限制：每日限 1 次
```

**每日收盘**：

```
23:30：闭市，统计今日涨幅之王、跌幅之王
00:05：发送群消息「每日富豪榜/负豪榜」
  - 富豪榜：总资产 TOP 5
  - 负豪榜：总资产倒数 5（亏损最多）
  - 今日涨幅最大/跌幅最大的股票
```

**对外接口**：

```python
# 拆股
def check_stock_split(stock_code: str) -> SplitEvent | None

# 分红
def process_weekly_dividend(group_id: str) -> list[DividendEvent]

# 破产恢复
def process_bankruptcy_recovery(user_id: str, group_id: str) -> str  # 返回结果消息

# 收盘
def daily_close(group_id: str) -> DailyReport
def generate_leaderboard(group_id: str) -> LeaderboardData
```

---

### 2.6 commands.py — 指令处理

**职责**：
- 解析并处理所有虚拟股相关的 QQ 指令
- 调用 market / account / risk / events 完成业务逻辑
- 返回格式化后的回复消息

**指令列表**：

| 指令 | 说明 | 示例 |
|------|------|------|
| `#买入 [股名] [数量]` | 做多买入 | `#买入 二游娱乐 10` |
| `#卖出 [股名] [数量]` | 平多卖出 | `#卖出 二游娱乐 5` |
| `#做空 [股名] [数量]` | 融券做空 | `#做空 水群地产 5` |
| `#平空 [股名] [数量]` | 空头平仓 | `#平空 水群地产 5` |
| `#杠杆 [股名] [数量] [倍数]` | 杠杆买入（1~3倍） | `#杠杆 二游娱乐 10 3` |
| `#持仓` | 查看个人持仓 | `#持仓` |
| `#股票行情 [股名]` | 查看单股行情 | `#股票行情 群主控股` |
| `#大盘` | 查看全部股票行情 | `#大盘` |
| `#富豪榜` | 查看富豪榜/负豪榜 | `#富豪榜` |
| `#申请破产恢复` | 破产救济 | `#申请破产恢复` |
| `#交易体力` | 查看剩余体力 | `#交易体力` |
| `#股票帮助` | 虚拟股帮助 | `#股票帮助` |

**对外入口**：

```python
# 由 command_handler.py 调用
def handle_stock_command(user_id: str, group_id: str, raw_text: str) -> str | None
# 匹配到指令 → 返回回复消息；未匹配 → 返回 None
```

---

### 2.7 data.py — 持久化层

**职责**：
- 所有虚拟股数据的 JSON 读写
- 提供原子写入（写临时文件 → 重命名）
- 启动时加载、变更后即时存盘

**数据文件**（全部存放在 `scripts/virtual_stock/data/`）：

| 文件 | 内容 |
|------|------|
| `accounts/{group_id}.json` | 该群所有用户账户 |
| `prices/{group_id}.json` | 该群股票当前价格 + 历史序列 |
| `config/{group_id}.json` | 该群配置（总发行量、熔断状态等） |
| `ecosystem_fund.json` | 生态发展基金（分红池）余额 |
| `leaderboard/{group_id}.json` | 最近一次收盘快照 |

**账户存储结构**（`accounts/{group_id}.json`）：

```json
{
  "user_123456": {
    "balance": 850.0,
    "frozen_balance": 300.0,
    "positions": {
      "100002": {
        "stock_code": "100002",
        "quantity": 10,
        "avg_cost": 50.0,
        "leveraged_quantity": 0,
        "leverage_multiplier": 1,
        "debt": 0
      }
    },
    "liabilities": {},
    "stamina": 8,
    "stamina_updated_at": "2026-07-14T09:00:00",
    "total_trade_count": 3,
    "bankruptcy_used_today": false,
    "no_leverage_until": null
  }
}
```

**价格存储结构**（`prices/{group_id}.json`）：

```json
{
  "current": {
    "600001": 100.0,
    "300001": 50.0,
    "300002": 80.0,
    "30003A": 120.0,
    "30003B": 110.0,
    "000001": 75.0,
    "100001": 90.0,
    "100002": 105.0,
    "900001": 60.0
  },
  "history": {
    "600001": [
      {"timestamp": "2026-07-14T09:00:00", "price": 98.0},
      {"timestamp": "2026-07-14T09:10:00", "price": 100.0}
    ]
  },
  "all_time_high": {
    "600001": 150.0,
    "100002": 300.0
  },
  "circuit_breaker": {
    "600001": null,
    "100002": {"halt_until": "2026-07-14T11:00:00", "reason": "1h涨幅32%"}
  }
}
```

**对外接口**：

```python
# 通用
def load_json(path: str) -> dict
def save_json(path: str, data: dict) -> None

# 账户
def load_accounts(group_id: str) -> dict[str, Account]
def save_accounts(group_id: str, accounts: dict) -> None

# 价格
def load_prices(group_id: str) -> dict
def save_prices(group_id: str, price_data: dict) -> None

# 生态基金
def load_ecosystem_fund() -> float
def save_ecosystem_fund(amount: float) -> None

# 初始化（首次启动时创建默认数据结构）
def init_group_data(group_id: str) -> None
```

---

### 2.8 scheduler.py — 后台定时任务

**职责**：
- 定时触发价格刷新（每 10 分钟）
- 定时触发爆仓检查（每次价格刷新后）
- 定时恢复体力值（每 30 分钟）
- 定时执行每日收盘（23:30）和发送富豪榜（00:05）
- 定时检查拆股条件
- 定时触发每周分红（周日 22:00）

**任务调度表**：

| 任务 | 频率 | 说明 |
|------|------|------|
| 股价刷新 | 每 10 分钟 | engine.refresh_prices() → risk.check_liquidation() → events.check_stock_split() |
| 体力恢复 | 每 30 分钟 | risk.recover_stamina()（所有用户 +1，不超过 10） |
| 每日收盘 | 每天 23:30 | events.daily_close() |
| 富豪榜发送 | 每天 00:05 | events.generate_leaderboard() → 群消息 |
| 每周分红 | 每周日 22:00 | events.process_weekly_dividend() |
| 杠杆利息 | 每天 00:00 | account 遍历杠杆账户扣除利息 |

**对外入口**：

```python
# 由 reverse_bot.py 在启动时调用，创建异步后台任务
async def start_scheduler(send_group_msg_callback) -> None
# send_group_msg_callback: 发送群消息的回调函数（用于爆仓公告、收盘播报等）
```

---

## 三、与主 Bot 的集成方式

### 3.1 触点一：命令注册

`command_handler.py` 中增加：

```python
# 在 handle_command() 函数的最前面增加虚拟股命令判断
from scripts.virtual_stock.commands import handle_stock_command

def handle_command(...):
    # 虚拟股指令（# 开头，优先级最高）
    result = handle_stock_command(user_id, group_id, raw_text)
    if result is not None:
        return result
    # ... 原有逻辑
```

### 3.2 触点二：消息 Hook

`reverse_bot.py` 的群消息处理循环中增加：

```python
# 每条群消息都传给虚拟股引擎做指标统计
from scripts.virtual_stock.engine import on_message
on_message(group_id, user_id, raw_message)
```

### 3.3 触点三：后台任务启动

`reverse_bot.py` 的 `main()` 中增加：

```python
from scripts.virtual_stock.scheduler import start_scheduler
asyncio.create_task(start_scheduler(send_group_msg))
```

### 3.4 主 Bot 改动量预估

| 文件 | 改动 |
|------|------|
| `reverse_bot.py` | +5 行（import + hook + scheduler 启动） |
| `scripts/command_handler.py` | +5 行（import + 最前面调用） |
| `VERSION_HISTORY.md` | 已添加虚拟股版本表 |
| **其他文件不改动** | — |

---

## 四、数据流全景

```
┌─────────────────────────────────────────────────────────────┐
│                      群消息流入                               │
│                          │                                   │
│                          ▼                                   │
│  ┌──────────────────────────────────────────────┐           │
│  │        engine.on_message()                    │           │
│  │  统计：字数/图片/关键词/转发/TPM/指令          │           │
│  │  窗口：10 分钟滚动数据                         │           │
│  └──────────────────┬───────────────────────────┘           │
│                     │                                        │
│         每 10 分钟  │  scheduler 触发                         │
│                     ▼                                        │
│  ┌──────────────────────────────────────────────┐           │
│  │        engine.refresh_prices()                │           │
│  │  8 支股票 × 各自定价公式 → P_new               │           │
│  │  写入 price_history，判定 all_time_high         │           │
│  └──────────────────┬───────────────────────────┘           │
│                     │                                        │
│                     ▼                                        │
│  ┌──────────────────────────────────────────────┐           │
│  │        risk.check_liquidation()               │           │
│  │  遍历杠杆账户 → 检查 MR ≤ 10%                  │           │
│  │  触发强平 → 全群公告                            │           │
│  └──────────────────┬───────────────────────────┘           │
│                     │                                        │
│                     ▼                                        │
│  ┌──────────────────────────────────────────────┐           │
│  │        events.check_stock_split()             │           │
│  │  检查 P ≥ 1000 → 自动 1:10 拆股                │           │
│  └──────────────────┬───────────────────────────┘           │
│                     │                                        │
│                     ▼                                        │
│              市场价格更新完毕                                  │
│                                                              │
│  ═══════════════════════════════════════════════════════    │
│                      交易路径                                 │
│                                                              │
│  用户发送 #指令                                               │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────────────────────────────┐                   │
│  │  1. risk.check_circuit_breaker()      │ ← 熔断？          │
│  │  2. risk.check_stamina()              │ ← 体力？          │
│  │  3. risk.check_position_limit()       │ ← 超限？          │
│  │  4. account.deduct_balance()          │ ← 资金？          │
│  └──────────────┬───────────────────────┘                   │
│                 │ 全部通过                                     │
│                 ▼                                            │
│  ┌──────────────────────────────────────┐                   │
│  │  market.buy_long() / sell_short() …   │                   │
│  │  → 计算点差 Ask/Bid                    │                   │
│  │  → 扣除手续费 → 进入生态基金池          │                   │
│  │  → 更新持仓 → account.add_position()   │                   │
│  │  → 返回 TradeResult                    │                   │
│  └──────────────────────────────────────┘                   │
│                                                              │
│  ═══════════════════════════════════════════════════════    │
│                      定时事件                                 │
│                                                              │
│  每 30 分钟 → risk.recover_stamina()                          │
│  每天 23:30 → events.daily_close()                            │
│  每天 00:00 → account（扣除杠杆利息）                          │
│  每天 00:05 → events.generate_leaderboard() → 群消息           │
│  每周日 22:00 → events.process_weekly_dividend()               │
└─────────────────────────────────────────────────────────────┘
```

---

## 五、风险与边界情况

### 5.1 需要特别处理的边界

| 场景 | 处理方式 |
|------|----------|
| 股价跌到 ≤ 0 | 下界 1 金币，不允许归零。归零后无法交易 |
| 用户金币不足但尝试买入 | 交易前检查余额，不足直接拒绝 |
| 杠杆爆仓后仍不够还债 | 庄家承担坏账（生态基金兜底），用户归零 |
| 熔断期间用户尝试交易 | 返回「股票/大盘已停牌，预计 X 点恢复」 |
| 体力值刚好恢复时大量请求涌入 | 体力值在交易前原子检查+扣减 |
| 同一用户重复发送同一指令 | 命令去重（与主 bot 的 _dedup_lock 共用或独立） |
| 数据文件损坏 | 启动时 JSON 解析异常 → 回退到默认初始化 |
| 多群隔离 | 所有数据按 group_id 分文件，群间完全隔离 |

### 5.2 股票初始价格设定

所有股票统一从 **100.0 金币** 起步，避免初始价差异导致的早期套利。

---

## 六、实施路线图

| 阶段 | 内容 | 预估行数 |
|------|------|----------|
| **Phase 1** | `data.py` + `engine.py` 基础：数据模型 + 定价算法 | ~500 行 |
| **Phase 2** | `account.py` 账户系统：CRUD + 持仓 + 保证金 | ~400 行 |
| **Phase 3** | `market.py` AMM：买/卖/做空/平空 + 手续费 | ~350 行 |
| **Phase 4** | `risk.py` 风控：爆仓 + 熔断 + 体力 + 限仓 | ~300 行 |
| **Phase 5** | `events.py` 事件：拆股 + 分红 + 破产恢复 + 收盘 | ~300 行 |
| **Phase 6** | `commands.py` 指令：解析 + 格式化输出 | ~400 行 |
| **Phase 7** | `scheduler.py` + `__init__.py` + 主 Bot 集成 | ~200 行 |
| **Phase 8** | 测试、调参、文档完善 | — |
| **合计** | — | ~2450 行 |

---

## 七、已确认决策

| # | 问题 | 决策 |
|---|------|------|
| 1 | 群主 QQ 号获取方式 | **手动输入**：在群配置 `config/{group_id}.json` 中指定 `owner_qq` 字段 |
| 2 | 思考者重工子母股关系 | **零和竞争（高风险）**：先分别算 Δ_A 和 Δ_B，然后实际 Δ_A' = Δ_A − Δ_B，Δ_B' = Δ_B − Δ_A，即谁的指标更强，资金就从对手方流出注入己方 |
| 3 | 股票总发行量 | **参考真实股市**：初始每支股票 **10,000 股**（模拟 A 股 IPO 规模），每股初始价 100 金币 → 总市值 1,000,000 金币。不设动态增发，总流通量固定 |
| 4 | 分红比例 | **参考真实股市股息率**：每周日按该股票总市值的 **0.05%** 派发（年化约 2.6%，对标 A 股平均股息率），资金来自生态发展基金（手续费池） |
| 5 | 签到奖励联动 | **联动**：每日签到额外奖励 = 用户虚拟股总资产的 **1‰**（千分之一），在现有签到模块中追加 |
| 6 | 跨群联动 | **不跨群**：每个群的虚拟股市完全独立，数据按 `group_id` 物理隔离 |

### 7.1 子母股零和竞争公式（细化）

```
# 原始变动率（各自独立计算）
Δ_A_raw = long_text_humanities × 0.04      # 人文指标
Δ_B_raw = long_text_tech × 0.04            # 科技指标

# 零和竞争：实际变动率 = 自身优势 − 对手优势
Δ_A = Δ_A_raw − Δ_B_raw     # 人文对科技的相对强度
Δ_B = Δ_B_raw − Δ_A_raw     # 科技对人文的相对强度

# 封顶 ±15%
Δ_A = clamp(Δ_A, -0.15, +0.15)
Δ_B = clamp(Δ_B, -0.15, +0.15)

# 新价格
P_A_new = P_A × (1 + Δ_A)
P_B_new = P_B × (1 + Δ_B)
```

效果：如果群里同时有人文和科技讨论，两支都涨但幅度不同；如果只有一方活跃，资金会从冷门方撤出。

### 7.2 群配置结构（`config/{group_id}.json`）

```json
{
  "owner_qq": "784427550",
  "stocks": {
    "600001": {"total_shares": 10000, "initial_price": 100.0},
    "300001": {"total_shares": 10000, "initial_price": 100.0},
    "300002": {"total_shares": 10000, "initial_price": 100.0},
    "30003A": {"total_shares": 10000, "initial_price": 100.0},
    "30003B": {"total_shares": 10000, "initial_price": 100.0},
    "000001": {"total_shares": 10000, "initial_price": 100.0},
    "100001": {"total_shares": 10000, "initial_price": 100.0},
    "100002": {"total_shares": 10000, "initial_price": 100.0},
    "900001": {"total_shares": 10000, "initial_price": 100.0}
  },
  "dividend_rate": 0.0005,
  "signin_bonus_rate": 0.001
}
```

### 7.3 签到联动实现要点

- 在 `command_handler.py` 签到流程完成后，检查用户是否有虚拟股账户
- 若有，计算 `bonus = get_total_assets(account) × 0.001`
- 将奖励金币打入虚拟股账户余额
- 签到回复消息中追加一行：「📈 虚拟股资产分红 +{bonus} 金币」

---

> 📅 最后更新：2026-07-14
> 📌 下一步：确认待定问题 → 进入 Phase 1 编码