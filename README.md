# Video2LivePhoto

将视频（和可选的静态图片）转换为 iOS 可识别的 Live Photo 格式，支持设置为动态壁纸。

## 功能特性

- ✅ 生成符合 iOS 标准的 Live Photo（HEIC/JPEG + MOV 配对）
- ✅ 支持设置为 iOS 动态壁纸
- ✅ 自动提取视频帧作为静态图片（可选）
- ✅ 支持 HEIC 和 JPEG 输出格式
- ✅ 智能码率控制，确保 iOS 兼容性
- ✅ 完整的 Live Photo 元数据注入（content identifier、still-image-time、live-photo-info）
- ✅ 内置验证功能，检查生成的文件是否符合标准

## 安装

### 依赖项

```bash
pip install -r requirements.txt
```

### FFmpeg

需要安装 FFmpeg（包含 libx264/libx265 编码器）。程序会自动检测系统 PATH 中的 ffmpeg/ffprobe，或使用 imageio-ffmpeg 提供的版本。

**Windows 用户**：推荐从 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下载完整版 FFmpeg。

## 使用方法

### 基本用法

```bash
# 使用视频和静态图片生成 Live Photo
python video2livephoto.py -v video.mp4 -i photo.jpg -o output

# 仅使用视频，并自动提取第一帧作为静态图片（LivePhoto的封面）
python video2livephoto.py -v video.mp4 -o output

# 指定视频时间点作为静态图片（LivePhoto的封面）
python video2livephoto.py -v video.mp4 -o output --still-time 1.5
```

### 高级选项

```bash
# 指定输出格式（HEIC 或 JPEG）
python video2livephoto.py -v video.mp4 -o output --image-format jpeg

# 裁剪视频时长
python video2livephoto.py -v video.mp4 -o output --duration 3.0

# 自定义视频质量（CRF 模式，0-51，越小质量越高）
python video2livephoto.py -v video.mp4 -o output --crf 18

# 自定义视频码率（Mbps）
python video2livephoto.py -v video.mp4 -o output --bitrate 15 --maxrate 18

# 自定义输出文件名
python video2livephoto.py -v video.mp4 -o output --name my_livephoto
```

### 验证现有文件

```bash
# 验证 Live Photo 配对是否符合 iOS 标准
python video2livephoto.py --verify photo.heic video.MOV
```

## 参数说明

| 参数 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| `--video` | `-v` | 输入视频文件（必需） | - |
| `--image` | `-i` | 输入静态图片（可选） | 自动提取 |
| `--output-dir` | `-o` | 输出目录 | `./output` |
| `--image-format` | `-fmt` | 静态图片格式：`heic` 或 `jpeg` | `heic` |
| `--duration` | `-d` | 裁剪视频时长（秒） | 不裁剪 |
| `--still-time` | `-st` | 指定哪一个时间点（秒）作为静态图片 | `0.0` |
| `--crf` | - | 视频质量（0-51，越小越好） | `None`（使用码率模式） |
| `--bitrate` | - | 平均视频码率（Mbps） | `10.0` |
| `--maxrate` | - | 最大视频码率（Mbps） | `10.0` |
| `--minrate` | - | 最小视频码率（Mbps） | `10.0` |
| `--bufsize` | - | 编码器缓冲区大小（Mbps） | `10.0` |
| `--name` | - | 输出文件基础名称 | 视频文件名 |
| `--verify` | - | 验证现有 Live Photo 配对 | - |

## 输出文件

程序会生成两个文件：

1. **静态图片**：`<name>.heic` 或 `<name>.jpg`
   - 包含 Apple MakerNote 元数据（Content Identifier）
   - HEIC 格式使用 nclx 色彩配置，确保 iOS 正确渲染

2. **视频文件**：`<name>.MOV`
   - HEVC (hvc1) 编码，BT.709 色彩空间
   - 包含完整的 Live Photo 元数据轨道：
     - `content.identifier`：与静态图片配对的唯一标识
     - `still-image-time`：标记静态帧位置
     - `live-photo-info`：60fps 运动数据轨道（用于动态壁纸）
   - 静音音轨（如果原视频无音频）
  
## 故障排除

### 生成的 Live Photo 无法设置为壁纸

- 检查视频码率是否过高（建议 < 20 Mbps）
- 检查视频分辨率是否小于1000x2166（通常选择886x1920），
- 检查视频帧率是否为60fps
- 检查视频时长是否至少 0.5 秒
- 确保 FFmpeg 包含 libx265、libx264 编码器
- 使用 `--verify` 检查元数据是否完整


## 技术细节

### Live Photo 结构

iOS Live Photo 由静态图片和视频文件通过 Content Identifier 配对：

```
静态图片 (HEIC/JPEG)
├── EXIF MakerNote
│   └── Content Identifier (UUID)
└── 像素数据

视频文件 (MOV)
├── moov/meta
│   └── com.apple.quicktime.content.identifier (UUID)
├── 视频轨道 (HEVC)
├── 音频轨道 (AAC)
└── 元数据轨道
    ├── still-image-time (标记静态帧)
    ├── live-photo-still-image-transform (变换矩阵)
    └── live-photo-info (60fps 运动数据，用于壁纸)
```

### 码率控制

- **CRF 模式**：当 `--crf < 18` 时，自动切换到固定码率模式（13 Mbps），防止超出 iOS 壁纸限制
- **固定码率模式**：默认使用 `--bitrate` 参数，建议 12-16 Mbps 以确保壁纸兼容性
- iOS 动态壁纸对视频码率有隐含限制（~16 Mbps），真机样本使用 11.8-15.5 Mbps

### 时间轴对齐

- 视频时长自动扩展到至少 1.05 秒（容纳 60 帧运动数据 + 0.05 秒空编辑）
- 静态帧位置固定在 0.5 秒处（与真机样本一致）
- 运动数据轨道使用固定 1000 ticks/帧（60fps）

