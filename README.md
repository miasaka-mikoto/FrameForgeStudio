# FrameForge Studio

AI 逐帧动画工作台，Windows 优先的本地桌面应用。项目按 `Project → Scene → Shot → Frame` 组织，每个 Frame 都是独立图片文件。开发阶段默认使用本地 Mock Image Provider，不会调用真实图片服务。

## 当前可运行功能

- PySide6 深色专业桌面界面：Project Browser、Viewer、Inspector、Storyboard、Timeline、Frame Browser、Generation Queue、Continuity、Prompt、Logs。
- 创建/打开项目，自动建立 `references/ characters/ scenes/ shots/ frames/ audio/ subtitles/ exports/ cache/ logs/ database/` 目录和 `project.json`。
- SQLite 项目数据库，角色 Character Master、Scene Master、Shot、Frame、Prompt、Generation Job、Audio、Subtitle、History 表。
- Mock Provider 逐帧生成、暂停/恢复队列、失败记录、关键帧标记、批准/锁定字段。
- 帧缩略图浏览、前后帧切换、播放、Onion Skin 叠加、Timeline 点击跳转、Storyboard 拖动排序。
- 相邻帧 Continuity Check：角色区域、色彩、构图、运动跳变和明确警告。
- WAV/MP3/AAC 导入记录、SRT 导入/导出、FFmpeg 图片序列到 MP4。
- 自动保存 `cache/autosave.json`、操作历史、日志、Sample Project。

## 首次启动

```powershell
cd FrameForgeStudio
python -m pip install -r requirements.txt
python -m frameforge
```

不带参数启动会在用户的 `Documents/FrameForgeStudio/FrameForge_Sample_Project`（没有 Documents 时使用应用数据目录）建立 Sample Project，内含角色、场景、3 个镜头、Mock Frames、静音音频和字幕。也可以显式指定：

```powershell
python -m frameforge --project D:\Projects\MyAnimation
python -m frameforge --sample D:\Projects\FrameForgeSample
```

## 日常工作流

1. `Project → New Project` 创建项目。
2. 在左侧 Project Browser 建立 Character 和 Scene；锁定 Character Master 后，把它作为生成基准。
3. 建立 Shot，填写动作、景别、镜头运动和 Prompt。
4. 选择 Shot，点击 `Generate Mock Frames` 建立队列并生成独立 PNG 帧。
5. 在 Frame Browser 查看帧；Viewer 支持前后帧、播放和 Onion Skin。
6. `Animation → Continuity Check` 检查相邻帧的角色、色彩、构图和运动变化。
7. 从 `Tools` 导入音频和 SRT；Timeline 显示视频、帧、音频、字幕轨。
8. `Render → Render MP4` 使用 FFmpeg 输出到 `exports/`『

## 测试

```powershell
cd FrameForgeStudio
pytest -q
```

不打开 GUI 的快速回归：

```powershell
python scripts\verify_core.py
```

测试覆盖项目创建、数据库 CRUD、Sample Project、Prompt 组合、Mock Provider、生成队列、连续性基础分析、SRT、FFmpeg command 和 Qt 界面冒烟。

## Windows 打包

在安装了 Python 3.12 的 Windows 环境中执行：

```powershell
scripts\build_windows.bat
```

输出位于 `dist\FrameForgeStudio\FrameForgeStudio.exe`。FFmpeg 不随应用强制捆绑；可把 `ffmpeg.exe` 放到 PATH，或在 `Tools → Settings → FFmpeg path` 指定路径。

PowerShell 用户也可以运行 `scripts\build_windows.ps1`。仓库还提供 `.github/workflows/windows-build.yml`，在 Windows runner 上生成可下载的 `FrameForgeStudio-Windows` artifact。

## 项目目录

```text
FrameForgeSTudio/
  frameforge/          # 应用和核心服务
  tests/               # 自动测试
  scripts/             # Windows 打包脚本
  FrameForge_Sample_Project/  # 首次运行自动创建（不提交源代码）
```

## 已预留接口

`ImageProvider (��统一定义 `generate / edit / variation / reference_generated​，以后可以接 Stable Defous�
