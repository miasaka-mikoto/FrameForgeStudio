# FrameForge Studio 验证报告

验证日期：2026-10-04

## 自动测试

命令：

```text
pytest -q
9 passed (latest run: 3.31s)
```

覆盖：

- Project 目录、`project.json`、SQLite 初始化
- Character / Scene / Shot / Frame CRUD
- Autosave 与重新打开恢复
- Sample Project（1 Character、1 Scene、3 Shots、24 Mock Frames、WAV、SRT）
- Prompt 组合与 In-between Prompt
- Mock Provider 与连续性基础分析
- Generation Queue 状态回调
- Duplicate interpolation provider baseline
- FFmpeg command 生成
- SRT 写入与解析
- PySide6 MainWindow offscreen 冒烟

## 真实工作流回归

临时 Sample Project 中执行：

1. 打开 PySide6 MainWindow
2. 选择 `SHOT_001`
3. Viewer / Frame Browser / Timeline 初始化
4. 运行 Continuity Check：7 组相邻帧
5. Timeline 显示 Video、Frame、Audio、Subtitle 轨
6. FFmpeg 生成 `exports/regression.mp4`

结果：通过。MP4 文件实际存在并可读取，测试输出约 11 KB（低分辨率回归样本）。

另一个 Qt 回归在新建 `SHOT_QUEUE` 后执行了真实生成队列，2 个 Mock Frames 均到达 `Done`。

## 打包验证

命令：

```text
pyinstaller --noconfirm --clean --windowed --name FrameForgeStudio frameforge_launcher.py
```

结果：Linux 开发版 one-folder 构建成功，`dist/FrameForgeStudio/FrameForgeStudio` 可执行，`--help` 输出正常。

随后使用冻结程序在 `QT_QPA_PLATFORM=offscreen` 下启动 3 秒并指定新的 Sample 路径；程序成功创建 `project.json` 和 24 张帧（进程因测试超时主动结束，退出码 124，非应用崩溃）。

Windows `.exe` 必须在 Windows 环境执行 `scripts\\build_windows.bat`，因为 PyInstaller 不跨平台生成 Windows 二进制。
