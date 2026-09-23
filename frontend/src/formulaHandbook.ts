// 公式函数手册数据：函数/字段 + 中文缩写 + 详细说明
// 双击行时插入：函数 → `名称(`；字段 → `名称`
export interface HandbookEntry {
  name: string // 函数名或字段名（插入用，翻译层写法）
  abbr: string // 中文缩写
  kind: 'func' | 'field'
  desc: string // 详细说明（用法/注意）
}

// ⚠ 这里的**书写顺序只表示"主题分组"**（便于维护阅读），**不是**手册的显示顺序 ——
//   显示顺序一律由下方 `buildHandbook()` 生成（v1.19.20 起），见其注释。
const RAW_HANDBOOK: HandbookEntry[] = [
  // ---------------- 行情字段 ----------------
  { name: 'CLOSE', abbr: '收盘价', kind: 'field', desc: '收盘价。\n用法:\n CLOSE 或 C\n X:=CLOSE;' },
  { name: 'HIGH', abbr: '最高价', kind: 'field', desc: '当日最高价。\n用法:\n HIGH 或 H' },
  { name: 'LOW', abbr: '最低价', kind: 'field', desc: '当日最低价。\n用法:\n LOW 或 L' },
  { name: 'OPEN', abbr: '开盘价', kind: 'field', desc: '当日开盘价。\n用法:\n OPEN 或 O' },
  { name: 'VOL', abbr: '成交量', kind: 'field', desc: '成交量（手）。\n用法:\n VOL 或 V\n注:数据里停牌日无成交为 NaN。' },
  { name: 'AMOUNT', abbr: '成交额', kind: 'field', desc: '成交金额（元）。\n用法:\n AMOUNT' },
  // 以下按字母升序：MARKET_CAP < TURNOVERRATE < VWAP
  { name: 'MARKET_CAP', abbr: '总市值', kind: 'field', desc: '总市值（元）。\n用法:\n MARKET_CAP\n注:含停牌日亦有值，需注意停牌对齐。' },
  { name: 'TURNOVERRATE', abbr: '换手率', kind: 'field', desc: '换手率（%）。\n用法:\n TURNOVERRATE' },
  { name: 'VWAP', abbr: '均价', kind: 'field', desc: '成交均价（当日总成交额/总量）。\n用法:\n VWAP' },

  // ---------------- 资金流字段（moneyflow，金额=万元/量=手/占比=%） ----------------
  { name: 'L2_AMO', abbr: '资金净额', kind: 'func', desc: '资金流档位金额函数。\n用法:\n L2_AMO(n[,b|s])\n n=0主力/1超大单/2大单/3中单/4小单；b=买入/s=卖出。\n L2_AMO(0)       主力净流入额(万元)\n L2_AMO(0,b)     主力买入额\n L2_AMO(0,s)     主力卖出额\n注意:L2_AMO(n,b)-L2_AMO(n,s)=L2_AMO(n)。' },
  { name: 'L2_PCT', abbr: '资金占比', kind: 'func', desc: '资金流档位占比函数（分母=当日总成交额）。\n用法:\n L2_PCT(n[,b|s])\n L2_PCT(0)       主力净流入占比(%)\n L2_PCT(1,b)     超大单买入占比' },
  { name: 'L2_VOL', abbr: '资金量', kind: 'func', desc: '资金流档位量函数（手）。\n用法:\n L2_VOL(n[,b|s])\n L2_VOL(0)       主力净流入量(手，可为负)\n L2_VOL(2,b)     大单买入量\n注意:数据源为 moneyflow3 买卖量(_bq/_sq)。' },

  // ---------------- 筹码分布（后端 v1.19.83 已支持；2026-09-17 补进手册） ----------------
  // ⚠ 以前这两个**只在后端**、手册里没有 ⇒ 用户搜不到也不知道怎么用 ✗
  //   （现在有 `tests/test_formula_handbook_sync.py` 守着："后端加了、手册忘写"会被测出来 ✓）
  { name: 'COST', abbr: '成本分位价', kind: 'func', desc: '成本分布：求"多少比例的筹码成本低于该价"。\n用法:\n COST(q)\n q 为百分数、必须是常量且在 (0,100) 之间，如 COST(95)。\n例:\n COST(95) > CLOSE   95% 的筹码成本都低于现价（上方套牢盘少）\n注意:q 目前只物化了 5 / 30 / 75 / 95 四档，其它分位会报"字段不存在"。' },
  { name: 'WINNER', abbr: '获利盘比例', kind: 'func', desc: '获利盘比例：价格 P 下方的持仓占比(0~100)。\n用法:\n WINNER(P)\n P 只能是 C / H / L（现价/最高价/最低价），如 WINNER(C)。\n例:\n WINNER(C) > 80   八成以上筹码处于获利状态\n注意:与 COST(q) 互为反向（COST 由分位求价格、WINNER 由价格求分位）；两者都由"换手率衰减"的筹码分布算出。' },

  // ---------------- 常用算术/统计函数 ----------------
  { name: 'MA', abbr: '简单移动平均', kind: 'func', desc: '简单移动平均。\n用法:\n MA(X,N)\n 返回 X 在 N 周期的简单平均\n 窗口不足按已有数据算(min_periods=1)。\n例:\n MA(CLOSE,5)' },
  { name: 'EMA', abbr: '指数移动平均', kind: 'func', desc: '指数移动平均（pandas ewm adjust=True 口径）。\n用法:\n EMA(X,N)\n N=4 即 span=4 归一化指数加权。\n注意:序列开头与通达信递归式有初值差；需要通达信递归口径可直接写 EMA_TDX(X,N)。' },
  { name: 'EMA_TDX', abbr: '通达信EMA', kind: 'func', desc: '通达信递归语义的指数移动平均（直接可写）。\n用法:\n EMA_TDX(X,N)\n Y_t=(2·X_t+(N-1)·Y_{t-1})/(N+1)，即 ewm(adjust=False)；与公式里的 EMA（qlib 内建 adjust=True）仅在序列开头有初值差。' },
  { name: 'WMA', abbr: '加权移动平均', kind: 'func', desc: '加权移动平均。\n用法:\n WMA(X,N)\n 权重线性递增(近期权重更大)。' },
  { name: 'HHV', abbr: 'N周期最高', kind: 'func', desc: '求 N 周期内最高值。\n用法:\n HHV(X,N)\n 窗口不足按已有数据算。\n例:\n HHV(HIGH,34)' },
  { name: 'LLV', abbr: 'N周期最低', kind: 'func', desc: '求 N 周期内最低值。\n用法:\n LLV(X,N)\n 窗口不足按已有数据算。\n例:\n LLV(LOW,34)' },
  { name: 'MAX', abbr: '取较大值', kind: 'func', desc: '两值取较大。\n用法:\n MAX(A,B)\n 通达信语义:两参数取大(不是 N 周期最大，周期最大用 HHV)。' },
  { name: 'MIN', abbr: '取较小值', kind: 'func', desc: '两值取较小。\n用法:\n MIN(A,B)\n 通达信语义:两参数取小(周期最小用 LLV)。' },
  { name: 'SUM', abbr: 'N周期求和', kind: 'func', desc: '求和。\n用法:\n SUM(X,N)\n X 在 N 周期的和。\n例:\n SUM(VOL,5)' },
  { name: 'COUNT', abbr: 'N周期计数', kind: 'func', desc: '统计 N 周期内条件成立次数。\n用法:\n COUNT(条件,N)\n例:\n COUNT(CLOSE>OPEN,5)\n注意:条件为 0/1 或布尔。' },
  { name: 'REF', abbr: '前N周期值', kind: 'func', desc: '向前引用。\n用法:\n REF(X,N)\n N 周期前的 X 值。\n例:\n REF(CLOSE,1) 昨收\n REF(CLOSE,5) 5日前收盘\n注意:停牌行语义由面板决定(见全局"停牌删行"开关)。' },
  { name: 'DELTA', abbr: '当前-前N', kind: 'func', desc: '当前值减前 N 周期值。\n用法:\n DELTA(X,N)\n = X - REF(X,N)。' },
  { name: 'ABS', abbr: '绝对值', kind: 'func', desc: '绝对值。\n用法:\n ABS(X)\n例:\n ABS(CLOSE-REF(CLOSE,1))' },
  { name: 'SQRT', abbr: '开平方', kind: 'func', desc: '平方根。\n用法:\n SQRT(X)' },
  { name: 'LOG', abbr: '自然对数', kind: 'func', desc: '自然对数 ln。\n用法:\n LOG(X)（别名 LN）' },
  { name: 'POW', abbr: '幂', kind: 'func', desc: '幂运算。\n用法:\n POW(X,N)\n X 的 N 次方。' },
  { name: 'STD', abbr: '标准差', kind: 'func', desc: 'N 周期标准差。\n用法:\n STD(X,N)' },
  { name: 'VAR', abbr: '方差', kind: 'func', desc: 'N 周期方差。\n用法:\n VAR(X,N)' },
  { name: 'SLOPE', abbr: '线性斜率', kind: 'func', desc: 'N 周期线性回归斜率。\n用法:\n SLOPE(X,N)' },
  { name: 'MED', abbr: '中位数', kind: 'func', desc: 'N 周期中位数。\n用法:\n MED(X,N)' },
  { name: 'MEAN', abbr: '均值', kind: 'func', desc: 'N 周期均值（同 MA）。\n用法:\n MEAN(X,N)' },

  // ---------------- 逻辑/条件函数 ----------------
  { name: 'IF', abbr: '条件取值', kind: 'func', desc: '条件分支。\n用法:\n IF(条件,A,B)\n 条件真取 A，假取 B。\n例:\n IF(CLOSE>OPEN,1,0)' },
  { name: 'IFS', abbr: '多重条件', kind: 'func', desc: '多条件分支（同 IF 的链式写法的别名）。\n用法:\n IFS(条件1,A1,条件2,A2,...,默认)' },
  { name: 'NOT', abbr: '逻辑非', kind: 'func', desc: '求逻辑非（益盟/同花顺/通达信同义）。\n用法:\n NOT(X) 或 NOT X\n X=0 时返回 1，否则返回 0。\n例:\n NOT(CLOSE>OPEN)   平盘或收阴\n COUNT(量王条件,BARSLAST(NOT(量王条件))+1)   统计"量王条件"连续成立的根数' },
  { name: 'CROSS', abbr: '上穿', kind: 'func', desc: '上穿信号。\n用法:\n CROSS(A,B)\n A 从下向上穿过 B 的时刻为真(=前一日 A<=B 且当日 A>B)。\n例:\n CROSS(MA(CLOSE,5),MA(CLOSE,20))' },
  { name: 'BETWEEN', abbr: '介于区间', kind: 'func', desc: 'X 是否介于 A、B 之间。\n用法:\n BETWEEN(X,A,B)\n 含边界；A、B 大小任意(内部取 min/max)。\n条件成立=1，否则=0；X 停牌(NaN)时输出 NaN。\n例:\n BETWEEN(CLOSE,LLV(LOW,20),HHV(HIGH,20))' },
  { name: 'SGN', abbr: '取符号', kind: 'func', desc: '取符号。\n用法:\n SGN(X)（别名 SIGN）\n X>0→1，X<0→-1，X=0→0。' },
  { name: 'INT', abbr: '向零取整', kind: 'func', desc: '向零方向截断取整（返回 X 的整数部分）。\n用法:\n INT(X)\n 3.7→3，-3.7→-3（注意:不是向下取整，负数方向是向零）。' },
  { name: 'MOD', abbr: '取余', kind: 'func', desc: '取余数（A 除以 B 的余数，符号随 A）。\n用法:\n MOD(A,B)\n例:\n MOD(BARSCOUNT(X),5)=0   每 5 根一根' },

  // ---------------- 动态窗口算子（窗口大小 N 可以是"变量"） ----------------
  // 说明:LLV/HHV/COUNT/REF/SUM/HHVBARS/LLVBARS 的 N 写成**变量/表达式**时，内部会自动改用这些
  // DYN_* 算子（常量窗口仍走标准算子，性能更好）；也可以直接写 DYN_* 显式指定。
  { name: 'DYN_MIN', abbr: '动态窗口最低', kind: 'func', desc: '动态窗口最小值:每个位置用该位置的 N 作窗口大小。\n用法:\n DYN_MIN(X,N)\n对比:\n LLV(X,N) 的 N 是常量。\n例:\n DYN_MIN(LOW, AT+1)' },
  { name: 'DYN_MAX', abbr: '动态窗口最高', kind: 'func', desc: '动态窗口最大值（每个位置用该位置的 N）。\n用法:\n DYN_MAX(X,N)\n对比:\n HHV(X,N) 的 N 是常量。' },
  { name: 'DYN_COUNT', abbr: '动态窗口计数', kind: 'func', desc: '动态窗口内条件成立次数（每个位置用该位置的 N）。\n用法:\n DYN_COUNT(条件,N)\n对比:\n COUNT(条件,N) 的 N 是常量。' },
  { name: 'DYN_REF', abbr: '动态窗口前值', kind: 'func', desc: '向前引用 N 个周期（每个位置用该位置的 N）。\n用法:\n DYN_REF(X,N)\n对比:\n REF(X,N) 的 N 是常量。' },
  { name: 'DYN_SUM', abbr: '动态窗口求和', kind: 'func', desc: '动态窗口求和（每个位置用该位置的 N）。\n用法:\n DYN_SUM(X,N)\n对比:\n SUM(X,N) 的 N 是常量。' },


  // ---------------- 状态/周期函数（外挂算子） ----------------
  { name: 'BARSLAST', abbr: '上次条件距今', kind: 'func', desc: '上一次条件成立距当前的周期数。\n用法:\n BARSLAST(条件)\n 数据起点起从未成立返回 0。\n例:\n BARSLAST(CLOSE/REF(CLOSE,1)>=1.1)' },
  { name: 'BARSCOUNT', abbr: '有效数据周期数', kind: 'func', desc: '第一个有效数据到当前的周期数。\n用法:\n BARSCOUNT(X)\n 返回 X 从上市/数据起点起累计有效值个数。\n注意:判断范围为指标计算时公式使用的数据。' },
  { name: 'BARSSINCEN', abbr: '周期内首次距今', kind: 'func', desc: 'N 周期内第一次条件成立到当前的周期数。\n用法:\n BARSSINCEN(条件,N)\n N 周期内从未成立返回 0。' },
  { name: 'BARSSINCE', abbr: '最早成立距今', kind: 'func', desc: '数据起点起条件第一次成立到当前的周期数。\n用法:\n BARSSINCE(条件)\n 与 BARSLAST(最近一次)相对；从未成立返回 0。' },
  { name: 'FILTER', abbr: '信号过滤', kind: 'func', desc: '过滤连续触发信号。\n用法:\n FILTER(条件,N)\n 条件成立输出 1 后，其后 N-1 个周期抑制不再输出；距本次触发≥N 个周期后若条件再成立才再次输出。\n例:\n FILTER(CROSS(MA(CLOSE,5),MA(CLOSE,20)),5)' },
  { name: 'SMA', abbr: '通达信递归均线', kind: 'func', desc: '通达信递归加权均线（不是简单平均 MA）。\n用法:\n SMA(X,N,M)\n Y=(M·X+(N-M)·Y前)/N；N 平滑周期、M 权重(1≤M≤N，M 越小越平滑)。\n例:\n SMA(CLOSE,5,1)' },
  { name: 'HHVBARS', abbr: '距N周期高点', kind: 'func', desc: '距 N 周期内最高值所在位置的周期数（含当日，当日最高→0）。\n用法:\n HHVBARS(X,N)\n 多日同为最高取最近一日。' },
  { name: 'LLVBARS', abbr: '距N周期低点', kind: 'func', desc: '距 N 周期内最低值所在位置的周期数（含当日，当日最低→0）。\n用法:\n LLVBARS(X,N)' },

  // ---------------- 注：绘图/颜色类（STICKLINE/DRAWICON/COLORRED...）不生成因子，未列入手册 ----------------
]

/** 核心行情字段：**固定排在最前 6 个**（用户 2026-09-13 定稿；即常说的 c / h / l / o / v / amount）。 */
const CORE_FIELDS = ['CLOSE', 'HIGH', 'LOW', 'OPEN', 'VOL', 'AMOUNT'] as const

/** 按名字升序（直接用字符串比较 = ASCII 序：大小写、下划线口径稳定，不受浏览器 locale 影响）。 */
const byNameAsc = (a: HandbookEntry, b: HandbookEntry) =>
  a.name < b.name ? -1 : a.name > b.name ? 1 : 0

/** 手册**展示顺序**（v1.19.20，用户要求「除了 c/h/l/o/v/amount，其他的都按英文字母排序」）：
 *  ① 前 6 个 = 核心行情字段（`CORE_FIELDS`，按上表顺序固定）；
 *  ② **其余全部条目**（字段 **+** 函数，如 L2_AMO / EMA / HHV / MA …）按**英文字母 A→Z 升序**。
 *  ⇒ 好处：以后新增条目**不用**手动插到正确位置，显示顺序自动就对（也不会再被随手追加打乱）。
 *  例：`ABS → BARSCOUNT → … → EMA → EMA_TDX → … → HHV → HHVBARS → … → L2_AMO → LLV → … → VWAP → WMA`
 */
function buildHandbook(raw: HandbookEntry[]): HandbookEntry[] {
  const isCore = new Set<string>(CORE_FIELDS)
  const head = CORE_FIELDS.map((n) => raw.find((e) => e.name === n)).filter(
    (e): e is HandbookEntry => !!e,
  )
  return [...head, ...raw.filter((e) => !isCore.has(e.name)).sort(byNameAsc)]
}

export const FORMULA_HANDBOOK: HandbookEntry[] = buildHandbook(RAW_HANDBOOK)

// 搜索过滤
export function filterHandbook(kw: string): HandbookEntry[] {
  const k = kw.trim().toLowerCase()
  if (!k) return FORMULA_HANDBOOK
  return FORMULA_HANDBOOK.filter(
    (e) => e.name.toLowerCase().includes(k) || e.abbr.includes(kw.trim()),
  )
}
