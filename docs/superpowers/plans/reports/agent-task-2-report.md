# Agent Task 2 报告 —— 真实 MCP 客户端

- 计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 2: 真实 MCP 客户端」
- 状态：DONE
- 日期：2026-10-18

## 改动的文件

| 文件 | 变更 |
| --- | --- |
| `agent/mcp_http_client.py` | 新增。真实 `McpHttpClient(mcp_contract.McpClient)` 实现 |
| `agent/tests/test_mcp_client.py` | 新增。15 条用例，全部走注入的 `session_factory`，不连网 |
| `requirements.txt` | 追加 `mcp==2.3.0`（原有 3 条保留） |

## 实现要点

- `McpHttpClient(config, session_factory=None)`；`interface_version` 取自注入配置。
- 连接方式照 `hadoop/tools/mcp_client.py:269-288`：`create_mcp_http_client(headers=...)`
  → `streamable_http_client(url, http_client=...)` → `ClientSession`，每次动作开一次短会话。
  鉴权头 `Authorization: Bearer <口令>` 只进请求头。
- 单一路径 `_run(worker)`：`worker` 为 `async def`；会话工厂注入时走假会话，否则真实建连。
  已按计划注释删除冗余——`list_tools` 不含死局部 `worker`，也无重复的 `_run_async_list`；
  列工具/调工具共用同一个 `_run`。
- `call_tool`：工具文本项拼接后 `json.loads` 得信封；非 JSON / 非对象 → 结构化
  `DRIVER_BAD_OUTPUT`。
- 配置不可用（无 url 或无 token）：`call_tool` / `get_task_status` /
  `describe_capabilities` / `health` / `list_tools` 一律结构化 `MCP_UNAVAILABLE`，**不连网**。
- 连接/协议异常翻成可读说明（`explain`，含异常组摊平）：区分 401（凭据）/ 连接被拒（连不上）/
  其它（调用失败）→ 结构化 `DRIVER_UNREACHABLE`。**口令绝不出现在任何返回消息里**。
- 公开面：`list_tools()`、`call_tool(name,args)`、`get_task_status(task_id)`、`health()`、
  `describe_capabilities()`、`negotiate_version(peer=None)`（继承基类）、`close()`（继承）。

## 测试命令与结果

命令：

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests/test_mcp_client.py -v
```

结果：`15 passed in 0.07s`

回归（计划要求 `test_stage8_mcp_seam.py` 不受影响，且未修改它）：

```
...\python.exe -m pytest agent/tests/test_mcp_client.py agent/tests/test_stage8_mcp_seam.py -v
```
→ `45 passed in 4.71s`

全量：

```
...\python.exe -m pytest agent/tests -q
```
→ `316 passed, 116 subtests passed in 40.00s`

真实路径冒烟（不在测试文件内，手动验证 SDK 接线与错误翻译）：

```
...\python.exe -c "..."  # 指向 127.0.0.1:1（关闭端口），配置可用、不注入假会话
```
→ `call_tool -> False DRIVER_UNREACHABLE`；消息含「连不上 … 原始错误：… ConnectError」；
`token leaked: False`；`health -> DRIVER_UNREACHABLE`；`list_tools -> []`。

## 关注点 / 备注

- 已装版本实测为 `mcp==2.3.0`，与计划「与 Hadoop 侧同版本」一致，故精确固定为 `mcp==2.3.0`。
- `health()` 复用 `McpConfig.health_url()`（免口令 GET `/health`，不走协议入口）。
- `_run` 使用 `asyncio.run`，要求在无运行中事件循环的同步上下文调用；当前调用点均为同步。
- 未改动 `agent/tests/test_stage8_mcp_seam.py`；真实客户端目前尚未被运行期装配
  （Task 9 负责），故对既有链路零影响。
