"""
上岸 — NoneBot2 入口
基于 OneBot v11 适配器，只处理私聊消息。
"""
from dotenv import load_dotenv
load_dotenv(override=True)  # 将 .env 变量加载到 os.environ（覆盖系统环境变量）

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 初始化 NoneBot2
nonebot.init()

# 注册 OneBot v11 适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 加载上岸插件
nonebot.load_plugins("plugins/shore")

if __name__ == "__main__":
    nonebot.run()
