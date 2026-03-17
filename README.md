# SmartRecommend - MoviePilot AI 智能推荐插件

基于 Emby 观看历史和热播数据，使用 AI 生成个性化推荐。

## 功能特性

- 🤖 **LLM 支持** - 支持 OpenAI、GLM、DeepSeek、Ollama 等多种大模型
- 📊 **观看分析** - 从 Emby 获取观看历史，分析用户偏好
- 🔥 **热播获取** - 实时获取 TMDB 热播电影/剧集
- 🎯 **智能推荐** - AI 综合分析，生成个性化推荐
- 📺 **仪表盘展示** - 在 MP Dashboard 显示推荐卡片
- ⚡ **一键订阅** - 推荐结果可直接订阅

## 安装

1. 在 MoviePilot 中添加插件市场源：
   ```
   https://github.com/pidanking/SmartRecommend
   ```

2. 在插件市场搜索 "AI智能推荐" 或 "SmartRecommend"

3. 点击安装并配置

## 配置说明

### LLM 配置
- **Provider**: 选择 LLM 提供商（OpenAI/GLM/DeepSeek/Ollama）
- **API Key**: 大模型 API 密钥
- **Base URL**: API 地址（OpenAI 兼容格式）
- **Model**: 模型名称

### Emby 配置
- **URL**: Emby 服务器地址
- **API Key**: Emby API 密钥
- **User ID**: 用户 ID（可选，留空自动获取）

### TMDB 配置
- **API Key**: TMDB API 密钥（可选，默认使用 MP 设置）

## 作者

- **pidanking**

## 许可证

MIT License
