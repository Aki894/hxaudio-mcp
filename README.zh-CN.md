# HXAudio MCP

[English](README.md) · [技术架构](docs/ARCHITECTURE.md) · [开源协议](LICENSE)

**让 AI Agent 直接控制 HXAudio APO。** HXAudio MCP 是一个运行在 Windows 本机的 [Model Context Protocol（MCP）](https://modelcontextprotocol.io/) 服务，可通过标准工具接口读取和修改 HXAudio 参数均衡器（PEQ）、前级增益及虚拟低音，无需模拟鼠标操作。

服务采用 **stdio** 通信，通过 Windows 注册表与 HXAudio 的配置刷新机制应用参数。

## 功能特性

- **参数均衡器：** 按音频设备读取、替换、添加、更新或删除 PEQ 滤波器。
- **音效控制：** 调整前级增益与虚拟低音，查看其他可用的 DSP 设置。
- **实时生效：** 通知 APO 重新加载配置，并查询运行状态。
- **配置管理：** 备份与恢复 DSP 参数，从 `.hxconfig` 文件读取或导入 PEQ 曲线。
- **Agent 集成：** 提供标准 MCP 工具、可选的 [EQ Skill](skills/hxaudio-eq/SKILL.md) 和只读模式。

## 运行要求

- Windows 10 / 11，已安装并配置 **HXAudio APO**。
- Python **3.11 或更高版本**。
- 支持 MCP 的客户端与 HXAudio 运行在**同一台 Windows 电脑**。
- 具有修改 HXAudio 注册表设置的权限；在 APO 模式下修改系统级注册表通常需要管理员权限。

## 安装

在项目目录中打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

查看 HXAudio 设备和当前配置：

```powershell
hxaudio-mcp doctor
hxaudio-mcp devices
hxaudio-mcp eq
```

> **说明：** `hxaudio-mcp serve` 会启动 stdio MCP 服务，通常由 MCP 客户端自动拉起；它不会打开网页或进入交互式命令行。

## 配置 MCP 客户端

以下路径均为示例，请替换成 `.venv\Scripts\hxaudio-mcp.exe` 在你电脑上的**绝对路径**。

### Codex

在 `~/.codex/config.toml` 中添加：

```toml
[mcp_servers.hxaudio]
command = 'C:\path\to\hxaudio-mcp\.venv\Scripts\hxaudio-mcp.exe'
args = ['serve']
startup_timeout_sec = 20
tool_timeout_sec = 30
```

也可以通过 Codex CLI 添加：

```powershell
codex mcp add hxaudio -- "C:\path\to\hxaudio-mcp\.venv\Scripts\hxaudio-mcp.exe" serve
```

### 使用 `mcpServers` JSON 的客户端

适用于支持 `mcpServers` 配置格式的 MCP 客户端：

```json
{
  "mcpServers": {
    "hxaudio": {
      "command": "C:\\path\\to\\hxaudio-mcp\\.venv\\Scripts\\hxaudio-mcp.exe",
      "args": ["serve"]
    }
  }
}
```

MCP 客户端会在本机启动服务进程。执行 APO 写入操作时，该进程需要具有相应的 Windows 注册表权限，通常继承自 MCP 客户端。

## MCP 工具

| 工具 | 功能 |
| --- | --- |
| `doctor` | 检查安装模式、设备配置和 APO 状态。 |
| `list_audio_devices` | 列出设备及其 `profile_key`。 |
| `get_eq` | 读取指定设备的 PEQ 参数。 |
| `set_eq` | 整体替换 PEQ 频段列表。 |
| `add_eq_band` | 添加或插入滤波器。 |
| `update_eq_band` | 修改指定滤波器的部分参数。 |
| `remove_eq_band` | 删除指定滤波器。 |
| `clear_eq` | 清除设备的所有 PEQ 频段。 |
| `get_preamp` / `set_preamp` | 查询或调整前级增益。 |
| `get_virtual_bass` / `set_virtual_bass` | 查询或调整虚拟低音。 |
| `inspect_dsp_settings` | 只读查看其他 DSP 注册表参数。 |
| `apply_eq` | 请求重新加载 DSP 设置。 |
| `get_runtime_status` | 查看 APO 刷新及运行状态。 |
| `wait_for_apo_apply` | 检查指定配置代际是否已被 APO 处理。 |
| `backup_eq` | 备份当前 DSP 设置。 |
| `restore_eq_backup` | 根据备份 ID 恢复设置，需 `confirm=true`。 |
| `inspect_hxconfig` | 读取 HXCF v1 `.hxconfig` 中的 PEQ 曲线。 |
| `import_eq_from_hxconfig` | 从 HXCF v1 配置导入 PEQ 曲线。 |

### PEQ 参数格式

每个滤波器由以下字段组成：

```json
{
  "enabled": true,
  "type": "PK",
  "frequency_hz": 1000,
  "gain_db": -2.5,
  "q": 1.0
}
```

支持 `PK`（峰值）、`LS`（低架）、`HS`（高架）、`LP`（低通）、`HP`（高通）、`NO`（陷波）。频段索引从 **0** 开始。

`set_eq` 会整体替换该设备的 EQ 曲线；只调整一个频段时优先使用 `update_eq_band`。如有多个设备，应传入 `list_audio_devices` 返回的准确 `profile_key`。

## Agent 使用示例

> 读取扬声器的 EQ 设置，先备份当前配置。

> 把第一个滤波器的增益调整为 −2 dB，频率和 Q 值保持不变。

> 添加一个 250 Hz、−1.5 dB、Q 为 1.2 的峰值滤波器，再显示当前曲线。

> 将 `C:\Users\me\Music\preset.hxconfig` 中的 PEQ 曲线导入当前选定的扬声器。

## 配置与使用说明

- **只读模式：** 在 MCP 客户端环境变量中设置 `HXAUDIO_MCP_READ_ONLY=1`，即可禁用写入操作。
- **备份位置：** `%LOCALAPPDATA%\HXAudioMCP\backups`。恢复时提供 `backup_eq` 返回的 ID，并设置 `confirm=true`。
- **GUI 并存：** HXAudio EQ 编辑器保持打开时，MCP 仍可写入并实时应用参数。避免随后在 GUI 中保存旧状态，覆盖较新的 MCP 修改。
- **音量余量：** 提升 EQ 增益时，建议合理调整前级增益，避免削波失真。
- **本地运行：** 服务使用 stdio，不开放 HTTP 接口；操作的是当前 Windows 电脑上的 HXAudio 配置。

有关注册表交互和项目结构，参见 [技术架构](docs/ARCHITECTURE.md)。

## 开源协议

[MIT](LICENSE)。本项目是独立开发的 HXAudio MCP 集成工具，与 HXAudio 开发者无隶属关系，仓库不包含 HXAudio 软件本体。

## 声学校准

安装可选依赖 `python -m pip install -e ".[calibration]"` 后重启 MCP 服务。
先用 `calibration_check` 识别播放、采集和 HXAudio 配置，再用
`calibration_measure` 采集。分别测量左右声道后，可将两个测量 ID
交给 `calibration_optimize`，预览一套共享 EQ。需要逐频复测时，
传入 `tones_hz`，并用 `calibration_compare_tones` 比较应用前后的结果。
自动校准入口为 `calibration_run`，使用 `keep=false` 试跑时会恢复起始配置。

路由设置、工具参数和对数扫频命令见 [声学校准指南](docs/CALIBRATION.md)。
