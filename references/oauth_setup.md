# Azure OAuth2 应用注册配置指南

## 步骤 1: 注册应用

1. 访问 [Azure Portal](https://portal.azure.com)
2. 搜索并进入 "Microsoft Entra ID" (或 "Azure Active Directory")
3. 选择 "App registrations" → "New registration"
4. 填写应用信息:
   - **Name**: Outlook Calendar Scheduler (或自定义)
   - **Supported account types**: 选择适合的范围
     - "Accounts in this organizational directory only" (单租户)
     - "Accounts in any organizational directory" (多租户)
     - "Accounts in any organizational directory and personal Microsoft accounts"
   - **Redirect URI**: 选择 "Web" 并填入 `http://localhost:8000/auth/microsoft/callback`

5. 点击 "Register"

## 步骤 2: 配置 API 权限

1. 在应用详情页，选择 "API permissions" → "Add a permission"
2. 选择 "Microsoft Graph" → "Delegated permissions"
3. 添加以下权限:
   - `Calendars.ReadWrite` - 读写日历
   - `User.Read` - 读取用户信息
4. 点击 "Add permissions"
5. 如果应用需要管理员同意 (某些权限),点击 "Grant admin consent for [tenant]"

## 步骤 3: 创建客户端密钥

1. 在应用详情页，选择 "Certificates & secrets"
2. 点击 "New client secret"
3. 描述: `outlook-scheduler` (或自定义),有效期选择合适长度
4. 点击 "Add"
5. **立即复制生成的密钥值** (Value) - 这是 `AZURE_CLIENT_SECRET`

## 步骤 4: 获取应用标识

从应用概览页 (Overview) 复制:
- **Application (client) ID** → `AZURE_CLIENT_ID`
- **Directory (tenant) ID** → `AZURE_TENANT_ID`

## 步骤 5: 设置环境变量

```bash
export AZURE_CLIENT_ID="your-client-id"
export AZURE_TENANT_ID="your-tenant-id"
export AZURE_CLIENT_SECRET="your-client-secret"
export REDIRECT_URI="http://localhost:8000/auth/microsoft/callback"
```

Windows PowerShell:
```powershell
$env:AZURE_CLIENT_ID="your-client-id"
$env:AZURE_TENANT_ID="your-tenant-id"
$env:AZURE_CLIENT_SECRET="your-client-secret"
$env:REDIRECT_URI="http://localhost:8000/auth/microsoft/callback"
```

## 常见问题

### Q: 重定向 URI 不匹配错误
**A**: 确保 Azure 应用注册中配置的 Redirect URI 与代码中使用的一致。本地开发通常使用 `http://localhost:8000` 或 `http://localhost:5000`。

### Q: 权限不足 (403 Forbidden)
**A**: 
- 检查是否添加了 `Calendars.ReadWrite` 权限
- 是否已点击 "Grant admin consent"
- 用户是否已退出并重新登录以接受新权限

### Q: 多租户应用如何工作?
**A**: 多租户应用允许任何 Azure AD 组织中的用户登录。每个租户的管理员需要为应用授予权限。

### Q: 刷新令牌过期?
**A**: MSAL 库会自动刷新令牌。如果刷新失败,用户需要重新运行 `auth` 命令。

## 测试认证

完成配置后,运行:

```bash
python main.py auth
```

按照提示在浏览器中授权,输入回调 URL 中的授权码,即可完成认证。

## 参考资源

- [Microsoft identity platform 文档](https://learn.microsoft.com/en-us/azure/active-directory/develop/)
- [MSAL Python 文档](https://learn.microsoft.com/en-us/azure/active-directory/develop/msal-python)
- [Graph API 权限参考](https://learn.microsoft.com/en-us/graph/permissions-reference)
