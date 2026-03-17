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
    plugin_version = "1.2.1"
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
    _cache_version: str = ""  # 缓存版本，用于检测插件更新
    
    # 状态缓存（减少 API 调用）
    _media_status_cache: dict = {}  # {tmdb_id: {status: str, timestamp: datetime}}
    _status_cache_ttl: int = 86400  # 状态缓存有效期（24小时）
    
    # API 限流保护
    _last_api_call_time: dict = {}  # 记录各 API 最后调用时间
    _api_call_count: dict = {}  # 记录各 API 调用次数
    _api_rate_limit_window: int = 60  # 限流窗口（秒）
    _api_max_calls_per_window: int = 10  # 每个窗口最大调用次数
    
    # 当前插件版本
    CURRENT_VERSION = "1.2.1"

    @staticmethod
    def _normalize_url(url: str) -> str:
        """规范化 URL，确保有协议前缀"""
        if not url:
            return url
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            url = f"http://{url}"
        return url.rstrip('/')

    def _check_rate_limit(self, api_name: str) -> bool:
        """
        检查 API 是否超过限流
        :param api_name: API 名称标识
        :return: True 表示可以调用，False 表示被限流
        """
        now = datetime.now()
        
        # 初始化该 API 的调用记录
        if api_name not in self._last_api_call_time:
            self._last_api_call_time[api_name] = now
            self._api_call_count[api_name] = 0
        
        last_call = self._last_api_call_time[api_name]
        time_diff = (now - last_call).total_seconds()
        
        # 如果超过窗口期，重置计数
        if time_diff > self._api_rate_limit_window:
            self._api_call_count[api_name] = 0
            self._last_api_call_time[api_name] = now
        
        # 检查是否超过限制
        if self._api_call_count[api_name] >= self._api_max_calls_per_window:
            logger.warning(f"[SmartRecommend] API '{api_name}' 超过限流 ({self._api_max_calls_per_window}次/{self._api_rate_limit_window}秒)")
            return False
        
        # 增加调用计数
        self._api_call_count[api_name] += 1
        return True

    def _make_api_request(self, method: str, url: str, api_name: str, **kwargs) -> Optional[requests.Response]:
        """
        带限流保护的 API 请求
        :param method: HTTP 方法 (get/post/put/delete)
        :param url: 请求 URL
        :param api_name: API 名称（用于限流统计）
        :param kwargs: 其他 requests 参数
        :return: Response 对象或 None
        """
        # 检查限流
        if not self._check_rate_limit(api_name):
            logger.error(f"[SmartRecommend] API '{api_name}' 请求被限流，请稍后重试")
            return None
        
        try:
            # 添加默认超时
            if 'timeout' not in kwargs:
                kwargs['timeout'] = 30
            
            # 执行请求
            resp = getattr(requests, method.lower())(url, **kwargs)
            return resp
            
        except requests.exceptions.Timeout:
            logger.error(f"[SmartRecommend] API '{api_name}' 请求超时")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"[SmartRecommend] API '{api_name}' 请求失败: {e}")
            return None

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
            self._cache_version = config.get("cache_version", "")
            
            # 检测版本变化，自动清除缓存
            if self._cache_version != self.CURRENT_VERSION:
                logger.info(f"[SmartRecommend] 检测到插件版本更新 ({self._cache_version} -> {self.CURRENT_VERSION})，清除缓存")
                self._recommend_cache = {}
                self._last_refresh = ""
                self._media_status_cache = {}  # 清除状态缓存，确保获取最新状态
                self._cache_version = self.CURRENT_VERSION
                
                # 保存配置（包括清空后的缓存）
                self.update_config({
                    "recommend_cache": {},
                    "last_refresh": "",
                    "cache_version": self.CURRENT_VERSION
                })
                
                # 版本更新后自动刷新一次
                if self._enabled:
                    logger.info("[SmartRecommend] 版本更新后自动刷新推荐")
                    self._onlyonce = True

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
            "refresh_cron": "0 8 * * *",
            "cache_version": ""
        }

    def get_page(self) -> List[dict]:
        """仪表盘页面 - 按 Emby 分类 + 播出状态展示"""
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
        
        # 构建推荐卡片 - 按分类+状态展示
        cards = []
        
        for category, status_data in recommendations.items():
            if not status_data or not isinstance(status_data, dict):
                continue
            
            # 分类标题
            category_header = [
                {
                    "component": "div",
                    "props": {"class": "d-flex align-center mb-2"},
                    "content": [
                        {"component": "VIcon", "props": {"start": True, "color": "primary", "size": "large"}, "icon": self._get_category_icon(category)},
                        {"component": "span", "props": {"class": "text-h5 ml-2"}, "text": category}
                    ]
                }
            ]
            
            # 按状态展示
            status_sections = []
            
            # 正在播出
            if status_data.get("正在播出"):
                status_sections.append(self._build_status_section("正在播出", "mdi-play-circle", "success", status_data["正在播出"]))
            
            # 即将上映
            if status_data.get("即将上映"):
                status_sections.append(self._build_status_section("即将上映", "mdi-clock-outline", "warning", status_data["即将上映"]))
            
            # 已完结
            if status_data.get("已完结"):
                status_sections.append(self._build_status_section("已完结", "mdi-check-circle", "info", status_data["已完结"]))
            
            if not status_sections:
                continue
            
            cards.append({
                "component": "VCol",
                "props": {"cols": 12},
                "content": [
                    {
                        "component": "VCard",
                        "props": {"variant": "outlined", "class": "mb-4"},
                        "content": [
                            {"component": "VCardTitle", "content": category_header},
                            {"component": "VCardText", "content": status_sections}
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

    def _build_status_section(self, status: str, icon: str, color: str, items: List[dict]) -> dict:
        """构建状态分组的展示区域"""
        content = [
            {
                "component": "div",
                "props": {"class": "d-flex align-center mb-2"},
                "content": [
                    {"component": "VIcon", "props": {"start": True, "color": color, "size": "small"}, "icon": icon},
                    {"component": "span", "props": {"class": "text-subtitle-1 font-weight-medium ml-1"}, "text": status},
                    {"component": "VChip", "props": {"size": "x-small", "class": "ml-2"}, "text": str(len(items))}
                ]
            }
        ]
        
        for item in items[:self._recommend_count]:
            content.append({
                "component": "div",
                "props": {"class": "d-flex align-center py-2 border-b"},
                "content": [
                    {
                        "component": "VAvatar",
                        "props": {"size": 50, "rounded": True, "class": "me-3"},
                        "content": [
                            {
                                "component": "VImg",
                                "props": {"src": item.get("poster", ""), "cover": True}
                            } if item.get("poster") else {
                                "component": "VIcon",
                                "props": {"size": 24, "color": "grey"},
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
                                "props": {"class": "text-subtitle-2 font-weight-medium"},
                                "text": item.get("title", "未知")
                            },
                            {
                                "component": "div",
                                "props": {"class": "text-caption text-grey"},
                                "text": f"{item.get('year', '')} · {item.get('type', '')} · 评分 {item.get('rating', '-')}" if item.get('year') else f"评分 {item.get('rating', '-')}"
                            },
                            {
                                "component": "div",
                                "props": {"class": "text-caption text-grey-lighten-1"},
                                "text": item.get("reason", "")[:50] + "..." if item.get("reason") and len(item.get("reason", "")) > 50 else (item.get("reason", ""))
                            } if item.get("reason") else None
                        ]
                    },
                    {
                        "component": "VBtn",
                        "props": {"size": "x-small", "color": "primary", "variant": "text"},
                        "content": [{"component": "VIcon", "icon": "mdi-plus"}],
                        "events": {
                            "click": {
                                "type": "request",
                                "path": "/api/v1/subscribe/",
                                "method": "POST",
                                "data": {
                                    "name": item.get("title"),
                                    "tmdbid": item.get("tmdb_id"),
                                    "type": "movie" if item.get("type") == "电影" or item.get("media_type") == "movie" else "tv"
                                }
                            }
                        }
                    }
                ]
            })
        
        return {"component": "div", "props": {"class": "mb-4"}, "content": content}

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
        
        # 开始时间，用于计算耗时
        import time
        start_time = time.time()
        
        try:
            # ==================== 1. 验证配置 ====================
            if not self._llm_api_key or not self._llm_base_url:
                logger.warning("[SmartRecommend] LLM 未配置，请先配置 API Key 和 Base URL")
                return
            
            if not self._emby_url or not self._emby_api_key:
                logger.warning("[SmartRecommend] Emby 未配置，请先配置地址和 API Key")
                return
            
            # ==================== 2. 获取观看历史 ====================
            try:
                watch_history = self._get_watch_history()
                logger.info(f"[SmartRecommend] 获取到 {len(watch_history)} 条观看记录")
            except Exception as e:
                logger.error(f"[SmartRecommend] 获取观看历史失败: {e}")
                watch_history = []
            
            # ==================== 3. 获取 Emby 分类 ====================
            try:
                categories = self._get_emby_categories()
                logger.info(f"[SmartRecommend] 获取到 {len(categories)} 个分类")
            except Exception as e:
                logger.error(f"[SmartRecommend] 获取 Emby 分类失败: {e}")
                categories = []
            
            # 如果没有分类，使用默认分类
            if not categories:
                logger.info("[SmartRecommend] Emby 未返回分类，使用默认分类")
                categories = [{"name": cat, "type": "unknown"} for cat in ["国产剧", "韩剧", "欧美剧", "日剧", "欧美电影", "华语电影", "日韩电影", "动画电影", "国漫", "日漫", "欧美动漫", "儿童动漫", "综艺", "纪录片", "未分类"]]
            
            # ==================== 4. 获取热播数据 ====================
            try:
                trending = self._get_trending_media()
                logger.info(f"[SmartRecommend] 获取到 {len(trending)} 条热播数据")
            except Exception as e:
                logger.error(f"[SmartRecommend] 获取热播数据失败: {e}")
                trending = []
            
            # ==================== 5. 调用 LLM 分析 ====================
            try:
                recommendations = self._analyze_with_llm(watch_history, categories, trending)
            except Exception as e:
                logger.error(f"[SmartRecommend] LLM 分析失败: {e}")
                # LLM 失败时返回规则匹配的备选结果
                recommendations = self._analyze_with_llm([], categories, trending)  # 空历史，仅用热播数据
            
            # ==================== 6. 保存结果 ====================
            self._recommend_cache = recommendations
            self._last_refresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_config()
            
            # 计算总耗时
            end_time = time.time()
            total_time = end_time - start_time
            
            logger.info(f"[SmartRecommend] 推荐刷新完成: {len(recommendations)}个分类, {sum(len(v) for v in recommendations.values())}条推荐, 耗时{total_time:.1f}秒")
            
        except Exception as e:
            # 总异常处理
            logger.error(f"[SmartRecommend] 刷新推荐过程发生未捕获异常: {e}", exc_info=True)
            end_time = time.time()
            total_time = end_time - start_time
            logger.error(f"[SmartRecommend] 刷新失败，总耗时{total_time:.1f}秒")
            
            # 简单检查是否超时（假设超过 180 秒为超时）
            if total_time > 180:
                logger.error(f"[SmartRecommend] ⚠️ 刷新操作耗时超过180秒，可能是TMDB API限流或LLM API响应缓慢")
                logger.error(f"[SmartRecommend] ⚠️ 建议：检查网络连接，降低推荐数量，或等待API恢复")
        
        finally:
            # 确保取消任何可能的超时设置
            try:
                import signal
                signal.alarm(0)  # 取消超时
            except:
                pass

    def _get_emby_categories(self) -> List[dict]:
        """获取 Emby 媒体库分类"""
        if not self._emby_url or not self._emby_api_key:
            return []
        
        try:
            # 规范化 Emby URL
            emby_url = self._normalize_url(self._emby_url)
            
            # 获取用户 ID
            user_id = self._emby_user_id
            if not user_id:
                users_url = f"{emby_url}/emby/Users?api_key={self._emby_api_key}"
                resp = self._make_api_request("get", users_url, "emby_users")
                if not resp:
                    return []
                resp.raise_for_status()
                users = resp.json()
                if users:
                    user_id = users[0].get("Id")
            
            # 获取媒体库视图
            views_url = f"{emby_url}/emby/Users/{user_id}/Views?api_key={self._emby_api_key}"
            resp = self._make_api_request("get", views_url, "emby_views")
            if not resp:
                return []
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
            # 规范化 Emby URL
            emby_url = self._normalize_url(self._emby_url)
            
            # 获取用户 ID
            user_id = self._emby_user_id
            if not user_id:
                users_url = f"{emby_url}/emby/Users?api_key={self._emby_api_key}"
                resp = requests.get(users_url, timeout=10)
                resp.raise_for_status()
                users = resp.json()
                if users:
                    user_id = users[0].get("Id")
            
            # 获取最近播放的项目
            items_url = f"{emby_url}/emby/Users/{user_id}/Items?api_key={self._emby_api_key}&SortBy=DatePlayed&SortOrder=Descending&Limit={limit}&Recursive=true&Fields=Name,Type,Genres,CommunityRating,ProductionYear,PlayCount,DateCreated"
            resp = self._make_api_request("get", items_url, "emby_history")
            if not resp:
                return []
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
        """获取热播数据（从 TMDB），并获取详细信息"""
        trending_list = []
        
        try:
            # TMDB API
            tmdb_api_key = getattr(settings, "TMDB_API_KEY", "") or self._tmdb_api_key
            
            # 获取热播电影和电视剧
            for media_type in ["movie", "tv"]:
                url = f"https://api.themoviedb.org/3/trending/{media_type}/week?api_key={tmdb_api_key}&language=zh-CN"
                resp = self._make_api_request("get", url, "tmdb_trending")
                if not resp:
                    continue
                resp.raise_for_status()
                data = resp.json()
                
                for item in data.get("results", [])[:8]:  # 减少数量，避免 API 调用过多，从15减到8
                    tmdb_id = item.get("id")
                    
                    # 获取详细信息（包含播出状态）- 注意：这会调用详情 API
                    # 但如果已有缓存或状态可用，就跳过详细调用
                    detail = {}
                    
                    # 首先检查是否有可用的状态信息
                    status_from_cache = None
                    cache_key = f"{tmdb_id}_{media_type}"
                    if cache_key in self._media_status_cache:
                        cache_data = self._media_status_cache[cache_key]
                        status_from_cache = cache_data.get("status")
                    
                    # 只有在需要时才调用详情 API
                    if status_from_cache:
                        # 使用缓存的状态
                        detail["status"] = status_from_cache
                        detail["in_production"] = False  # 默认值
                        detail["next_episode_to_air"] = None
                        detail["genres"] = []  # 类型信息不影响主要功能
                        logger.debug(f"[SmartRecommend] 使用缓存状态: {item.get('title', 'N/A')} -> {status_from_cache}")
                    else:
                        # 获取详细信息
                        detail = self._get_tmdb_detail(tmdb_id, media_type, tmdb_api_key)
                    
                    trending_list.append({
                        "title": item.get("title") or item.get("name", ""),
                        "original_title": item.get("original_title") or item.get("original_name", ""),
                        "type": "电影" if media_type == "movie" else "电视剧",
                        "media_type": media_type,
                        "year": (item.get("release_date") or item.get("first_air_date", ""))[:4] if item.get("release_date") or item.get("first_air_date") else None,
                        "rating": item.get("vote_average"),
                        "genres": detail.get("genres", []),
                        "tmdb_id": tmdb_id,
                        "poster": f"https://image.tmdb.org/t/p/w500{item.get('poster_path')}" if item.get("poster_path") else None,
                        "overview": item.get("overview", ""),
                        "status": detail.get("status", ""),
                        "in_production": detail.get("in_production", False),
                        "next_episode": detail.get("next_episode_to_air"),
                        "original_language": item.get("original_language", ""),
                        "origin_country": item.get("origin_country", []),
                    })
        except Exception as e:
            logger.error(f"[SmartRecommend] 获取热播数据失败: {e}")
        
        return trending_list
    
    def _get_tmdb_detail(self, tmdb_id: int, media_type: str, api_key: str) -> dict:
        """获取 TMDB 详情信息"""
        try:
            url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={api_key}&language=zh-CN"
            resp = self._make_api_request("get", url, "tmdb_detail")
            if resp and resp.ok:
                data = resp.json()
                genres = [g.get("name", "") for g in data.get("genres", [])]
                return {
                    "status": data.get("status", ""),
                    "in_production": data.get("in_production", False),
                    "next_episode_to_air": data.get("next_episode_to_air"),
                    "genres": genres,
                }
        except Exception:
            pass
        return {}

    def _get_media_status(self, item: dict) -> str:
        """
        判断媒体播出状态 - 使用 TMDB 详细信息
        
        状态判断优先级：
        1. TMDB 状态映射（最高优先）
        2. 下一集信息
        3. 制作中状态
        4. 上映/首播日期
        5. 默认状态
        """
        # 检查缓存
        tmdb_id = item.get("tmdb_id")
        if tmdb_id:
            cache_key = f"{tmdb_id}_{item.get('media_type', 'tv')}"
            if cache_key in self._media_status_cache:
                cache_data = self._media_status_cache[cache_key]
                cache_time = cache_data.get("timestamp")
                if cache_time:
                    now = datetime.now()
                    if (now - cache_time).total_seconds() < self._status_cache_ttl:
                        logger.debug(f"[SmartRecommend] 状态命中缓存: {item.get('title', 'N/A')} -> {cache_data.get('status')}")
                        return cache_data.get("status", "正在播出")
        
        try:
            media_type = item.get("media_type", "tv")
            status = item.get("status", "")
            in_production = item.get("in_production", False)
            next_episode = item.get("next_episode_to_air") or item.get("next_episode")
            
            if media_type == "movie":
                # 电影状态判断逻辑
                movie_status = item.get("status", "")
                release_date = item.get("release_date", "")
                if not release_date and item.get("year"):
                    release_date = f"{item.get('year')}-01-01"
                
                # 基于电影状态判断
                if movie_status:
                    # TMDB 电影状态: Rumored, Planned, In Production, Post Production, Released
                    if movie_status in ["Released", "Post Production"]:
                        # 已发布或后期制作中，检查上映日期
                        if release_date:
                            try:
                                release = datetime.strptime(release_date[:10], "%Y-%m-%d")
                                now = datetime.now()
                                if release > now:
                                    return "即将上映"
                                else:
                                    return "已完结"
                            except:
                                pass
                        return "已完结"
                    elif movie_status in ["Rumored", "Planned"]:
                        return "即将上映"
                    elif movie_status == "In Production":
                        return "正在播出"  # 电影制作中
                else:
                    # 没有状态信息，根据上映日期判断
                    if release_date:
                        try:
                            release = datetime.strptime(release_date[:10], "%Y-%m-%d")
                            now = datetime.now()
                            if release > now:
                                return "即将上映"
                            else:
                                return "已完结"
                        except:
                            pass
                    return "已完结"
            
            else:
                # 根据 TMDB 电视剧状态映射
                tv_status_map = {
                    # 正在播出的状态
                    "Returning Series": "正在播出",
                    "In Production": "正在播出",
                    "Post Production": "正在播出",
                    "Pilot": "正在播出",  # 试播集也算正在播出
                    
                    # 已完结的状态
                    "Ended": "已完结",
                    "Canceled": "已完结",
                    
                    # 即将上映的状态
                    "Planned": "即将上映",
                    "Development": "即将上映",
                    "Script": "即将上映",
                    "Upcoming": "即将上映",
                    
                    # 其他状态
                    "Released": "已完结",  # 通常是电影状态
                }
                
                # 使用状态映射
                if status in tv_status_map:
                    return tv_status_map[status]
                
                # 如果有下一集计划，肯定正在播出
                if next_episode:
                    return "正在播出"
                
                # 制作中，正在播出
                if in_production:
                    return "正在播出"
                
                # 根据首播日期判断
                first_air_date = item.get("first_air_date", "")
                if not first_air_date and item.get("year"):
                    first_air_date = f"{item.get('year')}-01-01"
                
                if first_air_date:
                    try:
                        first_air = datetime.strptime(first_air_date[:10], "%Y-%m-%d")
                        now = datetime.now()
                        
                        # 剧集超过3年（1095天）未更新，标记为已完结
                        if (now - first_air).days > 1095:
                            return "已完结"
                        
                        # 未来首播的剧集
                        if first_air > now:
                            return "即将上映"
                        
                        # 3年内（还在热度窗口）且没完结
                        return "正在播出"
                    except:
                        pass
                
                # 默认返回正在播出（对观众最友好）
                result_status = "正在播出"
        
        except Exception as e:
            logger.debug(f"[SmartRecommend] 获取播出状态失败：{e}")
            result_status = "正在播出"
        
        # 保存到缓存（包括异常后的默认状态）
        if tmdb_id:
            cache_key = f"{tmdb_id}_{item.get('media_type', 'tv')}"
            self._media_status_cache[cache_key] = {
                "status": result_status,
                "timestamp": datetime.now()
            }
            logger.debug(f"[SmartRecommend] 状态保存到缓存: {item.get('title', 'N/A')} -> {result_status}")
        
        return result_status

    def _match_emby_category(self, item: dict, emby_categories: List[dict]) -> str:
        """根据媒体信息匹配 Emby 分类"""
        title = item.get("title", "").lower()
        original_title = item.get("original_title", "").lower()
        original_language = item.get("original_language", "")
        origin_country = item.get("origin_country", [])
        genres = item.get("genres", [])
        media_type = item.get("media_type", "tv")
        item_type = item.get("type", "电视剧")
        
        # 获取分类名列表
        category_names = [c.get("name", "") for c in emby_categories]
        
        # 关键词匹配规则
        rules = {
            "国产剧": {
                "keywords": ["国产", "中国", "大陆", "内地"],
                "language": ["zh", "cn"],
                "country": ["CN", "CHN", "China"]
            },
            "韩剧": {
                "keywords": ["韩", "韩国", "korean"],
                "language": ["ko", "kr"],
                "country": ["KR", "KOR", "South Korea"]
            },
            "欧美剧": {
                "keywords": ["美", "英", "欧"],
                "language": ["en"],
                "country": ["US", "GB", "UK", "USA"]
            },
            "日剧": {
                "keywords": ["日", "日本"],
                "language": ["ja", "jp"],
                "country": ["JP", "JPN", "Japan"]
            },
            "华语电影": {
                "keywords": ["华语"],
                "language": ["zh", "cn"],
                "type": "电影"
            },
            "欧美电影": {
                "keywords": ["美", "欧"],
                "type": "电影"
            },
            "日韩电影": {
                "keywords": ["日", "韩"],
                "type": "电影"
            },
            "动画电影": {
                "genres": ["动画", "Animation", "Anime"],
                "type": "电影"
            },
            "国漫": {
                "keywords": ["国漫", "国产动画"],
                "language": ["zh", "cn"],
                "genres": ["动画", "Animation", "Anime"]
            },
            "日漫": {
                "keywords": ["日漫", "日本动画", "anime"],
                "language": ["ja", "jp"],
                "genres": ["动画", "Animation", "Anime"]
            },
            "欧美动漫": {
                "keywords": ["欧美动画"],
                "language": ["en"],
                "genres": ["动画", "Animation"]
            },
            "综艺": {
                "genres": ["综艺", "Reality", "Talk"]
            },
            "纪录片": {
                "genres": ["纪录", "Documentary"]
            },
            "儿童动漫": {
                "keywords": ["儿童", "kids", "children", "少儿"],
                "genres": ["动画", "Animation", "Anime", "Family"]
            },
            "未分类": {
                # 默认分类，不需要特殊规则
            }
        }
        
        # 排除的分类
        excluded_categories = ["食贫道", "演唱会", "其他动漫"]
        category_names = [c for c in category_names if c not in excluded_categories]
        
        # 检查标题和原标题关键词
        full_text = f"{title} {original_title}"
        
        for cat_name in category_names:
            if cat_name not in rules:
                continue
            
            rule = rules[cat_name]
            
            # 检查关键词
            keywords = rule.get("keywords", [])
            for kw in keywords:
                if kw.lower() in full_text:
                    return cat_name
            
            # 检查语言
            if original_language and original_language in rule.get("language", []):
                # 如果有类型限制，检查类型
                if "type" in rule:
                    if rule["type"] in item_type:
                        return cat_name
                elif "genres" in rule:
                    # 检查是否是动画
                    if media_type == "tv" and any(g in genres for g in ["Animation", "Anime", "动画"]):
                        return cat_name
                    elif media_type == "tv":
                        # 非动画，但是语言匹配
                        return cat_name
                else:
                    return cat_name
            
            # 检查产地
            if origin_country:
                for country in origin_country:
                    if country in rule.get("country", []):
                        return cat_name
            
            # 检查类型限制
            if "type" in rule and rule["type"] in item_type:
                # 检查类型匹配
                if "genres" not in rule:
                    return cat_name
            
            # 检查类型 + 类型
            if "type" in rule and rule["type"] in item_type:
                return cat_name
        
        # 默认分类
        if "电影" in item_type:
            if "欧美电影" in category_names:
                return "欧美电影"
        else:
            if "国产剧" in category_names:
                return "国产剧"
        
        # 返回第一个分类
        if category_names:
            return category_names[0]
        return "推荐"

    def _analyze_with_llm(self, watch_history: List[dict], categories: List[dict], trending: List[dict]) -> dict:
        """使用 LLM 分析并生成推荐，按 Emby 分类 + 播出状态划分"""
        
        # 构建分类列表，排除特定分类
        excluded_categories = ["食贫道", "演唱会", "其他动漫"]
        emby_category_names = [c["name"] for c in categories if c.get("name") and c["name"] not in excluded_categories]
        
        # 默认分类列表（完整 15 个分类）
        default_categories = [
            "国产剧", "韩剧", "欧美剧", "日剧",
            "欧美电影", "华语电影", "日韩电影", "动画电影",
            "国漫", "日漫", "欧美动漫", "儿童动漫",
            "综艺", "纪录片", "未分类"
        ]
        
        # 合并 Emby 分类和默认分类，确保完整性
        category_names = list(dict.fromkeys(emby_category_names + default_categories))
        # 移除排除的分类
        category_names = [c for c in category_names if c not in excluded_categories]
        
        logger.info(f"[SmartRecommend] 使用分类列表 ({len(category_names)}个): {category_names}")
        
        # 先对热播内容进行分类和状态分组，强制初始化所有分类
        categorized_trending = {}
        for cat in category_names:
            categorized_trending[cat] = {"正在播出": [], "即将上映": [], "已完结": []}
        
        for t in trending:
            # 获取播出状态
            status = self._get_media_status(t)
            
            # 匹配 Emby 分类
            category = self._match_emby_category(t, categories)
            
            # 确保分类在列表中
            if category not in categorized_trending:
                category = category_names[0] if category_names else "国产剧"
            
            # 按状态分组
            if status in ["正在播出", "正在更新"]:
                categorized_trending[category]["正在播出"].append(t)
            elif status in ["即将上映", "即将播出"]:
                categorized_trending[category]["即将上映"].append(t)
            else:
                categorized_trending[category]["已完结"].append(t)
        
        # 构建提示词
        prompt = f"""你是一个专业的影视推荐专家。根据用户的观看历史，为用户推荐最合适的影视作品。

## 用户观看历史 (最近{len(watch_history)}部)
{self._format_watch_history(watch_history[:50])}

## Emby 媒体库分类（必须使用这些分类名称）
{chr(10).join(f'- {cat}' for cat in category_names)}

## 当前热播内容（已按分类和播出状态分组）
{self._format_categorized_trending(categorized_trending)}

## 推荐要求
1. 必须使用上面列出的 Emby 媒体库分类名称，不要创造新分类
2. 每个分类下按三种播出状态组织：正在播出、即将上映、已完结
3. 每个状态下推荐恰好 5 部作品
4. 如果某个状态没有热播内容，可以根据用户偏好推荐其他相似作品
5. 优先选择符合用户观看偏好的内容
6. 返回严格的 JSON 格式，必须包含所有分类：

```json
{{
  "国产剧": {{
    "正在播出": [
      {{"title": "剧名", "year": 2024, "rating": 8.5, "reason": "推荐理由", "tmdb_id": 12345, "type": "电视剧"}}
    ],
    "即将上映": [...],
    "已完结": [...]
  }},
  "韩剧": {{
    "正在播出": [...],
    "即将上映": [...],
    "已完结": [...]
  }},
  ... (必须包含所有分类)
}}
```

只返回 JSON，不要其他内容。确保分类名称与 Emby 媒体库分类完全一致。"""

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
            
            resp = self._make_api_request("post", f"{self._llm_base_url.rstrip('/')}/chat/completions", "llm", headers=headers, json=data, timeout=60)
            if not resp:
                logger.error("[SmartRecommend] LLM API 调用被限流或失败，使用规则匹配结果作为备选")
                return categorized_trending
            resp.raise_for_status()
            result = resp.json()
            
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # 解析 JSON
            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            
            recommendations = json.loads(content)
            
            # 验证分类名称并构建结果 - 确保强制所有分类都存在
            valid_recommendations = {}
            
            # 1. 先处理 LLM 返回的分类
            for category, status_data in recommendations.items():
                if category in category_names and isinstance(status_data, dict):
                    valid_recommendations[category] = {}
                    for status, items in status_data.items():
                        if status in ["正在播出", "即将上映", "已完结"] and isinstance(items, list):
                            valid_recommendations[category][status] = items
            
            # 2. 强制添加所有分类，即使 LLM 没返回
            for category in category_names:
                if category not in valid_recommendations:
                    # 初始化完整结构
                    valid_recommendations[category] = {
                        "正在播出": [],
                        "即将上映": [],
                        "已完结": []
                    }
            
            # 3. 确保每个分类都有完整的状态结构
            for category, status_data in valid_recommendations.items():
                for status in ["正在播出", "即将上映", "已完结"]:
                    if status not in status_data:
                        status_data[status] = []
            
            # 补充 poster 等信息 - 改进匹配逻辑
            for category, status_data in valid_recommendations.items():
                for status, items in status_data.items():
                    for item in items:
                        item_title = item.get("title", "").lower().strip()
                        item_tmdb_id = item.get("tmdb_id")
                        
                        # 从热播数据中查找 poster - 改进匹配逻辑
                        # 1. 先尝试 TMDB ID 精确匹配（最高优先级）
                        poster_found = False
                        
                        if item_tmdb_id:
                            for t in trending:
                                t_tmdb_id = t.get("tmdb_id")
                                if t_tmdb_id is not None and t_tmdb_id == item_tmdb_id:
                                    item["poster"] = t.get("poster")
                                    if not item.get("type"):
                                        item["type"] = t.get("type")
                                    if not item.get("year"):
                                        item["year"] = t.get("year")
                                    if not item.get("rating"):
                                        item["rating"] = t.get("rating")
                                    poster_found = True
                                    break
                        
                        # 2. 标题多重匹配（如果 ID 匹配失败）
                        if not poster_found and item_title:
                            for t in trending:
                                t_title = t.get("title", "").lower().strip()
                                t_original = t.get("original_title", "").lower().strip()
                                
                                # 匹配策略 1: item.title 匹配 trending.title 或 trending.original_title
                                if item_title == t_title or item_title == t_original:
                                    item["poster"] = t.get("poster")
                                    if not item.get("type"):
                                        item["type"] = t.get("type")
                                    if not item.get("year"):
                                        item["year"] = t.get("year")
                                    if not item.get("rating"):
                                        item["rating"] = t.get("rating")
                                    poster_found = True
                                    break
                                
                                # 匹配策略 2: 去除特殊字符和标点后再匹配
                                clean_item = re.sub(r'[^\w\s]', '', item_title)
                                clean_trend = re.sub(r'[^\w\s]', '', t_title)
                                clean_trend_original = re.sub(r'[^\w\s]', '', t_original)
                                
                                if clean_item == clean_trend or clean_item == clean_trend_original:
                                    item["poster"] = t.get("poster")
                                    if not item.get("type"):
                                        item["type"] = t.get("type")
                                    if not item.get("year"):
                                        item["year"] = t.get("year")
                                    if not item.get("rating"):
                                        item["rating"] = t.get("rating")
                                    poster_found = True
                                    break
                                
                                # 匹配策略 3: 部分匹配（剧集名可能包含年份）
                                if f"({item.get('year', '')})" in t_title or clean_item in clean_trend:
                                    item["poster"] = t.get("poster")
                                    if not item.get("type"):
                                        item["type"] = t.get("type")
                                    if not item.get("year"):
                                        item["year"] = t.get("year")
                                    if not item.get("rating"):
                                        item["rating"] = t.get("rating")
                                    poster_found = True
                                    break
                        
                        # 记录未找到海报的项
                        if not poster_found and item_title:
                            logger.debug(f"[SmartRecommend] 未找到海报的媒体: {item.get('title', 'unknown')}, TMDB ID: {item_tmdb_id}")
            
            # 现在所有分类都已确保存在且结构完整
            # 如果有规则匹配的数据，优先填充空分类
            for category in category_names:
                if category in categorized_trending and category in valid_recommendations:
                    for status in ["正在播出", "即将上映", "已完结"]:
                        trend_items = categorized_trending[category].get(status, [])
                        current_items = valid_recommendations[category].get(status, [])
                        # 只有在当前为空且有趋势数据时才填充
                        if not current_items and trend_items:
                            valid_recommendations[category][status] = trend_items[:5]  # 最多取5个
            
            return valid_recommendations
            
        except json.JSONDecodeError as e:
            logger.error(f"[SmartRecommend] LLM 返回 JSON 解析失败: {e}")
            logger.error(f"[SmartRecommend] 原始内容: {content[:500]}")
            # 返回规则匹配结果作为备选
            logger.info("[SmartRecommend] 使用规则匹配结果作为备选")
            return categorized_trending
        except Exception as e:
            logger.error(f"[SmartRecommend] LLM 调用失败: {e}")
            # 返回规则匹配结果作为备选
            return categorized_trending

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
            genres = ", ".join(item.get("genres", [])[:2])
            lines.append(f"{i}. {item.get('title', '')} ({item.get('year', '未知')}) - {item.get('type', '')} {rating} [{genres}]")
        return "\n".join(lines)
    
    def _format_categorized_trending(self, categorized: dict) -> str:
        """格式化已分类的热播内容"""
        lines = []
        for category, status_data in categorized.items():
            total = sum(len(items) for items in status_data.values())
            if total > 0:
                lines.append(f"\n### {category} ({total}部)")
                for status, items in status_data.items():
                    if items:
                        lines.append(f"\n**{status}** ({len(items)}部)")
                        for item in items[:5]:
                            rating = f"评分{item.get('rating'):.1f}" if item.get("rating") else ""
                            lines.append(f"  - {item.get('title', '')} ({item.get('year', '')}) {rating}")
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
            "last_refresh": self._last_refresh,
            "cache_version": self._cache_version
        })