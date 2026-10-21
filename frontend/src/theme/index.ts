import {
  lightTheme,
  type GlobalTheme,
  type GlobalThemeOverrides,
} from 'naive-ui'

// 阶段 C · FC.6（迭代打磨）：全局主题。
// 一个主色 + 中性灰阶 + 统一字号阶梯 / 圆角 / 间距语义。
// 字号 / 字体尽量走主题（common），避免散落硬编码；组件级只做必要收敛。

/** 现代字族：西文优先 Inter / Segoe UI，中文 PingFang SC / 微软雅黑。 */
export const FONT_FAMILY =
  'Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, sans-serif'

/** 等宽字族：代码 / 参数 / 文件路径。 */
export const FONT_FAMILY_MONO =
  '"JetBrains Mono", "Cascadia Code", Consolas, "Courier New", monospace'

const PRIMARY = '#2f6fed'
const PRIMARY_HOVER = '#4a83f2'
const PRIMARY_PRESSED = '#1f57c4'
const PRIMARY_SUPPL = '#4a83f2'

// 中性灰阶（克制、偏冷调，和主色同色温）。对齐 Tailwind gray，观感更干净。
const TEXT_1 = '#111827'
const TEXT_2 = '#4b5563'
const TEXT_3 = '#6b7280'
const BORDER = '#e5e7eb'
const BODY = '#f7f8fa'
const CARD = '#ffffff'

export const naiveTheme: GlobalTheme = lightTheme

export const themeOverrides: GlobalThemeOverrides = {
  common: {
    fontFamily: FONT_FAMILY,
    fontFamilyMono: FONT_FAMILY_MONO,

    // 字号阶梯：正文基准 17px，行高 1.7 舒展；标题分级 19 / 22。
    fontSize: '17px',
    fontSizeTiny: '14px',
    fontSizeSmall: '15px',
    fontSizeMedium: '17px',
    fontSizeLarge: '19px',
    fontSizeHuge: '22px',
    lineHeight: '1.7',
    fontWeightStrong: '600',

    // 8px 栅格体系下的圆角语义（柔和、统一）。
    borderRadius: '10px',
    borderRadiusSmall: '8px',

    // 主色（唯一主色）。
    primaryColor: PRIMARY,
    primaryColorHover: PRIMARY_HOVER,
    primaryColorPressed: PRIMARY_PRESSED,
    primaryColorSuppl: PRIMARY_SUPPL,

    // 文本 / 边框 / 背景（层次与分隔）。
    textColor1: TEXT_1,
    textColor2: TEXT_2,
    textColor3: TEXT_3,
    borderColor: BORDER,
    dividerColor: BORDER,
    bodyColor: BODY,
    cardColor: CARD,
    modalColor: CARD,
    popoverColor: CARD,

    // 控件高度阶梯（触控友好）。
    heightTiny: '28px',
    heightSmall: '34px',
    heightMedium: '40px',
    heightLarge: '44px',
    heightHuge: '48px',

    // 阴影克制，用于卡片 / 浮层分层。
    boxShadow1: '0 1px 2px rgba(16, 24, 40, 0.04)',
    boxShadow2: '0 4px 16px rgba(16, 24, 40, 0.08)',
    boxShadow3: '0 12px 32px rgba(16, 24, 40, 0.14)',
  },

  Button: {
    fontWeight: '500',
    fontWeightStrong: '600',
    borderRadiusMedium: '10px',
    borderRadiusSmall: '8px',
  },

  Input: {
    fontSizeMedium: '17px',
    borderRadius: '10px',
  },

  Tabs: {
    tabFontWeightActive: '600',
  },

  Tag: {
    borderRadiusSmall: '999px',
  },

  Drawer: {
    bodyPadding: '20px 24px',
    headerPadding: '18px 24px',
    footerPadding: '16px 24px',
  },
}

export default themeOverrides
