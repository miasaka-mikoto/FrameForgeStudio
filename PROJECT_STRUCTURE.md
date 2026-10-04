# 项目目录说明

```text
FrameForgeStudio/
├─ frameforge/                    # 应用源码
│  ├─ app.py                      # CLI、启动与日志
│  ├─ ui.py                       # PySide6 工作台界面
│  ├─ project.py                  # 项目目录、JSON、CRUD、Sample
│  ├─ db.py                       # SQLite schema 与访问层
│  ├─ providers.py                # ImageProvider、Mock Provider、Queue
│  ├─ continuity.py               # 帧间连续性分析
│  ├─ prompting.py                # Prompt 模板与组合
│  ├─ render.py                   # FFmpeg command 与渲染
│  ├─ media.py                    # 音频长度、SRT
│  └─ config.py                   # 用户设置
├─ tests/                         # 自动测试
├─ scripts/build_windows.bat      # Windows 打包
├─ FrameForge_Sample_Project/     # 可直接打开的 Sample Project
├─ dist/FrameForgeStudio/         # 当前环境生成的 Linux 开发版
├─ frameforge_launcher.py         # PyInstaller 入口
├─ README.md
└─ TEST_REPORT.md
```

项目实例目录会自动创建：

```text
project/
├─ references/
├─ characters/
├─ scenes/
├─ shots/
├─ frames/<scene>/<shot>/frame_0001.png
├─ audio/
├─ subtitles/
├─ exports/
├─ cache/
├─ logs/
├─ database/project.sqlite3
└─ project.json
```

