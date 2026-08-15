# Codex Cache Hit Rate

一个本地 Codex 插件：每轮请求完成后，在回复后按模型显示当前轮次上下文内所有模型调用的 prompt cache 命中率。

显示示例：

```text
缓存命中率（按模型）：
- gpt-5.6-sol：87.58% · 缓存输入 68,352 / 78,054 tokens · 10 次调用
- gpt-5.6-luna：92.00% · 缓存输入 11,776 / 12,800 tokens · 2 次调用
```

## 工作方式

插件注册一个 `Stop` hook。Codex 完成一轮回复时，hook 从 Codex 提供的 transcript 中只读取模型标识与 token 统计字段，并按模型汇总当前轮次上下文内的所有模型调用：

```text
命中率 = cached_input_tokens / input_tokens * 100%
```

缓存写入 token 不计为命中；如果 Codex 返回缓存写入统计，插件会单独显示。脚本仅使用 Python 标准库，不发送网络请求，也不读取或显示对话正文。

Codex 当前不允许 hook 修改已经生成的助手消息，因此结果会以紧跟回复的 Codex system message 显示，而不是改写助手正文。

## 本地安装

在仓库根目录运行：

```bash
codex plugin marketplace add "$PWD"
codex plugin add codex-cache-hit-rate@personal
```

安装后新建一个 Codex 对话。Codex 首次加载 hook 时会要求审查并信任 hook 定义；确认后，后续每轮结束都会显示统计。

也可以从这个私有 GitHub 仓库安装：

```bash
codex plugin marketplace add NietzscheLi/codex-cache-hit-rate
codex plugin add codex-cache-hit-rate@personal
```

访问私有仓库需要本机 Git/GitHub 凭据具备相应权限。

## 兼容性

- 已在 `codex-cli 0.147.0` 上验证。
- Codex 官方将 transcript 格式标记为非稳定接口。脚本优先解析当前的 `token_count` 结构；如果轮次标记发生变化，会降级为显示最近一次模型调用的数据。
- 若 Codex 没有提供 token 统计，插件显示“暂无可用 token 统计”，并且不会影响正常回复。

## 开发与测试

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile plugins/codex-cache-hit-rate/scripts/cache_hit_rate.py
```

插件清单位于 `plugins/codex-cache-hit-rate/.codex-plugin/plugin.json`，hook 配置位于 `plugins/codex-cache-hit-rate/hooks/hooks.json`。
