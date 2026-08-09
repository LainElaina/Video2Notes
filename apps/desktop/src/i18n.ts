import { useCallback } from 'react'
import { useUiPreferences, type Locale } from './stores/uiPreferences'

type Message = string | ((params: Record<string, string | number>) => string)

/**
 * UI copy lives in one small module so the desktop shell and the long settings
 * workbench never drift into separate language systems. Technical product names
 * intentionally remain unchanged; only the surrounding explanation is localized.
 */
const messages: Record<string, { 'zh-CN': Message; 'en-US': Message }> = {
  'nav.create': { 'zh-CN': '新建任务', 'en-US': 'New task' },
  'nav.tasks': { 'zh-CN': '任务运行', 'en-US': 'Runs' },
  'nav.reader': { 'zh-CN': '笔记阅读', 'en-US': 'Reader' },
  'nav.settings': { 'zh-CN': '设置', 'en-US': 'Settings' },
  'nav.main': { 'zh-CN': '主导航', 'en-US': 'Main navigation' },
  'nav.workspaceMode': { 'zh-CN': '界面工作模式', 'en-US': 'Workspace mode' },
  'nav.guided': { 'zh-CN': '简约视图', 'en-US': 'Guided view' },
  'nav.professional': { 'zh-CN': '数据工作室', 'en-US': 'Data studio' },
  'nav.theme': { 'zh-CN': '主题预置', 'en-US': 'Theme preset' },
  'nav.language': { 'zh-CN': '界面语言', 'en-US': 'Interface language' },
  'nav.language.zh': { 'zh-CN': '中文', 'en-US': 'Chinese' },
  'nav.language.en': { 'zh-CN': 'English', 'en-US': 'English' },
  'nav.backend.real': { 'zh-CN': (p) => `后端 ${p.version || '正常'}`, 'en-US': (p) => `Backend ${p.version || 'ready'}` },
  'nav.backend.demo': { 'zh-CN': '演示模式', 'en-US': 'Demo mode' },
  'nav.backend.connecting': { 'zh-CN': '正在连接', 'en-US': 'Connecting' },
  'nav.backend.offline': { 'zh-CN': '后端离线', 'en-US': 'Backend offline' },
  'nav.backend.status': { 'zh-CN': '后端状态', 'en-US': 'Backend status' },
  'nav.brand.back': { 'zh-CN': '返回新建任务', 'en-US': 'Back to new task' },
  'nav.context.collapse': { 'zh-CN': '收起上下文栏', 'en-US': 'Collapse context panel' },
  'nav.context.expand': { 'zh-CN': '展开侧栏', 'en-US': 'Expand sidebar' },
  'nav.notice.close': { 'zh-CN': '关闭提示', 'en-US': 'Dismiss notice' },
  'settings.kicker': { 'zh-CN': 'Video2Notes 偏好设置', 'en-US': 'Video2Notes preferences' },
  'settings.title': { 'zh-CN': '设置中心', 'en-US': 'Settings center' },
  'settings.description': { 'zh-CN': '统一管理界面可读性、电脑性能余量、本地依赖和各处理角色的模型路由。', 'en-US': 'Manage readability, machine headroom, local dependencies, and model routing for every processing role.' },
  'settings.auto': { 'zh-CN': '自动配置', 'en-US': 'Automatic setup' },
  'settings.custom': { 'zh-CN': '自定义性能', 'en-US': 'Custom performance' },
  'settings.directory': { 'zh-CN': '设置目录', 'en-US': 'Settings index' },
  'settings.sections': { 'zh-CN': '工作台分区', 'en-US': 'Workbench sections' },
  'settings.appearance': { 'zh-CN': '显示与字体', 'en-US': 'Display & type' },
  'settings.appearance.detail': { 'zh-CN': '字号与可读性', 'en-US': 'Size and readability' },
  'settings.runtime': { 'zh-CN': '依赖与运行时', 'en-US': 'Dependencies & runtimes' },
  'settings.runtime.detail': { 'zh-CN': '检测、绑定与安装', 'en-US': 'Discover, bind, install' },
  'settings.components': { 'zh-CN': '本地识别模型', 'en-US': 'Local recognition models' },
  'settings.components.detail': { 'zh-CN': 'ASR / OCR 权重', 'en-US': 'ASR / OCR weights' },
  'settings.performance': { 'zh-CN': '性能与资源', 'en-US': 'Performance & resources' },
  'settings.performance.detail': { 'zh-CN': '余量与处理策略', 'en-US': 'Headroom and policy' },
  'settings.providers': { 'zh-CN': '模型供应商', 'en-US': 'Model providers' },
  'settings.providers.detail': { 'zh-CN': '协议与认证', 'en-US': 'Protocols and auth' },
  'settings.registry': { 'zh-CN': '模型目录', 'en-US': 'Model registry' },
  'settings.registry.detail': { 'zh-CN': '发现与登记', 'en-US': 'Discover and register' },
  'settings.routing': { 'zh-CN': '处理角色', 'en-US': 'Processing roles' },
  'settings.routing.detail': { 'zh-CN': '阶段模型绑定', 'en-US': 'Stage model bindings' },
  'settings.font.title': { 'zh-CN': '让长时间阅读保持舒服', 'en-US': 'Keep long reading comfortable' },
  'settings.font.description': { 'zh-CN': '全局字号会同时影响导航、设置、任务状态和笔记阅读，不会只放大某一个面板。', 'en-US': 'The global type scale applies consistently to navigation, settings, run status, and notes.' },
  'settings.font.compact': { 'zh-CN': '紧凑', 'en-US': 'Compact' },
  'settings.font.comfortable': { 'zh-CN': '舒适', 'en-US': 'Comfortable' },
  'settings.font.large': { 'zh-CN': '大字', 'en-US': 'Large' },
  'settings.font.compact.detail': { 'zh-CN': '保留更多同屏信息，正文仍保持可读下限。', 'en-US': 'Keep more information visible while retaining a readable body size.' },
  'settings.font.comfortable.detail': { 'zh-CN': '默认档位，适合长时间阅读和日常操作。', 'en-US': 'The default scale for long reading and daily operation.' },
  'settings.font.large.detail': { 'zh-CN': '放大正文、标签与控件文字，适合高分屏。', 'en-US': 'Larger body, labels, and controls for high-density displays.' },
  'language.zh': { 'zh-CN': '中文', 'en-US': 'Chinese' },
  'language.en': { 'zh-CN': 'English', 'en-US': 'English' },
  'common.copy': { 'zh-CN': '复制', 'en-US': 'Copy' },
  'common.copied': { 'zh-CN': '已复制', 'en-US': 'Copied' },
  'common.edit': { 'zh-CN': '修改', 'en-US': 'Edit' },
  'common.refresh': { 'zh-CN': '刷新', 'en-US': 'Refresh' },
  'common.discover': { 'zh-CN': '检测本机环境', 'en-US': 'Scan this computer' },
  'common.bind': { 'zh-CN': '绑定', 'en-US': 'Bind' },
  'common.rebind': { 'zh-CN': '重新绑定', 'en-US': 'Rebind' },
  'common.unbind': { 'zh-CN': '解绑', 'en-US': 'Unbind' },
  'common.cancel': { 'zh-CN': '取消', 'en-US': 'Cancel' },
  'common.close': { 'zh-CN': '关闭', 'en-US': 'Close' },
  'common.unknown': { 'zh-CN': '未知', 'en-US': 'Unknown' },
  'runtime.title': { 'zh-CN': '可选依赖与运行时', 'en-US': 'Optional dependencies & runtimes' },
  'runtime.description': { 'zh-CN': '优先复用本机已有工具；缺少时再安装到应用数据目录。所有路径都可以重新绑定。', 'en-US': 'Reuse tools already on this computer first, then install missing components into app data. Every path can be rebound.' },
  'runtime.path': { 'zh-CN': '路径', 'en-US': 'Path' },
  'runtime.version': { 'zh-CN': '版本', 'en-US': 'Version' },
  'runtime.capabilities': { 'zh-CN': '能力', 'en-US': 'Capabilities' },
  'runtime.compatible': { 'zh-CN': '兼容', 'en-US': 'Compatible' },
  'runtime.notFound': { 'zh-CN': '未发现', 'en-US': 'Not found' },
  'runtime.manual': { 'zh-CN': '手动选择程序或目录', 'en-US': 'Choose a program or folder' },
  'runtime.bindHelp': { 'zh-CN': '无法自动识别时，可指定 exe、脚本或包含运行时的目录。', 'en-US': 'When automatic discovery fails, choose an executable, script, or runtime folder.' },
  'runtime.copyPath': { 'zh-CN': '复制路径', 'en-US': 'Copy path' },
  'runtime.changePath': { 'zh-CN': '修改路径', 'en-US': 'Change path' },
  'runtime.rescan': { 'zh-CN': '重新检测', 'en-US': 'Rescan' },
  'runtime.noTools': { 'zh-CN': '尚未发现本机依赖', 'en-US': 'No local dependencies found' },
  'runtime.noToolsDetail': { 'zh-CN': '点击检测本机环境，或为具体依赖手动选择路径。', 'en-US': 'Scan this computer or choose a path for a specific dependency.' },
  'error.backend': { 'zh-CN': (p) => `无法连接本机 Video2Notes 后端。详情：${p.detail || '未知错误'}`, 'en-US': (p) => `Could not connect to the local Video2Notes backend. Details: ${p.detail || 'Unknown error'}` },
  'status.ready': { 'zh-CN': '已就绪', 'en-US': 'Ready' },
  'status.missing': { 'zh-CN': '路径缺失', 'en-US': 'Path missing' },
  'status.invalid': { 'zh-CN': '校验失败', 'en-US': 'Validation failed' },
  'status.degraded': { 'zh-CN': '受限可用', 'en-US': 'Degraded' },
}

export function translate(
  key: string,
  locale: Locale,
  params: Record<string, string | number> = {},
): string {
  const entry = messages[key]
  if (!entry) return key
  const value = entry[locale]
  return typeof value === 'function' ? value(params) : value
}

export function useI18n() {
  const locale = useUiPreferences(state => state.locale)
  const t = useCallback(
    (key: string, params?: Record<string, string | number>) => translate(key, locale, params),
    [locale],
  )
  const text = useCallback(
    (zh: string, en: string) => (locale === 'zh-CN' ? zh : en),
    [locale],
  )
  return { locale, t, text }
}

export const localeLabel = (locale: Locale): string =>
  locale === 'zh-CN' ? '中文' : 'English'
