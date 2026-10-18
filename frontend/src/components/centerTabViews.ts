// 阶段 C · 中栏资源标签视图注册表（kind → 组件，可插拔）。
// 新增一种资源标签只需在 `RESOURCE_TAB_VIEWS` 登记一个组件（并补一个 kind），
// 中栏容器（CenterTabs）无需改动即可渲染。标签构造助手把「模型 / 方案 / 文件」
// 归一成 ShellTab（key + label + kind + payload），payload 即对应组件的 props。

import type { Component } from 'vue'
import ModelDetailView from '@/views/tabs/ModelDetailView.vue'
import SchemeContentView from '@/views/tabs/SchemeContentView.vue'
import FilePreviewView from '@/views/tabs/FilePreviewView.vue'
import {
  fileTabKey,
  modelTabKey,
  schemeTabKey,
  type ResourceTabKind,
  type ShellTab,
} from './centerTabs'

/** 资源标签种类 → 渲染组件。 */
export const RESOURCE_TAB_VIEWS: Record<ResourceTabKind, Component> = {
  model: ModelDetailView,
  scheme: SchemeContentView,
  file: FilePreviewView,
}

/** 按种类取渲染组件；未知种类返回 null（由容器兜底）。 */
export function resourceViewFor(
  kind: ResourceTabKind | string | null | undefined,
): Component | null {
  if (!kind) return null
  return RESOURCE_TAB_VIEWS[kind as ResourceTabKind] ?? null
}

/** 由模型构造模型详情标签（label 取模型编号）。 */
export function modelTab(model: {
  model_id: string
  algorithm?: string | null
}): ShellTab {
  return {
    key: modelTabKey(model.model_id),
    label: model.model_id,
    kind: 'model',
    payload: { modelId: model.model_id },
  }
}

/** 由方案名构造方案内容标签（label 默认取方案名）。 */
export function schemeTab(name: string, label?: string): ShellTab {
  return {
    key: schemeTabKey(name),
    label: label ?? name,
    kind: 'scheme',
    payload: { name },
  }
}

/** 由任务号 + 路径构造文件预览标签（label 默认取路径）。 */
export function fileTab(taskId: string, path: string, label?: string): ShellTab {
  return {
    key: fileTabKey(taskId, path),
    label: label ?? path,
    kind: 'file',
    payload: { taskId, path },
  }
}
