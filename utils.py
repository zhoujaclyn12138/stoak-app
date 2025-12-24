import json
import os

CONFIG_FILE = "config.json"

def load_config():
    """加载配置，如果没有则创建默认"""
    default = {
        "api_key": "", 
        "base_url": "https://api.deepseek.com",
        "watch_list": {"600519.SS": {"strategy": "🌊 波段"}}, 
        "holding_list": {},
        # 持仓结构示例: {"600519.SS": {"cost": 100, "profit_target": 20, "loss_limit": -5, "support": 90}}
        "user_news": "", 
        "system_news": "",
        "thresholds": {
            "short": 3.0,   
            "band": -5.0,   
            "market": -8.0  
        }
    }
    
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding='utf-8') as f: json.dump(default, f)
        return default
    
    with open(CONFIG_FILE, "r", encoding='utf-8') as f:
        try:
            c = json.load(f)
        except:
            c = default
            
        # 补全缺失键值
        for k, v in default.items():
            if k not in c: c[k] = v
        if "thresholds" in c:
            for k, v in default["thresholds"].items():
                if k not in c["thresholds"]: c["thresholds"][k] = v
        
        return c

def save_config(data):
    """保存配置"""
    with open(CONFIG_FILE, "w", encoding='utf-8') as f: 
        json.dump(data, f, ensure_ascii=False, indent=4)

def convert_code(symbol, target="sina"):
    """
    股票代码格式转换
    600519.SS -> sh600519 (sina) / sh.600519 (baostock)
    00700.HK  -> hk00700 (sina)
    """
    if not symbol or "." not in symbol: return symbol
    code, exchange = symbol.split('.')
    
    # 港股处理
    if exchange == 'HK':
        if target == "sina": return f"hk{code}"
        return symbol 

    # A股处理
    prefix = 'sh' if exchange == 'SS' else 'sz'
    if target == "sina": return f"{prefix}{code}"
    elif target == "baostock": return f"{prefix}.{code}"
    return symbol