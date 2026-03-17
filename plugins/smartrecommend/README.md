# SmartRecommend - MoviePilot AI 智能推荐插件

基于 Emby 观看历史 + LLM 分析 + 热播数据，生成个性化影视推荐。

## 功能特点

- 🤖 **AI 智能分析**: 使用大模型分析用户观看偏好
- 📊 **仪表盘展示**: 在 MoviePilot 仪表盘中显示推荐结果
- 🎬 **多分类推荐**: 根据 Emby 分类目录，每个类目独立推荐
- 🔥 **实时热播**: 拉取 TMDB 当前热播数据
- 📱 **一键订阅**: 推荐结果可直接添加到 MoviePilot 订阅
- ⚙️ **灵活配置**: 支持多种 LLM 提供商，推荐数量可调

## 安装方法

### 方法 1: 插件市场安装

1. 进入 MoviePilot 设置 -> 插件 -> 插件市场
2. 添加私有仓库: `https://github.com/你的用户名/SmartRecommend`
3. 搜索 "AI智能推荐" 并安装

### 方法 2: 手动安装

1. 下载本仓库代码
2. 将 `SmartRecommend` 文件夹复制到 MoviePilot 的插件目录:
   ```
   /config/plugins/SmartRecommend/
   ```
3. 重启 MoviePilot

## 配置说明

### LLM 配置

支持以下 LLM 提供商:

| 提供商 | Base URL | 模型示例 |
|--------|----------|----------|
| OpenAI | `https://api.openai.com/v1` | gpt-4o-mini, gpt-4 |
| GLM (智谱) | `https://open.bigmodel.cn/api/paas/v4` | glm-4, glm-4-flash |
| DeepSeek | `https://api.deepseek.com/v1` | deepseek-chat |
| 本地 Ollama | `http://localhost:11434/v1` | llama3, qwen2 |

### Emby 配置

- **Emby 地址**: 如 `http://192.168.1.100:8096` 或 `https://emby.example.com`
- **API Key**: 在 Emby 后台 -> 高级 -> API 密钥 中生成
- **用户ID**: 可选，留空自动获取第一个用户

### 推荐设置

- **每类推荐数量**: 3-10 部，默认 5 部
- **自动刷新**: 开启后按设定周期自动更新推荐
- **刷新周期**: Cron 表达式，默认每天早上 8 点

## 使用说明

1. 安装并启用插件
2. 在插件设置中配置 LLM 和 Emby 信息
3. 点击"立即刷新一次"生成首次推荐
4. 在 MoviePilot 仪表盘中查看推荐结果
5. 点击推荐项的 + 按钮可直接订阅

## 支持的分类

根据你的 Emby 媒体库分类，自动为以下类目生成推荐:

- 电视剧: 国产剧、韩剧、欧美剧、日剧
- 电影: 欧美电影、华语电影、日韩电影、动画电影
- 动漫: 国漫、日漫、欧美动漫、其他动漫
- 其他: 综艺、纪录片、演唱会

## 命令

在 Telegram/微信等渠道发送:
- `/recommend` - 手动刷新推荐

## 技术架构

```
MoviePilot Dashboard
       │
       ▼
┌─────────────────┐
│ SmartRecommend  │
│    插件          │
└────────┬────────┘
         │
    ┌────┴────┬──────────┐
    ▼         ▼          ▼
┌──────┐  ┌──────┐  ┌────────┐
│ Emby │  │ TMDB │  │  LLM   │
│ 历史 │  │ 热播 │  │ 分析   │
└──────┘  └──────┘  └────────┘
```

## 更新日志

### v1.0.0
- 初始版本发布
- 支持 LLM 智能分析
- 支持多分类推荐
- 支持仪表盘展示

## 作者

- 作者: 皮蛋哥
- 项目: https://github.com/pidan2026/SmartRecommend

## 许可证

MIT License
