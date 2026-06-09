# 考研 Bot 部署指南

## 环境要求

| 项目 | 要求 |
|------|------|
| 服务器 | 2核2G 及以上 |
| 系统 | Ubuntu 20+（推荐 22.04） |
| Python | 3.10+ |
| 面板 | 1Panel（可选） |

---

## 一、上传项目文件

将 `prod/` 目录下所有文件上传到服务器 `/root/prod/`（通过 1Panel 文件管理器 / SCP / SFTP）。

目录结构：
```
/root/prod/
├── main.py              # 入口文件
├── init.sql             # 数据库建表脚本
├── requirements.txt     # Python 依赖
├── .env                 # 环境变量（API Key 等）
├── data/                # 运行时数据（自动生成）
├── personas/            # 角色配置
└── plugins/shore/       # 业务代码
    ├── core/            # 核心模块
    └── handlers/        # 命令处理器
```

---

## 二、安装 Python 环境

```bash
apt update && apt install -y python3 python3-venv python3-pip
cd /root/prod
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p data
```

---

## 三、配置 .env

确认 `/root/prod/.env` 中以下配置正确：

```env
# NoneBot
HOST=0.0.0.0
PORT=8080
DRIVER=~fastapi

# Gemini API（通用模型）
GEMINI_API_KEY=你的key
GEMINI_BASE_URL=https://fast.poloapi.com
GEMINI_MODEL=gemini-3-flash-preview

# Claude API（计划生成）
POLOAI_API_KEY=你的key
POLOAI_BASE_URL=https://fast.poloai.top

# 研招网爬虫账号
CHSI_USERNAME=你的账号
CHSI_PASSWORD=你的密码

# Tavily 搜索
TAVILY_API_KEY=你的key
```

---

## 四、安装 NapCat

```bash
curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh \
  && bash napcat.sh --docker n --cli y
```

---

## 五、启动服务

### 1. 启动 Bot
```bash
cd /root/prod
source .venv/bin/activate
nohup python main.py > data/bot.log 2>&1 &
```

### 2. 启动 NapCat
```bash
napcat start 3675472814
```

首次需要扫码登录 bot QQ 号。

### 3. 配置 NapCat 反向 WS
打开 NapCat WebUI（`http://服务器IP:6099`）：
- 网络配置 → 添加 → **反向 WebSocket**
- 地址：`ws://127.0.0.1:8080/onebot/v11/ws`

---

## 六、常用运维命令

```bash
# 查看 Bot 日志
tail -f /root/prod/data/bot.log

# 停止 Bot
kill $(cat /root/prod/data/bot.pid 2>/dev/null) 2>/dev/null
# 或
ps aux | grep main.py | grep -v grep | awk '{print $2}' | xargs kill

# 重启 Bot
cd /root/prod && source .venv/bin/activate
nohup python main.py > data/bot.log 2>&1 &

# NapCat 管理
napcat start 3675472814    # 启动
napcat stop                # 停止
napcat status              # 查看状态

# 查看端口占用
ss -tlnp | grep 8080
```

---

## 七、更新代码

从本地上传更新文件后：
```bash
# 1. 停止 Bot
ps aux | grep main.py | grep -v grep | awk '{print $2}' | xargs kill

# 2. 重新启动
cd /root/prod && source .venv/bin/activate
nohup python main.py > data/bot.log 2>&1 &
```

---

## 注意事项

- `.env` 包含敏感信息，不要上传到公开仓库
- `data/kaoyan.db` 是用户数据库，注意备份
- NapCat 需要保持运行，建议用 `systemd` 管理
- Bot 和 NapCat 都在 `127.0.0.1` 通信，不需要开外网端口（除了 NapCat WebUI 6099）
