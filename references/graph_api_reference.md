# Microsoft Graph API 日历参考

## 核心端点

### 日历管理
- `GET /me/calendars` - 列出用户所有日历
- `GET /me/calendars/{id}` - 获取指定日历
- `POST /me/calendars` - 创建新日历
- `PATCH /me/calendars/{id}` - 更新日历属性
- `DELETE /me/calendars/{id}` - 删除日历

### 事件管理
- `GET /me/events` - 列出事件（默认主日历）
- `GET /me/calendars/{calendar-id}/events` - 列出指定日历事件
- `GET /me/events/{event-id}` - 获取事件详情
- `POST /me/events` - 创建事件
- `PATCH /me/events/{event-id}` - 更新事件
- `DELETE /me/events/{event-id}` - 删除事件

### 辅助 API
- `POST /me/getFreeBusySchedule` - 查询忙闲状态
- `POST /me/findMeetingTimes` - 推荐会议时间

## 事件对象结构

```json
{
  "subject": "会议主题",
  "body": {
    "contentType": "HTML",
    "content": "会议内容..."
  },
  "start": {
    "dateTime": "2025-04-21T14:00:00",
    "timeZone": "China Standard Time"
  },
  "end": {
    "dateTime": "2025-04-21T15:00:00",
    "timeZone": "China Standard Time"
  },
  "location": {
    "displayName": "会议室A"
  },
  "attendees": [
    {
      "emailAddress": {
        "address": "user@example.com",
        "name": "张三"
      },
      "type": "required"
    }
  ],
  "isAllDay": false,
  "allowNewTimeProposals": true
}
```

## 查询参数

常用 OData 查询选项：
- `$select` - 选择特定字段
- `$filter` - 筛选条件
- `$top` - 限制返回数量
- `$orderby` - 排序

示例：
```
GET /me/events?$select=subject,start,end&$filter=start/dateTime ge '2025-04-21T00:00:00'
```

## 权限范围

| 权限 | 类型 | 说明 |
|------|------|------|
| `Calendars.Read` | 委托 | 读取日历和事件 |
| `Calendars.ReadWrite` | 委托 | 读写日历和事件 |
| `Calendars.Read.Shared` | 委托 | 读取共享日历 |
| `Calendars.ReadWrite.Shared` | 委托 | 读写共享日历 |

## 错误处理

常见错误码：
- `403` - 权限不足
- `404` - 资源不存在
- `409` - 冲突（如事件重复）
- `429` - 请求频率限制
- `503` - 服务暂时不可用

## 速率限制

Graph API 默认限制：
- 10000 请求/10分钟/应用
- 实际限制可能因租户而异

建议：实现指数退避重试机制。

## 参考资源

- [官方文档](https://learn.microsoft.com/en-us/graph/api/resources/calendar)
- [API 参考](https://learn.microsoft.com/en-us/graph/api/event-post-events)
- [SDK for Python](https://learn.microsoft.com/en-us/graph/sdks/sdks-overview)
