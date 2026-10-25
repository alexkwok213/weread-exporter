// 生成「微信读书导出助手」的 App 图标（1024×1024 母版 PNG）
//
// ⚠️⚠️ 第一原则：整张画布 100% 不透明铺满，绝不自己画圆角、绝不留透明像素 ⚠️⚠️
//   macOS 26/27（Tahoe 及之后）的 Icon Services 会检查 alpha 通道。图标里只要出现
//   「超出规定形状的像素」（哪怕只是四个角那点透明），系统就判定为「老式图标」，
//   把图整体缩小、后面垫一层灰色底盘 —— 俗称 "squircle jail"（图标监狱）。
//   症状：图标偏小、外面套一层灰框。
//
//   本项目 2.1 版就是这个毛病：母版不透明占比只有 64.18%，系统渲染后套了灰底盘。
//   反之，只要整幅不透明铺满，系统就自己套标准圆角，拿到的就是原生待遇
//   （渲染几何与 /System/Applications/Calculator.app 完全一致）。
//
//   代价：macOS 15 及更早系统不会自动圆角，会显示成直角方块。
//   要同时兼顾两端，唯一正统做法是 Xcode 26 的 Icon Composer 编 .icon，
//   但 actool 只在完整 Xcode 里、只用 CommandLineTools 编不了 —— 二选一，本项目选现代优先。
//
// 第二原则：字形用实心大形状，不画细描边、不做 3D 拟物。
//   1024 缩到 16px（Finder 列表、Dock 小尺寸）时细节全部糊掉。
//   2.1 版那套带投影、书页翻卷、渐变高光的 3D 风格，缩到 32px 就糊成一坨白块。
//   改成单色实心剪影后，16px 仍能读出「一本书 + 一个下箭头」。
//
// 第三原则：绝对不能用 CGBlendMode.clear 挖洞。
//   那会产生真正的透明像素 —— 正是触发图标监狱的条件。
//   需要「镂空露出底色」时（本图是书脊中缝），正确做法是把底色渐变原样画回该区域，
//   视觉上是挖空，但画布依然 100% 不透明。注意必须画得干净，不能留半透明边缘。
//
// 第四原则（本图踩过的坑）：不要让两个白形状互相重叠。
//   第一版把箭头压在书页上，结果箭杆被书脊缝切断、箭头被书页吞掉，32px 下完全糊掉。
//   改成上下分离构图：书在上、箭头在下，中间留 46px 蓝底间隙 ——
//   间隙在小尺寸下会自然融合成一条边，轮廓反而更清楚。
//
// 设计：微信蓝满幅渐变底 + 一个大号实心剪影 = 摊开的书 + 一个下箭头（导出到本地）。
//   书：左右两页严格镜像（用坐标映射实现，不用 scale(-1,1)，否则渐变坐标会跟着翻转）。
//       顶边外高内低形成 V 形开口，底边向中缝下垂 —— 光靠这条剪影轮廓，
//       即使 16px 下中缝细到看不见，也还是能读出「一本摊开的书」。
//   箭头：粗下箭头，箭杆短而粗、箭头宽大。放大头部是因为箭尖那个三角形
//       是小尺寸下唯一能稳定存活的识别特征，箭杆细到 1px 就消失了。

import CoreGraphics
import Foundation
import ImageIO

let S: CGFloat = 1024
let cs = CGColorSpaceCreateDeviceRGB()

func rgba(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor {
    CGColor(colorSpace: cs, components: [r, g, b, a])!
}

guard let ctx = CGContext(data: nil, width: Int(S), height: Int(S),
                          bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    fatalError("无法创建绘图上下文")
}
ctx.setAllowsAntialiasing(true)
ctx.setShouldAntialias(true)

// ── 调色板 ──
// 底色沿用微信读书的品牌蓝，压深底、提亮顶，让纯色底在 Dock 里也有体积感。
let bg = CGGradient(colorsSpace: cs, colors: [
    rgba(0.055, 0.376, 0.827),   // 底  深蓝
    rgba(0.110, 0.541, 0.886),   // 中  品牌蓝
    rgba(0.302, 0.784, 0.941),   // 顶  亮青蓝
] as CFArray, locations: [0, 0.58, 1])!

// 顶部柔光，让平面渐变有一点体积感
let gloss = CGGradient(colorsSpace: cs, colors: [
    rgba(1, 1, 1, 0.20),
    rgba(1, 1, 1, 0.0),
] as CFArray, locations: [0, 1])!

// 底部内阴影，压住下边缘
let shade = CGGradient(colorsSpace: cs, colors: [
    rgba(0, 0, 0, 0.20),
    rgba(0, 0, 0, 0.0),
] as CFArray, locations: [0, 1])!

// 字形渐变：纯白 → 极淡冷白。用渐变而非平白，是为了 1024 大图下有一点材质感；
// 到 16px 时这点差异可忽略，不影响识别。
let glyphGrad = CGGradient(colorsSpace: cs, colors: [
    rgba(1.000, 1.000, 1.000),
    rgba(0.906, 0.953, 0.992),
] as CFArray, locations: [0, 1])!

// 底色三层统一画法 —— 挖空时原样重画（绝不用 clear 挖洞）
func paintBackground() {
    ctx.drawLinearGradient(bg, start: CGPoint(x: 512, y: 0), end: CGPoint(x: 512, y: S), options: [])
    ctx.drawLinearGradient(gloss, start: CGPoint(x: 512, y: S), end: CGPoint(x: 512, y: S * 0.66), options: [])
    ctx.drawLinearGradient(shade, start: CGPoint(x: 512, y: 0), end: CGPoint(x: 512, y: S * 0.22), options: [])
}

// 往路径里原样铺回底色（= 视觉上的镂空，但保持 100% 不透明）
func fillBackground(_ path: CGPath) {
    ctx.saveGState()
    ctx.addPath(path)
    ctx.clip()
    paintBackground()
    ctx.restoreGState()
}

// ── 1. 底：铺满整张画布，不画圆角（圆角交给系统）
paintBackground()

// ── 2. 图形布局
//     系统会自动切掉四角，内容要留在中间安全区，同时够大够醒目。
//     整体占画布约 60% 宽 / 59% 高，居中。
let bookHalfW: CGFloat = 320          // 书半宽
let pageH: CGFloat = 290// 单页高度
let fanUp: CGFloat = 48// 外缘比中缝高出的量（V 形开口的深度）
let drape: CGFloat = 22// 外缘底比中缝底高出的量（下缘向中缝下垂）
let spineHalf: CGFloat = 13           // 书脊中缝半宽

// 竖向排布：书 → 46px 间隙 → 箭头
let bookTopInner: CGFloat = 778       // 中缝侧顶端
let bookTopOuter = bookTopInner + fanUp
let bookBotInner: CGFloat = bookTopInner - pageH
let bookBotOuter = bookBotInner + drape

let shaftTop: CGFloat = bookBotInner - 48
let neckY: CGFloat = shaftTop - 74
let tipY: CGFloat = neckY - 118
let shaftHalfW: CGFloat = 50
let headHalfW: CGFloat = 158

// 本地坐标 x=0 是书脊中轴；用显式坐标映射做镜像
func P(_ x: CGFloat, _ y: CGFloat, _ mirrored: Bool) -> CGPoint {
    mirrored ? CGPoint(x: 512 - x, y: y) : CGPoint(x: 512 + x, y: y)
}

// 单页路径。中缝侧是竖直内缘，外缘竖直，上下边各带弧度。
func makePage(_ mirrored: Bool) -> CGPath {
    let p = CGMutablePath()
    p.move(to: P(spineHalf, bookTopInner, mirrored))
    // 顶边：由中缝向外上方扬起，形成摊开的 V
    p.addQuadCurve(to: P(bookHalfW, bookTopOuter, mirrored),
                   control: P(spineHalf + (bookHalfW - spineHalf) * 0.52,
                              bookTopInner + fanUp * 0.42, mirrored))
    // 外缘：向下
    p.addLine(to: P(bookHalfW, bookBotOuter, mirrored))
    // 底边：向中缝下垂
    p.addQuadCurve(to: P(spineHalf, bookBotInner, mirrored),
                   control: P(spineHalf + (bookHalfW - spineHalf) * 0.46,
                              bookBotInner - drape * 1.1, mirrored))
    p.closeSubpath()   // 内缘自动闭合为竖线
    return p
}

// 箭：短粗箭杆 + 宽大箭头。头部做大是因为箭尖三角形是小尺寸下唯一稳定的识别特征。
let arrow = CGMutablePath()
let r: CGFloat = 16   // 箭杆顶部小圆角
let tipRound: CGFloat = 96 // 箭尖圆化量：越大箭尖越圆，越小越尖。96 是「圆润但不软」的值
arrow.move(to: CGPoint(x: 512 - shaftHalfW + r, y: shaftTop))
arrow.addLine(to: CGPoint(x: 512 + shaftHalfW - r, y: shaftTop))
arrow.addQuadCurve(to: CGPoint(x: 512 + shaftHalfW, y: shaftTop - r),
                   control: CGPoint(x: 512 + shaftHalfW, y: shaftTop))
arrow.addLine(to: CGPoint(x: 512 + shaftHalfW, y: neckY))
arrow.addLine(to: CGPoint(x: 512 + headHalfW, y: neckY))
// 两侧微鼓，箭尖收成软圆头（尖刺在 16px 会闪成一个点，反而认不出是箭头）
arrow.addQuadCurve(to: CGPoint(x: 512, y: tipY),
                   control: CGPoint(x: 512 + headHalfW * 0.30, y: tipY + tipRound))
arrow.addQuadCurve(to: CGPoint(x: 512 - headHalfW, y: neckY),
                   control: CGPoint(x: 512 - headHalfW * 0.30, y: tipY + tipRound))
arrow.addLine(to: CGPoint(x: 512 - shaftHalfW, y: neckY))
arrow.addLine(to: CGPoint(x: 512 - shaftHalfW, y: shaftTop - r))
arrow.addQuadCurve(to: CGPoint(x: 512 - shaftHalfW + r, y: shaftTop),
                   control: CGPoint(x: 512 - shaftHalfW, y: shaftTop))
arrow.closeSubpath()

// 画字形：书页 + 箭头放在同一个 clip 里填渐变，保证同色无缝、不出现接缝
let glyphTop = bookTopOuter
let glyphBottom = tipY
ctx.saveGState()
ctx.addPath(makePage(false))
ctx.addPath(makePage(true))
ctx.addPath(arrow)
ctx.clip()
ctx.drawLinearGradient(glyphGrad,
                       start: CGPoint(x: 512, y: glyphTop),
                       end: CGPoint(x: 512, y: glyphBottom), options: [])
ctx.restoreGState()

// ── 3. 书脊中缝：把底色画回中间那道竖缝，视觉上表示「书是摊开的」
//     缝高只覆盖书页范围，箭头在下方不受影响。
//     用 clear 挖真透明会直接触发图标监狱，所以是「铺回底色」。
let gap = CGPath(rect: CGRect(x: 512 - spineHalf,
                              y: bookBotInner - 2,
                              width: spineHalf * 2,
                              height: pageH + fanUp + 4), transform: nil)
fillBackground(gap)

// ── 输出 PNG
guard let image = ctx.makeImage() else { fatalError("生成图像失败") }
let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon-1024.png")
guard let dest = CGImageDestinationCreateWithURL(out as CFURL, "public.png" as CFString, 1, nil) else {
    fatalError("无法创建输出文件")
}
CGImageDestinationAddImage(dest, image, nil)
guard CGImageDestinationFinalize(dest) else { fatalError("写入 PNG 失败") }
print("已生成 \(out.path)")
