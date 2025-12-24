import baostock as bs
import pandas as pd
import requests
import datetime
import streamlit as st
import numpy as np
from utils import convert_code 

# ================= 1. Baostock 基础 =================
@st.cache_resource
def init_baostock():
    bs.logout()
    lg = bs.login()
    return lg

@st.cache_data(ttl=3600*4)
def get_stock_basic_cached():
    try:
        if pd.to_datetime(datetime.datetime.now()).minute % 15 == 0: bs.login()
    except: pass
    
    stock_map = {}
    search_list = []

    # 1. A股
    try:
        rs = bs.query_stock_basic()
        data = []
        while (rs.error_code == '0') & rs.next(): data.append(rs.get_row_data())
        for r in data:
            raw_code = r[0] 
            name = r[1]
            if not raw_code: continue
            if raw_code.startswith('sh.'): clean_code = raw_code.replace('sh.', '') + '.SS'
            elif raw_code.startswith('sz.'): clean_code = raw_code.replace('sz.', '') + '.SZ'
            else: clean_code = raw_code
            stock_map[clean_code] = name
            search_list.append(f"{name} | {clean_code}")
    except: pass

    # 2. 港股补充
    hk_stocks = [
        ("恒生科技指数", "03032.HK"), ("腾讯控股", "00700.HK"), ("阿里巴巴", "09988.HK"),
        ("美团-W", "03690.HK"), ("小米集团-W", "01810.HK"), ("快手-W", "01024.HK"),
        ("京东集团-SW", "09618.HK"), ("百度集团-SW", "09888.HK"), ("网易-S", "09999.HK"),
        ("中芯国际", "00981.HK"), ("理想汽车-W", "02015.HK"), ("小鹏汽车-W", "09868.HK"),
        ("蔚来-SW", "09866.HK"), ("哔哩哔哩-W", "09626.HK"), ("商汤-W", "00020.HK")
    ]
    for name, code in hk_stocks:
        stock_map[code] = name
        search_list.insert(0, f"{name} | {code}")

    return stock_map, search_list

def get_history_data(symbol, days=730):
    if "HK" in symbol: return pd.DataFrame() 
    try:
        bs_code = convert_code(symbol, "baostock")
        end = datetime.datetime.now().strftime("%Y-%m-%d")
        start = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(bs_code, "date,open,high,low,close,volume", 
            start_date=start, end_date=end, frequency="d", adjustflag="3")
        data = []
        while (rs.error_code == '0') & rs.next(): data.append(rs.get_row_data())
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data, columns=['date','open','high','low','close','volume'])
        df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
        # 计算全周期均线
        for ma in [10, 20, 30, 60]: df[f'MA{ma}'] = df['close'].rolling(ma).mean()
        return df
    except: return pd.DataFrame()

# ================= 2. 深度计算 (增加量比) =================

def get_belonging_index(stock_code):
    if stock_code.endswith(".SS"):
        if stock_code.startswith("688"): return "000688.SS", "科创50"
        return "000001.SS", "上证指数" 
    elif stock_code.endswith(".SZ"):
        if stock_code.startswith("30"): return "399006.SZ", "创业板指"
        return "399001.SZ", "深证成指"
    elif stock_code.endswith(".HK"):
        return "03032.HK", "恒生科技"
    return "000001.SS", "上证指数"

@st.cache_data(ttl=3600)
def get_index_history(index_code, days=730):
    return get_history_data(index_code, days=days)

def calculate_advanced_metrics(stock_code, current_price, current_vol):
    """
    计算核心指标：MA偏离度 + 量比 + 大盘折溢价
    """
    try:
        # 1. 均线偏离
        df_stock = get_history_data(stock_code, days=730)
        idx_code, idx_name = get_belonging_index(stock_code)
        
        # 港股或无数据时的默认值
        if df_stock.empty: 
            return {
                "MA10偏": 0, "MA20偏": 0, "MA30偏": 0, "MA60偏": 0, "量比": 0,
                "大盘折溢价": 0, "所属指数": idx_name, "指数代码": idx_code, "History": pd.DataFrame()
            }

        last = df_stock.iloc[-1]
        ma_devs = {}
        for ma in [10, 20, 30, 60]:
            if last[f'MA{ma}'] > 0:
                ma_devs[f'MA{ma}'] = (current_price - last[f'MA{ma}']) / last[f'MA{ma}'] * 100
            else: ma_devs[f'MA{ma}'] = 0

        # 2. 量比计算 (实时成交量 / (过去5日均量 / 240 * 当前交易分钟数))
        vol_ratio = 0.0
        if current_vol > 0:
            avg_vol_5 = df_stock['volume'].tail(5).mean() # 5日均量
            if avg_vol_5 > 0:
                # 计算当前交易了多少分钟
                now = datetime.datetime.now().time()
                minutes_elapsed = 0
                if now >= datetime.time(9, 30) and now <= datetime.time(11, 30):
                    minutes_elapsed = (datetime.datetime.combine(datetime.date.today(), now) - 
                                     datetime.datetime.combine(datetime.date.today(), datetime.time(9, 30))).seconds / 60
                elif now >= datetime.time(13, 0) and now <= datetime.time(15, 0):
                    minutes_elapsed = 120 + (datetime.datetime.combine(datetime.date.today(), now) - 
                                           datetime.datetime.combine(datetime.date.today(), datetime.time(13, 0))).seconds / 60
                elif now > datetime.time(15, 0):
                    minutes_elapsed = 240
                
                minutes_elapsed = max(1, minutes_elapsed) # 避免除0
                # 理论当前时刻应有的成交量 = 全天均量 * (已过时间/240)
                theoretical_vol_now = avg_vol_5 * (minutes_elapsed / 240)
                vol_ratio = current_vol / theoretical_vol_now

        # 3. 大盘折溢价
        idx_dev = 0.0
        theoretical = 0.0
        df_index = get_index_history(idx_code, days=730)
        if not df_index.empty:
            df_m = pd.merge(df_stock[['date','close']], df_index[['date','close']], on='date', suffixes=('_s','_i'))
            df_1y = df_m.tail(250).copy()
            if not df_1y.empty:
                avg_ratio = (df_1y['close_s'] / df_1y['close_i']).mean()
                curr_idx = df_index.iloc[-1]['close']
                theoretical = curr_idx * avg_ratio
                idx_dev = (current_price - theoretical) / theoretical * 100

        return {
            "MA10偏": ma_devs.get('MA10', 0),
            "MA20偏": ma_devs.get('MA20', 0),
            "MA30偏": ma_devs.get('MA30', 0),
            "MA60偏": ma_devs.get('MA60', 0),
            "量比": vol_ratio,
            "大盘折溢价": idx_dev,
            "所属指数": idx_name,
            "指数代码": idx_code,
            "History": df_stock
        }
    except: return None

# ================= 3. 实时行情 (返回成交量) =================

def parse_sina_response(code, content):
    """解析新浪行情，返回：现价, 昨收, 涨跌%, 成交量(股)"""
    if '="' not in content: return 0.0, 0.0, 0.0, 0.0
    
    # 港股 (hk00700="EngName,Name,Open,PrevClose,High,Low,Last,Diff,Pct,Bid,Ask,Turnover,Vol...")
    if code.startswith("hk"):
        try:
            val_str = content.split('="')[1].strip('";\n')
            p = val_str.split(',')
            if len(p) < 13: return 0.0, 0.0, 0.0, 0.0
            
            curr = float(p[6])
            last = float(p[3])
            chg_pct = float(p[8])
            vol = float(p[12]) # 港股成交量通常在12
            return curr, last, chg_pct, vol
        except: return 0.0, 0.0, 0.0, 0.0
        
    # A股 (sh600519="Name,Open,Prev,Price,High,Low,Bid,Ask,Vol,Amt...")
    else:
        try:
            val_str = content.split('="')[1].strip('";\n')
            p = val_str.split(',')
            if len(p) < 9: return 0.0, 0.0, 0.0, 0.0
            
            curr = float(p[3])
            last = float(p[2])
            chg_pct = (curr - last) / last * 100 if last > 0 else 0
            vol = float(p[8]) # A股成交量在8 (股数)
            return curr, last, chg_pct, vol
        except: return 0.0, 0.0, 0.0, 0.0

def get_realtime_sina(symbol):
    try:
        sina_code = convert_code(symbol, "sina")
        r = requests.get(f"http://hq.sinajs.cn/list={sina_code}", headers={'Referer': 'https://sina.com.cn'}, timeout=2)
        try: content = r.content.decode('gbk')
        except: content = r.text
        return parse_sina_response(sina_code, content)
    except: return 0.0, 0.0, 0.0, 0.0

def get_batch_realtime_sina(code_list):
    if not code_list: return []
    try:
        s_codes = [convert_code(c, "sina") for c in code_list]
        query = ",".join(s_codes)
        r = requests.get(f"http://hq.sinajs.cn/list={query}", headers={'Referer': 'https://sina.com.cn'}, timeout=3)
        try: content = r.content.decode('gbk')
        except: content = r.text
        
        res = []
        code_map = {convert_code(c, "sina"): c for c in code_list}
        
        for line in content.split('\n'):
            if '="' not in line: continue
            curr_scode = line.split('hq_str_')[-1].split('="')[0]
            
            p_price, p_last, p_chg, p_vol = parse_sina_response(curr_scode, line)
            
            origin = code_map.get(curr_scode, curr_scode)
            
            res.append({
                "代码": origin, 
                "现价": p_price, 
                "涨跌%": p_chg, 
                "成交量": p_vol
            })
        return res
    except: return []

def get_web_news():
    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=10&page=1"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        data = r.json()
        news_list = []
        for i in data["result"]["data"]:
            title = i.get('title', '')
            time_str = i.get('ctime', '')[11:16]
            if "融资" in title or "主力" in title or "龙虎榜" in title: continue 
            news_list.append(f"【{time_str}】{title}")
        return news_list
    except: return ["获取失败"]