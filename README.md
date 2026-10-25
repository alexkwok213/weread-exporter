<div align="center">
<img src="icons/preview-256.png" width="112" alt="微信读书导出助手图标">

# 微信读书导出助手

把 Mac 上已下载的微信读书 EPUB / TXT 缓存，整理为包含正文、目录和配图的本地 EPUB。

</div>

这是一个非官方 macOS 工具。SwiftUI 提供书籍列表，Python 标准库负责读取缓存和生成 EPUB。不需要 AI API、账号密码或登录凭据。

## 能做什么

- 自动发现本机缓存，显示下载是否完整、导出结果和失败原因。
- 按书名或作者搜索、按状态筛选，单本或批量导出。
- TXT 缓存按目录中的章节 UID 读取，按章节顺序转换为 EPUB，保留正文空格与段落换行。
- 可选自动导出：新安装默认开启，每 15 秒检查一次稳定且完整的缓存，将所有符合条件的书排队连续导出；单本失败后继续处理其余书籍，失败项至少等待 120 秒再重试。
- 保留正文和目录，嵌入配图，并在保存前验证 ZIP、XML 和本地资源引用。
- 支持 `Text/` 或书籍根目录中的 `.xhtml`、`.html` 正文，以及 `Styles/`、`css/` 样式。
- 兼容图片网址占位缓存、`&nbsp;` 和值相同的重复 `data-*` 属性。缺失的列表装饰图使用 CSS 原有文字符号回退，正文配图和其他必需资源仍严格检查。

## 先用命令行试一下

要求 macOS、Python 3.10+，以及已在本机下载 EPUB 或 TXT 书籍的微信读书应用。当前实测环境为 Apple Silicon；Windows、网页微信读书和 PDF 书籍不在支持范围内。

```bash
git clone https://github.com/alexkwok213/weread-exporter.git
cd weread-exporter
python3 src/worker.py scan
```

扫描返回 JSON。找到需要导出的书籍 `id` 后，替换下面的示例值：

```bash
python3 src/worker.py export --id '账号数字:书籍数字'
```

默认导出到 `~/Downloads/微信读书导出`。指定目录：

```bash
python3 src/worker.py export --id '账号数字:书籍数字' --output "$HOME/Downloads/MyBooks"
```

运行 `python3 src/worker.py --help` 查看选项。状态和补充配图缓存默认保存在 `~/Library/Application Support/WeReadExporter/`，不会写回微信读书源文件。

如果系统拒绝读取微信读书缓存，请为运行命令的终端应用授权完全磁盘访问权限，然后重新启动终端。

## 从源码构建 Mac 应用

需要 Xcode Command Line Tools（`xcode-select --install`）和**可搬移的独立 Python 运行时**。普通 Homebrew Python 或带外部绝对链接的虚拟环境不能直接作为随应用分发的运行时。

可从 [python-build-standalone 官方 Releases](https://github.com/astral-sh/python-build-standalone/releases) 获取适合机器架构的 CPython `install_only` 包，解压后将包含 `bin/`、`lib/` 的目录传入构建脚本：

```bash
python3 scripts/build.py --python-runtime /path/to/standalone/python
```

构建产物为 `dist/微信读书导出助手.app`。脚本会编译 Swift、打包 Python，并验证本地临时签名；请保留 Python 运行时自带的许可证文件。脚本不会覆盖已有的同名构建产物。

将应用放入「应用程序」，启动后：

1. 在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」添加这一份应用。
2. 退出并重新打开应用，点击「刷新书籍」。
3. 等待书籍在微信读书中下载完整后，点击「导出」。

本项目目前提供源码构建流程，没有 Apple 公证安装包。**重新构建或重新签名后，macOS 可能要求移除旧授权条目再重新添加。** 请避免同时运行多个同名副本。

## 常见情况

| 现象 | 怎么处理 |
| --- | --- |
| 无法读取缓存 | 检查运行的应用与完全磁盘访问权限中的副本是否一致；重新授权后重启 |
| 正文未下载完整 | 回到微信读书完成下载，再刷新 |
| 配图下载失败 | 检查网络后重试；失败时不会保存不完整 EPUB |
| 目录或缓存格式不支持 | 提交错误文本和目录结构；不要上传书籍正文、数据库或账号数据 |

缓存布局属于微信读书内部实现，后续版本可能变化。识别完整缓存和结构验证，不等于逐页校对排版；目前没有自动化视觉阅读器测试。

## 数据与网络

只读取当前用户的微信读书缓存和目录数据库，不修改源书籍。导出时会优先使用本地配图；缺图时可请求微信读书的 `res.weread.qq.com` 图片地址。没有分析统计或第三方上传功能。

仓库不包含书籍正文、导出的 EPUB、账号数据、真实缓存、Python 运行时或构建好的应用。请仅处理您有权访问和使用的内容；书籍和微信读书品牌权利归相应权利人所有。

## 开发与测试

```bash
python3 scripts/test.py
```

测试使用合成正文和小图片，覆盖 TXT 章节映射、缺章与损坏缓存、根目录与 `.html` 布局、图片网址占位、空格实体、重复属性、路径限制，以及 Swift 列表筛选、批量导出、取消和权限错误处理；不需要真实书库。

```text
src/        SwiftUI 界面、Python 扫描与导出核心
tests/      合成数据回归测试与 Swift 模型测试
scripts/    构建和测试入口
icons/      图标生成源码及预览
```

## 许可证

项目代码采用 [MIT License](LICENSE)。书籍内容、微信读书品牌及随应用使用的 Python 运行时不因本许可证而改变其原有权利或许可。
