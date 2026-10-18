// FB.8 · 资源 store（§3.11 直通 REST，不经 LLM）。
// 覆盖四段：原始数据 / 清洗后（已发布）/ 方案文件 / 产出文件。
// 原始数据只读（远端维护）；方案上传经 Agent 转发到远端能力服务落盘，成功后**就地刷新**（重列方案）。
// 全部经 `@/api/resources`，仅在此处保留可共享的加载态与缓存。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  fileDownloadUrl,
  getSchemeContent,
  listPublishedData,
  listRawData,
  listSchemes,
  listTaskFiles,
  previewFile,
  uploadScheme as uploadSchemeApi,
} from '@/api/resources'
import type {
  FilePreviewResponse,
  PublishedDataset,
  RawDataset,
  SchemeContentResponse,
  SchemeInfo,
  SchemeListResponse,
  SchemeUploadResponse,
  TaskFile,
} from '@/api/types'

export type ResourceDomain = 'raw' | 'published' | 'schemes' | 'files'

/** 方案列表信封形状以 driver 为准（宽松收）。 */
export function extractSchemes(res: SchemeListResponse): SchemeInfo[] {
  for (const candidate of [res.schemes, res.items, res.list, res.data]) {
    if (Array.isArray(candidate)) return candidate as SchemeInfo[]
  }
  return []
}

export const useResourcesStore = defineStore('resources', () => {
  const rawDir = ref('')
  const rawDatasets = ref<RawDataset[]>([])
  const published = ref<PublishedDataset[]>([])
  const schemes = ref<SchemeInfo[]>([])
  const taskFiles = ref<Record<string, TaskFile[]>>({})
  const loading = ref<Record<ResourceDomain, boolean>>({
    raw: false,
    published: false,
    schemes: false,
    files: false,
  })
  // 错误按域隔离：某一段失败只在该段回显，不串到其它段（四段用 v-show 常驻 DOM）。
  const errors = ref<Record<ResourceDomain, string | null>>({
    raw: null,
    published: null,
    schemes: null,
    files: null,
  })

  const rawCount = computed(() => rawDatasets.value.length)
  const publishedCount = computed(() => published.value.length)
  const schemeCount = computed(() => schemes.value.length)

  function setLoading(domain: ResourceDomain, value: boolean): void {
    loading.value[domain] = value
  }

  function fail(err: unknown, fallback: string, domain: ResourceDomain): void {
    errors.value[domain] = err instanceof Error ? err.message : fallback
  }

  // --- §3.11.1 原始数据 ---------------------------------------------------

  async function loadRaw(): Promise<void> {
    setLoading('raw', true)
    try {
      const res = await listRawData()
      rawDir.value = res.dir ?? ''
      rawDatasets.value = res.datasets ?? []
      errors.value.raw = null
    } catch (err) {
      fail(err, '原始数据拉取失败', 'raw')
    } finally {
      setLoading('raw', false)
    }
  }

  // --- §3.11.2 清洗后数据 -------------------------------------------------

  async function loadPublished(): Promise<void> {
    setLoading('published', true)
    try {
      const res = await listPublishedData()
      published.value = res.datasets ?? []
      errors.value.published = null
    } catch (err) {
      fail(err, '已发布数据拉取失败', 'published')
    } finally {
      setLoading('published', false)
    }
  }

  // --- §3.11.3 方案文件 ---------------------------------------------------

  async function loadSchemes(): Promise<void> {
    setLoading('schemes', true)
    try {
      const res = await listSchemes()
      schemes.value = extractSchemes(res)
      errors.value.schemes = null
    } catch (err) {
      fail(err, '方案列表拉取失败', 'schemes')
    } finally {
      setLoading('schemes', false)
    }
  }

  /** 上传方案（经 Agent 转发到远端能力服务落盘），随后就地重列方案。校验结果随返回值交给视图层。 */
  async function uploadScheme(
    file: File,
    name?: string,
  ): Promise<SchemeUploadResponse> {
    const res = await uploadSchemeApi(file, name)
    await loadSchemes()
    return res
  }

  /** 取方案完整内容（§3.11.3，供「方案文件」段查看）。文件不存在 → FILE_NOT_FOUND。 */
  function loadSchemeContent(name: string): Promise<SchemeContentResponse> {
    return getSchemeContent(name)
  }

  // --- §3.11.4 产出文件 ---------------------------------------------------
  // 注意：后端按 **Hadoop 任务号** 定位 `var/tasks/<hadoop_task_id>/`，故此处 taskId
  // 一律为 `task.hadoop_task_id`（不是 agent_task_id）。

  async function loadTaskFiles(taskId: string): Promise<TaskFile[]> {
    setLoading('files', true)
    try {
      const res = await listTaskFiles(taskId)
      const files = res.files ?? []
      taskFiles.value[taskId] = files
      errors.value.files = null
      return files
    } catch (err) {
      fail(err, '产出文件拉取失败', 'files')
      return []
    } finally {
      setLoading('files', false)
    }
  }

  function preview(
    taskId: string,
    path: string,
  ): Promise<FilePreviewResponse> {
    return previewFile(taskId, path)
  }

  /** 下载地址（可直接挂到 <a download>）。 */
  function downloadUrl(taskId: string, path: string): string {
    return fileDownloadUrl(taskId, path)
  }

  function filesOf(taskId: string): TaskFile[] {
    return taskFiles.value[taskId] ?? []
  }

  function clear(): void {
    rawDir.value = ''
    rawDatasets.value = []
    published.value = []
    schemes.value = []
    taskFiles.value = {}
    errors.value = { raw: null, published: null, schemes: null, files: null }
  }

  return {
    rawDir,
    rawDatasets,
    published,
    schemes,
    taskFiles,
    loading,
    errors,
    rawCount,
    publishedCount,
    schemeCount,
    loadRaw,
    loadPublished,
    loadSchemes,
    uploadScheme,
    loadSchemeContent,
    loadTaskFiles,
    preview,
    downloadUrl,
    filesOf,
    clear,
  }
})
