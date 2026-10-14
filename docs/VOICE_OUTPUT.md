# 独立语音输出

适配基线：上游 `main` 的 `b424c64`（2.5.0）。此功能独立于此前的互动菜单、队列、模型池和画像 PR。

`scripts/voice_output/` 只接收最终文字、群/用户/消息编号与文字回退回调。
上游接线只有聊天最终输出、断线清理、服务关闭三处；朗读使用原有 `@register`。
模型、人设、内容硬拦截、聊天去重、发言记录和语音转文字保持原有规则。

## 先使用 Edge TTS

在机器人虚拟环境执行，Edge 是可选依赖，其他 TTS 不需要安装：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python -m pip install -r requirements-voice.txt
```

默认 `config/voice_output.json` 的群列表为空，不会合成语音或发出语音网络请求。
给 `groups` 增加实际群号（下面是占位示例）：

```json
"groups": {
  "123456789": {
    "enabled": true,
    "mode": "text_and_voice",
    "voice": "edge-xiaoxiao"
  }
}
```

同时确认原有 `config/group_features.json` 中该群已开启 `commands`（朗读命令）或
`decision_engine`（自动回复）。语音配置不能绕过这些原有开关。

- `@bot 朗读 你好呀`：直接朗读，不调用模型。需空格分隔命令和内容。
- `mode: "off"`：不朗读聊天回复，仍可单独启用朗读命令。
- `mode: "text_and_voice"`：正常文字先发，语音在独立有界队列中处理；失败不重复发文字。
- `mode: "voice_only"`：成功时只发语音；不符合长度、冷却、容量条件，或合成/发送失败、等待过期时回退原文字一次。
- `only_at: true`：仅明确 @ 或原有别名触发的聊天回复参与语音；自主接话仍是文字。设为 `false` 才包含自主接话。
- `read_command: false`：关闭该群的朗读命令。

配置按 mtime/文件大小缓存，相关事件到来时更新，不轮询。群字段逐项继承 `defaults`；
未列入 `groups` 的群始终关闭。已有队列保存入队时的音色、参数和截止时间；修改配置仅影响新请求。
配置损坏时停止新语音准入，普通文字仍可用。此模块不写回群配置，不覆盖额外字段。

可在 `.env` 中设置 `QQBOT_VOICE_CONFIG` 为另一份配置的绝对路径，方便不同机器人使用各自配置，
或将私人群号、服务地址保存在仓库外。运行时配置不要提交公开仓库。

## NapCat 发送设置

需在 NapCat 中启用 OneBot HTTP **服务端**，示例地址为 `http://127.0.0.1:3000`；
根据实际配置修改 `napcat.base_url`。WebUI 管理页常用的 `6099` 端口与 OneBot API 是不同服务。
如果配置了访问令牌，在环境变量或 `.env` 中设置 `NAPCAT_HTTP_TOKEN`；JSON 只存变量名，不存密钥。

机器人把音频作为独立 `record` 段发到 `/send_group_msg`，检查 HTTP 状态、OneBot 的 `status/retcode`
及 `message_id`。文字里的 @ 不与语音段混合。使用 Base64 内联，不要求两端共享目录，也不产生待清理音频文件。
请求体分块编码并让出事件循环，避免一次性构造整份大型 Base64/JSON。NapCat 负责 Silk 编码和上传。

发送结果不明时不自动重发，避免群中出现两条相同语音。`voice_only` 此时会回退文字；若 NapCat
稍后完成上传，可能同时看到语音和回退文字。这不是“发送失败一定没有上传”的保证。
真实 QQ 手机/电脑客户端的显示、播放、转码支持和 HTTP 分块请求兼容性需在部署时验收。

## GPT-SoVITS HTTP 接口

适配的是官方 **`api_v2.py` 的 `/tts` 接口**。`api_v2` 是 API 脚本名称，模型版本仍可为 V4。
机器人不安装 Torch、不加载权重、不启动 GPU 服务，也不调用切换权重或 `/control` 接口。

1. 先在独立目录部署 GPT-SoVITS，并确认所选音色能在其自身推理界面正常生成声音。
2. 在该服务的推理配置中选择 `version: v4`，配置与模型匹配的 `t2s_weights_path`、
   `vits_weights_path` 及基础资源。仅有一个权重文件时，先确认它属于哪一部分及所需配套权重，勿靠文件名猜测。
3. 准备参考音频及其准确文本；参考音频路径是 **GPT-SoVITS 服务所在机器**上的路径。
4. 在 GPT-SoVITS 自己的环境启动 API，例如：

```powershell
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

将 `config/voice_output.gpt-sovits.example.json` 复制为你的运行配置，修改群号、`endpoint`、
`ref_audio_path`、`prompt_text` 和语言。为目标群选择 `voice: "my-sovits"` 即可，其他群仍可选 Edge。
示例中的参考音频路径是占位值，`prompt_text` 留空，使用时填写与参考音频一致的文本。
模型权重、参考音频、实际参考文本、角色 prompt 和私人运行配置均由部署者自行保管，不随接口提交。

`params` 透传该 API 的推理参数；适配器固定使用本条正文、`media_type: wav`、`streaming_mode: false`，
默认 `batch_size: 1`。下载仍使用异步流式读取和字节上限，不会先读完未知大小的响应。
这里不采用语音碎片逐条发送，避免一段回答拆成多条 QQ 语音。

**远端超时保护：** 取消 HTTP 等待不能证明 GPT-SoVITS 的 GPU 工作已经停止。
合成超时、传输中断或中途断线取消后，该 HTTP 端点在机器人内暂停，后续请求不会继续堆到同一服务。
确认服务已完成/恢复后，保存语音配置（更新 mtime）或重启机器人以恢复准入。
暂停期间，附带语音模式保留文字，只发语音模式回退文字。实际推理较慢时，可根据测量调整
`synthesis_timeout_seconds`；不要靠增加并发掩盖服务未完成的工作。

不同权重建议使用不同的 GPT-SoVITS 服务实例/端点。不要在同一服务上逐请求调用全局切权重接口，
否则多个群可能互相串音色。本模块不管理模型权重或角色人设，仅负责请求合成与发送音频。

## 其他 TTS

对于接收 JSON、返回 WAV/MP3 二进制的服务，可直接添加 `http` 音色：

```json
"my-tts": {
  "provider": "http",
  "endpoint": "http://127.0.0.1:9000/speak",
  "text_field": "input",
  "format": "wav",
  "token_env": "MY_TTS_TOKEN",
  "params": {"speaker": "my-speaker"}
}
```

`text_field` 指定正文的顶层字段；其他固定参数放在 `params`。
当前通用适配器不猜测 JSON 内的音频 URL、鉴权签名或 Gradio 队列协议。
这些服务可增加独立适配器，保持以下异步接口：

```python
async def synthesize(text, voice_config, http_client, max_audio_bytes) -> Audio:
    ...
```

将适配器加入 `providers.PROVIDERS` 即可；不要在适配器里发送 QQ 消息或发起额外 LLM 调用。
适配器须及时响应取消、限制响应大小，并返回经过验证的音频。

## 运行开销与默认限制

以下是个人试用的暂定值，均可配置：

| 项目 | 默认值 |
| --- | --- |
| 同时运行的语音任务 | 1 |
| 全局等待 / 每群等待 | 4 / 2 |
| 每群同时合成并发送 | 1 |
| 排队期限 / 合成期限 / 发送期限 | 30 / 30 / 10 秒 |
| 单条文字 | 200 字，超长不另调模型缩写 |
| 同群 / 同群同用户冷却 | 5 / 10 秒 |
| 同群同用户拒绝提示冷却 | 10 秒 |
| 单条原始音频 / 全局音频预算 | 8 / 32 MiB |

全局预算在合成开始前按单条上限预留，包含正在下载/合成、等待发送的原始音频。
它不是整个 Python 进程的 RSS 上限；HTTP、音频校验和对象本身还会使用内存。
音频仅在任务内保留，不做永久缓存，不落日志。日志只记录群号、提供方、原因和分阶段耗时。

名额在实际异步任务结束后释放，取消不会提前让新任务进入；原有模型并发名额完全不参与语音队列。
排队截止时间只限制等待，已开始的合成受自己的超时控制。到达截止时间的等待项在下一次调度时回退，
不创建周期清理器。调度只由提交与任务完成触发，同群串行，空闲名额可跳过忙碌群处理其他群。

## 离线验证

在虚拟环境、项目根目录执行：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python -m unittest discover -s tests -p 'test_voice*.py' -v
python -c "import scripts.voice_output; import reverse_bot; print('UTF-8 import OK')"
```

测试只读取临时配置，签到/菜单数据使用临时目录；TTS/NapCat 为模拟传输和本地 HTTP 桩，不调用真实 QQ、
Edge、模型或画像服务。覆盖文字先发、语音模式回退、队列突发/过期/同群串行、跨群调度、冷却/去重、
音色快照、坏配置、断线清理、超时暂停、损坏/超限音频、业务回执，以及原有命令和权限回归。

本次结果（2026-10-15，Python 3.10 虚拟环境）：44 项离线测试通过，UTF-8 导入自检通过；
Edge TTS 7.3.1 已安装并验证导入，未实际请求 Edge 服务。
本地 HTTP 桩验证了流式下载和分块上传。独立部署另已通过 GPT-SoVITS V4 → NapCat → QQ
的朗读和聊天语音收听验收；相关账号、音色、权重、prompt、音频与日志不包含在此变更中。
其他音色、版本及客户端组合仍需各部署者自行验证。

协议参考：

- [GPT-SoVITS api_v2.py](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/api_v2.py)
- [GPT-SoVITS V4 配置](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/GPT_SoVITS/configs/tts_infer.yaml)
- [Edge TTS](https://github.com/rany2/edge-tts)
- [NapCat 音频处理](https://napneko.github.io/develop/file)
