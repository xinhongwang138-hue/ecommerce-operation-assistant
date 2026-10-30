import {
  Bell,
  BookOpen,
  Bot,
  ClipboardCheck,
  ClipboardList,
  Database,
  FileCheck2,
  FlaskConical,
  Headset,
  LayoutDashboard,
  Library,
  ScanText,
  Scissors,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** 桌面端侧边栏的副标题，移动端不显示。 */
  hint: string;
};

/**
 * 全站导航的唯一来源。
 *
 * 桌面侧边栏与移动端抽屉共用这一份：此前两边各写一份数组，
 * 结果移动端少了 8 条（合同审批、直播切片、模型微调都点不到），
 * 是维护时最容易漏的地方。
 */
export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "工作台", icon: LayoutDashboard, hint: "多平台店铺运营总览" },
  { href: "/alerts", label: "待办中心", icon: Bell, hint: "低库存与异常收口" },
  { href: "/agent", label: "Agent 中心", icon: Bot, hint: "运营智能体任务" },
  { href: "/cs", label: "AI 客服", icon: Headset, hint: "四轨道分流与转人工" },
  { href: "/imports", label: "导入中心", icon: ClipboardList, hint: "商品/经营数据导入" },
  { href: "/knowledge", label: "掌柜智库", icon: BookOpen, hint: "资料入库与检索引用" },
  { href: "/data-platform", label: "数据中台", icon: Database, hint: "四层建模与指标口径" },
  { href: "/ask", label: "问数", icon: Sparkles, hint: "中文提问直接出 SQL" },
  { href: "/rag", label: "知识库", icon: Library, hint: "自建切片与混合检索" },
  { href: "/qa", label: "销售考核", icon: ClipboardCheck, hint: "会话质检与坐席排行" },
  { href: "/contracts", label: "合同审批", icon: FileCheck2, hint: "条款风险与留痕审批" },
  { href: "/live", label: "直播切片", icon: Scissors, hint: "转写稿里挑卖点片段" },
  { href: "/finetune", label: "模型微调", icon: FlaskConical, hint: "样本回流与评测台账" },
  { href: "/ocr", label: "图片取字", icon: ScanText, hint: "图片文字识别" },
  { href: "/settings", label: "模型配置", icon: Settings, hint: "模型与用户管理" },
];
