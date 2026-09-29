#!/usr/bin/env python3
"""
OAuth2 认证模块 - Microsoft Graph API 集成

使用 MSAL 库完成授权码流程,获取访问令牌。
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict

# 前置声明
_instance: Optional['OutlookAuth'] = None
from msal import ConfidentialClientApplication, SerializableTokenCache


class OutlookAuth:
    """Outlook Calendar OAuth2 认证管理"""

    def __init__(self):
        self.client_id = os.getenv("AZURE_CLIENT_ID")
        self.tenant_id = os.getenv("AZURE_TENANT_ID")
        self.client_secret = os.getenv("AZURE_CLIENT_SECRET")
        self.redirect_uri = os.getenv("REDIRECT_URI", "http://localhost:8000/auth/microsoft/callback")
        self.graph_api_version = os.getenv("GRAPH_API_VERSION", "v1.0")

        # 认证 authority:
        # - 默认: 租户 authority（工作/学校账号），由 AZURE_TENANT_ID 决定
        # - 个人 Microsoft 账号 (outlook.com 等): .env 中设 AZURE_TENANT_ID=consumers，
        #   或用 AAD_AUTHORITY 显式指定完整 authority（如 https://login.microsoftonline.com/common）
        #   注意: 个人账号模式要求应用在 Azure 中启用"个人 Microsoft 帐户"支持
        authority_override = os.getenv("AAD_AUTHORITY")
        if authority_override:
            self.authority = authority_override.strip().rstrip("/")
        else:
            self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scopes = [
            "https://graph.microsoft.com/Calendars.ReadWrite",
            "https://graph.microsoft.com/User.Read"
        ]
        # 注意: offline_access 权限由 MSAL 自动处理，不需要显式声明

        # 令牌缓存文件（存放在项目目录下）
        self.token_cache_path = Path(__file__).parent.parent / ".outlook_calendar_token_cache.json"
        self.token_cache = SerializableTokenCache()
        self._load_token_cache()

        self.app = ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
            token_cache=self.token_cache
        )

    def _load_token_cache(self) -> None:
        """加载令牌缓存"""
        if not self.token_cache_path.exists():
            return

        try:
            with open(self.token_cache_path, "r", encoding="utf-8") as f:
                cache_json = f.read()
            if cache_json:
                self.token_cache.deserialize(cache_json)
        except (json.JSONDecodeError, IOError, OSError):
            # 损坏的缓存文件不应阻塞认证流程
            self.token_cache = SerializableTokenCache()

    def _save_token_cache(self) -> None:
        """保存令牌缓存"""
        try:
            with open(self.token_cache_path, "w", encoding="utf-8") as f:
                f.write(self.token_cache.serialize())
        except Exception as e:
            print(f"警告: 无法保存令牌缓存: {e}")

    def get_authorization_url(self) -> str:
        """获取用户授权 URL"""
        # 授权 URL 只使用基础 scopes，不包含 offline_access
        return self.app.get_authorization_request_url(
            scopes=self.scopes,
            redirect_uri=self.redirect_uri
        )

    def acquire_token_by_code(self, auth_code: str) -> Dict:
        """使用授权码换取访问令牌"""
        result = self.app.acquire_token_by_authorization_code(
            code=auth_code,
            scopes=self.scopes,
            redirect_uri=self.redirect_uri
        )

        if "access_token" in result:
            self._save_token_cache()
            return result
        raise Exception(f"认证失败: {result.get('error_description', '未知错误')}")

    def get_access_token(self) -> Optional[str]:
        """获取有效的访问令牌 (自动刷新)"""
        accounts = self.app.get_accounts()
        if not accounts:
            return None

        result = self.app.acquire_token_silent(
            scopes=self.scopes,
            account=accounts[0]
        )

        if result:
            return result.get("access_token")
        return None

    def is_authenticated(self) -> bool:
        """检查是否已认证"""
        return self.get_access_token() is not None

    def logout(self):
        """清除认证缓存"""
        if self.token_cache_path.exists():
            self.token_cache_path.unlink()
        self.token_cache = SerializableTokenCache()


def get_auth_manager() -> 'OutlookAuth':
    """获取认证管理器实例（延迟初始化）"""
    global _instance
    if _instance is None:
        _instance = OutlookAuth()
    return _instance


