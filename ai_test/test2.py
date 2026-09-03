import os
import qlib
from qlib.constant import REG_CN
from qlib.data import D

qlib.init(provider_uri=os.path.expanduser(r"~/.qlib/qlib_data/cn_data"), region=REG_CN)

# 1. 交易日历
cal = D.calendar(start_time="2024-01-01", end_time="2024-02-01")
print(f"交易日数量:{len(cal)}")
print(cal[:5])

# 2. 股票列表 —— 注意：新版 qlib 中 D.instruments() 返回的是 dict，
#    需要用 D.list_instruments() 转成股票代码列表
inst = D.list_instruments(D.instruments(market="csi300"), as_list=True)
print(f"\n沪深300成分股数量: {len(inst)}")
print("前10只股票代码：", inst[:10])

# 3. 查询行情 —— 注意：cn_data 数据的代码格式是 "sz000001"/"sh600000"/"bj430017"，
#    不是 "000001.SZ"！"000001.SZ" 会查不到数据（返回空表）
df = D.features(["sz000001"], ["$close"], start_time="2024-01-01", end_time="2024-02-01")
print("\n查询结果（sz000001 平安银行 2024-01 收盘价）：")
print(df)
