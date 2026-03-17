"""
MoviePilot AI 智能推荐插件
基于 Emby 观看历史 + LLM 分析 + 热播数据，生成个性化推荐

Author: 皮蛋哥
Version: 1.0.0
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType


class SmartRecommend(_PluginBase):
    """AI 智能推荐插件"""

    # 插件基本信息
    plugin_name = "AI智能推荐"
    plugin_desc = "基于观看历史和热播数据，使用 AI 生成个性化推荐"
    plugin_icon = "smartrecommend.png"
    plugin_version = "1.0.1"
    plugin_author = "皮蛋哥"
    author_url = "https://github.com/pidan2026"
    plugin_config_prefix = "smartrecommend_"
    plugin_order = 10
    auth_level = 1

    # 配置属性
    _enabled: bool = False
    _onlyonce: bool = False
    
    # LLM 配置
    _llm_provider: str = "openai"
    _llm_api_key: str = ""
    _llm_base_url: str = ""
    _llm_model: str = "gpt-4o-mini"
    
    # Emby 配置
    _emby_url: str = ""
    _emby_api_key: str = ""
    
    # TMDB 配置（可选，优先使用 MP 设置）
    _tmdb_api_key: str = ""
    _emby_user_id: str = ""
    
    # 推荐配置
    _recommend_count: int = 5
    _auto_refresh: bool = True
    _refresh_cron: str = "0 8 * * *"  # 每天早上8点刷新
    
    # 缓存
    _recommend_cache: dict = {}
    _last_refresh: str = ""

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        if config:
            self._enabled = config.get("enabled", False)
            self._onlyonce = config.get("onlyonce", False)
            self._llm_provider = config.get("llm_provider", "openai")
            self._llm_api_key = config.get("llm_api_key", "")
            self._llm_base_url = config.get("llm_base_url", "")
            self._llm_model = config.get("llm_model", "gpt-4o-mini")
            self._emby_url = config.get("emby_url", "")
            self._emby_api_key = config.get("emby_api_key", "")
            self._emby_user_id = config.get("emby_user_id", "")
            self._tmdb_api_key = config.get("tmdb_api_key", "")
            self._recommend_count = config.get("recommend_count", 5)
            self._auto_refresh = config.get("auto_refresh", True)
            self._refresh_cron = config.get("refresh_cron", "0 8 * * *")
            self._recommend_cache = config.get("recommend_cache", {})
            self._last_refresh = config.get("last_refresh", "")

        # 立即运行一次
        if self._onlyonce:
            self._onlyonce = False
            self.update_config({"onlyonce": False})
            self._refresh_recommendations()

    def get_state(self) -> bool:
        """获取插件启用状态"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """注册命令"""
        return [
            {
                "cmd": "/recommend",
                "event": EventType.PluginAction,
                "desc": "刷新AI推荐",
                "category": "推荐",
                "data": {"action": "refresh_recommend"}
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """注册 API 端点"""
        return [
            {
                "path": "/recommendations",
                "endpoint": self.api_get_recommendations,
                "methods": ["GET"],
                "summary": "获取推荐列表"
            },
            {
                "path": "/refresh",
                "endpoint": self.api_refresh,
                "methods": ["POST"],
                "summary": "刷新推荐"
            },
            {
                "path": "/categories",
                "endpoint": self.api_get_categories,
                "methods": ["GET"],
                "summary": "获取Emby分类"
            },
            {
                "path": "/history",
                "endpoint": self.api_get_history,
                "methods": ["GET"],
                "summary": "获取观看历史"
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """注册定时任务"""
        if not self._enabled or not self._auto_refresh:
            return []
        
        services = []
        if self._refresh_cron:
            services.append({
                "id": "SmartRecommend_refresh",
                "name": "刷新AI推荐",
                "trigger": CronTrigger.from_crontab(self._refresh_cron),
                "func": self._refresh_recommendations,
                "kwargs": {}
            })
        return services

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """配置表单"""
        llm_options = [
            {"title": "OpenAI 兼容", "value": "openai"},
            {"title": "GLM (智谱)", "value": "glm"},
            {"title": "DeepSeek", "value": "deepseek"},
            {"title": "本地 Ollama", "value": "ollama"},
        ]
        
        return [
            {
                "component": "VForm",
                "content": [
                    # 基本设置
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "auto_refresh", "label": "自动刷新"}}]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即刷新一次"}}]
                            }
                        ]
                    },
                    # LLM 配置
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "LLM 大模型配置"}}]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VSelect", "props": {"model": "llm_provider", "label": "LLM 提供商", "items": llm_options}}]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 5},
                                "content": [{"component": "VTextField", "props": {"model": "llm_base_url", "label": "API Base URL", "placeholder": "https://api.openai.com/v1"}}]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{"component": "VTextField", "props": {"model": "llm_api_key", "label": "API Key", "type": "password"}}]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{"component": "VTextField", "props": {"model": "llm_model", "label": "模型名称", "placeholder": "gpt-4o-mini / glm-4 / deepseek-chat"}}]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{"component": "VCronField", "props": {"model": "refresh_cron", "label": "刷新周期", "placeholder": "0 8 * * *"}}]
                            }
                        ]
                    },
                    # Emby 配置
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "Emby 配置"}}]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{"component": "VTextField", "props": {"model": "emby_url", "label": "Emby 地址", "placeholder": "http://192.168.1.x:8096"}}]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VTextField", "props": {"model": "emby_api_key", "label": "API Key", "type": "password"}}]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [{"component": "VTextField", "props": {"model": "emby_user_id", "label": "用户ID (可选)", "placeholder": "留空自动获取"}}]
                            }
                        ]
                    },
                    # TMDB 配置
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "TMDB 配置（可选，默认使用 MoviePilot 设置中的 TMDB API Key）"}}]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{"component": "VTextField", "props": {"model": "tmdb_api_key", "label": "TMDB API Key (可选)", "type": "password", "placeholder": "留空使用 MP 设置"}}]
                            }
                        ]
                    },
                    # 推荐配置
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "推荐设置"}}]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSlider",
                                        "props": {
                                            "model": "recommend_count",
                                            "label": "每类推荐数量",
                                            "min": 3,
                                            "max": 10,
                                            "step": 1,
                                            "thumb-label": True
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "llm_provider": "openai",
            "llm_api_key": "",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_model": "gpt-4o-mini",
            "emby_url": "",
            "emby_api_key": "",
            "emby_user_id": "",
            "tmdb_api_key": "",
            "recommend_count": 5,
            "auto_refresh": True,
            "refresh_cron": "0 8 * * *"
        }

    def get_page(self) -> List[dict]:
        """仪表盘页面"""
        recommendations = self._recommend_cache or {}
        categories = self._get_emby_categories()
        
        # 如果没有缓存，显示提示
        if not recommendations:
            return [
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [
                                {
                                    "component": "VCard",
                                    "props": {"variant": "tonal"},
                                    "content": [
                                        {
                                            "component": "VCardText",
                                            "props": {"class": "text-center py-8"},
                                            "content": [
                                                {"component": "VIcon", "props": {"size": "64", "color": "grey-lighten-1"}, "icon": "mdi-brain"},
                                                {"component": "div", "props": {"class": "text-h6 mt-4"}, "text": "AI 智能推荐"},
                                                {"component": "div", "props": {"class": "text-body-2 text-grey mt-2"}, "text": "请在插件设置中配置 LLM 和 Emby 信息，然后点击刷新获取推荐"},
                                                {
                                                    "component": "VBtn",
                                                    "props": {"class": "mt-4", "color": "primary", "variant": "elevated"},
                                                    "content": [{"component": "VIcon", "props": {"start": True}, "icon": "mdi-refresh"}, {"component": "span", "text": "刷新推荐"}],
                                                    "events": {"click": {"type": "request", "path": "/plugin/SmartRecommend/refresh", "method": "POST"}}
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        
        # 构建推荐卡片
        cards = []
        for category, items in recommendations.items():
            if not items:
                continue
            card_content = [
                {
                    "component": "div",
                    "props": {"class": "text-h6 mb-3"},
                    "content": [
                        {"component": "VIcon", "props": {"start": True, "color": "primary"}, "icon": self._get_category_icon(category)},
                        {"component": "span", "text": f" {category}"}
                    ]
                }
            ]
            
            for item in items[:self._recommend_count]:
                card_content.append({
                    "component": "div",
                    "props": {"class": "d-flex align-center py-2 border-b"},
                    "content": [
                        {
                            "component": "VAvatar",
                            "props": {"size": 60, "rounded": True, "class": "me-3"},
                            "content": [
                                {
                                    "component": "VImg",
                                    "props": {
                                        "src": item.get("poster", ""),
                                        "cover": True
                                    }
                                } if item.get("poster") else {
                                    "component": "VIcon",
                                    "props": {"size": 32, "color": "grey"},
                                    "icon": "mdi-movie"
                                }
                            ]
                        },
                        {
                            "component": "div",
                            "props": {"class": "flex-grow-1"},
                            "content": [
                                {
                                    "component": "div",
                                    "props": {"class": "text-subtitle-1 font-weight-medium"},
                                    "text": item.get("title", "未知")
                                },
                                {
                                    "component": "div",
                                    "props": {"class": "text-caption text-grey"},
                                    "content": [
                                        {"component": "span", "text": f"{item.get('year', '')} · {item.get('type', '')}"},
                                        {"component": "span", "props": {"class": "mx-1"}, "text": "·"},
                                        {"component": "span", "text": f"评分: {item.get('rating', '-')}"}
                                    ] if item.get('year') else []
                                }
                            ]
                        },
                        {
                            "component": "VBtn",
                            "props": {"size": "small", "color": "primary", "variant": "text"},
                            "content": [{"component": "VIcon", "icon": "mdi-plus"}],
                            "events": {
                                "click": {
                                    "type": "request",
                                    "path": f"/api/v1/subscribe/",
                                    "method": "POST",
                                    "data": {
                                        "name": item.get("title"),
                                        "tmdbid": item.get("tmdb_id"),
                                        "type": item.get("type")
                                    }
                                }
                            }
                        }
                    ]
                })
            
            cards.append({
                "component": "VCol",
                "props": {"cols": 12, "md": 6, "lg": 4},
                "content": [
                    {
                        "component": "VCard",
                        "props": {"variant": "outlined"},
                        "content": [
                            {"component": "VCardText", "content": card_content}
                        ]
                    }
                ]
            })
        
        # 添加刷新按钮和状态
        header = [
            {
                "component": "VCol",
                "props": {"cols": 12},
                "content": [
                    {
                        "component": "div",
                        "props": {"class": "d-flex justify-space-between align-center mb-4"},
                        "content": [
                            {
                                "component": "div",
                                "content": [
                                    {"component": "span", "props": {"class": "text-h5"}, "text": "🤖 AI 智能推荐"},
                                    {"component": "div", "props": {"class": "text-caption text-grey"}, "text": f"上次更新: {self._last_refresh or '未更新'}"}
                                ]
                            },
                            {
                                "component": "VBtn",
                                "props": {"color": "primary", "variant": "elevated", "loading": False},
                                "content": [
                                    {"component": "VIcon", "props": {"start": True}, "icon": "mdi-refresh"},
                                    {"component": "span", "text": "刷新推荐"}
                                ],
                                "events": {"click": {"type": "request", "path": "/plugin/SmartRecommend/refresh", "method": "POST"}}
                            }
                        ]
                    }
                ]
            }
        ]
        
        return [{"component": "VRow", "content": header + cards}]

    def stop_service(self):
        """停止服务"""
        pass

    # ==================== API 端点 ====================

    def api_get_recommendations(self) -> dict:
        """获取推荐列表 API"""
        return {
            "success": True,
            "data": self._recommend_cache,
            "last_refresh": self._last_refresh
        }

    def api_refresh(self) -> dict:
        """刷新推荐 API"""
        try:
            self._refresh_recommendations()
            return {"success": True, "message": "推荐已刷新"}
        except Exception as e:
            logger.error(f"刷新推荐失败: {e}")
            return {"success": False, "message": str(e)}

    def api_get_categories(self) -> dict:
        """获取 Emby 分类 API"""
        categories = self._get_emby_categories()
        return {"success": True, "data": categories}

    def api_get_history(self) -> dict:
        """获取观看历史 API"""
        history = self._get_watch_history()
        return {"success": True, "data": history}

    # ==================== 事件处理 ====================

    @eventmanager.register(EventType.PluginAction)
    def handle_command(self, event: Event):
        """处理命令事件"""
        if not event:
            return
        action = (event.event_data or {}).get("action", "")
        if action == "refresh_recommend":
            self._refresh_recommendations()

    # ==================== 核心逻辑 ====================

    def _refresh_recommendations(self):
        """刷新推荐"""
        logger.info("[SmartRecommend] 开始刷新推荐...")
        
        # 1. 验证配置
        if not self._llm_api_key or not self._llm_base_url:
            logger.warning("[SmartRecommend] LLM 未配置，请先配置 API Key 和 Base URL")
            return
        
        if not self._emby_url or not self._emby_api_key:
            logger.warning("[SmartRecommend] Emby 未配置，请先配置地址和 API Key")
            return
        
        # 2. 获取观看历史
        watch_history = self._get_watch_history()
        logger.info(f"[SmartRecommend] 获取到 {len(watch_history)} 条观看记录")
        
        # 3. 获取 Emby 分类
        categories = self._get_emby_categories()
        logger.info(f"[SmartRecommend] 获取到 {len(categories)} 个分类")
        
        # 4. 获取热播数据
        trending = self._get_trending_media()
        logger.info(f"[SmartRecommend] 获取到 {len(trending)} 条热播数据")
        
        # 5. 调用 LLM 分析
        recommendations = self._analyze_with_llm(watch_history, categories, trending)
        
        # 6. 保存结果
        self._recommend_cache = recommendations
        self._last_refresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_config()
        
        logger.info(f"[SmartRecommend] 推荐刷新完成，共 {sum(len(v) for v in recommendations.values())} 条推荐")

    def _get_emby_categories(self) -> List[dict]:
        """获取 Emby 媒体库分类"""
        if not self._emby_url or not self._emby_api_key:
            return []
        
        try:
            # 获取用户 ID
            user_id = self._emby_user_id
            if not user_id:
                users_url = f"{self._emby_url.rstrip('/')}/emby/Users?api_key={self._emby_api_key}"
                resp = requests.get(users_url, timeout=10)
                resp.raise_for_status()
                users = resp.json()
                if users:
                    user_id = users[0].get("Id")
            
            # 获取媒体库视图
            views_url = f"{self._emby_url.rstrip('/')}/emby/Users/{user_id}/Views?api_key={self._emby_api_key}"
            resp = requests.get(views_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            categories = []
            for item in data.get("Items", []):
                categories.append({
                    "id": item.get("Id"),
                    "name": item.get("Name"),
                    "type": item.get("CollectionType", "unknown")
                })
            return categories
        except Exception as e:
            logger.error(f"[SmartRecommend] 获取 Emby 分类失败: {e}")
            return []

    def _get_watch_history(self, limit: int = 100) -> List[dict]:
        """获取观看历史"""
        if not self._emby_url or not self._emby_api_key:
            return []
        
        try:
            # 获取用户 ID
            user_id = self._emby_user_id
            if not user_id:
                users_url = f"{self._emby_url.rstrip('/')}/emby/Users?api_key={self._emby_api_key}"
                resp = requests.get(users_url, timeout=10)
                resp.raise_for_status()
                users = resp.json()
                if users:
                    user_id = users[0].get("Id")
            
            # 获取最近播放的项目
            items_url = f"{self._emby_url.rstrip('/')}/emby/Users/{user_id}/Items?api_key={self._emby_api_key}&SortBy=DatePlayed&SortOrder=Descending&Limit={limit}&Recursive=true&Fields=Name,Type,Genres,CommunityRating,ProductionYear,PlayCount,DateCreated"
            resp = requests.get(items_url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            history = []
            for item in data.get("Items", []):
                history.append({
                    "title": item.get("Name", ""),
                    "type": item.get("Type", ""),
                    "year": item.get("ProductionYear"),
                    "rating": item.get("CommunityRating"),
                    "genres": item.get("Genres", []),
                    "play_count": item.get("PlayCount", 0),
                    "id": item.get("Id")
                })
            return history
        except Exception as e:
            logger.error(f"[SmartRecommend] 获取观看历史失败: {e}")
            return []

    def _get_trending_media(self) -> List[dict]:
        """获取热播数据（从 TMDB）"""
        trending_list = []
        
        try:
            # TMDB API
            # 优先从 MP 设置获取，否则使用插件配置
            tmdb_api_key = getattr(settings, "TMDB_API_KEY", "") or self._tmdb_api_key
            
            # 获取热播电影和电视剧
            for media_type in ["movie", "tv"]:
                url = f"https://api.themoviedb.org/3/trending/{media_type}/week?api_key={tmdb_api_key}&language=zh-CN"
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                
                for item in data.get("results", [])[:20]:
                    trending_list.append({
                        "title": item.get("title") or item.get("name", ""),
                        "type": "电影" if media_type == "movie" else "电视剧",
                        "year": (item.get("release_date") or item.get("first_air_date", ""))[:4] if item.get("release_date") or item.get("first_air_date") else None,
                        "rating": item.get("vote_average"),
                        "genres": [],  # TMDB 返回的是 genre_ids，需要转换
                        "tmdb_id": item.get("id"),
                        "poster": f"https://image.tmdb.org/t/p/w500{item.get('poster_path')}" if item.get("poster_path") else None,
                        "overview": item.get("overview", "")
                    })
        except Exception as e:
            logger.error(f"[SmartRecommend] 获取热播数据失败: {e}")
        
        return trending_list

    def _analyze_with_llm(self, watch_history: List[dict], categories: List[dict], trending: List[dict]) -> dict:
        """使用 LLM 分析并生成推荐"""
        
        # 构建分类列表
        category_names = [c["name"] for c in categories if c.get("name")]
        
        # 构建提示词
        prompt = f"""你是一个专业的影视推荐专家。根据用户的观看历史和当前热播内容，为用户推荐最合适的影视作品。

## 用户观看历史 (最近{len(watch_history)}部)
{self._format_watch_history(watch_history[:50])}

## 媒体库分类
{', '.join(category_names)}

## 当前热播内容 (前20部)
{self._format_trending(trending[:20])}

## 推荐要求
1. 根据用户的观看偏好，从热播内容中挑选推荐
2. 每个分类推荐 {self._recommend_count} 部作品
3. 优先推荐评分高、符合用户口味的内容
4. 特别关注动漫/番剧分类
5. 返回 JSON 格式，结构如下：

```json
{{
  "国产剧": [
    {{"title": "剧名", "year": 2024, "rating": 8.5, "reason": "推荐理由", "tmdb_id": 12345}}
  ],
  "日漫": [...],
  ...
}}
```

只返回 JSON，不要其他内容。"""

        try:
            # 调用 LLM
            headers = {
                "Authorization": f"Bearer {self._llm_api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": self._llm_model,
                "messages": [
                    {"role": "system", "content": "你是一个专业的影视推荐专家，擅长根据用户偏好推荐内容。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 4000
            }
            
            resp = requests.post(
                f"{self._llm_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=data,
                timeout=60
            )
            resp.raise_for_status()
            result = resp.json()
            
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # 解析 JSON
            # 尝试提取 JSON 块
            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            
            recommendations = json.loads(content)
            
            # 补充 poster 等信息
            for category, items in recommendations.items():
                for item in items:
                    # 从热播数据中查找 poster
                    for t in trending:
                        if t.get("title") == item.get("title") or t.get("tmdb_id") == item.get("tmdb_id"):
                            item["poster"] = t.get("poster")
                            item["type"] = t.get("type")
                            break
            
            return recommendations
            
        except json.JSONDecodeError as e:
            logger.error(f"[SmartRecommend] LLM 返回 JSON 解析失败: {e}")
            logger.error(f"[SmartRecommend] 原始内容: {content[:500]}")
            return {}
        except Exception as e:
            logger.error(f"[SmartRecommend] LLM 调用失败: {e}")
            return {}

    def _format_watch_history(self, history: List[dict]) -> str:
        """格式化观看历史"""
        lines = []
        for i, item in enumerate(history, 1):
            genres = ", ".join(item.get("genres", [])[:3])
            rating = f"评分{item.get('rating')}" if item.get("rating") else ""
            lines.append(f"{i}. {item.get('title', '')} ({item.get('year', '未知')}) - {item.get('type', '')} {rating} [{genres}]")
        return "\n".join(lines)

    def _format_trending(self, trending: List[dict]) -> str:
        """格式化热播内容"""
        lines = []
        for i, item in enumerate(trending, 1):
            rating = f"评分{item.get('rating')}" if item.get("rating") else ""
            lines.append(f"{i}. {item.get('title', '')} ({item.get('year', '未知')}) - {item.get('type', '')} {rating}")
        return "\n".join(lines)

    def _get_category_icon(self, category: str) -> str:
        """获取分类图标"""
        icons = {
            "电影": "mdi-movie",
            "电视剧": "mdi-television",
            "国产剧": "mdi-television",
            "韩剧": "mdi-television",
            "欧美剧": "mdi-television",
            "日剧": "mdi-television",
            "动漫": "mdi-animation",
            "日漫": "mdi-animation",
            "国漫": "mdi-animation",
            "欧美动漫": "mdi-animation",
            "综艺": "mdi-microphone-variant",
            "纪录片": "mdi-filmstrip",
        }
        for key, icon in icons.items():
            if key in category:
                return icon
        return "mdi-movie"

    def _save_config(self):
        """保存配置"""
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "llm_provider": self._llm_provider,
            "llm_api_key": self._llm_api_key,
            "llm_base_url": self._llm_base_url,
            "llm_model": self._llm_model,
            "emby_url": self._emby_url,
            "emby_api_key": self._emby_api_key,
            "emby_user_id": self._emby_user_id,
            "tmdb_api_key": self._tmdb_api_key,
            "recommend_count": self._recommend_count,
            "auto_refresh": self._auto_refresh,
            "refresh_cron": self._refresh_cron,
            "recommend_cache": self._recommend_cache,
            "last_refresh": self._last_refresh
        })