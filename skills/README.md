# Agent Skills

[English](#use-the-eq-skill) · [简体中文](#使用-eq-skill)

HXAudio MCP includes a skill that helps your Agent choose a device, back up its EQ, make targeted adjustments, and read back the result.

| Skill | What it helps you do |
| --- | --- |
| [hxaudio-eq](hxaudio-eq/SKILL.md) | Read and adjust PEQ, preamp, and virtual bass; back up and restore settings; import HXAudio presets. |

## Use the EQ skill

1. Install HXAudio MCP and connect it to your Agent client using the [setup guide](../README.md#mcp-client-configuration).
2. Copy the `hxaudio-eq` folder into your client's skills directory, or register [SKILL.md](hxaudio-eq/SKILL.md) using your client's skill-loading mechanism.
3. Ask your Agent: “Read my speaker EQ, save a backup, then set the 3.7 kHz band's gain to 0 dB while keeping all other settings.”

The MCP server provides the audio tools; the skill supplies the workflow. Both must be available to the Agent. Adjustments apply to HXAudio on the same Windows computer as the server.

## 使用 EQ Skill

1. 按[配置指南](../README.zh-CN.md#配置-mcp-客户端)安装 HXAudio MCP，并连接 Agent 客户端。
2. 将 `hxaudio-eq` 文件夹复制到客户端的技能目录，或通过客户端的技能加载功能注册 [SKILL.md](hxaudio-eq/SKILL.md)。
3. 向 Agent 提出请求：“读取音箱 EQ，先备份，再把 3.7 kHz 频段的增益设为 0 dB，其他设置保持不变。”

MCP 服务提供音频工具，Skill 提供操作流程，二者需要同时提供给 Agent。调节作用于 MCP 服务所在 Windows 电脑上的 HXAudio。
