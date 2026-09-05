// 公式函数手册数据：函数/字段 + 中文缩写 + 详细说明
// 双击行时插入：函数 → `名称(`；字段 → `名称`
export interface HandbookEntry {
  name: string // 函数名或字段名（插入用，翻译层写法）
  abbr: string // 中文缩写
  kind: 'func' | 'field'
  desc: string // 详细说明（用法/注意）
}

export const FORMULA_HANDBOOK: HandbookEntry[] = [
  // ---------------- 行情字段 ----------------
  { name: 'CLOSE', abbr: '收盘价', kind: 'field', desc: '收盘价。\n用法:\n CLOSE 或 C\n X:=CLOSE;' },
  { name: 'HIGH', abbr: '最高价', kind: 'field', desc: '当日最高价。\n用法:\n HIGH 或 H' },
  { name: 'LOW', abbr: '最低价', kind: 'field', desc: '当日最低价。\n用法:\n LOW 或 L' },
  { name: 'OPEN', abbr: '开盘价', kind: 'field', desc: '当日开盘价。\n用法:\n OPEN 或 O' },
  { name: 'VOL', abbr: '成交量', kind: 'field', desc: '成交量（手）。\n用法:\n VOL 或 V\n注:数据里停牌日无成交为 NaN。' },
  { name: 'AMOUNT', abbr: '成交额', kind: 'field', desc: '成交金额（元）。\n用法:\n AMOUNT' },
  { name: 'VWAP', abbr: '均价', kind: 'field', desc: '成交均价（当日总成交额/总量）。\n用法:\n VWAP' },
  { name: 'TURNOVERRATE', abbr: '换手率', kind: 'field', desc: '换手率（%）。\n用法:\n TURNOVERRATE' },
  { name: 'MARKET_CAP', abbr: '总市值', kind: 'field', desc: '总市值（元）。\n用法:\n MARKET_CAP\n注:含停牌日亦有值，需注意停牌对齐。' },

  // ---------------- 资金流字段（moneyflow，金额=万元/量=手/占比=%） ----------------
  { name: 'L2_AMO', abbr: '资金净额', kind: 'func', desc: '资金流档位金额函数。\n用法:\n L2_AMO(n[,b|s])\n n=0主力/1超大单/2大单/3中单/4小单；b=买入/s=卖出。\n L2_AMO(0)       主力净流入额(万元)\n L2_AMO(0,b)     主力买入额\n L2_AMO(0,s)     主力卖出额\n注意:L2_AMO(n,b)-L2_AMO(n,s)=L2_AMO(n)。' },
  { name: 'L2_PCT', abbr: '资金占比', kind: 'func', desc: '资金流档位占比函数（分母=当日总成交额）。\n用法:\n L2_PCT(n[,b|s])\n L2_PCT(0)       主力净流入占比(%)\n L2_PCT(1,b)     超大单买入占比' },
  { name: 'L2_VOL', abbr: '资金量', kind: 'func', desc: '资金流档位量函数（手）。\n用法:\n L2_VOL(n[,b|s])\n L2_VOL(0)       主力净流入量(手，可为负)\n L2_VOL(2,b)     大单买入量\n注意:数据源为 moneyflow3 买卖量(_bq/_sq)。' },

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
  { name: 'CROSS', abbr: '上穿', kind: 'func', desc: '上穿信号。\n用法:\n CROSS(A,B)\n A 从下向上穿过 B 的时刻为真(=前一日 A<=B 且当日 A>B)。\n例:\n CROSS(MA(CLOSE,5),MA(CLOSE,20))' },
  { name: 'BETWEEN', abbr: '介于区间', kind: 'func', desc: 'X 是否介于 A、B 之间。\n用法:\n BETWEEN(X,A,B)\n 含边界；A、B 大小任意(内部取 min/max)。\n条件成立=1，否则=0；X 停牌(NaN)时输出 NaN。\n例:\n BETWEEN(CLOSE,LLV(LOW,20),HHV(HIGH,20))' },
  { name: 'SGN', abbr: '取符号', kind: 'func', desc: '取符号。\n用法:\n SGN(X)（别名 SIGN）\n X>0→1，X<0→-1，X=0→0。' },
  { name: 'INT', abbr: '向零取整', kind: 'func', desc: '向零方向截断取整（返回 X 的整数部分）。\n用法:\n INT(X)\n 3.7→3，-3.7→-3（注意:不是向下取整，负数方向是向零）。' },


  // ---------------- 状态/周期函数（外挂算子） ----------------
  { name: 'BARSLAST', abbr: '上次条件距今', kind: 'func', desc: '上一次条件成立距当前的周期数。\n用法:\n BARSLAST(条件)\n 数据起点起从未成立返回 0。\n例:\n BARSLAST(CLOSE/REF(CLOSE,1)>=1.1)' },
  { name: 'BARSCOUNT', abbr: '有效数据周期数', kind: 'func', desc: '第一个有效数据到当前的周期数。\n用法:\n BARSCOUNT(X)\n 返回 X 从上市/数据起点起累计有效值个数。\n注意:判断范围为指标计算时公式使用的数据。' },
  { name: 'BARSSINCEN', abbr: '周期内首次距今', kind: 'func', desc: 'N 周期内第一次条件成立到当前的周期数。\n用法:\n BARSSINCEN(条件,N)\n N 周期内从未成立返回 0。' },

  // ---------------- 注：绘图/颜色类（STICKLINE/DRAWICON/COLORRED...）不生成因子，未列入手册 ----------------
]

// 搜索过滤
export function filterHandbook(kw: string): HandbookEntry[] {
  const k = kw.trim().toLowerCase()
  if (!k) return FORMULA_HANDBOOK
  return FORMULA_HANDBOOK.filter(
    (e) => e.name.toLowerCase().includes(k) || e.abbr.includes(kw.trim()),
  )
}
