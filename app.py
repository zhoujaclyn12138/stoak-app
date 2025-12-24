import streamlit as st
import pandas as pd
import time
import re
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from openai import OpenAI
from utils import load_config, save_config, convert_code
import data_service as ds
from concurrent.futures import ThreadPoolExecutor

# ================= 1. 初始化 =================
st.set_page_config(page_title="AI 量化极速版", layout="wide", page_icon="📡")
ds.init_baostock()
config = load_config()

# 读取阈值
TH_SHORT = config["thresholds"]["short"]
TH_BAND = config["thresholds"]["band"]
TH_MARKET = config["thresholds"]["market"]

try: stock_map, search_list = ds.get_stock_basic_cached()
except: stock_map, search_list = {}, []

STRATEGIES = {
    "⚡ 短线": f"监控异动，涨跌 > ±{TH_SHORT}%",
    "🌊 波段": f"监控均线，跌破MA20 > {TH_BAND}%",
    "⚓ 大盘": f"监控估值，相对指数低估 > {TH_MARKET}%"
}

SECTOR_MAP = {
    "酒类/消费": "512690.SS", "半导体/芯片": "512480.SS", "新能源": "516160.SS",
    "光伏": "515790.SS", "医药": "512170.SS", "证券": "512880.SS",
    "银行": "512800.SS", "红利": "510880.SS", "中概互联": "513050.SS"
}

if "messages" not in st.session_state: st.session_state.messages = []

# ================= 2. 侧边栏 =================
with st.sidebar:
    st.header("🎮 控制台")
    with st.expander("🤖 AI 设置"):
        user_key = st.text_input("DeepSeek Key", value=config.get("api_key", ""), type="password")
        user_url = st.text_input("Base URL", value=config.get("base_url", "https://api.deepseek.com"))
        if st.button("💾 保存 AI 配置"):
            config["api_key"] = user_key; config["base_url"] = user_url
            save_config(config); st.success("已保存"); time.sleep(0.5); st.rerun()

    monitor_mode = st.toggle("⚡ 开启极速盯盘 (1s)", value=False)
    if st.button("🔄 刷新全站"): st.rerun()
    
    with st.expander("⚙️ 阈值设置"):
        new_short = st.number_input("⚡ 短线(%)", value=float(TH_SHORT), step=0.5)
        new_band = st.number_input("🌊 波段(%)", value=float(TH_BAND), step=0.5, max_value=0.0)
        new_market = st.number_input("⚓ 大盘(%)", value=float(TH_MARKET), step=0.5, max_value=0.0)
        if st.button("💾 保存参数"):
            config["thresholds"]["short"] = new_short; config["thresholds"]["band"] = new_band; config["thresholds"]["market"] = new_market
            save_config(config); st.rerun()

    st.divider()
    st.subheader("➕ 添加自选/持仓")
    s = st.selectbox("搜股票(含港股)", [""]+search_list)
    selected_strategy = st.radio("监控策略", list(STRATEGIES.keys()), index=1)
    
    if s:
        c = s.split("|")[1].strip()
        c1, c2 = st.columns(2)
        if c1.button(f"关注 {c}"): 
            config["watch_list"][c] = {"strategy": selected_strategy}
            save_config(config); st.success("已添加"); time.sleep(0.5); st.rerun()
        if c2.button(f"加持仓"): 
            config["holding_list"][c] = {"cost":0.0, "profit_target": 20.0, "loss_limit": -10.0, "support": 0.0}
            save_config(config); st.rerun()

    with st.expander("📂 批量导入"):
        bulk_input = st.text_area("粘贴代码 (空格/逗号)", height=70)
        if st.button("📥 一键导入"):
            raw_codes = re.split(r'[,\s\n]+', bulk_input.strip())
            lookup = {}
            for item in search_list:
                parts = item.split("|"); full=parts[1].strip(); lookup[full]=full; lookup[full.split(".")[0]]=full
            count = 0
            for rc in raw_codes:
                if not rc: continue
                clean = re.sub(r'(sh|sz|ss|hk)', '', rc.lower())
                target = lookup.get(rc) or lookup.get(clean)
                if target: config["watch_list"][target] = {"strategy": selected_strategy}; count += 1
            if count > 0: save_config(config); st.success(f"导入 {count} 只"); time.sleep(1); st.rerun()

    st.divider()
    st.subheader("🛡️ 持仓风控设置")
    hold_c = list(config["holding_list"].keys())
    if hold_c:
        sel = st.selectbox("选择持仓股", [f"{stock_map.get(x,x)}|{x}" for x in hold_c])
        if sel:
            sc = sel.split("|")[1]
            info = config["holding_list"][sc]
            st.caption(f"当前设置: {sc}")
            c_cost = st.number_input(f"持仓成本", value=float(info.get("cost", 0.0)), step=0.1)
            c_support = st.number_input(f"关键支撑位(价格)", value=float(info.get("support", 0.0)), step=0.1, help="跌破此价格报警卖出")
            c1, c2 = st.columns(2)
            c_profit = c1.number_input(f"止盈阈值(%)", value=float(info.get("profit_target", 20.0)), step=1.0)
            c_loss = c2.number_input(f"止损回撤(%)", value=float(info.get("loss_limit", -10.0)), step=1.0)
            if st.button("💾 保存风控计划"): 
                config["holding_list"][sc].update({"cost": c_cost, "profit_target": c_profit, "loss_limit": c_loss, "support": c_support})
                save_config(config); st.success("计划已更新"); time.sleep(0.5); st.rerun()
            if st.button("🗑️ 删除持仓"): del config["holding_list"][sc]; save_config(config); st.rerun()

# ================= 3. 顶部指数 =================
cols = st.columns(3)
idxs = [("上证指数","000001.SS"), ("创业板指","399006.SZ"), ("恒生科技","03032.HK")]
for col, (n, c) in zip(cols, idxs):
    # data_service 更新后返回4个值，用 _ 忽略成交量
    p, _, chg, _ = ds.get_realtime_sina(c)
    col.metric(n, f"{p:.2f}", f"{chg:.2f}%")

# ================= 4. 主功能区 =================
tabs = st.tabs(["🎯 策略/风控扫描", "🌊 板块", "🛡️ 持仓监控", "🔥 情报", "🤖 AI 顾问"])
if 'analysis_res' not in st.session_state: st.session_state.analysis_res = {}

# Tab 1: 策略 + 风控扫描
with tabs[0]:
    c1, c2 = st.columns([1, 4])
    with c1:
        st.markdown("##### 🚀 智能扫描")
        if st.button("开始全面扫描", type="primary"):
            st.session_state.analysis_res = {}
            progress = st.progress(0)
            all_codes = list(set(list(config["watch_list"].keys()) + list(config["holding_list"].keys())))
            alerts = [] 
            
            for i, code in enumerate(all_codes):
                # 获取实时价格和【成交量】
                curr_p, _, chg, curr_vol = ds.get_realtime_sina(code)
                name = stock_map.get(code, code)
                
                if curr_p > 0:
                    metrics = ds.calculate_advanced_metrics(code, curr_p, curr_vol)
                    if metrics: st.session_state.analysis_res[code] = metrics

                    # 策略逻辑
                    if code in config["watch_list"]:
                        strategy = config["watch_list"][code].get("strategy", "🌊 波段")
                        if strategy == "⚡ 短线" and abs(chg) > TH_SHORT: alerts.append(f"⚡ {name} 异动 {chg:.2f}%")
                        elif strategy == "🌊 波段" and metrics['MA20偏'] < TH_BAND: alerts.append(f"🌊 {name} 击穿MA20 {metrics['MA20偏']:.1f}%")
                        elif strategy == "⚓ 大盘" and metrics['大盘折溢价'] < TH_MARKET: alerts.append(f"⚓ {name} 低估 {-metrics['大盘折溢价']:.1f}%")

                    # 风控逻辑
                    if code in config["holding_list"]:
                        info = config["holding_list"][code]
                        cost, support = info.get("cost", 0), info.get("support", 0)
                        p_target, l_limit = info.get("profit_target", 999), info.get("loss_limit", -999)
                        curr_profit_pct = (curr_p - cost)/cost*100 if cost > 0 else 0
                        
                        if support > 0 and curr_p < support: alerts.append(f"🚨 {name} 跌破支撑位! {curr_p}<{support}")
                        if cost > 0 and curr_profit_pct >= p_target: alerts.append(f"💰 {name} 止盈达标! {curr_profit_pct:.1f}%")
                        if cost > 0 and curr_profit_pct <= l_limit: alerts.append(f"😭 {name} 触及止损! {curr_profit_pct:.1f}%")

                progress.progress((i + 1) / len(all_codes))
            progress.empty()
            if alerts: 
                for a in alerts: st.toast(a, icon="🔔")
            else: st.success("扫描完成，持仓安全", icon="✅")

    watch_codes = list(config["watch_list"].keys())
    market_placeholder = st.empty()

    def render_table():
        if not watch_codes: return pd.DataFrame()
        base_data = ds.get_batch_realtime_sina(watch_codes)
        if not base_data: return pd.DataFrame()
        df = pd.DataFrame(base_data)
        df["名称"] = df["代码"].apply(lambda x: f"{stock_map.get(x, x)} ({x})")
        df["策略"] = df["代码"].apply(lambda x: config["watch_list"][x].get("strategy", "🌊"))
        
        if st.session_state.analysis_res:
            def get_metric(code, key): return st.session_state.analysis_res.get(code, {}).get(key, 0)
            df["MA10%"] = df["代码"].apply(lambda x: get_metric(x, "MA10偏"))
            df["MA20%"] = df["代码"].apply(lambda x: get_metric(x, "MA20偏"))
            df["MA30%"] = df["代码"].apply(lambda x: get_metric(x, "MA30偏"))
            df["MA60%"] = df["代码"].apply(lambda x: get_metric(x, "MA60偏"))
            df["量比"] = df["代码"].apply(lambda x: get_metric(x, "量比"))
            
            def get_signal(row):
                strat = row['策略']
                chg = row['涨跌%']
                if strat == "⚡ 短线" and abs(chg) > TH_SHORT: return "⚡ 异动"
                if strat == "🌊 波段" and row.get('MA20%', 0) < TH_BAND: return "🌊 机会"
                return ""
            df["信号"] = df.apply(get_signal, axis=1)
            # 列顺序
            cols = ["名称", "现价", "涨跌%", "量比", "MA10%", "MA20%", "MA30%", "MA60%", "信号"]
        else: cols = ["名称", "现价", "涨跌%", "策略"]
        return df[cols]

    with market_placeholder.container():
        df = render_table()
        if not df.empty:
            fmt = {"现价":"{:.2f}", "涨跌%":"{:.2f}%"}
            
            # === 修复核心：安全地构建样式 ===
            styler = df.style.format(fmt).map(lambda x: 'color:#ff4d4d' if x>0 else 'color:#2ecc71', subset=['涨跌%'])
            
            # 只有当列存在时，才添加对应的格式和样式，避免报错
            if "MA10%" in df.columns:
                styler = styler.format({
                    "MA10%":"{:.1f}%", "MA20%":"{:.1f}%", "MA30%":"{:.1f}%", "MA60%":"{:.1f}%", "量比":"{:.2f}"
                })
                # 量比高亮
                styler = styler.map(lambda x: 'color:#ff4d4d; font-weight:bold' if float(x)>1.5 else '', subset=['量比'])
                # 均线红绿
                styler = styler.map(lambda x: 'color:#ff4d4d' if float(x)>0 else 'color:#2ecc71', subset=['MA10%','MA20%','MA30%','MA60%'])

            st.dataframe(styler, width='stretch')

# Tab 2: 板块
with tabs[1]:
    if st.button("🚀 扫描板块"):
        res = []
        for n, c in SECTOR_MAP.items():
            _, _, chg, _ = ds.get_realtime_sina(c)
            res.append({"板块": n, "涨跌幅": chg})
        st.plotly_chart(px.bar(pd.DataFrame(res).sort_values("涨跌幅"), x="涨跌幅", y="板块", orientation='h', color="涨跌幅", color_continuous_scale=["#00FF00", "#FF0000"]), width='stretch')

# Tab 3: 持仓监控
with tabs[2]:
    h_res = []
    st.info("🛡️ 此处仅监控价格与预设阈值的关系，不显示具体持有金额。")
    for c, info in config["holding_list"].items():
        p, _, chg, _ = ds.get_realtime_sina(c)
        if p>0:
            cost = info.get('cost', 0)
            prof_pct = (p-cost)/cost*100 if cost>0 else 0
            target, loss_lim, support = info.get("profit_target", 20), info.get("loss_limit", -10), info.get("support", 0)
            status = "🟢 持有"
            if support > 0 and p < support: status = "🚨 破位卖出"
            elif prof_pct >= target: status = "💰 止盈卖出"
            elif prof_pct <= loss_lim: status = "😭 止损卖出"
            h_res.append({"名称": f"{stock_map.get(c,c)}", "代码": c, "现价": p, "成本": cost, "当前盈亏%": prof_pct, "止盈目标%": target, "止损回撤%": loss_lim, "支撑位": support, "状态": status})
    if h_res:
        df_h = pd.DataFrame(h_res)
        def highlight_status(val):
            if "🚨" in val or "😭" in val: return 'color: white; background-color: #ff4d4d; font-weight: bold'
            if "💰" in val: return 'color: white; background-color: #ff9f43; font-weight: bold'
            return 'color: #2ecc71; font-weight: bold'
        st.dataframe(df_h.style.format({"现价":"{:.2f}", "成本":"{:.2f}", "支撑位":"{:.2f}", "当前盈亏%":"{:.2f}%", "止盈目标%":"{:.1f}%", "止损回撤%":"{:.1f}%"}).map(lambda x:'color:red' if x>0 else 'color:green', subset=['当前盈亏%']).map(highlight_status, subset=['状态']), width='stretch')
    else: st.write("暂无持仓，请在侧边栏添加。")

# Tab 4: 情报
with tabs[3]:
    c1, c2 = st.columns([1, 6])
    if c1.button("🌐 抓取新闻"):
        news = ds.get_web_news()
        if news:
            new_text = "\n".join(news)
            config["system_news"] = new_text
            save_config(config)
            st.success("更新成功"); time.sleep(0.5); st.rerun()
    current_news = st.text_area("编辑情报 (AI素材)", value=config.get("system_news", ""), height=400, key="news_edit_area")
    if current_news != config.get("system_news", ""):
        config["system_news"] = current_news
        save_config(config)

# Tab 5: AI 顾问
with tabs[4]:
    st.markdown("#### 🤖 AI 投资顾问")
    def build_context():
        ctx = "【用户持仓风控数据】\n"
        for c, info in config["holding_list"].items():
            p, _, _, _ = ds.get_realtime_sina(c)
            name = stock_map.get(c, c)
            cost = info.get('cost', 0)
            prof_pct = (p - cost) / cost * 100 if cost > 0 else 0
            ctx += f"- {name}: 现价{p}, 成本{cost}, 盈亏{prof_pct:.2f}%, 支撑位{info.get('support')}\n"
        ctx += f"\n【市场情报】\n{config.get('system_news', '无')}" 
        return ctx
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])
    if prompt := st.chat_input("问问AI关于持仓的建议..."):
        if not config["api_key"]: st.error("请先填写 API Key")
        else:
            st.chat_message("user").markdown(prompt)
            st.session_state.messages.append({"role": "user", "content": prompt})
            context_data = build_context()
            system_prompt = f"你是一个量化风控助手。依据：\n{context_data}"
            with st.chat_message("assistant"):
                stream = OpenAI(api_key=config["api_key"], base_url=config["base_url"]).chat.completions.create(model="deepseek-chat", messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}], stream=True)
                response = st.write_stream(stream)
            st.session_state.messages.append({"role": "assistant", "content": response})